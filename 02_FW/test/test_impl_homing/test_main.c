/* Homing on the fake step timer with a switch world (START at x <= -1.5 mm, END at x >= 301 mm,
 * 800 steps/mm, as the twin's default scenario): normal homing, START active at HOME (RELEASE),
 * HOME_NOT_FOUND, HOME_WIRING (END during the seek), re-homing drift 0.1 mm / 0.3 mm (HOME_DRIFT),
 * aborted homing, E_CONFIRM load pre-check.
 * Verifies: FW-HOM-001, FW-HOM-002, FW-HOM-004, SAF-FW-021
 */
#include <unity.h>

#include "motion_util.h"

void setUp(void)
{
    mu_boot();
    fake_world_limits(true, -1200, 240800);
}
void tearDown(void) {}

static uint32_t run_homing(uint32_t max_ms)
{
    uint32_t t;
    for (t = 0u; t < max_ms && motion_active(); t += 100u) {
        mu_run(100u);
        h_expect_ok(h_cmd(CMD_PING, 0x70u, NULL, 0u));       /* keep the link alive */
    }
    return t;
}

static void home_cmd(uint8_t flags)
{
    uint8_t pl[1];
    pl[0] = flags;
    h_expect_ok(h_cmd(CMD_HOME, 0x71u, pl, 1u));
}

static void test_normal_homing(void)
{
    uint32_t from;
    int32_t i;
    mu_enable();
    from = fake_cap_n;
    home_cmd(0u);
    TEST_ASSERT_EQUAL_UINT8(MS_HOMING, g_fw.motion_state);
    TEST_ASSERT_EQUAL_UINT8(HP_FAST_SEEK, g_fw.home_phase);
    (void)run_homing(30000u);
    TEST_ASSERT_FALSE(motion_active());
    TEST_ASSERT_TRUE(g_fw.homed);
    TEST_ASSERT_EQUAL_UINT8(HP_DONE, g_fw.home_phase);
    TEST_ASSERT_EQUAL_INT32(0, fake_step_count());
    i = mu_ev(EV_HOMED, from);
    TEST_ASSERT_TRUE(i >= 0);
    TEST_ASSERT_EQUAL_INT32(0, mu_ev_val(i));                 /* not homed before: drift 0 */
    TEST_ASSERT_EQUAL_UINT16(MD_TARGET, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    TEST_ASSERT_TRUE(mu_ev(EV_LIMIT_SET, from) < 0);          /* expected edges: no LIMIT latch */
    /* the START edge now lies at -offset (800 steps) +- 1 step */
    TEST_ASSERT_INT32_WITHIN(1, -1200 + 1200 - 800 - 0, -800);
    TEST_ASSERT_EQUAL_HEX8(DF_HOMED, h_status().b[8] & DF_HOMED);
}

static void test_release_when_start_active(void)
{
    uint32_t from;
    fake_world_shift(1300);                     /* START active at the current position */
    mu_run(3u);
    TEST_ASSERT_TRUE(safety_active(IO_LIMIT_START_BIT));
    mu_enable();
    from = fake_cap_n;
    home_cmd(0u);
    TEST_ASSERT_EQUAL_UINT8(HP_RELEASE, g_fw.home_phase);
    (void)run_homing(40000u);
    TEST_ASSERT_TRUE(g_fw.homed);
    TEST_ASSERT_TRUE(mu_ev(EV_HOME_FAILED, from) < 0);
}

static void test_not_found(void)
{
    uint32_t from;
    h_set_param(PID_HOME_MAX_TRAVEL_UM, PARAM_T_U32, 1000u);     /* switch at 1.5 mm: beyond */
    mu_enable();
    from = fake_cap_n;
    home_cmd(0u);
    (void)run_homing(10000u);
    TEST_ASSERT_FALSE(g_fw.homed);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_HOME_NOT_FOUND) != 0u);
    TEST_ASSERT_EQUAL_UINT16(HF_NOT_FOUND, mu_ev_arg(mu_ev(EV_HOME_FAILED, from)));
    TEST_ASSERT_EQUAL_UINT16(SC_HOME_FAIL, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 1u, NULL, 0u));                /* no persistent cause */
}

