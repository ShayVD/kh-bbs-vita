/* Dispatch-miss log for tools/merge_misses.py: one JSON line per distinct miss
 * target of a run,
 *   {"seq":0,"order":0,"stepped":true,"target":"0x08A8BE6C","ra":"0x08A8BE28","sp":"0x083FFFC0","last":"0x08A8BD58"}
 *
 * - seq counts lines since khbbs_misslog_open and restarts at 0 on every open.
 * - order is the k-th distinct miss target of the boot run (0 = the first); it
 *   restarts with each khbbs_boot, so a log shared by several runs has several
 *   order-0 lines.
 * - stepped says the run carried on past this miss (keep-going). Only the first
 *   miss of a run (order 0) is fully trustworthy: a stepped-over call returns
 *   with made-up results, and a stepped-over `jr` miss even returns from a
 *   function mid-frame, so what follows may be garbage.
 * - ra is $ra at the miss. For a `jal`/`jalr` miss that is the call site + 8;
 *   for a `jr` tail miss it is the current function's own return address.
 * - last is the most recently entered recompiled function when the generated
 *   code is built with PSPRECOMP_TRACE, otherwise null.
 *
 * The file is opened for appending, so several runs can share it. */
#ifndef KHBBS_MISSLOG_H
#define KHBBS_MISSLOG_H

#include <stdint.h>

/* 0 or -1. Also -1 (nothing opened, a message on stderr) when closing a
 * previously open log failed, so that error is not lost. */
int      khbbs_misslog_open(const char *path);
int      khbbs_misslog_active(void);             /* 1 while a log is open */
void     khbbs_misslog_record(uint32_t target, uint32_t order, int stepped);  /* no-op when closed */
uint64_t khbbs_misslog_count(void);              /* recorded since open */
int      khbbs_misslog_close(void);              /* 0, or -1 if a write failed */

#endif
