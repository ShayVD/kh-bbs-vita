/* khbbs_parse_count: the host's numeric option parser. */
#include "khbbs/args.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static int parses(const char *s, uint64_t want) {
    uint64_t v = 12345;
    return khbbs_parse_count(s, &v) && v == want;
}
static int rejects(const char *s) {
    uint64_t v;
    return !khbbs_parse_count(s, &v);
}

int main(void) {
    CHECK(parses("0", 0), "0");
    CHECK(parses("50", 50), "decimal");
    CHECK(parses("010", 10), "a leading zero is decimal, not octal");
    CHECK(parses("0x10", 16), "0x is hex");
    CHECK(parses("0X1f", 31), "0X is hex");
    CHECK(parses("18446744073709551615", UINT64_MAX), "max");
    CHECK(rejects(""), "empty");
    CHECK(rejects(" 5"), "leading space");
    CHECK(rejects("-1"), "negative");
    CHECK(rejects("5x"), "trailing junk");
    CHECK(rejects("0x"), "bare 0x");
    CHECK(rejects("0xg"), "bad hex");
    CHECK(parses("08", 8), "08 is decimal 8");
    CHECK(rejects("18446744073709551616"), "overflow");
    printf("%s\n", failures ? "test_args: FAILED" : "test_args: ok");
    return failures ? 1 : 0;
}
