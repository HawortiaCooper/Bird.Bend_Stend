/* µm <-> steps and the speed cap against vectors/units_vectors.json (ICD §0.1, exact incl. ties).
 * Verifies: SYS-003, FW-MOT-002, FW-MOT-009
 */
#include <stdio.h>
#include <string.h>
#include <unity.h>

#include "units.h"
#include "vec_units.h"

void setUp(void) {}
void tearDown(void) {}

static float f32(uint32_t bits)
{
    float f;
    memcpy(&f, &bits, 4);
    return f;
}

static void test_um_to_steps(void)
{
    uint32_t i;
    char msg[64];
    for (i = 0u; i < VEC_UM2ST_N; i++) {
        (void)snprintf(msg, sizeof msg, "um_to_steps #%u", (unsigned)i);
        TEST_ASSERT_EQUAL_INT32_MESSAGE(VEC_UM2ST[i].b, units_um_to_steps(VEC_UM2ST[i].a, f32(VEC_UM2ST[i].spm_bits)), msg);
    }
}

static void test_steps_to_um(void)
{
    uint32_t i;
    char msg[64];
    for (i = 0u; i < VEC_ST2UM_N; i++) {
        (void)snprintf(msg, sizeof msg, "steps_to_um #%u", (unsigned)i);
        TEST_ASSERT_EQUAL_INT32_MESSAGE(VEC_ST2UM[i].b, units_steps_to_um(VEC_ST2UM[i].a, f32(VEC_ST2UM[i].spm_bits)), msg);
    }
}

static void test_rate_cap(void)
{
    uint32_t i;
    for (i = 0u; i < VEC_CAP_N; i++) {
        TEST_ASSERT_EQUAL_UINT32(VEC_CAP[i].cap, units_rate_cap_um_s(VEC_CAP[i].rate, f32(VEC_CAP[i].spm_bits)));
    }
}

static void test_half_away_from_zero(void)
{
    /* spm 100: 5 um = 0.5 step -> 1, -5 um -> -1 */
    TEST_ASSERT_EQUAL_INT32(1, units_um_to_steps(5, 100.0f));
    TEST_ASSERT_EQUAL_INT32(-1, units_um_to_steps(-5, 100.0f));
    TEST_ASSERT_EQUAL_INT32(0, units_um_to_steps(4, 100.0f));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_um_to_steps);
    RUN_TEST(test_steps_to_um);
    RUN_TEST(test_rate_cap);
    RUN_TEST(test_half_away_from_zero);
    return UNITY_END();
}
