/* Validator E - µm <-> steps and speed cap (independent suite; FW_test_plan v0.1 §2, level U).
 * Oracles: units_vectors.json (Integrator, "S" lines) and the validator's own binary64 + round half
 * away from zero model of ICD §0.1 (test/val_oracles/gen_val_vectors.py, "V" lines: ties, negatives,
 * values near the int32 result range).
 *
 * Verifies: SYS-003, FW-MOT-002 (conversion part), FW-MOT-009 (cap formula)
 * TC: TC-SYS-003-01
 */
#include "../val_common/val_io.h"

#include "le.h"
#include "units.h"

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

static void test_units_vectors_and_oracle(void)
{
    val_file_t v;
    uint32_t done = 0u, shared = 0u, own = 0u;
    val_open(&v, "units.txt");
    while (val_tok(&v, T)) {
        char kind[4], src[4], msg[160];
        uint32_t bits = val_u32(&v);
        uint32_t a = val_u32(&v), b = val_u32(&v);
        float spm = f32_from_bits(bits);
        strcpy(kind, T);
        val_tok(&v, src);
        snprintf(msg, sizeof msg, "%s spm=%.6g a=%d (%s)", kind, (double)spm, (int)(int32_t)a, src);
        if (strcmp(kind, "UM") == 0) {
            TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)b, units_um_to_steps((int32_t)a, spm), msg);
        } else if (strcmp(kind, "SU") == 0) {
            TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)b, units_steps_to_um((int32_t)a, spm), msg);
        } else if (strcmp(kind, "RC") == 0) {
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(b, units_rate_cap_um_s(a, spm), msg);
        } else {
            TEST_FAIL_MESSAGE(kind);
        }
        if (strcmp(src, "S") == 0) shared++; else own++;
        done++;
    }
    val_close(&v, done);
    printf("VALUNITS shared=%u validator_oracle=%u\n", shared, own);
}

/* symmetry and sign: round half AWAY from zero for exact ties (spm = 1000 -> steps = um) */
static void test_units_ties_exact(void)
{
    TEST_ASSERT_EQUAL_INT32(1, units_um_to_steps(1, 500.0f));     /* 0.5 -> 1 */
    TEST_ASSERT_EQUAL_INT32(-1, units_um_to_steps(-1, 500.0f));   /* -0.5 -> -1 */
    TEST_ASSERT_EQUAL_INT32(2, units_um_to_steps(3, 500.0f));     /* 1.5 -> 2 */
    TEST_ASSERT_EQUAL_INT32(-2, units_um_to_steps(-3, 500.0f));
    TEST_ASSERT_EQUAL_INT32(3, units_steps_to_um(1, 400.0f));     /* 2.5 -> 3 */
    TEST_ASSERT_EQUAL_INT32(-3, units_steps_to_um(-1, 400.0f));
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_units_vectors_and_oracle),
        VAL_CASE(test_units_ties_exact),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
