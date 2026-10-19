/* khbbs_boot: argument setup and stop conditions, using synthetic guest
 * functions registered by hand. No game data. */
#include "khbbs/boot.h"
#include "khbbs/misslog.h"
#include "khbbs/unimplemented.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"
#include "psprecomp/vfpu.h"

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
    psp_cpu.r[PSP_REG_V1] = 0x5678u;
    psp_dispatch(0x0BAD0000u);
    psp_dispatch(0x0BAD0004u);
}
/* One target missed five times, then a second one. */
static void five_then_other(void) {
    for (int i = 0; i < 5; i++) psp_dispatch(0x0BAD0000u);
    psp_dispatch(0x0BAD0008u);
}
/* More distinct targets than the seen-set holds, then two repeats. */
static void many_wild(void) {
    for (uint32_t i = 0; i < 1100; i++) psp_dispatch(0x0BAE0000u + 4 * i);
    psp_dispatch(0x0BAE0000u);                 /* in the seen-set: not counted */
    psp_dispatch(0x0BAE0000u + 4 * 1050);      /* past the set: counted again */
}
/* Reads the miss log into `out` (empty if absent) and returns its line count. */
static int read_log(const char *path, char out[4][400]) {
    FILE *f = fopen(path, "r");
    int n = 0;
    char buf[400];
    while (f && fgets(buf, sizeof buf, f)) {
        if (n < 4) memcpy(out[n], buf, sizeof buf);
        n++;
    }
    if (f) fclose(f);
    return n;
}
static void asserts(void) { psp_break(0x08804700u, 7); }
static void miss_then_break(void) {
    psp_dispatch(0x0BAD0000u);
    psp_break(0x08804800u, 6);
}
/* In a forked child, psp_break must abort: no boot is running, so there is no
 * jmp_buf to jump to. Checked right after each kind of stop, so a stop path
 * that forgot to end the run is caught where it happens. */
static void aborts_outside_boot(const char *after) {
    char msg[96];
    snprintf(msg, sizeof msg, "psp_break aborts after a boot that %s", after);
    fflush(NULL);
    pid_t pid = fork();
    if (pid == 0) {
        struct rlimit nocore = {0, 0};
        setrlimit(RLIMIT_CORE, &nocore);   /* abort() must not leave a core file */
        if (!freopen("/dev/null", "w", stderr)) _exit(2);
        psp_break(0x08804700u, 7);
        _exit(0);                          /* reached only if psp_break returned */
    }
    int st = 0;
    waitpid(pid, &st, 0);
    CHECK(WIFSIGNALED(st) && WTERMSIG(st) == SIGABRT, msg);
}
static void spin(void) { psp_dispatch(0x08804300u); }
/* VFPU state a run leaves behind: a pending prefix, an advanced vrnd
 * generator. The next boot must not see either. */
static void leave_vfpu_state(void) {
    for (int i = 0; i < 3; i++) psp_vrnd(PSP_VR_I, 0x00u, 1);
    psp_vfpu_set_prefix(PSP_VPFX_S, 0x1Bu);
}
static int seen_pending;
static uint32_t seen_draw;
static void look_at_vfpu(void) {
    seen_pending = psp_vfpu_prefix_pending();
    psp_vrnd(PSP_VR_I, 0x00u, 1);
    seen_draw = psp_mfv(0x00u);
}

