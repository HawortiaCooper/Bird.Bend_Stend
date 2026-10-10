/* Validator E - code review FWR-03 (02_FW/docs/FW_code_review.md): time-of-check / time-of-use gap
 * in core/cmd.c @e600169. cmd_execute() fills the context (fw_cmd_ctx) and runs the pure check
 * (cmd_check) WITHOUT a critical section; only the execution of a motion command is under CRIT_TICK
 * (lines 80-87 vs 243-254). The 1 kHz control tick (level 4) can pre-empt the thread in between and
 * fold an input record into a latch (LIMIT_x, PAUSED, ESTOP, a FAULT). The command is then executed
 * against the OLD verdict.
 *
 * Reproduction on the host: the unchanged core/cmd.c is compiled into this suite with its entry
 * points renamed, and its single hal_time_us() call - which lies exactly in that window (line 87,
 * after cmd_check(), before CRIT_BEGIN(HAL_CRIT_TICK)) - is routed to a hook that runs one
 * core_tick_1ms(), as the TIM5 interrupt can do on the target. Everything else (core, fake seams of
 * test/common_impl, harness.h) is used unchanged; the fake step HAL counts PUL rising edges.
 *
 * Expected (SAF-FW-013: "while a limit is active or latched, only motion away from that switch is
 * accepted"; D-30 / SAF-FW-023: "while PAUSED every new motion start shall be refused"): no PUL edge
 * toward the latched switch / while PAUSED, whichever way the race falls.
 * Verifies: SAF-FW-013, SAF-FW-023 (D-30) - review finding FWR-03
 * TC: TC-SAF-FW-013-02, TC-SAF-FW-023-04, TC-SAF-FW-005-02 (FWR-16, re-check v0.8)
 */
#include "unity.h"

#include "harness.h"

#include "hal_time.h"
#include "frame.h"

void core_tick_1ms(void);

static bool     s_hook_armed;
static uint32_t s_hook_fired;
static int      s_hook_kind;                 /* 0: the 1 kHz tick, 1: an E-stop edge (levels 0/1) */

static uint32_t val_hook_time_us(void)
{
    if (s_hook_armed) {
        s_hook_armed = false;
        s_hook_fired++;
        if (s_hook_kind == 1) {
            fake_input_set(0u, true);        /* E-stop opens: fixed reaction + record, not yet folded */
        } else {
            core_tick_1ms();                 /* TIM5 CC1 pre-empts the thread here (level 4) */
        }
    }
    return hal_time_us();
}

/* the unchanged command handlers, entry points renamed (the core's own copy stays linked) */
#define hal_time_us        val_hook_time_us
#define cmd_execute        val_cmd_execute
#define cmd_reboot_service val_cmd_reboot_service
#define fw_cmd_ctx         val_fw_cmd_ctx
#include "../../src/core/cmd.c"
#undef hal_time_us
#undef cmd_execute
#undef cmd_reboot_service
#undef fw_cmd_ctx

#define SAMPLE_US 12000u                     /* 83 SPS (fake sample grid is 1 ms) */

void setUp(void)
{
    s_hook_armed = false;
    s_hook_fired = 0u;
    s_hook_kind = 0;
}
void tearDown(void) {}

/* boot, ENABLE, settle with fresh unloaded samples -> MS_IDLE, not homed */
static void boot_idle(void)
{
    h_boot(true);
    fake_run_ms_samples(50u, SAMPLE_US, 0);
    h_expect_ok(h_cmd(CMD_ENABLE, 0x10u, NULL, 0u));
    fake_run_ms_samples(700u, SAMPLE_US, 0);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
}

static void jog_pl(uint8_t pl[12], int32_t v_um_s)
{
    le_put32(&pl[0], (uint32_t)v_um_s);
    le_put32(&pl[4], 0u);                    /* a = a_max */
    le_put32(&pl[8], (uint32_t)PROTO_JOG_NO_BOUND);
}

/* control: the tick folds the END edge BEFORE the command -> JOG toward END refused, no pulse */
void test_control_end_folded_before_check(void)
{
    uint8_t pl[12];
    const fake_frame_t *r;
    boot_idle();
    fake_input_set(2u, true);                /* END active edge: HAL CLEAN (idle no-op) + record */
    fake_run_ms_samples(1u, SAMPLE_US, 0);   /* tick folds it: LIMIT_END latched */
    TEST_ASSERT_TRUE(g_fw.lat.limit_end);
    jog_pl(pl, 1000);
    r = h_cmd(CMD_JOG, 0x20u, pl, 12u);
    TEST_ASSERT_EQUAL_UINT8(ST_E_STATE, h_status_of(r));
    TEST_ASSERT_TRUE((h_detail_of(r) & BLOCK_LIMIT) != 0u);
}

/* FWR-03: the same END edge, folded by a tick that pre-empts cmd_execute() between cmd_check() and
 * CRIT_BEGIN(HAL_CRIT_TICK): the JOG toward the latched END switch must not produce pulses */
void test_fwr03_end_folded_inside_check_window(void)
{
    uint8_t pl[12];
    uint32_t rise0;
    boot_idle();
    fake_input_set(2u, true);                /* END active edge, not yet folded (< 1 ms ago) */
    jog_pl(pl, 1000);
    rise0 = fake_rise_n;
    s_hook_armed = true;
    val_cmd_execute(CMD_JOG, 0x21u, pl, 12u);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1u, s_hook_fired, "window hook did not fire");
    TEST_ASSERT_TRUE_MESSAGE(g_fw.lat.limit_end, "LIMIT_END not latched by the pre-empting tick");
    fake_run_ms_samples(50u, SAMPLE_US, 0);  /* the line is masked, the latch blocks the backup check */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(rise0, fake_rise_n,
                                     "PUL edges toward the latched END switch (FWR-03, SAF-FW-013)");
}

