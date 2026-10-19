/* Instructions the recompiler could not translate, and VFPU traps. Carrying
 * on would run with wrong state, so stop at the guest address; abort() leaves
 * a backtrace for the debugger. (The guest's own `break` is psp_break, in boot.c.) */
#include "khbbs/unimplemented.h"
#include "khbbs/trace.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/vfpu.h"

#include <stdio.h>
#include <stdlib.h>

void psp_unimplemented(uint32_t addr, const char *what) {
    fflush(stdout);
    fprintf(stderr, "khbbs: untranslated instruction at 0x%08X (%s)\n", addr, what);
    psp_miss_context();
    khbbs_trace_flush();
    abort();
}

/* A VFPU trap (a matrix op with a prefix pending, an unknown selector) would
 * otherwise print and run on with the destination unwritten. */
static void vfpu_trap(uint32_t addr, const char *what) {
    char buf[64];
    snprintf(buf, sizeof buf, "VFPU %s", what);
    psp_unimplemented(addr, buf);
}

void khbbs_install_traps(void) { psp_vfpu_set_trap_handler(vfpu_trap); }