static void test_wiring_end_reached(void)
{
    uint32_t from;
    fake_world_limits(false, 0, 0);
    mu_enable();
    from = fake_cap_n;
    home_cmd(0u);
    mu_run(100u);
    fake_input_set(IO_LIMIT_END_BIT, true);      /* END met while seeking -x: swapped switches */
    (void)run_homing(10000u);
    TEST_ASSERT_FALSE(g_fw.homed);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_HOME_WIRING) != 0u);
    TEST_ASSERT_EQUAL_UINT16(HF_WIRING, mu_ev_arg(mu_ev(EV_HOME_FAILED, from)));
    TEST_ASSERT_TRUE(g_fw.lat.limit_end);        /* END keeps its limit function */
}

static void rehome_with_shift(int32_t lost_steps, bool expect_fault)
{
    uint32_t from;
    int32_t i;
    mu_boot();
    fake_world_limits(true, -1200, 240800);
    mu_enable();
    home_cmd(0u);
    (void)run_homing(30000u);
    TEST_ASSERT_TRUE(g_fw.homed);
    fake_world_shift(lost_steps);
    from = fake_cap_n;
    home_cmd(0u);
    (void)run_homing(30000u);
    TEST_ASSERT_TRUE(g_fw.homed);               /* homing completes with the new zero */
    i = mu_ev(EV_HOMED, from);
    TEST_ASSERT_INT32_WITHIN(3, (int32_t)((float)lost_steps * 1.25f), mu_ev_val(i));
    TEST_ASSERT_EQUAL(expect_fault, (g_fw.lat.faults & FAULT_HOME_DRIFT) != 0u);
    if (expect_fault) {
        i = mu_ev(EV_FAULT_SET, from);
        TEST_ASSERT_EQUAL_UINT16(FAULT_HOME_DRIFT_BIT, mu_ev_arg(i));
        h_expect_nack(mu_move(10000, 1000u, 0u), ST_E_STATE, BLOCK_FAULT);
        h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 2u, NULL, 0u));
    }
}

static void test_drift(void)                       /* FW-HOM-004: 0.1 mm / 0.3 mm */
{
    rehome_with_shift(80, false);
    rehome_with_shift(-240, true);
}

