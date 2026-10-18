/* Load a fixed-address PSP ELF (ET_EXEC, as KH BBS's decrypted EBOOT is)
 * into guest memory. Relocatable PRX loading is not needed by this game. */
#ifndef KHBBS_LOADER_H
#define KHBBS_LOADER_H

#include <stdint.h>

typedef struct {
    uint32_t entry;   /* e_entry */
    uint32_t gp;      /* .reginfo ri_gp_value, 0 if absent */
    uint32_t lo, hi;  /* lowest vaddr and end of the highest PT_LOAD (memsz) */
} khbbs_image;

/* Returns 0 on success, -1 on any error (message on stderr). psp_mem_init()
 * must have been called. */
int khbbs_load_elf(const char *path, khbbs_image *out);

#endif
