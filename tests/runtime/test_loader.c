/* Loader: PT_LOAD segments land at their vaddr, the bss tail is zeroed, and
 * entry and $gp are reported. A synthetic ELF built here; no game data. */
#include "khbbs/loader.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }

/* Layout: ehdr @0, phdr @0x34, payload @0x60 (8 bytes), .reginfo @0x68 (24 bytes),
 * shstrtab @0x80, shdrs @0xA0 (3 entries). */
static size_t build_elf(uint8_t *b) {
    memset(b, 0, 0x200);
    memcpy(b, "\x7f" "ELF\x01\x01\x01", 7);
    put16(b + 0x10, 2);            /* ET_EXEC */
    put16(b + 0x12, 8);            /* EM_MIPS */
    put32(b + 0x14, 1);
    put32(b + 0x18, 0x08804004u);  /* e_entry */
    put32(b + 0x1C, 0x34);         /* e_phoff */
    put32(b + 0x20, 0xA0);         /* e_shoff */
    put16(b + 0x28, 0x34);         /* e_ehsize */
    put16(b + 0x2A, 0x20);         /* e_phentsize */
    put16(b + 0x2C, 1);            /* e_phnum */
    put16(b + 0x2E, 0x28);         /* e_shentsize */
    put16(b + 0x30, 3);            /* e_shnum */
    put16(b + 0x32, 2);            /* e_shstrndx */
    uint8_t *ph = b + 0x34;
    put32(ph + 0, 1);              /* PT_LOAD */
    put32(ph + 4, 0x60);
    put32(ph + 8, 0x08804000u);
    put32(ph + 16, 8);             /* filesz */
    put32(ph + 20, 16);            /* memsz: 8 bytes of bss */
    put32(b + 0x60, 0x11223344u);
    put32(b + 0x64, 0x55667788u);
    put32(b + 0x68 + 20, 0x08BD97E0u); /* .reginfo ri_gp_value */
    memcpy(b + 0x80, "\0.reginfo\0.shstrtab\0", 20);
    uint8_t *sh = b + 0xA0 + 0x28;     /* section 1: .reginfo */
    put32(sh + 0, 1);
    put32(sh + 4, 0x70000006u);
    put32(sh + 16, 0x68);
    put32(sh + 20, 24);
    sh += 0x28;                        /* section 2: .shstrtab */
    put32(sh + 0, 10);
    put32(sh + 4, 3);
    put32(sh + 16, 0x80);
    put32(sh + 20, 20);
    return 0xA0 + 3 * 0x28;
}

static void write_file(const char *path, const uint8_t *d, size_t n) {
    FILE *f = fopen(path, "wb");
    if (f) { fwrite(d, 1, n, f); fclose(f); }
}

int main(void) {
    uint8_t elf[0x200];
    size_t n = build_elf(elf);
    write_file("t_loader.elf", elf, n);

    CHECK(psp_mem_init() == 0, "memory");
    psp_write32(0x08804008u, 0xFFFFFFFFu);   /* must be zeroed as bss */

    khbbs_image img;
    CHECK(khbbs_load_elf("t_loader.elf", &img) == 0, "load succeeds");
    CHECK(img.entry == 0x08804004u, "entry");
    CHECK(img.gp == 0x08BD97E0u, "gp from .reginfo");
    CHECK(img.lo == 0x08804000u && img.hi == 0x08804010u, "image extent");
    CHECK(psp_read32(0x08804000u) == 0x11223344u, "segment byte 0");
    CHECK(psp_read32(0x08804004u) == 0x55667788u, "segment byte 4");
    CHECK(psp_read32(0x08804008u) == 0 && psp_read32(0x0880400Cu) == 0, "bss zeroed");

    CHECK(khbbs_load_elf("does-not-exist.elf", &img) != 0, "missing file fails");

    uint8_t trunc[16] = "\x7f" "ELF";
    write_file("t_loader_trunc.elf", trunc, sizeof trunc);
    CHECK(khbbs_load_elf("t_loader_trunc.elf", &img) != 0, "truncated file fails");

    put16(elf + 0x12, 3);                    /* EM_386 */
    write_file("t_loader_x86.elf", elf, n);
    CHECK(khbbs_load_elf("t_loader_x86.elf", &img) != 0, "non-MIPS file fails");

    remove("t_loader.elf");
    remove("t_loader_trunc.elf");
    remove("t_loader_x86.elf");

    printf("%s\n", failures ? "test_loader: FAILED" : "test_loader: ok");
    return failures ? 1 : 0;
}
