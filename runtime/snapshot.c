#include "khbbs/snapshot.h"
#include "khbbs/args.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

static char     g_event[128];  /* as given, for the sidecar */
static char     g_name[128];   /* empty for the 0xNID form */
static uint32_t g_nid;
static uint64_t g_want, g_seen;
static char     g_path[1024];
static int      g_fired;

/* Writes the dump and its sidecar; 0 or -1. */
static int write_snapshot(uint32_t nid, uint32_t ra) {
    if (!psp_mem.ram) return -1;
    FILE *f = fopen(g_path, "wb");
    if (!f) return -1;
    int bad = fwrite(psp_mem.ram, 1, PSP_RAM_SIZE, f) != PSP_RAM_SIZE;
    bad |= fclose(f) != 0;
    if (bad) return -1;
    char side[sizeof g_path + 8];
    snprintf(side, sizeof side, "%s.json", g_path);
    f = fopen(side, "w");
    if (!f) return -1;
    bad = fprintf(f, "{\"base\":\"0x%08X\",\"size\":%u,\"event\":\"%s\",\"side\":\"khbbs\","
                     "\"nid\":\"0x%08X\",\"ra\":\"0x%08X\"}\n",
                  PSP_RAM_BASE, PSP_RAM_SIZE, g_event, nid, ra) < 0;
    bad |= fclose(f) != 0;
    return bad ? -1 : 0;
}

static void on_pre_call(uint32_t nid, const char *name, const uint32_t args[8], uint32_t ra) {
    (void)args;
    if (g_name[0] ? !(name && !strcmp(name, g_name)) : nid != g_nid) return;
    if (++g_seen < g_want) return;
    psp_hle_set_pre_call(NULL);
    g_fired = write_snapshot(nid, ra) == 0 ? 1 : -1;
    if (g_fired < 0) fprintf(stderr, "khbbs: cannot write snapshot %s\n", g_path);
}

/* NAME is a C identifier; NID is 0x and 1-8 hex digits. */
static int parse_event(const char *event) {
    const char *colon = event ? strrchr(event, ':') : NULL;
    if (!colon || colon == event || (size_t)(colon - event) >= sizeof g_name) return -1;
    uint64_t k;
    if (!khbbs_parse_count(colon + 1, &k) || k < 1 || k > UINT32_MAX) return -1;
    size_t n = (size_t)(colon - event);
    g_name[0] = 0;
    if (event[0] == '0' && (event[1] == 'x' || event[1] == 'X')) {
        if (n < 3 || n > 10) return -1;
        uint32_t v = 0;
        for (size_t i = 2; i < n; i++) {
            if (!isxdigit((unsigned char)event[i])) return -1;
            v = v << 4 | (uint32_t)(isdigit((unsigned char)event[i]) ? event[i] - '0'
                                    : tolower((unsigned char)event[i]) - 'a' + 10);
        }
        g_nid = v;
    } else {
        if (isdigit((unsigned char)event[0])) return -1;
        for (size_t i = 0; i < n; i++)
            if (!isalnum((unsigned char)event[i]) && event[i] != '_') return -1;
        memcpy(g_name, event, n);
        g_name[n] = 0;
    }
    g_want = k;
    return 0;
}

int khbbs_snapshot_arm(const char *event, const char *path) {
    khbbs_snapshot_disarm();
    g_fired = 0;
    if (!event || strlen(event) >= sizeof g_event || parse_event(event) != 0) {
        fprintf(stderr, "khbbs: bad snapshot event \"%s\" (want NAME:K or 0xNID:K, K >= 1)\n",
                event ? event : "(null)");
        return -1;
    }
    if (!path || strlen(path) >= sizeof g_path) {
        fprintf(stderr, "khbbs: bad snapshot path\n");
        return -1;
    }
    strcpy(g_event, event);
    strcpy(g_path, path);
    char side[sizeof g_path + 8];
    snprintf(side, sizeof side, "%s.json", g_path);
    remove(g_path);
    remove(side);
    g_seen = 0;
    psp_hle_set_pre_call(on_pre_call);
    return 0;
}

int khbbs_snapshot_fired(void) { return g_fired; }

void khbbs_snapshot_disarm(void) { psp_hle_set_pre_call(NULL); }
