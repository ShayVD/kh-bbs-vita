/* The snapshot hook writes guest RAM and a sidecar once, just before the K-th
 * matching firmware call. Synthetic, no game data. */
#include "khbbs/snapshot.h"
#include "khbbs/trace.h"
#include "psprecomp/hle.h"
#include "psprecomp/cpu.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

#define NID_OPEN  0x109F50BCu      /* sceIoOpen */
#define NID_CLOSE 0x810C4BC3u      /* sceIoClose */
#define NID_NONE  0xDEADBEEFu      /* unregistered */
#define MARK      0x08900000u      /* each handler call bumps this word */

static void fake(void) { psp_write32(MARK, psp_read32(MARK) + 1); psp_ret(0); }

/* The whole file, or NULL; *n is its size. */
static unsigned char *slurp(const char *path, long *n) {
    *n = -1;
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *n = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *b = malloc((size_t)*n + 1);
    if (b) { b[fread(b, 1, (size_t)*n, f)] = 0; }
    fclose(f);
    return b;
}

static int exists(const char *path) {
    FILE *f = fopen(path, "rb");
    if (f) fclose(f);
    return f != NULL;
}

/* A directory: a file can be made inside it. */
static int is_dir(const char *path) {
    char probe[256];
    snprintf(probe, sizeof probe, "%s/probe", path);
    FILE *f = fopen(probe, "w");
    if (!f) return 0;
    fclose(f);
    remove(probe);
    return 1;
}

