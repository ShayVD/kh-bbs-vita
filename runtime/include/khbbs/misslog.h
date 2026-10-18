/* Dispatch-miss log for tools/merge_misses.py: one JSON line per miss,
 *   {"seq":0,"target":"0x08A8BE6C","ra":"0x08A8BE28","sp":"0x083FFFC0","last":"0x08A8BD58"}
 * `last` is the most recently entered recompiled function when the generated
 * code is built with PSPRECOMP_TRACE, otherwise null. The file is opened for
 * appending, so several runs can share it. */
#ifndef KHBBS_MISSLOG_H
#define KHBBS_MISSLOG_H

#include <stdint.h>

int      khbbs_misslog_open(const char *path);   /* 0 or -1 */
void     khbbs_misslog_record(uint32_t target);  /* no-op when closed */
uint64_t khbbs_misslog_count(void);              /* recorded since open */
int      khbbs_misslog_close(void);              /* 0, or -1 if a write failed */

#endif