/* FWR-03 (PAUSED): a PAUSE button press folded in the same window - the JOG must not start (D-30) */
void test_fwr03_pause_folded_inside_check_window(void)
{
    uint8_t pl[12];
    uint32_t rise0;
    boot_idle();
    fake_input_set(4u, false);               /* PAUSE (NO, CLOSED_ACTIVE default) pressed */
    jog_pl(pl, -1000);
    rise0 = fake_rise_n;
    s_hook_armed = true;
    val_cmd_execute(CMD_JOG, 0x22u, pl, 12u);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1u, s_hook_fired, "window hook did not fire");
    TEST_ASSERT_TRUE_MESSAGE(g_fw.lat.paused, "PAUSED not latched by the pre-empting tick");
    fake_run_ms_samples(50u, SAMPLE_US, 0);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(rise0, fake_rise_n, "PUL edges while PAUSED (FWR-03, D-30)");
}

/* re-check v0.8, FWR-16: an E-stop edge between the check of ENABLE and its execution (no tick in
 * between, so the v0.8 re-validation does not run) - the fixed reaction disabled ENA, the ENABLE must
 * not drive it back to "enabled" (SAF-FW-005 b: ENA disabled within 1 ms and kept there) */
void test_fwr16_enable_after_estop_edge_in_check_window(void)
{
    boot_idle();
    h_expect_ok(h_cmd(CMD_DISABLE, 0x30u, NULL, 0u));
    fake_run_ms_samples(5u, SAMPLE_US, 0);
    TEST_ASSERT_FALSE(fake_ena_enabled);
    s_hook_kind = 1;
    s_hook_armed = true;
    val_cmd_execute(CMD_ENABLE, 0x31u, NULL, 0u);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1u, s_hook_fired, "window hook did not fire");
    TEST_ASSERT_FALSE_MESSAGE(fake_ena_enabled,
                              "ENA re-enabled by ENABLE after the E-stop reaction disabled it (FWR-16)");
    fake_run_ms_samples(2u, SAMPLE_US, 0);                 /* the tick's level check corrects it */
    TEST_ASSERT_FALSE(fake_ena_enabled);
    TEST_ASSERT_TRUE(g_fw.lat.estop);
}

/* control (v0.8 FWR-01 path): the same E-stop edge before a JOG start - the start must emit no edge */
void test_estop_edge_in_check_window_blocks_start(void)
{
    uint8_t pl[12];
    uint32_t rise0;
    boot_idle();
    jog_pl(pl, 1000);
    rise0 = fake_rise_n;
    s_hook_kind = 1;
    s_hook_armed = true;
    val_cmd_execute(CMD_JOG, 0x32u, pl, 12u);
    TEST_ASSERT_EQUAL_UINT32(1u, s_hook_fired);
    fake_run_ms_samples(20u, SAMPLE_US, 0);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(rise0, fake_rise_n, "PUL edges after an E-stop edge in the check window");
    TEST_ASSERT_TRUE(g_fw.lat.estop);
}

/* re-check v0.8, FWR-07: a JOG start parked by the sniffed-stop hold, then reversed while still parked;
 * the sniffed STOP frame is never dispatched (lost to the dispatcher, e.g. an RX overrun): when the hold
 * times out (SNIFF_HOLD_MAX_MS) the parked jog must be discarded - not left as MS_JOG without motion
 * (e600169: stuck until JOG 0 / STOP / the 250 ms dead-man). Ticks only, no main loop: the STOP stays
 * undispatched. TC: TC-FW-MOT-007 (review FWR-07) */
void test_fwr07_parked_jog_reversal_resolved_by_hold_timeout(void)
{
    uint8_t pl[12], st[1] = {0u};
    uint8_t fr[PROTO_FRAME_MAX];
    uint16_t n;
    uint32_t rise0, k;
    boot_idle();
    n = frame_build(CMD_STOP, 0x40u, st, 1u, fr);
    fake_rx(fr, n);
    core_tick_1ms();                                   /* the sniffer sees the STOP: hold pending */
    TEST_ASSERT_FALSE(link_motion_start_allowed());
    rise0 = fake_rise_n;
    jog_pl(pl, 1000);
    val_cmd_execute(CMD_JOG, 0x41u, pl, 12u);          /* parked */
    TEST_ASSERT_EQUAL_UINT8(MS_JOG, g_fw.motion_state);
    jog_pl(pl, -1000);
    val_cmd_execute(CMD_JOG, 0x42u, pl, 12u);          /* reversal while parked */
    for (k = 0u; k < 40u; k++) {                       /* > SNIFF_HOLD_MAX_MS, < jog dead-man */
        fake_advance_tk(1000u * (FAKE_F_TICK / 1000000u));
        core_tick_1ms();
    }
    TEST_ASSERT_TRUE(link_motion_start_allowed());
    TEST_ASSERT_NOT_EQUAL_MESSAGE(MS_JOG, g_fw.motion_state,
                                  "parked + reversed jog left as MS_JOG after the hold timed out (FWR-07)");
    TEST_ASSERT_EQUAL_UINT32(rise0, fake_rise_n);      /* never moved */
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_fwr07_parked_jog_reversal_resolved_by_hold_timeout);
    RUN_TEST(test_fwr16_enable_after_estop_edge_in_check_window);
    RUN_TEST(test_estop_edge_in_check_window_blocks_start);
    RUN_TEST(test_control_end_folded_before_check);
    RUN_TEST(test_fwr03_end_folded_inside_check_window);
    RUN_TEST(test_fwr03_pause_folded_inside_check_window);
    return UNITY_END();
}
