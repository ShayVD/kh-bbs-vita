#include "khbbs/args.h"

#include <ctype.h>
#include <errno.h>
#include <stdlib.h>

int khbbs_parse_count(const char *s, uint64_t *out) {
    if (!s || !isdigit((unsigned char)*s)) return 0;   /* strtoull takes " 5" and "-1" */
    const int hex = s[0] == '0' && (s[1] == 'x' || s[1] == 'X');
    char *end;
    errno = 0;
    const unsigned long long v = strtoull(s, &end, hex ? 16 : 10);
    if (*end != '\0' || errno) return 0;
    *out = v;
    return 1;
}
