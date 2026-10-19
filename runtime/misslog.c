#include "khbbs/misslog.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"

#include <stdio.h>

static FILE *g_out;
static uint64_t g_n;
static int g_err;

int khbbs_misslog_open(const char *path) {
    if (khbbs_misslog_close() != 0) {
        fprintf(stderr, "khbbs: the previous miss log had a write error\n");
        return -1;
    }
    g_out = fopen(path, "a");
    if (!g_out) { fprintf(stderr, "khbbs: cannot write miss log %s\n", path); return -1; }
    setvbuf(g_out, NULL, _IOLBF, 0);   /* the run may abort right after */
    g_n = 0;
    return 0;
}

int khbbs_misslog_active(void) { return g_out != NULL; }

void khbbs_misslog_record(uint32_t target, uint32_t order, int stepped) {
    if (!g_out) return;
    const uint32_t last = psp_trace_last();
    char lastbuf[16];
    if (last) snprintf(lastbuf, sizeof lastbuf, "\"0x%08X\"", last);
    if (fprintf(g_out,
                "{\"seq\":%llu,\"order\":%lu,\"stepped\":%s,\"target\":\"0x%08X\","
                "\"ra\":\"0x%08X\",\"sp\":\"0x%08X\",\"last\":%s}\n",
                (unsigned long long)g_n, (unsigned long)order, stepped ? "true" : "false", target,
                psp_cpu.r[PSP_REG_RA], psp_cpu.r[PSP_REG_SP], last ? lastbuf : "null") < 0)
        g_err = 1;
    g_n++;
}

uint64_t khbbs_misslog_count(void) { return g_n; }

int khbbs_misslog_close(void) {
    if (g_out && fclose(g_out) != 0) g_err = 1;
    g_out = NULL;
    int rc = g_err ? -1 : 0;
    g_err = 0;
    return rc;
}
