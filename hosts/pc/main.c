/* khbbs (PC, P1): load the decrypted EBOOT, run it, report why it stopped.
 * No window yet; that is P7. */
#include "khbbs/boot.h"
#include "khbbs/loader.h"
#include "khbbs/trace.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void psp_recomp_register(void);  /* generated */

/* The generated code calls this (host-provided, see main_funcs.h) at any
 * instruction the recompiler could not translate: mostly VFPU ops, and the
 * game's own `break` traps. Carrying on would run with wrong state, so stop
 * here with the guest address; abort() leaves a backtrace for the debugger. */
void psp_unimplemented(uint32_t addr, const char *what) {
    fflush(stdout);
    fprintf(stderr, "khbbs: untranslated instruction at 0x%08X (%s)\n", addr, what);
    psp_miss_context();
    khbbs_trace_flush();
    abort();
}

static int usage(void) {
    fprintf(stderr, "usage: khbbs --elf <EBOOT.dec.ELF> [--trace <file.jsonl>] [--budget N]\n");
    return 2;
}

int main(int argc, char **argv) {
    const char *elf = NULL, *trace = NULL;
    uint64_t budget = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--elf") && i + 1 < argc) elf = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc) trace = argv[++i];
        else if (!strcmp(argv[i], "--budget") && i + 1 < argc) budget = strtoull(argv[++i], NULL, 0);
        else return usage();
    }
    if (!elf) return usage();

    if (psp_mem_init() != 0) { fprintf(stderr, "khbbs: out of memory\n"); return 1; }
    psp_hle_init();
    psp_recomp_register();
    if (trace && khbbs_trace_open(trace) != 0) return 1;

    khbbs_image img;
    if (khbbs_load_elf(elf, &img) != 0) return 1;
    printf("khbbs: loaded %s  [0x%08X, 0x%08X)  entry 0x%08X  gp 0x%08X\n",
           elf, img.lo, img.hi, img.entry, img.gp);
    fflush(stdout);

    khbbs_result r = khbbs_boot(&img, "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN", budget);
    printf("khbbs: stopped: %s", khbbs_stop_name(r.why));
    if (r.why == KHBBS_DISPATCH_MISS) printf(" at 0x%08X", r.addr);
    printf("  (%llu dispatches, %llu firmware calls)\n",
           (unsigned long long)r.calls, (unsigned long long)khbbs_trace_count());
    fflush(stdout);
    if (r.why == KHBBS_DISPATCH_MISS) psp_miss_context();
    if (trace && khbbs_trace_close() != 0) fprintf(stderr, "khbbs: trace write failed\n");
    return r.why == KHBBS_RETURNED ? 0 : 3;
}
