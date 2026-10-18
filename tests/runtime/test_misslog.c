/* The dispatch-miss log. No game data. */
#include "khbbs/misslog.h"
#include "psprecomp/cpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

int main(void) {
    const char *path = "t_misses.jsonl";
    remove(path);
    CHECK(khbbs_misslog_open(path) == 0, "open");
    psp_cpu.r[PSP_REG_RA] = 0x08A8BE28u;
    psp_cpu.r[PSP_REG_SP] = 0x083FFFC0u;
    khbbs_misslog_record(0x08A8BE6Cu);
    psp_cpu.r[PSP_REG_RA] = 0x08804010u;
    khbbs_misslog_record(0x08900000u);
    CHECK(khbbs_misslog_count() == 2, "two misses counted");
    CHECK(khbbs_misslog_close() == 0, "close");

    FILE *f = fopen(path, "r");
    char a[256] = "", b[256] = "";
    CHECK(f && fgets(a, sizeof a, f) && fgets(b, sizeof b, f), "two lines");
    if (f) fclose(f);
    CHECK(strstr(a, "\"target\":\"0x08A8BE6C\"") && strstr(a, "\"ra\":\"0x08A8BE28\"") &&
          strstr(a, "\"sp\":\"0x083FFFC0\"") && strstr(a, "\"seq\":0"), "first line fields");
    CHECK(strstr(b, "\"target\":\"0x08900000\"") && strstr(b, "\"seq\":1"), "second line");

    /* Re-opening appends, so several runs can share one log. */
    CHECK(khbbs_misslog_open(path) == 0, "reopen");
    khbbs_misslog_record(0x08800000u);
    khbbs_misslog_close();
    f = fopen(path, "r");
    int lines = 0;
    char buf[256];
    while (f && fgets(buf, sizeof buf, f)) lines++;
    if (f) fclose(f);
    CHECK(lines == 3, "appended");

    khbbs_misslog_record(0x1u);          /* closed: a no-op, not a crash */
    remove(path);
    printf("%s\n", failures ? "test_misslog: FAILED" : "test_misslog: ok");
    return failures ? 1 : 0;
}
