/* Motion executor on the fake step timer (twin semantics): ENABLE settle, MOVE_ABS exact count and
 * duration, JOG (dead-man, refresh, bound, JOG 0, reversal), STOP 0 / 1, HALT, PAUSE / RESUME with
 * real motion, controlled-stop paths (ISR / stretch / clean halt), sniffed-stop hold with a MOVE_ABS +
 * HALT burst, step fault, link watchdog, MOVE_UNTIL_LOAD not in build, DIR polarity encoding.
 * Verifies: FW-MOT-001 (DIR before the first edge), FW-MOT-002, FW-MOT-003, FW-MOT-004, FW-MOT-005,
 *           FW-MOT-007, FW-MOT-008, SAF-FW-001, SAF-FW-002, SAF-FW-003, SAF-FW-004, SAF-FW-015,
 *           SAF-FW-016, SAF-FW-023, DEF-P1-04 (hold)
 */
#include <unity.h>

#include "hal_step.h"
#include "motion_util.h"
#include "stepgen.h"

void setUp(void) { mu_boot(); }
void tearDown(void) {}

static void test_enable_settle(void)              /* FW-MOT-008 */
{
    const fake_frame_t *r = h_cmd(CMD_ENABLE, 1u, NULL, 0u);
    uint32_t from = fake_cap_n;
    h_expect_ok(r);
    TEST_ASSERT_EQUAL_UINT16(500u, le_get16(&r->payload[1]));
    TEST_ASSERT_TRUE(fake_ena_enabled);
    TEST_ASSERT_EQUAL_UINT8(MS_ENABLING, g_fw.motion_state);
    h_expect_nack(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND), ST_E_BUSY, BUSY_ENABLING);
    mu_run(497);
    TEST_ASSERT_EQUAL_UINT8(MS_ENABLING, g_fw.motion_state);
    mu_run(4);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    TEST_ASSERT_TRUE(mu_ev(EV_DRIVER_ENABLED, from) >= 0);
    TEST_ASSERT_EQUAL_UINT32(0u, fake_rise_n);                    /* no PUL edge during the settle */
    r = h_cmd(CMD_ENABLE, 2u, NULL, 0u);                          /* already enabled: no-op, 0 */
    TEST_ASSERT_EQUAL_UINT16(0u, le_get16(&r->payload[1]));
}

