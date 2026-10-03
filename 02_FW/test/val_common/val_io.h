/* Validator E - shared helpers of the test_val_* Unity suites (header-only; this folder is not a
 * PlatformIO suite). Independent of Implementer A's test helpers (test/common_impl): only the
 * pure FW API under test and the validator's own vector files are used.
 *
 * Vector files: written by test/val_oracles/gen_val_vectors.py from 00_System/tools/vectors (json)
 * (read in place) into $VAL_VEC_DIR (default .pio/val_vectors relative to 02_FW). Line 1
 * "H <icd_version> <param_dict_hash>" is compared with the generated FW headers (stale vectors are a
 * failure, never a skip); line 2 "N <count>" is compared with the executed case count.
 *
 * Test order: VAL_SEED unset or 0 = declaration order; otherwise a seeded shuffle (printed).
 * Verifies: (harness for) IF-010 - vectors consumed in place, counted
 */
#ifndef VAL_IO_H
#define VAL_IO_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <unity.h>

#include "params_gen.h"
#include "proto_gen.h"

#define VAL_TOK 8192
#define VAL_UNUSED __attribute__((unused))

typedef struct {
    FILE    *f;
    uint32_t n_expected;
    char     path[512];
} val_file_t;

VAL_UNUSED static void val_path(char *out, size_t n, const char *name)
{
    const char *d = getenv("VAL_VEC_DIR");
    snprintf(out, n, "%s/%s", (d != NULL && d[0] != '\0') ? d : ".pio/val_vectors", name);
}

VAL_UNUSED static int val_tok(val_file_t *v, char *buf)
{
    return fscanf(v->f, "%8191s", buf) == 1;
}

/* open, check the H line against the FW headers, read N */
VAL_UNUSED static void val_open(val_file_t *v, const char *name)
{
    char icd[64];
    unsigned long hash = 0ul;
    char tag[8];
    val_path(v->path, sizeof v->path, name);
    v->f = fopen(v->path, "r");
    if (v->f == NULL) {
        char msg[600];
        snprintf(msg, sizeof msg, "validator vectors missing: %s (run test/val_oracles/gen_val_vectors.py)",
                 v->path);
        TEST_FAIL_MESSAGE(msg);
    }
    if (fscanf(v->f, "%7s %63s %lu", tag, icd, &hash) != 3 || strcmp(tag, "H") != 0) {
        TEST_FAIL_MESSAGE("vector file header H missing");
    }
    TEST_ASSERT_EQUAL_STRING_MESSAGE(PROTO_ICD_VERSION, icd, "vectors icd_version != PROTO_ICD_VERSION");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE((uint32_t)PARAM_DICT_HASH, (uint32_t)hash,
                                    "vectors param_dict_hash != PARAM_DICT_HASH");
    if (fscanf(v->f, "%7s %u", tag, &v->n_expected) != 2 || strcmp(tag, "N") != 0) {
        TEST_FAIL_MESSAGE("vector file count line N missing");
    }
}

VAL_UNUSED static void val_close(val_file_t *v, uint32_t executed)
{
    char msg[600];
    snprintf(msg, sizeof msg, "anti-skip: executed %u of %u cases in %s", executed, v->n_expected, v->path);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v->n_expected, executed, msg);
    printf("VALCOUNT %s executed=%u expected=%u\n", v->path, executed, v->n_expected);
    fclose(v->f);
    v->f = NULL;
}

VAL_UNUSED static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* "-" = empty */
VAL_UNUSED static uint16_t val_hex(const char *s, uint8_t *out, uint16_t max)
{
    size_t n = strlen(s), i;
    if (strcmp(s, "-") == 0) {
        return 0u;
    }
    TEST_ASSERT_TRUE_MESSAGE((n % 2u) == 0u && n / 2u <= max, "hex field length");
    for (i = 0u; i < n / 2u; i++) {
        int hi = hexval(s[2u * i]), lo = hexval(s[2u * i + 1u]);
        TEST_ASSERT_TRUE_MESSAGE(hi >= 0 && lo >= 0, "hex digit");
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return (uint16_t)(n / 2u);
}

VAL_UNUSED static uint32_t val_u32(val_file_t *v)
{
    char b[64];
    TEST_ASSERT_TRUE_MESSAGE(fscanf(v->f, "%63s", b) == 1, "number expected");
    return (uint32_t)strtoll(b, NULL, 10);
}

/* ---------------------------------------------------------------- seeded test order */
typedef struct {
    void (*fn)(void);
    const char *name;
    int line;
} val_case_t;

VAL_UNUSED static uint32_t val_rng = 1u;
VAL_UNUSED static uint32_t val_rand(void)
{
    val_rng ^= val_rng << 13;
    val_rng ^= val_rng >> 17;
    val_rng ^= val_rng << 5;
    return val_rng;
}

VAL_UNUSED static int val_run_f(val_case_t *c, unsigned n, const char *file)
{
    const char *s = getenv("VAL_SEED");
    unsigned long seed = (s != NULL) ? strtoul(s, NULL, 10) : 0ul;
    unsigned i;
    if (seed != 0ul) {
        val_rng = (uint32_t)seed;
        for (i = n - 1u; i > 0u; i--) {
            unsigned j = val_rand() % (i + 1u);
            val_case_t t = c[i];
            c[i] = c[j];
            c[j] = t;
        }
    }
    printf("VALSEED %lu\n", seed);
    UnityBegin(file);
    for (i = 0u; i < n; i++) {
        UnityDefaultTestRun(c[i].fn, c[i].name, c[i].line);
    }
    return UNITY_END();
}

#define VAL_CASE(f) { f, #f, __LINE__ }
#define val_run(c, n) val_run_f((c), (n), __FILE__)

#endif /* VAL_IO_H */
