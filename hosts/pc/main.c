/* khbbs (PC, P1): load the decrypted EBOOT, run it, report why it stopped.
 * No window yet; that is P7. */
#include "khbbs/args.h"
#include "khbbs/boot.h"
#include "khbbs/loader.h"
#include "khbbs/misslog.h"
#include "khbbs/snapshot.h"
#include "khbbs/trace.h"
#include "khbbs/unimplemented.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

void psp_recomp_register(void);  /* generated */

static int usage(void) {
    fprintf(stderr, "usage: khbbs --elf <EBOOT.dec.ELF> [--trace <file.jsonl>] [--budget N]\n"
            "               [--miss-log <file.jsonl>] [--keep-going N]\n"
            "               [--snapshot-at NAME:K|0xNID:K --snapshot <file.bin>]\n"
            "--snapshot-at/--snapshot (together): just before the K-th call to firmware\n"
            "function NAME (or NID), write guest RAM 0x08000000-0x0A000000 to <file.bin>\n"
            "and a sidecar <file.bin>.json, for tools/oracle/mem_diff.py; any old copies\n"
            "are removed first. A snapshot that never fired or failed to write only warns:\n"
            "the exit status is the run's, so check that <file.bin>.json exists\n"
            "exit status: 0 the entry point returned; 3 the run stopped at its first\n"
            "dispatch miss or on budget; 4 it carried on past a miss (--keep-going) and then\n"
            "returned, ran out of budget, hit a guest break or stopped at a later miss, so\n"
            "only its first miss is trustworthy; 5 the guest executed `break` (integer\n"
            "divide by zero, overflow or an assert) and no miss was stepped over before it;\n"
            "2 bad usage; 1 other failure\n");
    return 2;
}

static int same_inode(const struct stat *sa, const struct stat *sb) {
    return sa->st_ino != 0 && sa->st_dev == sb->st_dev && sa->st_ino == sb->st_ino;  /* 0: no inodes (Windows) */
}

/* stat() of the directory holding path; *base is set to its last component. */
static int stat_parent(const char *path, struct stat *st, const char **base) {
    char dir[4096];
    const char *slash = strrchr(path, '/');
    *base = slash ? slash + 1 : path;
    if (!slash) return stat(".", st);
    size_t n = slash == path ? 1 : (size_t)(slash - path);   /* "/x": the root */
    if (n >= sizeof dir) return -1;
    memcpy(dir, path, n);
    dir[n] = 0;
    return stat(dir, st);
}

/* Same path, or both exist and are the same file, or neither exists and both
 * name the same entry of the same directory (./t and t). */
static int same_file(const char *a, const char *b) {
    struct stat sa, sb;
    const char *ba, *bb;
    if (!a || !b) return 0;
    if (!strcmp(a, b)) return 1;
    int ea = stat(a, &sa) == 0, eb = stat(b, &sb) == 0;
    if (ea || eb) return ea && eb && same_inode(&sa, &sb);
    return stat_parent(a, &sa, &ba) == 0 && stat_parent(b, &sb, &bb) == 0 && *ba &&
           !strcmp(ba, bb) && same_inode(&sa, &sb);
}

/* The snapshot and its sidecar are deleted when armed: never let either be an input or another output. */
static int snapshot_clashes(const char *snap, const char *elf, const char *trace, const char *misslog) {
    char side[4096];
    if (snprintf(side, sizeof side, "%s.json", snap) >= (int)sizeof side) {
        fprintf(stderr, "khbbs: --snapshot path too long\n");
        return 1;
    }
    const char *others[] = { elf, trace, misslog };
    for (int i = 0; i < 3; i++) {
        if (same_file(snap, others[i]) || same_file(side, others[i])) {
            fprintf(stderr, "khbbs: refusing --snapshot %s: it or its sidecar is also %s\n", snap, others[i]);
            return 1;
        }
    }
    return 0;
}

static const char *break_kind(uint32_t code) {
    switch (code) {
    case 7: return " (integer divide by zero)";
    case 6: return " (overflow)";
    default: return "";
    }
}

