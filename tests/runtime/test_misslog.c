/* The dispatch-miss log. No game data. */
#include "khbbs/misslog.h"
#include "psprecomp/cpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static int count_lines(const char *path) {
    FILE *f = fopen(path, "r");
    int lines = 0;
    char buf[512];
    while (f && fgets(buf, sizeof buf, f)) lines++;
    if (f) fclose(f);
    return lines;
}

int main(void) {
    const char *path = "t_misses.jsonl";
    remove(path);
    CHECK(!khbbs_misslog_active(), "not active before open");
    CHECK(khbbs_misslog_open(path) == 0, "open");
    CHECK(khbbs_misslog_active(), "active after open");
    psp_cpu.r[PSP_REG_RA] = 0x08A8BE28u;
    psp_cpu.r[PSP_REG_SP] = 0x083FFFC0u;
    khbbs_misslog_record(0x08A8BE6Cu, 0, 1);
    psp_cpu.r[PSP_REG_RA] = 0x08804010u;
    khbbs_misslog_record(0x08900000u, 1, 0);
    CHECK(khbbs_misslog_count() == 2, "two misses counted");
    CHECK(khbbs_misslog_close() == 0, "close");
    CHECK(!khbbs_misslog_active(), "not active after close");

    FILE *f = fopen(path, "r");
    char a[256] = "", b[256] = "";
    CHECK(f && fgets(a, sizeof a, f) && fgets(b, sizeof b, f), "two lines");
    if (f) fclose(f);
    /* tests/tools/test_merge_misses.py has the same lines, written out by hand. */
    CHECK(strcmp(a, "{\"seq\":0,\"order\":0,\"stepped\":true,\"target\":\"0x08A8BE6C\","
                    "\"ra\":\"0x08A8BE28\",\"sp\":\"0x083FFFC0\",\"last\":null}\n") == 0, "first line exact");
    CHECK(strcmp(b, "{\"seq\":1,\"order\":1,\"stepped\":false,\"target\":\"0x08900000\","
                    "\"ra\":\"0x08804010\",\"sp\":\"0x083FFFC0\",\"last\":null}\n") == 0, "second line exact");

    /* Re-opening appends, so several runs can share one log; seq restarts. */
    CHECK(khbbs_misslog_open(path) == 0, "reopen");
    khbbs_misslog_record(0x08800000u, 0, 0);
    khbbs_misslog_close();
    CHECK(count_lines(path) == 3, "appended");
    f = fopen(path, "r");
    char c[256] = "";
    for (int i = 0; f && i < 3; i++) if (!fgets(c, sizeof c, f)) break;
    if (f) fclose(f);
    CHECK(strstr(c, "\"seq\":0,") != NULL, "seq restarts per open");

    khbbs_misslog_record(0x1u, 0, 0);          /* closed: a no-op, not a crash */
    CHECK(count_lines(path) == 3, "nothing written when closed");
    remove(path);
    printf("%s\n", failures ? "test_misslog: FAILED" : "test_misslog: ok");
    return failures ? 1 : 0;
}
