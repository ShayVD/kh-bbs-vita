#include "khbbs/boot.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"

#include <setjmp.h>
#include <stdio.h>
#include <string.h>

/* Boot-time scratch in the "kernel" partition (docs/architecture.md, Memory):
 * the argument string and the initial stack. The game's own threads get
 * stacks from the sysmem HLE later; this stack only runs module_start. */
#define BOOT_ARGP      0x08000100u
#define BOOT_STACK_TOP 0x08400000u

static jmp_buf g_stop;
static khbbs_result g_res;

static void on_miss(uint32_t addr) {
    g_res.why = KHBBS_DISPATCH_MISS;
    g_res.addr = addr;
    longjmp(g_stop, 1);
}

static void on_budget(void) {
    g_res.why = KHBBS_BUDGET;
    longjmp(g_stop, 1);
}

khbbs_result khbbs_boot(const khbbs_image *img, const char *path, uint64_t budget) {
    memset(&g_res, 0, sizeof g_res);
    size_t n = strlen(path) + 1;
    void *dst = n <= KHBBS_BOOT_PATH_MAX + 1 ? psp_mem_ptr(BOOT_ARGP, (uint32_t)n) : NULL;
    if (!dst) {
        fprintf(stderr, "khbbs: boot path too long (%zu bytes, max %d)\n",
                n - 1, KHBBS_BOOT_PATH_MAX);
        g_res.why = KHBBS_BAD_ARGS;
        return g_res;
    }

    psp_cpu_reset();
    memcpy(dst, path, n);
    psp_cpu.r[PSP_REG_A0] = (uint32_t)n;
    psp_cpu.r[PSP_REG_A1] = BOOT_ARGP;
    psp_cpu.r[PSP_REG_SP] = BOOT_STACK_TOP - 0x40;
    psp_cpu.r[PSP_REG_GP] = img->gp;
    psp_cpu.r[PSP_REG_RA] = 0;
    psp_set_miss_handler(on_miss);
    /* Always set: budget 0 also clears a limit left by an earlier boot. */
    psp_dispatch_set_budget(budget, on_budget);

    if (setjmp(g_stop) == 0) {
        psp_dispatch(img->entry);
        g_res.why = KHBBS_RETURNED;
    }
    g_res.calls = psp_dispatch_calls();
    return g_res;
}

const char *khbbs_stop_name(khbbs_stop s) {
    switch (s) {
    case KHBBS_RETURNED:      return "entry point returned";
    case KHBBS_DISPATCH_MISS: return "dispatch miss";
    case KHBBS_BUDGET:        return "dispatch budget exhausted";
    case KHBBS_BAD_ARGS:      return "bad boot arguments";
    }
    return "?";
}
