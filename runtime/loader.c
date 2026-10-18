#include "khbbs/loader.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

/* True if [off, off+size) lies within a buffer of len bytes (64-bit math, no overflow). */
static int in_range(uint64_t off, uint64_t size, size_t len) { return off + size <= (uint64_t)len; }

static uint8_t *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    rewind(f);
    uint8_t *b = n > 0 ? (uint8_t *)malloc((size_t)n) : NULL;
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
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

int khbbs_load_elf(const char *path, khbbs_image *out) {
    size_t len = 0;
    uint8_t *d = slurp(path, &len);
    if (!d) { fprintf(stderr, "khbbs: cannot read %s\n", path); return -1; }
    if (len < 0x34 || memcmp(d, "\x7f" "ELF\x01\x01", 6) != 0 || rd16(d + 0x12) != 8) {
        fprintf(stderr, "khbbs: %s is not a little-endian 32-bit MIPS ELF\n", path);
        free(d);
        return -1;
    }
    uint32_t phoff = rd32(d + 0x1C);
    uint16_t phentsize = rd16(d + 0x2A), phnum = rd16(d + 0x2C);
    if (phentsize < 0x20 || !in_range(phoff, (uint64_t)phnum * phentsize, len)) {
        fprintf(stderr, "khbbs: %s: bad program headers\n", path);
        free(d);
        return -1;
    }
    out->entry = rd32(d + 0x18);
    out->gp = find_gp(d, len);
    out->lo = 0xFFFFFFFFu;
    out->hi = 0;
    int loaded = 0;
    for (uint16_t i = 0; i < phnum; i++) {
        const uint8_t *ph = d + phoff + (size_t)i * phentsize;
        if (rd32(ph) != 1 /* PT_LOAD */) continue;
        uint32_t off = rd32(ph + 4), vaddr = rd32(ph + 8), filesz = rd32(ph + 16), memsz = rd32(ph + 20);
        uint8_t *dst = memsz ? (uint8_t *)psp_mem_ptr(vaddr, memsz) : NULL;
        if (memsz && (!dst || filesz > memsz || !in_range(off, filesz, len))) {
            fprintf(stderr, "khbbs: %s: segment at 0x%08X (+0x%X) does not fit guest memory\n",
                    path, vaddr, memsz);
            free(d);
            return -1;
        }
        if (memsz) {
            memcpy(dst, d + off, filesz);
            memset(dst + filesz, 0, memsz - filesz);
        }
        if (vaddr < out->lo) out->lo = vaddr;
        if (vaddr + memsz > out->hi) out->hi = vaddr + memsz;
        loaded++;
    }
    free(d);
    if (!loaded) { fprintf(stderr, "khbbs: %s has no PT_LOAD segment\n", path); return -1; }
    return 0;
}
