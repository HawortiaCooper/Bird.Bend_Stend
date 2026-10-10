/* Validator E - tick order of the stop-generation capture vs the input-record fold (FW_code_review.md
 * §11 FWR-18, §12 re-check of FW v0.8.2). Drives the REAL core_tick_1ms() (fake seams of
 * test/common_impl; documented exception to FW_test_plan §1.1 rule 1: the question is the core's own
 * tick order; expected values from the SRS / ICD only).
 *
 * 1. Homing: the EXPECTED START edge of FAST_SEEK (HAL CLEAN halt + record) is injected
 *    (a) between two ticks (control: folded by the next tick), and
 *    (b) inside a tick AFTER safety_tick() took the records (fake_peek_hook = the sniffer's
 *        hal_uart_peek() in link_tick(); the record stays pending until the next tick).
 *    Both must complete homing: HOMED, no HOME_FAILED, no fault (FW-HOM-001/002). At v0.8.1 the
 *    (equivalent) window between the early capture and the fold failed with HOME_WIRING (FWR-18).
 * 2. Deferral bound (re-check §12 (a)): a chattering E-stop that opens again after the take in every
 *    tick keeps an edge record pending; the stopped motion must not move (no PUL edge after the first
 *    E-stop edge), and MOVE_DONE must follow within 2 ticks once the chatter stops.
 * 3. Natural segment end inside the tick (re-check §13 (b), FWR-20): step events processed after the record take
 *    (the step ISR pre-empting the tick); jog reversal restart and homing must still work.
 * Verifies: FW-HOM-001, FW-HOM-002, FW-MOT-005, SAF-FW-002, SAF-FW-005 (tick path, FWR-17 / FWR-18 / FWR-20)
 * TC: TC-FW-HOM-001-02, TC-SAF-FW-005-03, TC-FW-HOM-001-03, TC-FW-MOT-005-02
 */
#include <unity.h>

#include "motion_util.h"

#include "hal_step.h"

void setUp(void)
{
    mu_boot();
    fake_world_limits(false, -1200, 240800);           /* world off: the first START edge is injected */
}
void tearDown(void) {}

#define INJ_BETWEEN_TICKS 1
#define INJ_AFTER_TAKE    2

static bool s_done;

static void inject_start(void)
{
    s_done = true;
    fake_world_limits(true, -1200, 240800);           /* START active: HAL CLEAN halt + record */
}

static void run_ms(uint32_t ms, int mode)
{
    uint32_t i;
    for (i = 0u; i < ms; i++) {
        fake_advance_tk(1000u * (uint64_t)(FAKE_F_TICK / 1000000u));
        if (!s_done && g_fw.home_phase == (uint8_t)HP_FAST_SEEK && fake_step_count() <= -1200) {
            if (mode == INJ_BETWEEN_TICKS) {
                inject_start();
            } else {
                fake_peek_hook = inject_start;        /* inside the next tick, after the take */
            }
        }
        core_tick_1ms();
        app_loop();
        if ((fake_now_us() / 1000u) % 12u == 0u) {
            fake_sample(fake_now_us(), 0);
        }
    }
    h_expect_ok(h_cmd(CMD_PING, 0x70u, NULL, 0u));      /* link kept alive (called every 50 ms) */
}

static void homing_case(int mode)
{
    uint8_t pl[1] = {0u};
    uint32_t from, t;
    s_done = false;
    mu_enable();
    from = fake_cap_n;
    h_expect_ok(h_cmd(CMD_HOME, 0x71u, pl, 1u));
    for (t = 0u; t < 60000u && motion_active(); t += 50u) {
        run_ms(50u, mode);
    }
    TEST_ASSERT_TRUE_MESSAGE(s_done, "START edge not injected (fast seek did not reach the switch)");
    TEST_ASSERT_FALSE_MESSAGE(motion_active(), "homing still active after 60 s");
    TEST_ASSERT_TRUE_MESSAGE(mu_ev(EV_HOME_FAILED, from) < 0, "HOME_FAILED");
    TEST_ASSERT_EQUAL_HEX16_MESSAGE(0u, g_fw.lat.faults, "fault latched during homing");
    TEST_ASSERT_TRUE_MESSAGE(g_fw.homed, "not homed");
}

void test_control_expected_edge_between_ticks(void) { homing_case(INJ_BETWEEN_TICKS); }
void test_expected_homing_edge_inside_tick_after_take(void) { homing_case(INJ_AFTER_TAKE); }

/* ---------------------------------------------------------------- deferral bound (§12 (a)) */
static void estop_chatter(void)
{
    fake_input_set(0u, false);                         /* closed ... */
    fake_input_set(0u, true);                          /* ... and open again: a new active edge record */
}

void test_estop_chatter_keeps_deferral_bounded_and_still(void)
{
    uint8_t pl[12];
    uint32_t from, rise_at_estop, k;
    int32_t i;
    mu_enable();
    le_put32(&pl[0], (uint32_t)2000);                  /* un-homed jog +2 mm/s */
    le_put32(&pl[4], 0u);
    le_put32(&pl[8], (uint32_t)PROTO_JOG_NO_BOUND);
    h_expect_ok(h_cmd(CMD_JOG, 0x72u, pl, 12u));
    mu_run(100u);
    TEST_ASSERT_TRUE(fake_rise_n > 0u);
    from = fake_cap_n;
    fake_input_set(0u, true);                          /* E-stop opens between ticks */
    rise_at_estop = fake_rise_n;
    for (k = 0u; k < 30u; k++) {                       /* chatter: re-opens after the take of every tick */
        fake_advance_tk(1000u * (uint64_t)(FAKE_F_TICK / 1000000u));
        fake_peek_hook = estop_chatter;
        core_tick_1ms();
        app_loop();
    }
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(rise_at_estop, fake_rise_n, "PUL edge while the segment end is deferred");
    for (k = 0u; k < 2u && mu_ev(EV_MOVE_DONE, from) < 0; k++) {
        mu_run(1u);                                    /* chatter over (input stays open) */
    }
    i = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_TRUE_MESSAGE(i >= 0, "no MOVE_DONE within 2 ticks after the chatter ended");
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    TEST_ASSERT_FALSE(motion_active());
    TEST_ASSERT_TRUE(g_fw.lat.estop);
    TEST_ASSERT_FALSE(fake_ena_enabled);
    TEST_ASSERT_EQUAL_UINT32(rise_at_estop, fake_rise_n);
}

