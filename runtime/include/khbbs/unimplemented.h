/* The trap the generated code calls at any instruction the recompiler could
 * not translate (main_funcs.h declares it as host-provided; the runtime
 * provides it so every host gets the same behaviour). */
#ifndef KHBBS_UNIMPLEMENTED_H
#define KHBBS_UNIMPLEMENTED_H

#include <stdint.h>

/* Prints the guest address and mnemonic, the miss context and flushes the
 * trace, then abort()s. Never returns. */
void psp_unimplemented(uint32_t addr, const char *what);

/* Make every VFPU trap fatal (through psp_unimplemented). Call once at
 * start-up. The address a VFPU trap reports is psp_cpu.pc, which is exact
 * only at dispatch boundaries. */
void khbbs_install_traps(void);

#endif
