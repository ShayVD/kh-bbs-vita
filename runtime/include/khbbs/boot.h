/* Run a loaded module's entry point until it returns or the run must stop. */
#ifndef KHBBS_BOOT_H
#define KHBBS_BOOT_H

#include "khbbs/loader.h"
#include <stdint.h>

typedef enum {
    KHBBS_RETURNED,
    KHBBS_DISPATCH_MISS,
    KHBBS_BUDGET,
    KHBBS_BAD_ARGS,     /* nothing was run: `path` too long for the boot area */
    KHBBS_GUEST_BREAK   /* the game executed `break`, usually a failed assert */
} khbbs_stop;

typedef struct {
    khbbs_stop why;
    uint32_t   addr;       /* the missed target (KHBBS_DISPATCH_MISS) or the break's address (KHBBS_GUEST_BREAK) */
    uint64_t   calls;      /* dispatches made, including the entry dispatch */
    uint32_t   code;       /* the break code, for KHBBS_GUEST_BREAK */
    uint32_t   misses;     /* distinct miss targets, including any stepped over */
} khbbs_result;

typedef struct {
    uint64_t budget;       /* indirect dispatches allowed, 0 = unlimited (see below) */
    uint32_t keep_going;   /* distinct miss targets to step over, 0 = stop at the first */
} khbbs_boot_opts;

/* The module gets module_start(argc, argp) with argp = `path` placed in guest
 * memory. `opts` may be NULL (no budget, stop at the first miss). A stepped-over
 * miss returns from the dispatch with $v0 and $v1 zeroed, as if the callee did
 * nothing; that is wrong, but it lets one run collect several misses. Only the
 * first miss of such a run is fully trustworthy: a stepped-over `jr` miss
 * returns from its function mid-frame, so later misses may be garbage.
 * keep_going counts distinct targets: a target missed again (it was stepped
 * over the first time) is stepped over again, neither counted nor logged, up
 * to 1024 distinct targets per run; beyond that every miss counts. Each
 * distinct miss, the stopping one included, goes to the miss log if one is
 * open (see misslog.h), or else a stepped-over one is reported on stderr.
 * psp_mem_init() must be called before khbbs_boot. A budget of N counts the
 * entry dispatch itself, so it allows N-1 further indirect dispatches.
 * On return (any path) the dispatch budget is cleared and the miss handler is
 * reset to psprecomp's default; a handler installed by the host beforehand is
 * not preserved (psprecomp has no getter).
 * A guest `break` (psp_break, see unimplemented.h) ends the run with
 * KHBBS_GUEST_BREAK, addr and code; psp_break called outside a boot aborts.
 * NULL img or path gives KHBBS_BAD_ARGS. The trace (if open) is flushed on return.
 * `path` must be at most KHBBS_BOOT_PATH_MAX bytes. */
#define KHBBS_BOOT_PATH_MAX 255
khbbs_result khbbs_boot(const khbbs_image *img, const char *path, const khbbs_boot_opts *opts);

const char *khbbs_stop_name(khbbs_stop s);

#endif
