/* Mostly VFPU ops, and the game's own `break` traps. Carrying on would run
 * with wrong state, so stop at the guest address; abort() leaves a backtrace
 * for the debugger. */
#include "khbbs/unimplemented.h"
#include "khbbs/trace.h"
#include "psprecomp/dispatch.h"

#include <stdio.h>
#include <stdlib.h>

void psp_unimplemented(uint32_t addr, const char *what) {
    fflush(stdout);
    fprintf(stderr, "khbbs: untranslated instruction at 0x%08X (%s)\n", addr, what);
    psp_miss_context();
    khbbs_trace_flush();
    abort();
}