int main(int argc, char **argv) {
    const char *elf = NULL, *trace = NULL, *misslog = NULL, *snap_at = NULL, *snap = NULL;
    uint64_t budget = 0, keep = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--elf") && i + 1 < argc) elf = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc) trace = argv[++i];
        else if (!strcmp(argv[i], "--miss-log") && i + 1 < argc) misslog = argv[++i];
        else if (!strcmp(argv[i], "--snapshot-at") && i + 1 < argc) snap_at = argv[++i];
        else if (!strcmp(argv[i], "--snapshot") && i + 1 < argc) snap = argv[++i];
        else if (!strcmp(argv[i], "--budget") && i + 1 < argc) {
            if (!khbbs_parse_count(argv[++i], &budget)) return usage();
        }
        else if (!strcmp(argv[i], "--keep-going") && i + 1 < argc) {
            if (!khbbs_parse_count(argv[++i], &keep) || keep > UINT32_MAX) return usage();
        }
        else return usage();
    }
    if (!elf || !snap_at != !snap) return usage();
    if (snap && snapshot_clashes(snap, elf, trace, misslog)) return 2;

    if (psp_mem_init() != 0) { fprintf(stderr, "khbbs: out of memory\n"); return 1; }
    psp_hle_init();
    khbbs_install_traps();
    psp_recomp_register();
    if (trace && khbbs_trace_open(trace) != 0) return 1;
    if (misslog && khbbs_misslog_open(misslog) != 0) return 1;
    if (snap && khbbs_snapshot_arm(snap_at, snap) != 0) return 2;

    khbbs_image img;
    if (khbbs_load_elf(elf, &img) != 0) return 1;
    printf("khbbs: loaded %s  [0x%08X, 0x%08X)  entry 0x%08X  gp 0x%08X\n",
           elf, img.lo, img.hi, img.entry, img.gp);
    fflush(stdout);

    khbbs_result r = khbbs_boot(&img, "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN",
                                &(khbbs_boot_opts){ .budget = budget, .keep_going = (uint32_t)keep });
    printf("khbbs: stopped: %s", khbbs_stop_name(r.why));
    if (r.why == KHBBS_DISPATCH_MISS) printf(" at 0x%08X", r.addr);
    if (r.why == KHBBS_GUEST_BREAK) printf(" at 0x%08X (code 0x%05X)%s", r.addr, r.code, break_kind(r.code));
    printf("  (%llu dispatches", (unsigned long long)r.calls);
    if (trace) printf(", %llu traced firmware calls", (unsigned long long)khbbs_trace_count());
    if (r.misses) printf(", %" PRIu32 " distinct misses", r.misses);
    printf(")\n");
    fflush(stdout);
    if (r.why == KHBBS_DISPATCH_MISS || r.why == KHBBS_GUEST_BREAK) psp_miss_context();
    if (trace && khbbs_trace_close() != 0) fprintf(stderr, "khbbs: trace write failed\n");
    if (misslog && khbbs_misslog_close() != 0) fprintf(stderr, "khbbs: miss log write failed\n");
    if (snap) {
        khbbs_snapshot_disarm();
        if (khbbs_snapshot_fired() == 0)
            fprintf(stderr, "khbbs: warning: snapshot event %s never happened; no snapshot written\n"
                    "khbbs: (NAME matches only names psprecomp registers; an unimplemented\n"
                    "khbbs: function needs the 0xNID:K form)\n",
                    snap_at);
        else if (khbbs_snapshot_fired() > 0)
            printf("khbbs: snapshot at %s written to %s\n", snap_at, snap);
    }
    /* A run that carried on past a miss ran on made-up results (boot.h). */
    if (r.misses > (r.why == KHBBS_DISPATCH_MISS ? 1u : 0u)) return 4;
    if (r.why == KHBBS_GUEST_BREAK) return 5;
    return r.why == KHBBS_RETURNED ? 0 : 3;
}
