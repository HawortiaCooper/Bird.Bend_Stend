/* Hard rules H1..H5 (ICD §11.4): pairwise boundaries, detail = the other parameter, whole-image
 * check, H5 at 10 / 80 SPS. (The rule_h* check vectors run in test_impl_check.)
 * Verifies: FW-CFG-003, SAF-FW-010, SAF-FW-012 (H5, D-33a)
 */
#include <unity.h>

#include "param_rules.h"

void setUp(void) {}
void tearDown(void) {}

static params_t P;

static uint16_t set(uint16_t id, uint32_t raw) { return param_rules_check_set(&P, id, raw); }

static void test_defaults_hold(void)
{
    params_set_defaults(&P);
    TEST_ASSERT_TRUE(param_rules_check_all(&P));
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_AFE_GAIN_CHANNEL, 2u));         /* no rule */
    TEST_ASSERT_EQUAL_UINT16(0u, set(0x0401u, 0u));                      /* unknown / retired id */
}

static void test_h1_soft_limits(void)
{
    params_set_defaults(&P);                                             /* 500 / 290000 */
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_LIMITS_SOFT_MIN_UM, 289999u));
    TEST_ASSERT_EQUAL_UINT16(PID_LIMITS_SOFT_MAX_UM, set(PID_LIMITS_SOFT_MIN_UM, 290000u));
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_LIMITS_SOFT_MAX_UM, 501u));
    TEST_ASSERT_EQUAL_UINT16(PID_LIMITS_SOFT_MIN_UM, set(PID_LIMITS_SOFT_MAX_UM, 500u));
    TEST_ASSERT_EQUAL_UINT16(PID_LIMITS_SOFT_MIN_UM, set(PID_LIMITS_SOFT_MAX_UM, 0u));
}

static void test_h2_load_limits(void)
{
    params_set_defaults(&P);
    TEST_ASSERT_EQUAL_UINT16(PID_SAFETY_LOAD_RAW_MAX, set(PID_SAFETY_LOAD_RAW_MIN, 7022271u));
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_SAFETY_LOAD_RAW_MIN, 7022270u));
    TEST_ASSERT_EQUAL_UINT16(PID_SAFETY_LOAD_RAW_MIN, set(PID_SAFETY_LOAD_RAW_MAX, (uint32_t)-7022271));
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_SAFETY_LOAD_RAW_MAX, (uint32_t)-7022270));
}

static void test_h3_step_timing_u64(void)
{
    params_set_defaults(&P);                                             /* 50 kHz, 10 us + 10 us */
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_MOTION_MAX_STEP_RATE_HZ, 50000u));      /* = 1e9 exactly */
    TEST_ASSERT_EQUAL_UINT16(PID_MOTION_PULSE_HIGH_NS, set(PID_MOTION_MAX_STEP_RATE_HZ, 50001u));
    TEST_ASSERT_EQUAL_UINT16(PID_MOTION_MAX_STEP_RATE_HZ, set(PID_MOTION_PULSE_HIGH_NS, 10001u));
    TEST_ASSERT_EQUAL_UINT16(PID_MOTION_MAX_STEP_RATE_HZ, set(PID_MOTION_PULSE_LOW_MIN_NS, 100000u));
    P.motion.max_step_rate_hz = 100u;                                    /* 100 * 200000 = 2e7 */
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_MOTION_PULSE_HIGH_NS, 100000u));
    P.motion.max_step_rate_hz = 100000u;                                 /* overflow-safe product */
    P.motion.pulse_high_ns = 100000u;
    P.motion.pulse_low_min_ns = 100000u;
    TEST_ASSERT_FALSE(param_rules_check_all(&P));
}

static void test_h4_speeds(void)
{
    params_set_defaults(&P);                                             /* load 20000 <= travel 30000 */
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_MOTION_V_MAX_LOAD_UM_S, 30000u));
    TEST_ASSERT_EQUAL_UINT16(PID_MOTION_V_MAX_TRAVEL_UM_S, set(PID_MOTION_V_MAX_LOAD_UM_S, 30001u));
    TEST_ASSERT_EQUAL_UINT16(PID_MOTION_V_MAX_LOAD_UM_S, set(PID_MOTION_V_MAX_TRAVEL_UM_S, 19999u));
}

static void test_h5_afe_timeout(void)
{
    params_set_defaults(&P);                                             /* 250 ms, SPS80 */
    TEST_ASSERT_EQUAL_UINT32(80u, param_rules_sps(AFE_RATE_SPS_SPS80));
    TEST_ASSERT_EQUAL_UINT32(10u, param_rules_sps(AFE_RATE_SPS_SPS10));
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_AFE_TIMEOUT_MS, 25u));                  /* 25 * 80 = 2000 */
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_AFE_RATE_SPS, AFE_RATE_SPS_SPS10));     /* 250 * 10 */
    P.afe.timeout_ms = 199u;
    TEST_ASSERT_EQUAL_UINT16(PID_AFE_TIMEOUT_MS, set(PID_AFE_RATE_SPS, AFE_RATE_SPS_SPS10));
    P.afe.timeout_ms = 200u;
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_AFE_RATE_SPS, AFE_RATE_SPS_SPS10));
    P.afe.rate_sps = AFE_RATE_SPS_SPS10;
    TEST_ASSERT_EQUAL_UINT16(PID_AFE_RATE_SPS, set(PID_AFE_TIMEOUT_MS, 199u));
    TEST_ASSERT_EQUAL_UINT16(0u, set(PID_AFE_TIMEOUT_MS, 200u));
    P.afe.timeout_ms = 100u;
    TEST_ASSERT_FALSE(param_rules_check_all(&P));
}

static void test_check_all_each_rule(void)
{
    params_set_defaults(&P);
    P.limits.soft_min_um = P.limits.soft_max_um;
    TEST_ASSERT_FALSE(param_rules_check_all(&P));
    params_set_defaults(&P);
    P.safety.load_raw_min = P.safety.load_raw_max;
    TEST_ASSERT_FALSE(param_rules_check_all(&P));
    params_set_defaults(&P);
    P.motion.v_max_load_um_s = P.motion.v_max_travel_um_s + 1u;
    TEST_ASSERT_FALSE(param_rules_check_all(&P));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_defaults_hold);
    RUN_TEST(test_h1_soft_limits);
    RUN_TEST(test_h2_load_limits);
    RUN_TEST(test_h3_step_timing_u64);
    RUN_TEST(test_h4_speeds);
    RUN_TEST(test_h5_afe_timeout);
    RUN_TEST(test_check_all_each_rule);
    return UNITY_END();
}