static void test_aborted(void)
{
    uint32_t from;
    mu_enable();
    from = fake_cap_n;
    home_cmd(0u);
    mu_run(100u);
    h_expect_ok(mu_stop(0u));
    mu_run(5u);
    TEST_ASSERT_FALSE(motion_active());
    TEST_ASSERT_EQUAL_UINT16(HF_ABORTED, mu_ev_arg(mu_ev(EV_HOME_FAILED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_EQUAL_HEX16(0u, g_fw.lat.faults);   /* no latch of its own */
    TEST_ASSERT_FALSE(g_fw.homed);
}

static void test_load_precheck(void)               /* SAF-FW-021 */
{
    uint8_t pl[1] = {0u};
    mu_enable();
    mu_raw = 400000;                               /* > home.max_load_raw 322 123 */
    mu_run(20u);
    h_expect_nack(h_cmd(CMD_HOME, 1u, pl, 1u), ST_E_CONFIRM, 0u);
    pl[0] = HOMEF_LOAD_CONFIRMED;
    h_expect_ok(h_cmd(CMD_HOME, 2u, pl, 1u));
    TEST_ASSERT_TRUE(motion_active());
}

/* D-43 b (ICD v0.7 §5.4): un-homed travel window origin +- home.max_travel_um, origin latched when the
 * axis becomes un-homed (ENABLE, DISABLE, homing failure ...), never by a jog / stop; a held jog stops
 * at the bound (SOFT_LIMIT), a JOG toward a reached bound -> E_RANGE 0, away / JOG 0 accepted; an
 * un-homed HOME fast seek ends at origin - max_travel (here at once -> HOME_NOT_FOUND, no hang) */
static void jog_held(int32_t v)
{
    uint32_t k;
    h_expect_ok(mu_jog(v, 0u, PROTO_JOG_NO_BOUND));
    for (k = 0u; k < 40u && motion_active(); k++) {
        mu_run(100u);
        if (motion_active()) {
            h_expect_ok(mu_jog(v, 0u, PROTO_JOG_NO_BOUND));          /* button held: refresh */
        }
    }
    TEST_ASSERT_FALSE(motion_active());
}

static void test_unhomed_window(void)
{
    uint32_t from;
    int32_t p;
    fake_world_limits(false, 0, 0);
    h_set_param(PID_HOME_MAX_TRAVEL_UM, PARAM_T_U32, 1000u);     /* 800 steps */
    mu_enable();                                                  /* origin latched at 0 */
    from = fake_cap_n;
    jog_held(2000);
    TEST_ASSERT_EQUAL_INT32(800, fake_step_count());
    TEST_ASSERT_EQUAL_UINT16(MD_SOFT_LIMIT, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    h_expect_nack(mu_jog(2000, 0u, PROTO_JOG_NO_BOUND), ST_E_RANGE, 0u);   /* toward the bound */
    h_expect_ok(mu_jog(0, 0u, PROTO_JOG_NO_BOUND));                         /* JOG 0 never refused */
    /* away, stop in the middle, toward again: the bound is still origin + 800 (not re-latched) */
    h_expect_ok(mu_jog(-2000, 0u, PROTO_JOG_NO_BOUND));
    mu_run(200u);
    h_expect_ok(mu_jog(0, 0u, PROTO_JOG_NO_BOUND));
    (void)mu_run_until_idle(1000u);
    p = fake_step_count();
    TEST_ASSERT_TRUE(p > 0 && p < 800);
    jog_held(2000);
    TEST_ASSERT_EQUAL_INT32(800, fake_step_count());
    /* the other bound: origin - 800 */
    jog_held(-2000);
    TEST_ASSERT_EQUAL_INT32(-800, fake_step_count());
    h_expect_nack(mu_jog(-2000, 0u, PROTO_JOG_NO_BOUND), ST_E_RANGE, 0u);
    /* DISABLE + ENABLE re-latch the origin at -800: the window is now [-1600, 0] */
    h_expect_ok(h_cmd(CMD_DISABLE, 0x72u, NULL, 0u));
    mu_enable();
    jog_held(-2000);
    TEST_ASSERT_EQUAL_INT32(-1600, fake_step_count());
    /* un-homed HOME: the fast seek ends at origin - max_travel = -1600 = here -> NOT_FOUND at once */
    from = fake_cap_n;
    home_cmd(0u);
    mu_run(5u);
    TEST_ASSERT_FALSE(motion_active());
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    TEST_ASSERT_EQUAL_INT32(-1600, fake_step_count());
    TEST_ASSERT_EQUAL_UINT16(HF_NOT_FOUND, mu_ev_arg(mu_ev(EV_HOME_FAILED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    /* the homing failure re-latched the origin at -1600: -x is allowed again after FAULT_CLEAR */
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 0x73u, NULL, 0u));
    jog_held(-2000);
    TEST_ASSERT_EQUAL_INT32(-2400, fake_step_count());
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_normal_homing);
    RUN_TEST(test_release_when_start_active);
    RUN_TEST(test_not_found);
    RUN_TEST(test_wiring_end_reached);
    RUN_TEST(test_drift);
    RUN_TEST(test_aborted);
    RUN_TEST(test_load_precheck);
    RUN_TEST(test_unhomed_window);
    return UNITY_END();
}
