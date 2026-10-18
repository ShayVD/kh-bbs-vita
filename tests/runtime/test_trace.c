/* The trace writer emits one JSON object per firmware call, in a format
 * tools/oracle (P2) will parse. Synthetic, no game data. */
#include "khbbs/trace.h"
#include "psprecomp/hle.h"
#include "psprecomp/cpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static void fake(void) { psp_cpu.r[PSP_REG_V1] = 0x00000001u; psp_ret(7); }

int main(void) {
    psp_cpu_reset();
    psp_hle_register(0x109F50BCu, "IoFileMgrForUser", "sceIoOpen", fake);
    CHECK(khbbs_trace_open("t_trace.jsonl") == 0, "open");
    psp_cpu.r[PSP_REG_A0] = 0x08900000u;
    psp_cpu.r[PSP_REG_RA] = 0x08804100u;
    psp_hle_call(0x109F50BCu);
    psp_hle_call(0xDEADBEEFu);
    CHECK(khbbs_trace_close() == 0, "close ok");

    char buf[1024] = {0};
    FILE *f = fopen("t_trace.jsonl", "rb");
    CHECK(f != NULL, "file written");
    if (f) { buf[fread(buf, 1, sizeof buf - 1, f)] = 0; fclose(f); }
    const char *want1 =
        "{\"seq\":0,\"nid\":\"0x109F50BC\",\"name\":\"sceIoOpen\",\"ra\":\"0x08804100\","
        "\"args\":[\"0x08900000\",\"0x00000000\",\"0x00000000\",\"0x00000000\","
        "\"0x00000000\",\"0x00000000\",\"0x00000000\",\"0x00000000\"],"
        "\"ret\":\"0x00000007\",\"ret_hi\":\"0x00000001\"}\n";
    CHECK(strncmp(buf, want1, strlen(want1)) == 0, "first line exact");
    const char *want2 =
        "{\"seq\":1,\"nid\":\"0xDEADBEEF\",\"name\":null,\"ra\":\"0x08804100\","
        "\"args\":[\"0x08900000\",\"0x00000000\",\"0x00000000\",\"0x00000000\","
        "\"0x00000000\",\"0x00000000\",\"0x00000000\",\"0x00000000\"],"
        "\"ret\":\"0x00000000\",\"ret_hi\":\"0x00000001\"}\n";
    CHECK(strcmp(buf + strlen(want1), want2) == 0, "unknown NID line exact");
    CHECK(khbbs_trace_count() == 2, "count");

    /* Calls after close write nothing. */
    psp_hle_call(0x109F50BCu);
    CHECK(khbbs_trace_count() == 2, "count unchanged after close");
    f = fopen("t_trace.jsonl", "rb");
    char buf2[1024] = {0};
    size_t n1 = f ? fread(buf2, 1, sizeof buf2 - 1, f) : 0;
    if (f) fclose(f);
    CHECK(n1 == strlen(want1) + strlen(want2), "nothing written after close");

    /* An unwritable path fails, and seq restarts at 0 on reopen. */
    CHECK(khbbs_trace_open("/nonexistent-dir/x.jsonl") == -1, "unwritable path");
    CHECK(khbbs_trace_open("t_trace.jsonl") == 0, "reopen");
    psp_hle_call(0x109F50BCu);
    CHECK(khbbs_trace_close() == 0, "close ok 2");
    f = fopen("t_trace.jsonl", "rb");
    memset(buf2, 0, sizeof buf2);
    if (f) { fread(buf2, 1, sizeof buf2 - 1, f); fclose(f); }
    CHECK(strncmp(buf2, "{\"seq\":0,", 9) == 0 && strchr(strchr(buf2, '\n') + 1, '{') == NULL,
          "seq restarts at 0");

    remove("t_trace.jsonl");
    printf("%s\n", failures ? "test_trace: FAILED" : "test_trace: ok");
    return failures ? 1 : 0;
}
