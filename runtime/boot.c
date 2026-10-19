#include "khbbs/boot.h"
#include "khbbs/misslog.h"
#include "khbbs/trace.h"
#include "khbbs/unimplemented.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"
#include "psprecomp/vfpu.h"

#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Boot-time scratch in the "kernel" partition (docs/architecture.md, Memory):
 * the argument string and the initial stack. The game's own threads get
 * stacks from the sysmem HLE later; this stack only runs module_start. */
#define BOOT_ARGP      0x08000100u
#define BOOT_STACK_TOP 0x08400000u

static jmp_buf g_stop;
static khbbs_result g_res;
static uint32_t g_keep_going;
/* Set only while g_stop is live (between setjmp and the end of the run), so
 * psp_break can tell a longjmp target exists. */
static volatile int g_running;

/* Targets already missed in this run, so a target missed again (a loop, or a
 * table of callers) neither counts against --keep-going nor repeats in the
 * log. Past the end every miss counts as new. */
#define SEEN_MAX 1024
static uint32_t g_seen[SEEN_MAX];
static uint32_t g_nseen;

static int seen_before(uint32_t addr) {
    for (uint32_t i = 0; i < g_nseen; i++)
        if (g_seen[i] == addr) return 1;
    if (g_nseen < SEEN_MAX) g_seen[g_nseen++] = addr;
    return 0;
}

static void step_over(void) {
    psp_cpu.r[PSP_REG_V0] = 0;
    psp_cpu.r[PSP_REG_V1] = 0;
}

static void on_miss(uint32_t addr) {
    if (seen_before(addr)) {             /* only reachable for a stepped-over target */
        step_over();
        return;
    }
    const uint32_t order = g_res.misses++;
    const int stepped = g_res.misses <= g_keep_going;
    khbbs_misslog_record(addr, order, stepped);
    if (stepped) {
        if (!khbbs_misslog_active())
            fprintf(stderr, "khbbs: stepped over dispatch miss at 0x%08X (ra 0x%08X)\n",
                    addr, psp_cpu.r[PSP_REG_RA]);
        step_over();
        return;                          /* psp_dispatch returns to its caller */
    }
    g_res.why = KHBBS_DISPATCH_MISS;
    g_res.addr = addr;
    longjmp(g_stop, 1);
}

/* The guest's own trap. Most game breaks are the compiler's integer
 * divide-by-zero check (code 7), which in a recompiled run more likely means
 * a wrong register value, i.e. a translation bug, than a bug in the game. */
void psp_break(uint32_t addr, uint32_t code) {
    fflush(stdout);
    fprintf(stderr, "khbbs: guest break at 0x%08X (code 0x%05X)\n", addr, code);
    if (!g_running) {                    /* no boot to stop: behave like psp_unimplemented */
        psp_miss_context();
        khbbs_trace_flush();
        abort();
    }
    g_res.why = KHBBS_GUEST_BREAK;
    g_res.addr = addr;
    g_res.code = code;
    longjmp(g_stop, 1);
}

static void on_budget(void) {
    g_res.why = KHBBS_BUDGET;
    longjmp(g_stop, 1);
}

khbbs_result khbbs_boot(const khbbs_image *img, const char *path, const khbbs_boot_opts *opts) {
    const uint64_t budget = opts ? opts->budget : 0;
    memset(&g_res, 0, sizeof g_res);
    if (!img || !path) {
        fprintf(stderr, "khbbs: boot called with NULL image or path\n");
        g_res.why = KHBBS_BAD_ARGS;
        return g_res;
    }
    size_t n = strlen(path) + 1;
    void *dst = n <= KHBBS_BOOT_PATH_MAX + 1 ? psp_mem_ptr(BOOT_ARGP, (uint32_t)n) : NULL;
    if (!dst) {
        fprintf(stderr, "khbbs: boot path too long (%zu bytes, max %d)\n",
                n - 1, KHBBS_BOOT_PATH_MAX);
        g_res.why = KHBBS_BAD_ARGS;
        return g_res;
    }

    psp_cpu_reset();
    psp_vfpu_reset();                   /* prefixes and the vrnd generator; the trap handler stays */
    memcpy(dst, path, n);
    psp_cpu.r[PSP_REG_A0] = (uint32_t)n;
    psp_cpu.r[PSP_REG_A1] = BOOT_ARGP;
    psp_cpu.r[PSP_REG_SP] = BOOT_STACK_TOP - 0x40;
    psp_cpu.r[PSP_REG_GP] = img->gp;
    psp_cpu.r[PSP_REG_RA] = 0;
    g_keep_going = opts ? opts->keep_going : 0;
    g_nseen = 0;
    psp_trace_reset();                  /* "last" must not name a function of an earlier run */
    psp_set_miss_handler(on_miss);
    /* psp_dispatch only counts while a budget is armed, so "unlimited" arms
     * UINT64_MAX (never reached) to keep result.calls meaningful. Always set:
     * this also clears a limit left by an earlier boot. */
    psp_dispatch_set_budget(budget ? budget : UINT64_MAX, on_budget);

    g_running = 1;
    if (setjmp(g_stop) == 0) {
        psp_dispatch(img->entry);
        g_res.why = KHBBS_RETURNED;
    }
    g_running = 0;                      /* every way out lands here: return, miss, budget, break */
    g_res.calls = psp_dispatch_calls();
    /* on_miss/on_budget longjmp into g_stop, which is dead once we return.
     * psprecomp has no getter, so the previous miss handler cannot be
     * restored; NULL restores its default (report and abort). */
    psp_set_miss_handler(NULL);
    g_keep_going = 0;
    psp_dispatch_set_budget(0, NULL);
    khbbs_trace_flush();
    return g_res;
}

const char *khbbs_stop_name(khbbs_stop s) {
    switch (s) {
    case KHBBS_RETURNED:      return "entry point returned";
    case KHBBS_DISPATCH_MISS: return "dispatch miss";
    case KHBBS_BUDGET:        return "dispatch budget exhausted";
    case KHBBS_BAD_ARGS:      return "bad boot arguments";
    case KHBBS_GUEST_BREAK:   return "guest break";
    }
    return "?";
}
