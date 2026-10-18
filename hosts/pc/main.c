/* khbbs (PC, P1): load the decrypted EBOOT, run it, report why it stopped.
 * No window yet; that is P7. */
#include "khbbs/boot.h"
#include "khbbs/loader.h"
#include "khbbs/misslog.h"
#include "khbbs/trace.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <ctype.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void psp_recomp_register(void);  /* generated */

/* An unsigned count (decimal or 0x..): strtoull alone accepts "", " 5", "-1" and "5x". */
static int parse_count(const char *s, uint64_t *out) {
    char *end;
    errno = 0;
    *out = strtoull(s, &end, 0);
    return isdigit((unsigned char)*s) && *end == '\0' && !errno;
}

static int usage(void) {
    fprintf(stderr, "usage: khbbs --elf <EBOOT.dec.ELF> [--trace <file.jsonl>] [--budget N]\n"
            "               [--miss-log <file.jsonl>] [--keep-going N]\n");
    return 2;
}

int main(int argc, char **argv) {
    const char *elf = NULL, *trace = NULL, *misslog = NULL;
    uint64_t budget = 0, keep = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--elf") && i + 1 < argc) elf = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc) trace = argv[++i];
        else if (!strcmp(argv[i], "--miss-log") && i + 1 < argc) misslog = argv[++i];
        else if (!strcmp(argv[i], "--budget") && i + 1 < argc) {
            if (!parse_count(argv[++i], &budget)) return usage();
        }
        else if (!strcmp(argv[i], "--keep-going") && i + 1 < argc) {
            if (!parse_count(argv[++i], &keep) || keep > UINT32_MAX) return usage();
        }
        else return usage();
    }
    if (!elf) return usage();

    if (psp_mem_init() != 0) { fprintf(stderr, "khbbs: out of memory\n"); return 1; }
    psp_hle_init();
    psp_recomp_register();
    if (trace && khbbs_trace_open(trace) != 0) return 1;
    if (misslog && khbbs_misslog_open(misslog) != 0) return 1;

    khbbs_image img;
    if (khbbs_load_elf(elf, &img) != 0) return 1;
    printf("khbbs: loaded %s  [0x%08X, 0x%08X)  entry 0x%08X  gp 0x%08X\n",
           elf, img.lo, img.hi, img.entry, img.gp);
    fflush(stdout);

    khbbs_result r = khbbs_boot(&img, "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN",
                                &(khbbs_boot_opts){ .budget = budget, .keep_going = (uint32_t)keep });
    printf("khbbs: stopped: %s", khbbs_stop_name(r.why));
    if (r.why == KHBBS_DISPATCH_MISS) printf(" at 0x%08X", r.addr);
    printf("  (%llu dispatches", (unsigned long long)r.calls);
    if (trace) printf(", %llu traced firmware calls", (unsigned long long)khbbs_trace_count());
    if (r.misses) printf(", %u misses", r.misses);
    printf(")\n");
    fflush(stdout);
    if (r.why == KHBBS_DISPATCH_MISS) psp_miss_context();
    if (trace && khbbs_trace_close() != 0) fprintf(stderr, "khbbs: trace write failed\n");
    if (misslog && khbbs_misslog_close() != 0) fprintf(stderr, "khbbs: miss log write failed\n");
    return r.why == KHBBS_RETURNED ? 0 : 3;
}
