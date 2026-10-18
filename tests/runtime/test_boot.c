/* khbbs_boot: argument setup and stop conditions, using synthetic guest
 * functions registered by hand. No game data. */
#include "khbbs/boot.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <signal.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static uint32_t seen_a0, seen_a1;
static void returns(void) { seen_a0 = psp_cpu.r[PSP_REG_A0]; seen_a1 = psp_cpu.r[PSP_REG_A1]; }
static void wild(void) { psp_dispatch(0x0BAD0000u); }
static void two_wild(void) {
    psp_cpu.r[PSP_REG_V0] = 0x1234u;
    psp_dispatch(0x0BAD0000u);
    psp_dispatch(0x0BAD0004u);
}
static void spin(void) { psp_dispatch(0x08804300u); }

int main(void) {
    CHECK(psp_mem_init() == 0, "memory");
    psp_register(0x08804100u, returns);
    psp_register(0x08804200u, wild);
    psp_register(0x08804300u, spin);
    psp_register(0x08804400u, two_wild);

    const char *path = "ms0:/PSP/GAME/KH/EBOOT.BIN";
    khbbs_image img = {0};
    img.entry = 0x08804100u;
    khbbs_result r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });
    CHECK(r.why == KHBBS_RETURNED, "returned");
    CHECK(seen_a0 == strlen(path) + 1, "a0 = strlen+1");
    CHECK(seen_a1 == 0x08000100u, "a1 = argp");
    const char *g = psp_mem_ptr(0x08000100u, (uint32_t)strlen(path) + 1);
    CHECK(g && strcmp(g, path) == 0, "path in guest memory");

    img.entry = 0x08804200u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0000u, "dispatch miss");

    img.entry = 0x08804300u;   /* spin re-dispatches itself forever */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 50 });
    CHECK(r.why == KHBBS_BUDGET, "budget");

    img.entry = 0x08804100u;   /* a previous budget must not leak into this run */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });
    CHECK(r.why == KHBBS_RETURNED, "no stale budget");

    /* keep_going: log and step over misses, then return normally. */
    img.entry = 0x08804400u;   /* two wild dispatches, then returns */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 2 });
    CHECK(r.why == KHBBS_RETURNED && r.misses == 2, "two misses stepped over");
    CHECK(psp_cpu.r[PSP_REG_V0] == 0, "a stepped-over call returns 0");
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 1 });
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0004u && r.misses == 2,
          "the miss past the limit stops the run");
    img.entry = 0x08804200u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.misses == 1, "no keep_going: first miss stops");

    char longpath[400];
    memset(longpath, 'x', sizeof longpath - 1);
    longpath[sizeof longpath - 1] = 0;
    r = khbbs_boot(&img, longpath, NULL);
    CHECK(r.why == KHBBS_BAD_ARGS, "overlong path rejected");

    /* After boot our longjmp handler must be gone: a miss now takes psprecomp's
     * default path (abort), not a longjmp into a dead jmp_buf. */
    img.entry = 0x08804200u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });   /* installs then must remove its handler */
    CHECK(r.calls >= 1, "unlimited run still counts calls");
    CHECK(khbbs_boot(NULL, path, NULL).why == KHBBS_BAD_ARGS, "NULL img");
    CHECK(khbbs_boot(&img, NULL, NULL).why == KHBBS_BAD_ARGS, "NULL path");
    fflush(NULL);
    pid_t pid = fork();
    if (pid == 0) {
        struct rlimit nocore = {0, 0};
        setrlimit(RLIMIT_CORE, &nocore);   /* abort() must not leave a core file */
        if (!freopen("/dev/null", "w", stderr)) _exit(2);
        psp_dispatch(0x0BAD0000u);
        _exit(0);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    CHECK(WIFSIGNALED(st) && WTERMSIG(st) == SIGABRT, "default miss handler restored after boot");

    printf("%s\n", failures ? "test_boot: FAILED" : "test_boot: ok");
    return failures ? 1 : 0;
}
