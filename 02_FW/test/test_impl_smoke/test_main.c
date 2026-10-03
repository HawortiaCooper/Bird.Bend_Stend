/* WP0 smoke suite: the native env builds pure + gen + core against the fake seams, and the
 * generated dictionary is the one the vectors were made for.
 * Verifies: FW-PLT-001 (host build), FW-CFG-001 (generated table present)
 */
#include <unity.h>

#include "params_gen.h"
#include "proto_gen.h"

void setUp(void) {}
void tearDown(void) {}

static void test_generated_tables_present(void)
{
    params_t p;
    TEST_ASSERT_EQUAL_UINT32(47u, PARAM_COUNT);             /* dict 4: io.stop_active_level retired */
    TEST_ASSERT_EQUAL_STRING("0.5", PROTO_ICD_VERSION);
    TEST_ASSERT_NULL(param_find(0x0603u));               /* retired io.stop_active_level (CR-01) */
    params_set_defaults(&p);
    TEST_ASSERT_EQUAL_FLOAT(800.0f, p.motion.steps_per_mm);
    TEST_ASSERT_NOT_NULL(param_find(PID_DRV_K1_WELD_MS));
    TEST_ASSERT_NULL(param_find(0x0401u));               /* retired home.ref_switch */
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_generated_tables_present);
    return UNITY_END();
}