static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int main(void) {
    psp_cpu_reset();
    CHECK(psp_mem_init() == 0, "mem init");
    psp_hle_register(NID_OPEN, "IoFileMgrForUser", "sceIoOpen", fake);
    psp_hle_register(NID_CLOSE, "IoFileMgrForUser", "sceIoClose", fake);
    psp_write32(PSP_RAM_BASE, 0xA1B2C3D4u);
    psp_write32(PSP_RAM_BASE + PSP_RAM_SIZE - 4, 0x01020304u);
    psp_write32(MARK, 0);

    /* NAME:K, with a non-matching call in between and the trace hook on too. */
    CHECK(khbbs_trace_open("t_snap.jsonl") == 0, "trace open");
    CHECK(khbbs_snapshot_arm("sceIoOpen:2", "t_snap.bin") == 0, "arm by name");
    CHECK(khbbs_snapshot_fired() == 0, "not fired yet");
    psp_cpu.r[PSP_REG_RA] = 0x08804100u;
    psp_hle_call(NID_OPEN);                     /* match 1: no dump */
    CHECK(!exists("t_snap.bin") && khbbs_snapshot_fired() == 0, "no dump at the first match");
    psp_hle_call(NID_CLOSE);                    /* other name: ignored */
    psp_hle_call(NID_NONE);                     /* no name: ignored */
    CHECK(!exists("t_snap.bin"), "non-matching calls ignored");
    psp_cpu.r[PSP_REG_RA] = 0x08804200u;
    psp_hle_call(NID_OPEN);                     /* match 2: dump, before the handler */
    CHECK(khbbs_snapshot_fired() == 1, "fired at the second match");
    long n;
    unsigned char *dump = slurp("t_snap.bin", &n);
    CHECK(dump && n == 0x02000000, "dump is 32 MiB");
    if (dump && n == 0x02000000) {
        CHECK(le32(dump) == 0xA1B2C3D4u, "first word");
        CHECK(le32(dump + PSP_RAM_SIZE - 4) == 0x01020304u, "last word");
        CHECK(le32(dump + (MARK - PSP_RAM_BASE)) == 2, "taken before the handler ran");
    }
    free(dump);
    CHECK(psp_read32(MARK) == 3, "handler still ran");
    unsigned char *side = slurp("t_snap.bin.json", &n);
    CHECK(side && !strcmp((char *)side,
        "{\"base\":\"0x08000000\",\"size\":33554432,\"event\":\"sceIoOpen:2\",\"side\":\"khbbs\","
        "\"nid\":\"0x109F50BC\",\"ra\":\"0x08804200\"}\n"), "sidecar exact");
    free(side);
    CHECK(khbbs_trace_close() == 0 && khbbs_trace_count() == 4, "trace hook saw every call");

    /* Exactly once: a third match writes nothing. */
    remove("t_snap.bin");
    psp_hle_call(NID_OPEN);
    CHECK(!exists("t_snap.bin") && khbbs_snapshot_fired() == 1, "one dump only");

    /* 0xNID:K matches an unregistered NID; arming removes stale outputs. */
    FILE *f = fopen("t_snap.bin", "wb");
    if (f) fclose(f);
    CHECK(khbbs_snapshot_arm("0xdeadbeef:1", "t_snap.bin") == 0, "arm by NID");
    CHECK(!exists("t_snap.bin") && !exists("t_snap.bin.json"), "stale outputs removed");
    CHECK(khbbs_snapshot_fired() == 0, "re-arm resets fired");
    psp_hle_call(NID_OPEN);
    CHECK(!exists("t_snap.bin"), "other NID ignored");
    psp_cpu.r[PSP_REG_RA] = 0x08804300u;
    psp_hle_call(NID_NONE);
    CHECK(khbbs_snapshot_fired() == 1, "fired on the NID");
    side = slurp("t_snap.bin.json", &n);
    CHECK(side && strstr((char *)side, "\"event\":\"0xdeadbeef:1\"") &&
          strstr((char *)side, "\"nid\":\"0xDEADBEEF\",\"ra\":\"0x08804300\""), "NID sidecar");
    free(side);

    /* A registered name also matches by NID. */
    CHECK(khbbs_snapshot_arm("0x109F50BC:1", "t_snap.bin") == 0, "arm by registered NID");
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == 1, "registered NID fires");

    /* Disarm: nothing fires. */
    CHECK(khbbs_snapshot_arm("sceIoOpen:1", "t_snap.bin") == 0, "arm again");
    khbbs_snapshot_disarm();
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == 0 && !exists("t_snap.bin"), "disarmed");

    /* An unwritable path: the event fires, the write fails. */
    CHECK(khbbs_snapshot_arm("sceIoOpen:1", "/nonexistent-dir/x.bin") == 0, "arm unwritable");
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == -1, "write failure reported");
    CHECK(!exists("/nonexistent-dir/x.bin") && !exists("/nonexistent-dir/x.bin.json"),
          "nothing left after a failed write");

    /* Directories in the way (made by the snapshot_dirs fixture) are not
     * removed: remove() also deletes an empty directory. */
    CHECK(is_dir("t_snap_dir.bin") && is_dir("t_snap_dir.bin.json") && is_dir("t_snap_sd.bin.json"),
          "fixture directories exist");
    CHECK(khbbs_snapshot_arm("sceIoOpen:1", "t_snap_dir.bin") == 0, "arm over directories");
    CHECK(is_dir("t_snap_dir.bin") && is_dir("t_snap_dir.bin.json"), "arming keeps directories");
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == -1 && is_dir("t_snap_dir.bin"), "dump onto a directory fails");
    CHECK(khbbs_snapshot_arm("sceIoOpen:1", "t_snap_sd.bin") == 0, "arm with a sidecar directory");
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == -1, "sidecar onto a directory fails");
    CHECK(!exists("t_snap_sd.bin") && is_dir("t_snap_sd.bin.json"), "failed write keeps the directory");

    /* The dump writes but its sidecar cannot: a 251-byte file name is fine,
     * the sidecar's 256 bytes are over NAME_MAX. This relies on NAME_MAX
     * being 255, as on macOS and Linux. Neither is left behind. */
    char lng[252];
    memset(lng, 'x', sizeof lng - 1);
    memcpy(lng, "t_snap_", 7);
    memcpy(lng + sizeof lng - 5, ".bin", 5);
    CHECK(khbbs_snapshot_arm("sceIoOpen:1", lng) == 0, "arm long name");
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == -1, "sidecar failure reported");
    CHECK(!exists(lng), "dump removed after a sidecar failure");
    remove(lng);

    /* Bad events are rejected and leave nothing armed. */
    static const char *bad[] = {
        "", "sceIoOpen", "sceIoOpen:", "sceIoOpen:0", ":1", "0x:1", "0xZZ:1", "0x123456789:1",
        "sceIoOpen:-1", "sceIoOpen:1x", "sceIoOpen: 1", "1abc:1", "sce Io:1", "sceIoOpen:1:2",
        "sceIoOpen:99999999999",
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        char m[64];
        snprintf(m, sizeof m, "bad event \"%s\" rejected", bad[i]);
        CHECK(khbbs_snapshot_arm(bad[i], "t_snap.bin") == -1, m);
    }
    CHECK(khbbs_snapshot_arm(NULL, "t_snap.bin") == -1, "NULL event rejected");
    CHECK(khbbs_snapshot_arm("sceIoOpen:1", NULL) == -1, "NULL path rejected");
    psp_hle_call(NID_OPEN);
    CHECK(khbbs_snapshot_fired() == 0 && !exists("t_snap.bin"), "bad arm leaves nothing armed");
    CHECK(khbbs_snapshot_arm("sceIoOpen:0x2", "t_snap.bin") == 0, "hex K accepted");
    khbbs_snapshot_disarm();

    remove("t_snap.bin");
    remove("t_snap.bin.json");
    remove("t_snap.jsonl");
    printf("%s\n", failures ? "test_snapshot: FAILED" : "test_snapshot: ok");
    return failures ? 1 : 0;
}
