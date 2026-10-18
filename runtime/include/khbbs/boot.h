/* Run a loaded module's entry point until it returns or the run must stop. */
#ifndef KHBBS_BOOT_H
#define KHBBS_BOOT_H

#include "khbbs/loader.h"
#include <stdint.h>

typedef enum {
    KHBBS_RETURNED,
    KHBBS_DISPATCH_MISS,
    KHBBS_BUDGET,
    KHBBS_BAD_ARGS      /* nothing was run: `path` too long for the boot area */
} khbbs_stop;

typedef struct {
    khbbs_stop why;
    uint32_t   addr;       /* the missed target, for KHBBS_DISPATCH_MISS */
    uint64_t   calls;      /* psp_dispatch_calls() at the stop */
} khbbs_result;

/* `budget` caps indirect dispatches (0 = unlimited). The module gets
 * module_start(argc, argp) with argp = `path` placed in guest memory.
 * Guest memory (psp_mem_init) must already be set up.
 * `path` must be at most KHBBS_BOOT_PATH_MAX bytes. */
#define KHBBS_BOOT_PATH_MAX 255
khbbs_result khbbs_boot(const khbbs_image *img, const char *path, uint64_t budget);

const char *khbbs_stop_name(khbbs_stop s);

#endif
