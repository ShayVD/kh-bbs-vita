#include "khbbs/loader.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

/* True if [off, off+size) lies within a buffer of len bytes (64-bit math, no overflow). */
static int in_range(uint64_t off, uint64_t size, size_t len) { return off + size <= (uint64_t)len; }

#define MAX_ELF_BYTES (64u << 20)

static uint8_t *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    uint8_t *b = NULL;
    long n = -1;
    if (fseek(f, 0, SEEK_END) == 0) n = ftell(f);
    if (n > 0 && n <= (long)MAX_ELF_BYTES && fseek(f, 0, SEEK_SET) == 0) {
        b = (uint8_t *)malloc((size_t)n);
        if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    }
    fclose(f);
    if (b) *len = (size_t)n;
    return b;
}

static uint32_t find_gp(const uint8_t *d, size_t len) {
    uint32_t shoff = rd32(d + 0x20);
    uint16_t shentsize = rd16(d + 0x2E), shnum = rd16(d + 0x30);
    if (!shoff || shentsize < 0x28 || !in_range(shoff, (uint64_t)shnum * shentsize, len)) return 0;
    for (uint16_t i = 0; i < shnum; i++) {
        const uint8_t *sh = d + shoff + (size_t)i * shentsize;
        uint32_t off = rd32(sh + 16);
        if (rd32(sh + 4) == 0x70000006u /* SHT_MIPS_REGINFO */ && rd32(sh + 20) >= 24 &&
            in_range(off, 24, len))
            return rd32(d + off + 20);
    }
    return 0;
}

static int fail(const char *path, const char *msg, uint32_t a, uint32_t b, uint8_t *d) {
    if (msg) fprintf(stderr, msg, path, a, b);
    free(d);
    return -1;
}

int khbbs_load_elf(const char *path, khbbs_image *out) {
    size_t len = 0;
    uint8_t *d = slurp(path, &len);
    if (!d) { fprintf(stderr, "khbbs: cannot read %s (missing, empty or over 64 MiB)\n", path); return -1; }
    if (len < 0x34 || memcmp(d, "\x7f" "ELF\x01\x01\x01", 7) != 0 || rd16(d + 0x12) != 8)
        return fail(path, "khbbs: %s is not a little-endian 32-bit MIPS ELF\n", 0, 0, d);
    if (rd16(d + 0x10) != 2)
        return fail(path, "khbbs: %s is not ET_EXEC (type 0x%X); relocatable PRX loading is not supported\n",
                    rd16(d + 0x10), 0, d);
    uint32_t phoff = rd32(d + 0x1C);
    uint16_t phentsize = rd16(d + 0x2A), phnum = rd16(d + 0x2C);
    if (phentsize < 0x20 || !in_range(phoff, (uint64_t)phnum * phentsize, len))
        return fail(path, "khbbs: %s: bad program headers\n", 0, 0, d);

    khbbs_image img = { rd32(d + 0x18), find_gp(d, len), 0xFFFFFFFFu, 0 };
    int loaded = 0;

    /* Pass 1: validate every PT_LOAD before touching guest memory. */
    for (uint16_t i = 0; i < phnum; i++) {
        const uint8_t *ph = d + phoff + (size_t)i * phentsize;
        if (rd32(ph) != 1 /* PT_LOAD */) continue;
        uint32_t off = rd32(ph + 4), vaddr = rd32(ph + 8), filesz = rd32(ph + 16), memsz = rd32(ph + 20);
        if (!memsz) continue;
        uint32_t a = vaddr & PSP_ADDR_MASK;
        if (a < PSP_RAM_BASE || (uint64_t)a + memsz > (uint64_t)PSP_RAM_BASE + PSP_RAM_SIZE)
            return fail(path, "khbbs: %s: segment at 0x%08X (+0x%X) is outside guest RAM\n", vaddr, memsz, d);
        if (filesz > memsz || !in_range(off, filesz, len))
            return fail(path, "khbbs: %s: segment at 0x%08X has bad file size or offset\n", vaddr, 0, d);
        if (vaddr < img.lo) img.lo = vaddr;
        if ((uint64_t)vaddr + memsz > img.hi) img.hi = vaddr + memsz;
        loaded++;
    }
    if (!loaded) return fail(path, "khbbs: %s has no PT_LOAD segment\n", 0, 0, d);

    /* Pass 2: copy and zero the bss tail. */
    for (uint16_t i = 0; i < phnum; i++) {
        const uint8_t *ph = d + phoff + (size_t)i * phentsize;
        if (rd32(ph) != 1) continue;
        uint32_t off = rd32(ph + 4), vaddr = rd32(ph + 8), filesz = rd32(ph + 16), memsz = rd32(ph + 20);
        if (!memsz) continue;
        uint8_t *dst = (uint8_t *)psp_mem_ptr(vaddr, memsz);
        if (!dst) return fail(path, "khbbs: %s: segment at 0x%08X (+0x%X) is not mapped\n", vaddr, memsz, d);
        memcpy(dst, d + off, filesz);
        memset(dst + filesz, 0, memsz - filesz);
    }
    free(d);
    *out = img;
    return 0;
}
