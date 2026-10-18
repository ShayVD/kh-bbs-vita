/* Command-line number parsing for the hosts. Platform-free so it can be tested. */
#ifndef KHBBS_ARGS_H
#define KHBBS_ARGS_H

#include <stdint.h>

/* Parses an unsigned count: decimal digits, or hex after a 0x/0X prefix. A
 * leading zero is not octal ("010" is ten). Rejects "", spaces, signs, trailing
 * text and overflow. Returns 1 and sets *out on success, else 0 and leaves it. */
int khbbs_parse_count(const char *s, uint64_t *out);

#endif