int main(void) {
    CHECK(psp_mem_init() == 0, "memory");
    psp_register(0x08804100u, returns);
    psp_register(0x08804200u, wild);
    psp_register(0x08804300u, spin);
    psp_register(0x08804400u, two_wild);
    psp_register(0x08804500u, five_then_other);
    psp_register(0x08804600u, many_wild);
    psp_register(0x08804700u, asserts);
    psp_register(0x08804800u, miss_then_break);
    psp_register(0x08804900u, leave_vfpu_state);
    psp_register(0x08804A00u, look_at_vfpu);

    const char *path = "ms0:/PSP/GAME/KH/EBOOT.BIN";
    khbbs_image img = {0};
    img.entry = 0x08804100u;
    khbbs_result r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });
    CHECK(r.why == KHBBS_RETURNED, "returned");
    CHECK(seen_a0 == strlen(path) + 1, "a0 = strlen+1");
    CHECK(seen_a1 == 0x08000100u, "a1 = argp");
    const char *g = psp_mem_ptr(0x08000100u, (uint32_t)strlen(path) + 1);
    CHECK(g && strcmp(g, path) == 0, "path in guest memory");
    aborts_outside_boot("returned");

    img.entry = 0x08804200u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0000u, "dispatch miss");
    aborts_outside_boot("stopped at a miss");

    img.entry = 0x08804300u;   /* spin re-dispatches itself forever */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 50 });
    CHECK(r.why == KHBBS_BUDGET, "budget");
    aborts_outside_boot("ran out of budget");

    img.entry = 0x08804100u;   /* a previous budget must not leak into this run */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 0 });
    CHECK(r.why == KHBBS_RETURNED, "no stale budget");

    /* keep_going: log and step over misses, then return normally. */
    img.entry = 0x08804400u;   /* two wild dispatches, then returns */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 2 });
    CHECK(r.why == KHBBS_RETURNED && r.misses == 2, "two misses stepped over");
    CHECK(psp_cpu.r[PSP_REG_V0] == 0 && psp_cpu.r[PSP_REG_V1] == 0, "a stepped-over call returns 0, 0");
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 1 });
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0004u && r.misses == 2,
          "the miss past the limit stops the run");
    img.entry = 0x08804200u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.misses == 1, "no keep_going: first miss stops");

    /* keep_going counts distinct targets, and the log has each one once. */
    const char *logp = "t_boot_misses.jsonl";
    char L[4][400];
    remove(logp);
    CHECK(khbbs_misslog_open(logp) == 0, "open miss log");
    img.entry = 0x08804500u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 1 });
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0008u && r.misses == 2,
          "one target missed five times used one keep_going; the second distinct target stops");
    CHECK(read_log(logp, L) == 2, "repeats are not logged: two lines");
    CHECK(strstr(L[0], "\"target\":\"0x0BAD0000\"") && strstr(L[0], "\"order\":0") &&
          strstr(L[0], "\"stepped\":true"), "first line: order 0, stepped");
    CHECK(strstr(L[1], "\"target\":\"0x0BAD0008\"") && strstr(L[1], "\"order\":1") &&
          strstr(L[1], "\"stepped\":false"), "stopping miss is logged: order 1, not stepped");
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 2 });
    CHECK(r.why == KHBBS_RETURNED && r.misses == 2, "five_then_other returns when both are stepped over");
    CHECK(read_log(logp, L) == 4, "a second run appends two more lines");
    CHECK(strstr(L[2], "\"order\":0"), "order restarts with each run");
    khbbs_misslog_close();
    remove(logp);
    CHECK(khbbs_misslog_open(logp) == 0, "open miss log again");
    img.entry = 0x08804400u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 1 });
    CHECK(read_log(logp, L) == 2 && strstr(L[1], "\"target\":\"0x0BAD0004\"") &&
          strstr(L[1], "\"stepped\":false"), "two_wild: both misses logged, the last one not stepped");
    khbbs_misslog_close();
    remove(logp);

    img.entry = 0x08804600u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 5000 });
    CHECK(r.why == KHBBS_RETURNED && r.misses == 1101,
          "repeats inside the seen-set are free; past it every miss counts");

    img.entry = 0x08804700u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_GUEST_BREAK && r.addr == 0x08804700u && r.code == 7, "guest break stops the run");
    CHECK(strcmp(khbbs_stop_name(r.why), "guest break") == 0, "guest break has a name");
    aborts_outside_boot("ended in a guest break");
    img.entry = 0x08804800u;   /* a stepped-over miss, then a break */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 1 });
    CHECK(r.why == KHBBS_GUEST_BREAK && r.addr == 0x08804800u && r.code == 6 && r.misses == 1,
          "a break after a stepped-over miss keeps the miss count");
    img.entry = 0x08804100u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_RETURNED && r.code == 0, "a run after a break is clean");

    /* Every boot starts from a reset VFPU: no prefix pending, the vrnd
     * generator at its seed. */
    img.entry = 0x08804A00u;
    r = khbbs_boot(&img, path, NULL);
    const uint32_t first_draw = seen_draw;
    CHECK(r.why == KHBBS_RETURNED && !seen_pending, "a first boot has no VFPU prefix pending");
    img.entry = 0x08804900u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_RETURNED && psp_vfpu_prefix_pending(), "a run can end with a prefix pending");
    img.entry = 0x08804A00u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(!seen_pending, "a prefix left pending does not leak into the next boot");
    CHECK(seen_draw == first_draw, "the vrnd generator restarts at every boot");
    psp_vfpu_reset();

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