/* ---------------------------------------------------------------- re-check §13 (b): natural segment end
 * inside the tick. The step ISR (level 2) pre-empts the tick (level 4): the OPM final update of a segment
 * (= the timer stops by itself, the HAL increments the stop generation, step_tim2.c TIM2_IRQHandler) can
 * fall after the tick's generation capture (since v0.8.1 at / in safety_tick()) and before motion_tick()
 * looks at hal_step_running(). Model: every tick, step events are processed INSIDE the tick after the
 * record take (fake_peek_hook = link_tick()), until the timer stops (<= 3 ms); the outer loop advances
 * the time between ticks as usual. A tick-initiated next segment (jog reversal restart, next homing
 * phase) must still start and run. */
static void steps_inside_tick(void)
{
    uint32_t k;
    for (k = 0u; k < 300u && hal_step_running(); k++) {
        fake_advance_tk(10u * (uint64_t)(FAKE_F_TICK / 1000000u));
    }
}

static uint32_t s_last_sample_us;

static void run_ms_inside(uint32_t ms)
{
    uint32_t i;
    for (i = 0u; i < ms; i++) {
        fake_advance_tk(1000u * (uint64_t)(FAKE_F_TICK / 1000000u));
        fake_peek_hook = steps_inside_tick;
        core_tick_1ms();
        app_loop();
        if ((uint32_t)(fake_now_us() - s_last_sample_us) >= 12000u) {   /* ~83 SPS on the model time */
            s_last_sample_us = fake_now_us();
            fake_sample(fake_now_us(), 0);
        }
    }
}

static void jog_pl2(uint8_t pl[12], int32_t v)
{
    le_put32(&pl[0], (uint32_t)v);
    le_put32(&pl[4], 0u);
    le_put32(&pl[8], (uint32_t)PROTO_JOG_NO_BOUND);
}

void test_jog_reversal_restart_after_segment_end_inside_tick(void)
{
    uint8_t pl[12];
    uint32_t from, t;
    int32_t c_rev;
    mu_enable();
    from = fake_cap_n;
    jog_pl2(pl, 2000);
    h_expect_ok(h_cmd(CMD_JOG, 0x73u, pl, 12u));
    for (t = 0u; t < 300u; t += 50u) {
        run_ms_inside(50u);
        h_expect_ok(h_cmd(CMD_JOG, 0x73u, pl, 12u));   /* dead-man refresh */
    }
    jog_pl2(pl, -2000);                                /* reversal: decelerate, standstill, restart */
    for (t = 0u; t < 1500u; t += 50u) {
        h_expect_ok(h_cmd(CMD_JOG, 0x74u, pl, 12u));
        run_ms_inside(50u);
    }
    c_rev = fake_step_count();
    for (t = 0u; t < 500u; t += 50u) {
        h_expect_ok(h_cmd(CMD_JOG, 0x74u, pl, 12u));
        run_ms_inside(50u);
    }
    TEST_ASSERT_EQUAL_HEX16_MESSAGE(0u, g_fw.lat.faults, "fault latched after the reversal (STEP_FAULT?)");
    TEST_ASSERT_TRUE_MESSAGE(mu_ev(EV_FAULT_SET, from) < 0, "FAULT_SET after the reversal");
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(MS_JOG, g_fw.motion_state, "reversed jog not running");
    TEST_ASSERT_TRUE_MESSAGE(fake_step_count() < c_rev, "reversed jog does not move in the new direction");
}

void test_homing_with_segment_ends_inside_tick(void)
{
    uint8_t pl[1] = {0u};
    uint32_t from, t;
    fake_world_limits(true, -1200, 240800);
    mu_enable();
    from = fake_cap_n;
    h_expect_ok(h_cmd(CMD_HOME, 0x75u, pl, 1u));
    for (t = 0u; t < 60000u && motion_active(); t += 50u) {
        run_ms_inside(50u);
        h_expect_ok(h_cmd(CMD_PING, 0x70u, NULL, 0u));
    }
    TEST_ASSERT_FALSE_MESSAGE(motion_active(), "homing still active after 60 s");
    TEST_ASSERT_TRUE_MESSAGE(mu_ev(EV_HOME_FAILED, from) < 0, "HOME_FAILED");
    TEST_ASSERT_EQUAL_HEX16_MESSAGE(0u, g_fw.lat.faults, "fault latched during homing");
    TEST_ASSERT_TRUE_MESSAGE(g_fw.homed, "not homed");
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_jog_reversal_restart_after_segment_end_inside_tick);
    RUN_TEST(test_homing_with_segment_ends_inside_tick);
    RUN_TEST(test_control_expected_edge_between_ticks);
    RUN_TEST(test_expected_homing_edge_inside_tick_after_take);
    RUN_TEST(test_estop_chatter_keeps_deferral_bounded_and_still);
    return UNITY_END();
}
