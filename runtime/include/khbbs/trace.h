/* JSON-lines trace of firmware calls: one object per line,
 *   {"seq":N,"nid":"0x...","name":"..."|null,"ra":"0x...","args":[8 x "0x..."],
 *    "ret":"0x...","ret_hi":"0x..."}
 * ret is $v0 and ret_hi is $v1 after the call (meaningful only for 64-bit
 * returns). The format is the contract with tools/oracle (P2); change both
 * together. */
#ifndef KHBBS_TRACE_H
#define KHBBS_TRACE_H

#include <stdint.h>

int  khbbs_trace_open(const char *path);   /* installs the psprecomp hook */
void khbbs_trace_close(void);              /* removes it and flushes */
uint64_t khbbs_trace_count(void);

#endif
