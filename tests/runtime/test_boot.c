/* khbbs_boot: argument setup and stop conditions, using synthetic guest
 * functions registered by hand. No game data. */
#include "khbbs/boot.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static uint32_t seen_a0, seen_a1;
static void returns(void) { seen_a0 = psp_cpu.r[PSP_REG_A0]; seen_a1 = psp_cpu.r[PSP_REG_A1]; }
static void wild(void) { psp_dispatch(0x0BAD0000u); }
static void spin(void) { psp_dispatch(0x08804300u); }

int main(void) {
    CHECK(psp_mem_init() == 0, "memory");
    psp_register(0x08804100u, returns);
    psp_register(0x08804200u, wild);
    psp_register(0x08804300u, spin);

    const char *path = "ms0:/PSP/GAME/KH/EBOOT.BIN";
    khbbs_image img = {0};
    img.entry = 0x08804100u;
    khbbs_result r = khbbs_boot(&img, path, 0);
    CHECK(r.why == KHBBS_RETURNED, "returned");
    CHECK(seen_a0 == strlen(path) + 1, "a0 = strlen+1");
    CHECK(seen_a1 == 0x08000100u, "a1 = argp");
    const char *g = psp_mem_ptr(0x08000100u, (uint32_t)strlen(path) + 1);
    CHECK(g && strcmp(g, path) == 0, "path in guest memory");

    img.entry = 0x08804200u;
    r = khbbs_boot(&img, path, 0);
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0000u, "dispatch miss");

    img.entry = 0x08804300u;   /* spin re-dispatches itself forever */
    r = khbbs_boot(&img, path, 50);
    CHECK(r.why == KHBBS_BUDGET, "budget");

    img.entry = 0x08804100u;   /* a previous budget must not leak into this run */
    r = khbbs_boot(&img, path, 0);
    CHECK(r.why == KHBBS_RETURNED, "no stale budget");

    char longpath[400];
    memset(longpath, 'x', sizeof longpath - 1);
    longpath[sizeof longpath - 1] = 0;
    r = khbbs_boot(&img, longpath, 0);
    CHECK(r.why == KHBBS_BAD_ARGS, "overlong path rejected");

    printf("%s\n", failures ? "test_boot: FAILED" : "test_boot: ok");
    return failures ? 1 : 0;
}