static void test_move_abs_exact(void)              /* FW-MOT-004: 10 mm at 5 mm/s */
{
    uint32_t from, t0, t;
    int32_t i;
    ramp_plan_t p;
    mu_enable();
    g_fw.homed = true;
    from = fake_cap_n;
    t0 = fake_now_us();
    h_expect_ok(mu_move(10000, 5000u, 0u));
    TEST_ASSERT_EQUAL_UINT8(MS_MOVE_ABS, g_fw.motion_state);
    h_expect_nack(mu_move(20000, 5000u, 0u), ST_E_BUSY, BUSY_MOTION);   /* busy, not queued */
    t = mu_run_until_idle(5000u);
    TEST_ASSERT_TRUE(t < 5000u);
    TEST_ASSERT_EQUAL_INT32(8000, fake_step_count());
    TEST_ASSERT_EQUAL_UINT32(8000u, fake_rise_n);
    i = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_TRUE(i >= 0);
    TEST_ASSERT_EQUAL_UINT16(MD_TARGET, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_INT32(10000, mu_ev_val(i));
    TEST_ASSERT_EQUAL_INT32(8000, mu_ev_val2(i));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    p = ramp_plan(8000u, 4000.0, 80000.0, 80000.0);              /* 800 steps/mm, 100 mm/s^2 */
    {
        double dur_ms = (double)(fake_rise_tk[7999] - fake_rise_tk[0]) / 90000.0;
        TEST_ASSERT_TRUE(dur_ms > p.t_s * 1000.0 - 10.0 && dur_ms < p.t_s * 1000.0 + 1.0);
    }
    (void)t0;
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    h_expect_ok(mu_move(10000, 5000u, 0u));                       /* at the target: done at once */
    TEST_ASSERT_EQUAL_UINT32(8000u, fake_rise_n);
}

static void test_dir_polarity_encoding(void)       /* motion.dir_invert -> +-2 (SR-M2-01) */
{
    mu_enable();
    g_fw.homed = true;
    h_set_param(PID_MOTION_DIR_INVERT, PARAM_T_BOOL, 1u);
    h_expect_ok(mu_move(1000, 5000u, 0u));
    TEST_ASSERT_EQUAL_INT(2, fake_dir_arg);
    (void)mu_run_until_idle(2000u);
    TEST_ASSERT_EQUAL_INT32(800, fake_step_count());              /* counted logically */
}

static void test_jog_deadman_and_refresh(void)     /* SAF-FW-016 */
{
    uint32_t from, k, n0;
    int32_t i;
    uint8_t pl[1] = {1u};
    mu_enable();
    h_expect_ok(h_cmd(CMD_SET_VALID, 3u, pl, 1u));
    from = fake_cap_n;
    h_expect_ok(mu_jog(2000, 0u, PROTO_JOG_NO_BOUND));          /* un-homed: <= v_unhomed */
    for (k = 0u; k < 10u; k++) {
        mu_run(200u);
        TEST_ASSERT_TRUE(motion_active());
        h_expect_ok(mu_jog(2000, 0u, PROTO_JOG_NO_BOUND));
    }
    n0 = fake_rise_n;
    mu_run(250u);                                                 /* no refresh: dead-man */
    TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    (void)mu_run_until_idle(2000u);
    i = mu_ev(EV_STOPPED, from);
    TEST_ASSERT_TRUE(i >= 0);
    TEST_ASSERT_EQUAL_UINT16(SC_JOG_DEADMAN, mu_ev_arg(i));
    TEST_ASSERT_TRUE(mu_ev(EV_VALID_CLEARED, from) < 0);         /* VALID unchanged */
    TEST_ASSERT_EQUAL_HEX8(DF_VALID, h_status().b[8] & DF_VALID);
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_TRUE(mu_intervals_non_decreasing(n0 + 2u));
}

static void test_jog_zero_bound_and_unhomed_limit(void)   /* FW-MOT-005 */
{
    uint32_t from;
    mu_enable();
    from = fake_cap_n;
    h_expect_nack(mu_jog(1000, 0u, 50000), ST_E_STATE, BLOCK_NOT_HOMED);   /* bound needs HOMED */
    h_expect_nack(mu_jog(5000, 0u, PROTO_JOG_NO_BOUND), ST_E_RANGE, 0u);   /* > v_unhomed */
    h_expect_ok(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND));
    mu_run(100u);
    h_expect_ok(mu_jog(0, 0u, PROTO_JOG_NO_BOUND));               /* JOG 0: controlled, a_stop */
    (void)mu_run_until_idle(500u);
    TEST_ASSERT_EQUAL_UINT16(MD_JOG_ZERO, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_TRUE(mu_ev(EV_STOPPED, from) < 0);                /* not a stop source */
    h_expect_ok(mu_jog(0, 0u, PROTO_JOG_NO_BOUND));               /* not jogging: OK no-op */
    /* homed: bound reached exactly -> MD_BOUND */
    g_fw.homed = true;
    from = fake_cap_n;
    h_expect_ok(mu_jog(5000, 0u, 3000));
    {
        uint32_t k;
        for (k = 0u; k < 20u && motion_active(); k++) {
            mu_run(100u);
            if (motion_active()) {
                h_expect_ok(mu_jog(5000, 0u, 3000));
            }
        }
    }
    TEST_ASSERT_EQUAL_INT32(2400, fake_step_count());
    TEST_ASSERT_EQUAL_UINT16(MD_BOUND, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
}

static void test_jog_reversal(void)
{
    uint32_t from, k;
    h_set_param(PID_LIMITS_SOFT_MIN_UM, PARAM_T_I32, (uint32_t)-10000);
    mu_enable();
    g_fw.homed = true;
    from = fake_cap_n;
    h_expect_ok(mu_jog(2000, 0u, PROTO_JOG_NO_BOUND));
    mu_run(200u);
    h_expect_ok(mu_jog(-2000, 0u, PROTO_JOG_NO_BOUND));          /* decel to 0, then -x */
    for (k = 0u; k < 8u; k++) {
        mu_run(100u);
        h_expect_ok(mu_jog(-2000, 0u, PROTO_JOG_NO_BOUND));
    }
    TEST_ASSERT_EQUAL_INT(-1, fake_dir_arg);
    TEST_ASSERT_TRUE(motion_active());
    TEST_ASSERT_TRUE(mu_ev(EV_MOVE_DONE, from) < 0);              /* one motion: no MOVE_DONE yet */
    h_expect_ok(mu_stop(0u));
    (void)mu_run_until_idle(100u);
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
}

static void test_stop_immediate_not_resumed(void)  /* SAF-FW-001/002 */
{
    uint32_t from, n;
    uint8_t pl[1] = {1u};
    int32_t i;
    mu_enable();
    g_fw.homed = true;
    h_expect_ok(h_cmd(CMD_SET_VALID, 3u, pl, 1u));
    h_expect_ok(mu_move(100000, 20000u, 0u));
    mu_run(300u);
    from = fake_cap_n;
    h_expect_ok(mu_stop(0u));                     /* executed before the ACK */
    fake_advance_tk(90u * 20u);                   /* <= PW: a running pulse completes */
    n = fake_rise_n;
    TEST_ASSERT_FALSE(hal_step_running());
    mu_run(1000u);
    TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);     /* never resumed */
    i = mu_ev(EV_STOPPED, from);
    TEST_ASSERT_EQUAL_UINT16(SC_PC_STOP, mu_ev_arg(i));
    TEST_ASSERT_TRUE(mu_ev(EV_VALID_CLEARED, from) > i);
    TEST_ASSERT_TRUE(mu_ev(EV_MOVE_DONE, from) > i);
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_TRUE(fake_ena_enabled);           /* ENA unchanged (holding) */
    TEST_ASSERT_EQUAL_INT32(fake_step_count(), (int32_t)n);
    TEST_ASSERT_FALSE(g_fw.pos_uncertain);        /* CLEAN */
}

static uint32_t first_edge_after(uint64_t tk)
{
    uint32_t k;
    for (k = 0u; k < fake_rise_n && k < FAKE_RISE_LOG; k++) {
        if (fake_rise_tk[k] > tk) {
            return k;
        }
    }
    return fake_rise_n;
}

static void ctrl_stop_case(uint32_t v_um_s, uint32_t a_stop, uint8_t want_path)
{
    uint32_t from, k0;
    uint64_t t_trig;
    uint32_t calls0;
    mu_boot();
    h_set_param(PID_MOTION_A_STOP_UM_S2, PARAM_T_U32, a_stop);
    mu_enable();
    g_fw.homed = true;
    h_expect_ok(mu_move(200000, v_um_s, 0u));
    mu_run(1500u);                                 /* cruise */
    TEST_ASSERT_EQUAL_UINT8(MS_MOVE_ABS, g_fw.motion_state);
    from = fake_cap_n;
    calls0 = fake_set_now_calls;
    t_trig = (uint64_t)fake_now_us() * 90u;
    k0 = first_edge_after(t_trig);
    h_expect_ok(mu_stop(1u));
    if (want_path == STOPPATH_HALT) {
        TEST_ASSERT_TRUE(g_fw.motion_state != MS_STOPPING);   /* clean halt: no STOPPING */
    } else {
        TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    }
    TEST_ASSERT_EQUAL(want_path == STOPPATH_STRETCH, fake_set_now_calls > calls0);
    (void)mu_run_until_idle(10000u);
    TEST_ASSERT_EQUAL_UINT16(SC_PC_STOP_CONTROLLED, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_TRUE(mu_intervals_non_decreasing(k0));
    TEST_ASSERT_FALSE(g_fw.pos_uncertain);
    if (want_path == STOPPATH_HALT) {
        TEST_ASSERT_TRUE(fake_rise_n <= k0 + 1u);  /* at most the pulse in flight */
    }
}

static void test_controlled_stop_paths(void)       /* SAF-FW-003, D-29 d / D-30, OI-ICD-07 */
{
    ctrl_stop_case(10000u, 1000000u, STOPPATH_ISR);     /* P = 11.25 us */
    ctrl_stop_case(1000u, 10000u, STOPPATH_STRETCH);    /* P = 1.125 ms (1..2 ms) */
    ctrl_stop_case(500u, 1000000u, STOPPATH_HALT);      /* P = 2.25 ms, d = 0.125 step */
    ctrl_stop_case(500u, 10000u, STOPPATH_STRETCH);     /* P = 2.25 ms but d = 12.5 steps */
}

static void test_move_halt_burst_zero_pulses(void) /* DEF-P1-04 sniffed-stop hold */
{
    uint8_t mv[12];
    uint32_t from;
    h_set_param(PID_MOTION_A_MAX_UM_S2, PARAM_T_U32, 10000000u);
    mu_enable();
    g_fw.homed = true;
    from = fake_cap_n;
    le_put32(mv, 100000u);
    le_put32(&mv[4], 10000u);
    le_put32(&mv[8], 0u);
    fake_cmd(CMD_MOVE_ABS, 1u, mv, 12u);
    fake_cmd(CMD_HALT, 2u, NULL, 0u);
    fake_set_time_us(fake_now_us() + 1000u);
    core_tick_1ms();                              /* sniffer sees the HALT before the dispatch */
    app_loop();
    mu_run(50u);
    TEST_ASSERT_EQUAL_UINT32(0u, fake_rise_n);
    TEST_ASSERT_EQUAL_UINT8(ST_OK, fake_cap[fake_cap_find(CMD_MOVE_ABS | PROTO_RESP_BIT, 1, from)].payload[0]);
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_EQUAL_UINT16(SC_PC_HALT, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_TRUE(g_fw.lat.halt);
}

static void test_sniffed_stop_while_moving(void)   /* SAF-FW-002: STOP last byte -> <= 2 ms */
{
    uint32_t n;
    uint8_t pl[1] = {0u};
    mu_enable();
    g_fw.homed = true;
    h_expect_ok(mu_move(100000, 20000u, 0u));
    mu_run(300u);
    fake_cmd(CMD_STOP, 9u, pl, 1u);               /* frame complete now; no main-loop pass yet */
    fake_run_ms(1u);                               /* tick: sniffer -> CLEAN halt */
    n = fake_rise_n;
    fake_advance_tk(90u * 1000u);
    TEST_ASSERT_TRUE(fake_rise_n <= n + 1u);
    TEST_ASSERT_FALSE(hal_step_running());
}

static void test_pause_resume_with_motion(void)    /* SAF-FW-023, D-30, D-31 */
{
    uint32_t from, n;
    mu_enable();
    g_fw.homed = true;
    h_expect_ok(mu_move(100000, 10000u, 0u));
    mu_run(300u);
    from = fake_cap_n;
    h_expect_ok(h_cmd(CMD_PAUSE, 5u, NULL, 0u));
    TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    (void)mu_run_until_idle(2000u);
    TEST_ASSERT_EQUAL_UINT16(SC_PC_PAUSE, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_TRUE(mu_ev(EV_PAUSED, from) >= 0);
    h_expect_nack(mu_move(1000, 10000u, 0u), ST_E_STATE, BLOCK_PAUSED);
    n = fake_rise_n;
    h_expect_ok(h_cmd(CMD_RESUME, 6u, NULL, 0u));
    mu_run(100u);
    TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);     /* RESUME never starts motion */
    TEST_ASSERT_FALSE(g_fw.lat.paused);
    h_expect_ok(mu_move(1000, 10000u, 0u));       /* the PC re-issues the target */
    (void)mu_run_until_idle(20000u);
    TEST_ASSERT_EQUAL_INT32(800, fake_step_count());
}

static void test_step_fault(void)                  /* SAF-FW-004 (TC-SAF-FW-004-03) */
{
    uint32_t from;
    int32_t i;
    mu_enable();
    g_fw.homed = true;
    h_expect_ok(mu_move(100000, 30000u, 0u));
    mu_run(300u);
    from = fake_cap_n;
    fake_step_skip_isr = true;
    (void)mu_run_until_idle(100u);
    i = mu_ev(EV_FAULT_SET, from);
    TEST_ASSERT_TRUE(i >= 0);
    TEST_ASSERT_EQUAL_UINT16(FAULT_STEP_FAULT_BIT, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_UINT16(SC_STEP_FAULT, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_FALSE(g_fw.homed);
    TEST_ASSERT_TRUE(g_fw.pos_uncertain);
    TEST_ASSERT_EQUAL_HEX16(DS_POS_UNCERTAIN, le_get16(&h_status().b[10]) & DS_POS_UNCERTAIN);
    h_expect_nack(mu_move(1000, 10000u, 0u), ST_E_STATE, BLOCK_FAULT | BLOCK_NOT_HOMED);
}

static void test_link_watchdog(void)               /* SAF-FW-015 */
{
    uint32_t from;
    mu_enable();
    g_fw.homed = true;
    h_set_param(PID_SAFETY_LINK_TIMEOUT_MS, PARAM_T_U16, 1000u);
    h_expect_ok(mu_move(200000, 10000u, 0u));
    from = fake_cap_n;
    mu_run(998u);
    TEST_ASSERT_EQUAL_UINT8(MS_MOVE_ABS, g_fw.motion_state);
    mu_run(3u);
    TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    TEST_ASSERT_TRUE(mu_ev(EV_LINK_WDG, from) >= 0);
    TEST_ASSERT_EQUAL_UINT16(SC_LINK_WDG, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_HEX16(DS_LINK_WDG, le_get16(&h_status().b[10]) & DS_LINK_WDG);   /* refreshes */
    mu_run(2u);
    TEST_ASSERT_TRUE(mu_ev(EV_LINK_RESTORED, from) >= 0);
    (void)mu_run_until_idle(2000u);
    TEST_ASSERT_FALSE(motion_active());           /* nothing restarts */
}

static void test_move_until_load_not_in_build(void)  /* M4 */
{
    uint8_t pl[17] = {0};
    mu_enable();
    g_fw.homed = true;
    le_put32(pl, 50000u);
    le_put32(&pl[4], 1000u);
    h_expect_nack(h_cmd(CMD_MOVE_UNTIL_LOAD, 7u, pl, 17u), ST_E_INTERNAL, INTERNAL_NOT_IN_BUILD);
    TEST_ASSERT_FALSE(motion_active());
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_enable_settle);
    RUN_TEST(test_move_abs_exact);
    RUN_TEST(test_dir_polarity_encoding);
    RUN_TEST(test_jog_deadman_and_refresh);
    RUN_TEST(test_jog_zero_bound_and_unhomed_limit);
    RUN_TEST(test_jog_reversal);
    RUN_TEST(test_stop_immediate_not_resumed);
    RUN_TEST(test_controlled_stop_paths);
    RUN_TEST(test_move_halt_burst_zero_pulses);
    RUN_TEST(test_sniffed_stop_while_moving);
    RUN_TEST(test_pause_resume_with_motion);
    RUN_TEST(test_step_fault);
    RUN_TEST(test_link_watchdog);
    RUN_TEST(test_move_until_load_not_in_build);
    return UNITY_END();
}
