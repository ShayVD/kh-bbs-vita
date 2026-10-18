#include "khbbs/trace.h"
#include "psprecomp/hle.h"

#include <stdio.h>

static FILE *g_out;
static uint64_t g_seq;
static int g_err;      /* latched: a line was truncated or a write/close failed */

/* Names are written unescaped: firmware function names are C identifiers. */
static void on_call(uint32_t nid, const char *name, const uint32_t args[8],
                    uint32_t ret_lo, uint32_t ret_hi, uint32_t ra) {
    if (!g_out) return;
    char buf[512];
    int n = snprintf(buf, sizeof buf, "{\"seq\":%llu,\"nid\":\"0x%08X\",\"name\":",
                     (unsigned long long)g_seq, nid);
    if (n > 0 && (size_t)n < sizeof buf) {
        int m;
        if (name) m = snprintf(buf + n, sizeof buf - (size_t)n, "\"%s\"", name);
        else      m = snprintf(buf + n, sizeof buf - (size_t)n, "null");
        n = m < 0 ? m : n + m;
    }
    for (int i = -1; i < 8 && n > 0 && (size_t)n < sizeof buf; i++) {
        int m;
        if (i < 0) m = snprintf(buf + n, sizeof buf - (size_t)n, ",\"ra\":\"0x%08X\",\"args\":[", ra);
        else       m = snprintf(buf + n, sizeof buf - (size_t)n, "%s\"0x%08X\"", i ? "," : "", args[i]);
        n = m < 0 ? m : n + m;
    }
    if (n > 0 && (size_t)n < sizeof buf) {
        int m = snprintf(buf + n, sizeof buf - (size_t)n,
                         "],\"ret\":\"0x%08X\",\"ret_hi\":\"0x%08X\"}\n", ret_lo, ret_hi);
        n = m < 0 ? m : n + m;
    }
    if (n <= 0 || (size_t)n >= sizeof buf) { g_err = 1; return; }
    g_seq++;
    if (fwrite(buf, 1, (size_t)n, g_out) != (size_t)n) g_err = 1;
}

int khbbs_trace_open(const char *path) {
    khbbs_trace_close();
    g_err = 0;
    g_out = fopen(path, "w");
    if (!g_out) { fprintf(stderr, "khbbs: cannot write trace %s\n", path); return -1; }
    setvbuf(g_out, NULL, _IOLBF, 0);   /* a crash must not lose the tail */
    g_seq = 0;
    psp_hle_set_trace(on_call);
    return 0;
}

void khbbs_trace_flush(void) {
    if (g_out && fflush(g_out) != 0) g_err = 1;
}

int khbbs_trace_close(void) {
    psp_hle_set_trace(NULL);
    if (g_out && fclose(g_out) != 0) g_err = 1;
    g_out = NULL;
    int rc = g_err ? -1 : 0;
    g_err = 0;
    return rc;
}

uint64_t khbbs_trace_count(void) { return g_seq; }
