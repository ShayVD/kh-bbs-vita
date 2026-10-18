#include "khbbs/trace.h"
#include "psprecomp/hle.h"

#include <stdio.h>

static FILE *g_out;
static uint64_t g_seq;

/* Firmware function names are fixed C identifiers, so no JSON escaping. */
static void on_call(uint32_t nid, const char *name, const uint32_t args[8],
                    uint32_t ret_lo, uint32_t ret_hi, uint32_t ra) {
    if (!g_out) return;
    fprintf(g_out, "{\"seq\":%llu,\"nid\":\"0x%08X\",\"name\":", (unsigned long long)g_seq++, nid);
    if (name) fprintf(g_out, "\"%s\"", name); else fputs("null", g_out);
    fprintf(g_out, ",\"ra\":\"0x%08X\",\"args\":[", ra);
    for (int i = 0; i < 8; i++) fprintf(g_out, "%s\"0x%08X\"", i ? "," : "", args[i]);
    fprintf(g_out, "],\"ret\":\"0x%08X\",\"ret_hi\":\"0x%08X\"}\n", ret_lo, ret_hi);
}

int khbbs_trace_open(const char *path) {
    khbbs_trace_close();
    g_out = fopen(path, "w");
    if (!g_out) { fprintf(stderr, "khbbs: cannot write trace %s\n", path); return -1; }
    g_seq = 0;
    psp_hle_set_trace(on_call);
    return 0;
}

void khbbs_trace_close(void) {
    psp_hle_set_trace(NULL);
    if (g_out) fclose(g_out);
    g_out = NULL;
}

uint64_t khbbs_trace_count(void) { return g_seq; }
