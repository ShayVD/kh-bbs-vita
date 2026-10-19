/* The trap the generated code calls at any instruction the recompiler could
 * not translate (main_funcs.h declares it as host-provided; the runtime
 * provides it so every host gets the same behaviour). */
#ifndef KHBBS_UNIMPLEMENTED_H
#define KHBBS_UNIMPLEMENTED_H

#include <stdint.h>

/* Prints the guest address and mnemonic, the miss context and flushes the
 * trace, then abort()s. Never returns. */
void psp_unimplemented(uint32_t addr, const char *what);

/* The guest executed `break` (an assert in the game). During khbbs_boot this
 * stops the run with KHBBS_GUEST_BREAK; outside it, it aborts like
 * psp_unimplemented. Defined in boot.c, which owns the stop. */
void psp_break(uint32_t addr, uint32_t code);

/* Make every VFPU trap fatal (through psp_unimplemented). Every host must
 * call it once at start-up (hosts/pc does; hosts/vita must too), or a trap
 * only prints and the run carries on with wrong values. The address a VFPU
 * trap reports is the trapping instruction's, which the generated code
 * stores in psp_cpu.pc before each VFPU call. */
void khbbs_install_traps(void);

#endif
