/* WP0 smoke suite: the native env builds pure + gen + core against the fake seams, and the
 * generated dictionary is the one the vectors were made for.
 * Verifies: FW-PLT-001 (host build), FW-CFG-001 (generated table present)
 */
#include <string.h>
#include <unity.h>

#include "params_gen.h"
#include "proto_gen.h"

void setUp(void) {}
void tearDown(void) {}

static void test_generated_tables_present(void)
{
    params_t p;
    TEST_ASSERT_EQUAL_UINT32(48u, PARAM_COUNT);             /* dict 5: + drv.k1_check_enable (ICD v0.7) */
    /* the FW implements ICD 0.7.x: the generated version must be of that line (patch levels such as
     * 0.7.1 = Appendix C only are accepted; the dictionary is checked by count / hash elsewhere) */
    TEST_ASSERT_EQUAL_INT(0, strncmp(PROTO_ICD_VERSION, "0.7", 3u));
    TEST_ASSERT_TRUE(PROTO_ICD_VERSION[3] == '\0' || PROTO_ICD_VERSION[3] == '.');
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
