/* JSON-lines trace of firmware calls: one object per line,
 *   {"seq":N,"nid":"0x...","name":"..."|null,"ra":"0x...","args":[8 x "0x..."],
 *    "ret":"0x...","ret_hi":"0x..."}
 * The format is the contract with tools/oracle (P2); change both together.
 *
 * - The hook fires after the handler returns, so lines are in completion
 *   order: a nested firmware call (a handler that runs guest code) appears
 *   before its parent. A call whose handler never returns (longjmp out,
 *   abort) is not traced.
 * - ret is $v0. ret_hi is $v1 as the handler left it and is only meaningful
 *   for 64-bit returns (e.g. sceKernelGetSystemTimeWide, sceIoLseek);
 *   consumers must ignore it otherwise.
 * - name is null for unregistered NIDs. Names are written unescaped because
 *   firmware names are C identifiers.
 * - Output is line-buffered so a crash loses at most the line in progress. */
#ifndef KHBBS_TRACE_H
#define KHBBS_TRACE_H

#include <stdint.h>

int  khbbs_trace_open(const char *path);   /* installs the psprecomp hook; closes any open trace */
void khbbs_trace_flush(void);
/* Removes the hook and closes the file. Returns 0, or -1 if any line was
 * truncated or any write, flush or close failed since open. */
int  khbbs_trace_close(void);
uint64_t khbbs_trace_count(void);          /* lines written since open */

#endif
