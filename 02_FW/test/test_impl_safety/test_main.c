/* Inputs and supervision on the fake seams (HAL fixed reactions modelled as in the twin): E-stop
 * (latch, EVENT order, ESTOP_CLEAR closed time), boot with active inputs, limit hit / toward-away /
 * release latch, LIMIT_WIRING, DRV_PWR loss (filter, reaction, events, settle after return), K1 weld,
 * ALM start-block, PAUSE button, FW load limit (trip in the sample ISR, regrow), AFE stale -> AFE_FAULT,
 * idle disable (unloaded / loaded / stale), NOT_SETTLED, HX711 status bits (re-init, OVERRUN), settle.
 * Verifies: SAF-FW-005, SAF-FW-006, SAF-FW-007, SAF-FW-008, SAF-FW-009, SAF-FW-011, SAF-FW-012,
 *           SAF-FW-013, SAF-FW-014, SAF-FW-017, SAF-FW-018, SAF-FW-023, SAF-FW-024, SAF-FW-025,
 *           SAF-FW-026, FW-SW-001...005, FW-AFE-003, FW-CMD-003, FW-MOT-008
 */
#include <unity.h>

#include "hal_step.h"
#include "motion_util.h"

void setUp(void) { mu_boot(); }
void tearDown(void) {}

#define IN_ESTOP  IO_ESTOP_OPEN_BIT
#define IN_START  IO_LIMIT_START_BIT
#define IN_END    IO_LIMIT_END_BIT
#define IN_PAUSE  IO_PAUSE_BTN_BIT
#define IN_ALM    IO_ALM_BIT
#define IN_PEND   IO_PEND_BIT
#define IN_PWR    IO_DRV_PWR_BIT

static void moving_homed(uint32_t v)
{
    uint8_t pl[1] = {1u};
    mu_enable();
    g_fw.homed = true;
    h_expect_ok(h_cmd(CMD_SET_VALID, 3u, pl, 1u));
    h_expect_ok(mu_move(200000, v, 0u));
    mu_run(300u);
}

static void test_estop_during_move(void)           /* SAF-FW-005/006 */
{
    uint32_t from, n;
    int32_t a, b, c, d, e;
    const fake_frame_t *r;
    mu_boot_sense(false);                         /* the end checks DRV_UNPOWERED */
    moving_homed(10000u);
    from = fake_cap_n;
    fake_input_set(IN_ESTOP, true);               /* HAL: TRUNCATE + ENA disabled at the edge */
    n = fake_rise_n;
    TEST_ASSERT_FALSE(hal_step_running());
    TEST_ASSERT_FALSE(fake_ena_enabled);
    mu_run(2u);
    TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);
    TEST_ASSERT_TRUE(g_fw.lat.estop);
    TEST_ASSERT_EQUAL_UINT8(MS_NOT_ENABLED, g_fw.motion_state);
    TEST_ASSERT_FALSE(g_fw.homed);
    a = mu_ev(EV_ESTOP_SET, from);
    b = mu_ev(EV_STOPPED, from);
    c = mu_ev(EV_VALID_CLEARED, from);
    d = mu_ev(EV_DRIVER_DISABLED, from);
    e = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_TRUE(a >= 0 && a < b && b < c && c < d && d < e);
    TEST_ASSERT_EQUAL_UINT16(SC_ESTOP, mu_ev_arg(b));
    TEST_ASSERT_EQUAL_UINT16(DD_ESTOP, mu_ev_arg(d));
    TEST_ASSERT_EQUAL_HEX8(DF_ESTOP, h_status().b[8] & DF_ESTOP);
    h_expect_nack(h_cmd(CMD_ESTOP_CLEAR, 4u, NULL, 0u), ST_E_CAUSE_ACTIVE, PROTO_DETAIL_CAUSE_INPUT);
    fake_input_set(IN_ESTOP, false);
    mu_run(50u);
    r = h_cmd(CMD_ESTOP_CLEAR, 5u, NULL, 0u);
    TEST_ASSERT_EQUAL_UINT8(ST_E_CAUSE_ACTIVE, h_status_of(r));
    TEST_ASSERT_TRUE(h_detail_of(r) >= 49u && h_detail_of(r) <= 51u);
    fake_input_set(IN_ESTOP, true);               /* bounce restarts the count */
    fake_input_set(IN_ESTOP, false);
    mu_run(99u);
    TEST_ASSERT_EQUAL_UINT8(ST_E_CAUSE_ACTIVE, h_status_of(h_cmd(CMD_ESTOP_CLEAR, 6u, NULL, 0u)));
    mu_run(2u);
    h_expect_ok(h_cmd(CMD_ESTOP_CLEAR, 7u, NULL, 0u));
    h_expect_nack(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND), ST_E_STATE, BLOCK_NOT_ENABLED);
    fake_input_set(IN_PWR, true);                 /* K1 not reset yet */
    mu_run(25u);
    h_expect_nack(h_cmd(CMD_ENABLE, 8u, NULL, 0u), ST_E_STATE, BLOCK_DRV_UNPOWERED);
    fake_input_set(IN_PWR, false);
    mu_run(25u);
    mu_enable();
    h_expect_nack(mu_move(10000, 1000u, 0u), ST_E_STATE, BLOCK_NOT_HOMED);
}

static void test_boot_with_inputs_active(void)     /* SAF-FW-007/018, OI-FW-22 */
{
    h_status_t s;
    fake_hal_reset();
    fake_flash_blank();
    fake_input_set(IN_ESTOP, true);
    fake_input_set(IN_START, true);
    fake_input_set(IN_END, true);
    app_init();
    app_loop();
    TEST_ASSERT_FALSE(fake_ena_enabled);          /* E-stop open at boot -> disabled level */
    s = h_status();
    TEST_ASSERT_EQUAL_HEX8(DF_ESTOP | DF_FAULT, s.b[8] & (DF_ESTOP | DF_FAULT));
    TEST_ASSERT_EQUAL_HEX16(FAULT_LIMIT_WIRING, le_get16(&s.b[12]));
    TEST_ASSERT_EQUAL_HEX16(IO_ESTOP_OPEN | IO_LIMIT_START | IO_LIMIT_END | IO_ENA_DISABLED,
                            le_get16(&s.b[14]) & (IO_ESTOP_OPEN | IO_LIMIT_START | IO_LIMIT_END | IO_ENA_DISABLED));
    /* DRV_PWR input off at boot with the default (sensing off, ICD v0.7 / D-41): ignored */
    fake_hal_reset();
    fake_flash_blank();
    fake_input_set(IN_PWR, true);
    app_init();
    TEST_ASSERT_TRUE(fake_ena_enabled);
    TEST_ASSERT_EQUAL_HEX16(DS_DRV_PWR, le_get16(&h_status().b[10]) & DS_DRV_PWR);
    /* DRV_PWR off at boot (sense enabled) -> disabled level too */
    mu_boot_sense(false);
    fake_hal_reset();
    fake_input_set(IN_PWR, true);
    app_init();
    TEST_ASSERT_FALSE(fake_ena_enabled);
    TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&h_status().b[10]) & DS_DRV_PWR);
    /* everything normal: ENA left at the holding level (D-13) */
    fake_hal_reset();
    app_init();
    TEST_ASSERT_TRUE(fake_ena_enabled);
    TEST_ASSERT_EQUAL_UINT32(0u, fake_ena_changes);
}

static void test_limit_hit_and_release(void)       /* SAF-FW-013, FW-SW-001 */
{
    uint32_t from, n, k;
    int32_t i;
    moving_homed(10000u);
    from = fake_cap_n;
    fake_input_set(IN_END, true);                 /* HAL: CLEAN stop at the edge */
    fake_advance_tk(90u * 20u);
    n = fake_rise_n;
    TEST_ASSERT_FALSE(hal_step_running());
    mu_run(2u);
    TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);
    i = mu_ev(EV_LIMIT_SET, from);
    TEST_ASSERT_TRUE(i >= 0);
    TEST_ASSERT_EQUAL_UINT16(LIM_END, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_UINT16(SC_LIMIT_END, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_TRUE(mu_ev(EV_VALID_CLEARED, from) >= 0);
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    h_expect_nack(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND), ST_E_STATE, BLOCK_LIMIT);   /* toward */
    fake_input_set(IN_END, false);
    for (k = 0u; k < 19u; k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE(g_fw.lat.limit_end);          /* latched after 19 ms of release */
    fake_run_ms(2u);
    TEST_ASSERT_FALSE(g_fw.lat.limit_end);         /* cleared after 20 ms (D-33 h) */
    TEST_ASSERT_TRUE(mu_ev(EV_LIMIT_CLEARED, from) >= 0);
    h_expect_ok(mu_jog(-1000, 0u, PROTO_JOG_NO_BOUND));            /* away */
    mu_run(50u);
    TEST_ASSERT_TRUE(motion_active());
}

static void test_both_limits_wiring(void)          /* SAF-FW-014 */
{
    moving_homed(10000u);
    fake_input_set(IN_START, true);
    fake_input_set(IN_END, true);
    mu_run(3u);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_LIMIT_WIRING) != 0u);
    h_expect_nack(h_cmd(CMD_FAULT_CLEAR, 1u, NULL, 0u), ST_E_CAUSE_ACTIVE, FAULT_LIMIT_WIRING);
    fake_input_set(IN_END, false);
    mu_run(3u);
    fake_input_set(IN_START, false);
    mu_run(3u);
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 2u, NULL, 0u));
}

static void test_drv_power_loss_during_jog(void)   /* SAF-FW-024, FW-SW-005 */
{
    uint32_t from, k, t0;
    int32_t a, b, c, d;
    uint8_t pl[1] = {1u};
    mu_boot_sense(false);
    mu_enable();
    h_expect_ok(h_cmd(CMD_SET_VALID, 3u, pl, 1u));
    h_expect_ok(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND));
    mu_run(100u);
    /* 19 ms toggle ignored */
    fake_input_set(IN_PWR, true);
    for (k = 0u; k < 19u; k++) {
        fake_run_ms(1u);
    }
    fake_input_set(IN_PWR, false);
    h_expect_ok(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND));
    TEST_ASSERT_TRUE(motion_active());
    from = fake_cap_n;
    t0 = fake_now_us();
    fake_input_set(IN_PWR, true);
    for (k = 0u; k < 30u && g_fw.motion_state != MS_NOT_ENABLED; k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE(fake_now_us() - t0 <= 23000u);          /* + RC 2 ms <= 25 ms */
    TEST_ASSERT_FALSE(fake_ena_enabled);
    TEST_ASSERT_FALSE(hal_step_running());
    mu_run(2u);
    a = mu_ev(EV_DRIVER_POWER, from);
    b = mu_ev(EV_STOPPED, from);
    c = mu_ev(EV_DRIVER_DISABLED, from);
    d = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_TRUE(a >= 0 && a < b && b < c && c < d);
    TEST_ASSERT_EQUAL_UINT16(0u, mu_ev_arg(a));
    TEST_ASSERT_EQUAL_UINT16(SC_DRV_POWER_LOST, mu_ev_arg(b));
    TEST_ASSERT_EQUAL_UINT16(DD_DRV_POWER_LOST, mu_ev_arg(c));
    TEST_ASSERT_TRUE(mu_ev(EV_VALID_CLEARED, from) >= 0);
    h_expect_nack(h_cmd(CMD_ENABLE, 9u, NULL, 0u), ST_E_STATE, BLOCK_DRV_UNPOWERED);
    /* ENABLE first, then power return: the settle counts from the later of both (FW-MOT-008) */
    fake_input_set(IN_PWR, false);
    mu_run(25u);
    h_expect_ok(h_cmd(CMD_ENABLE, 10u, NULL, 0u));
    mu_run(498u);
    TEST_ASSERT_EQUAL_UINT8(MS_ENABLING, g_fw.motion_state);
    mu_run(3u);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
}

static void test_estop_then_power_only_driver_power_event(void)   /* OI-ICD-06 */
{
    uint32_t from;
    mu_boot_sense(true);
    mu_enable();
    from = fake_cap_n;
    fake_input_set(IN_ESTOP, true);
    mu_run(2u);
    fake_input_set(IN_PWR, true);                 /* (optional) power removal 20 ms later */
    mu_run(30u);
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_DRIVER_DISABLED, from));   /* only the E-stop's */
    TEST_ASSERT_TRUE(mu_ev(EV_DRIVER_POWER, from) >= 0);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_K1_WELDED) == 0u);
}

static void test_k1_welded(void)                   /* SAF-FW-025 */
{
    uint32_t k;
    int32_t i;
    uint32_t from;
    mu_boot_sense(true);                          /* K1 check needs both parameters (SRS OI-18) */
    from = fake_cap_n;
    fake_input_set(IN_ESTOP, true);               /* power stays present */
    for (k = 0u; k < 150u; k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_K1_WELDED) == 0u);
    for (k = 0u; k < 52u; k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_K1_WELDED) != 0u);   /* in (200, 202] ms */
    i = mu_ev(EV_FAULT_SET, from);
    TEST_ASSERT_EQUAL_UINT16(FAULT_K1_WELDED_BIT, mu_ev_arg(i));
    TEST_ASSERT_TRUE(mu_ev_val(i) > 200);
    h_expect_nack(h_cmd(CMD_FAULT_CLEAR, 1u, NULL, 0u), ST_E_CAUSE_ACTIVE, FAULT_K1_WELDED);
    fake_input_set(IN_PWR, true);
    mu_run(25u);
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 2u, NULL, 0u));
}

/* ICD v0.7 / D-41 / SRS OI-18: K1_WELDED only with drv.pwr_sense_enable AND drv.k1_check_enable;
 * D-42: the hardwired E-stop ENA cut forces the opto to "disabled" independent of the MCU output - the
 * FW never reads ENA back, so the forced level causes no fault and a release never moves (the axis
 * is NOT_ENABLED after the E-stop and needs ESTOP_CLEAR + ENABLE) */
static void test_k1_and_power_optional(void)
{
    uint32_t k, from;
    /* defaults (sense off): PA7 "off" ignored, E-stop held 1 s -> no K1, DRV_PWR reads 1 */
    from = fake_cap_n;
    fake_input_set(IN_PWR, true);
    fake_input_set(IN_ESTOP, true);
    for (k = 0u; k < 1000u; k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_K1_WELDED) == 0u);
    TEST_ASSERT_EQUAL_HEX16(DS_DRV_PWR, le_get16(&h_status().b[10]) & DS_DRV_PWR);
    TEST_ASSERT_EQUAL_INT32(-1, mu_ev(EV_DRIVER_POWER, from));
    TEST_ASSERT_FALSE(fake_ena_enabled);
    fake_input_set(IN_ESTOP, false);
    mu_run(110u);
    h_expect_ok(h_cmd(CMD_ESTOP_CLEAR, 1u, NULL, 0u));
    TEST_ASSERT_EQUAL_UINT8(MS_NOT_ENABLED, g_fw.motion_state);   /* release never moves / enables */
    TEST_ASSERT_FALSE(fake_ena_enabled);
    mu_enable();                                                    /* no DRV_UNPOWERED refusal */
    TEST_ASSERT_EQUAL_HEX16(0u, g_fw.lat.faults);
    /* sense on, K1 check off: power present during an E-stop is only reported */
    mu_boot_sense(false);
    from = fake_cap_n;
    fake_input_set(IN_ESTOP, true);
    for (k = 0u; k < 1000u; k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_K1_WELDED) == 0u);
    TEST_ASSERT_EQUAL_HEX16(DS_DRV_PWR, le_get16(&h_status().b[10]) & DS_DRV_PWR);
    fake_input_set(IN_PWR, true);
    mu_run(25u);
    TEST_ASSERT_TRUE(mu_ev(EV_DRIVER_POWER, from) >= 0);           /* reported */
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_K1_WELDED) == 0u);
}

static void test_alm_start_block(void)             /* SAF-FW-026, FW-SW-004 */
{
    uint32_t from;
    mu_boot_sense(false);                         /* the end checks DRV_UNPOWERED */
    mu_enable();
    g_fw.homed = true;
    fake_input_set(IN_ALM, true);
    mu_run(2u);
    h_expect_nack(mu_move(10000, 1000u, 0u), ST_E_STATE, BLOCK_DRIVER_ALARM);
    h_expect_nack(mu_jog(1000, 0u, PROTO_JOG_NO_BOUND), ST_E_STATE, BLOCK_DRIVER_ALARM);
    fake_input_set(IN_ALM, false);
    mu_run(25u);
    h_expect_ok(mu_move(5000, 5000u, 0u));
    from = fake_cap_n;
    fake_input_set(IN_ALM, true);                 /* during the move: no stop (D-16) */
    (void)mu_run_until_idle(5000u);
    TEST_ASSERT_EQUAL_UINT16(MD_TARGET, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_EQUAL_UINT16(1u, mu_ev_arg(mu_ev(EV_ALM_CHANGED, from)));
    fake_input_set(IN_PWR, true);                 /* power off: DRV_UNPOWERED instead */
    mu_run(25u);
    h_expect_nack(mu_move(1000, 1000u, 0u), ST_E_STATE,
                  BLOCK_NOT_ENABLED | BLOCK_DRV_UNPOWERED | BLOCK_NOT_HOMED);
}

static void test_pause_button(void)                /* SAF-FW-023, FW-SW-003 (NO, closed-active) */
{
    uint32_t from;
    int32_t a, b;
    moving_homed(10000u);
    from = fake_cap_n;
    fake_input_set(IN_PAUSE, false);              /* NO pressed = low */
    fake_input_set(IN_PAUSE, true);               /* bounce */
    fake_input_set(IN_PAUSE, false);
    mu_run(2u);
    TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    a = mu_ev(EV_PAUSE_BUTTON, from);
    b = mu_ev(EV_PAUSED, from);
    TEST_ASSERT_TRUE(a >= 0 && b > a);
    TEST_ASSERT_EQUAL_UINT16(SRC_BUTTON, mu_ev_arg(b));
    TEST_ASSERT_EQUAL_UINT16(SC_PAUSE_BUTTON, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_PAUSE_BUTTON, from));      /* one EVENT per press */
    TEST_ASSERT_EQUAL_HEX16(DS_PAUSE_BTN | DS_PAUSED, le_get16(&h_status().b[10]) & (DS_PAUSE_BTN | DS_PAUSED));
    fake_input_set(IN_PAUSE, true);
    mu_run(25u);
    TEST_ASSERT_EQUAL_UINT32(2u, mu_ev_count(EV_PAUSE_BUTTON, from));      /* released */
    (void)mu_run_until_idle(2000u);
    fake_input_set(IN_PAUSE, false);              /* second press while PAUSED */
    mu_run(2u);
    TEST_ASSERT_TRUE(mu_ev(EV_RESUME_REQUEST, from) >= 0);
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_PAUSED, from));
}

static void test_load_limit_trip_and_regrow(void)  /* SAF-FW-008/009/011 */
{
    uint32_t from, n;
    int32_t i;
    moving_homed(10000u);
    mu_afe_on = false;
    from = fake_cap_n;
    h_expect_ok(h_cmd(CMD_STREAM_START, 1u, NULL, 0u));
    fake_sample(fake_now_us(), 7022272);          /* raw_max + 1: CLEAN halt in the sample ISR */
    fake_advance_tk(90u * 20u);
    n = fake_rise_n;
    TEST_ASSERT_FALSE(hal_step_running());
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_LOAD_LIMIT) != 0u);
    {
        int32_t d = fake_cap_find(ASYNC_DATA, -1, from);  /* the deciding frame carries it */
        TEST_ASSERT_TRUE(d >= 0);
        TEST_ASSERT_EQUAL_HEX16(DS_LOAD_LIMIT, le_get16(&fake_cap[d].payload[16]) & DS_LOAD_LIMIT);
        TEST_ASSERT_EQUAL_HEX8(0u, fake_cap[d].payload[5] & DF_VALID);
    }
    fake_run_ms(2u);
    TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);
    i = mu_ev(EV_FAULT_SET, from);
    TEST_ASSERT_EQUAL_UINT16(FAULT_LOAD_LIMIT_BIT, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_INT32(7022272, mu_ev_val(i));
    TEST_ASSERT_TRUE(mu_ev(EV_STOPPED, from) > i);
    TEST_ASSERT_EQUAL_UINT16(SC_LOAD_LIMIT, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_TRUE(mu_ev(EV_VALID_CLEARED, from) > mu_ev(EV_STOPPED, from));
    /* FAULT_CLEAR with the load still beyond: allowed; regrow > load_regrow_raw re-trips */
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 2u, NULL, 0u));
    from = fake_cap_n;
    fake_sample(fake_now_us(), 7022272 + 128849);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_LOAD_LIMIT) == 0u);
    fake_sample(fake_now_us(), 7022272 + 128850);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_LOAD_LIMIT) != 0u);
    /* rail with thresholds at the caps, stream off, NOT_ENABLED */
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 3u, NULL, 0u));
    fake_sample(fake_now_us(), 0);
    h_expect_ok(h_cmd(CMD_STREAM_STOP, 4u, NULL, 0u));
    h_set_param(PID_SAFETY_LOAD_RAW_MAX, PARAM_T_I32, 7151121u);
    fake_sample(fake_now_us(), PROTO_RAW_MAX);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_LOAD_LIMIT) != 0u);
    TEST_ASSERT_TRUE(g_fw.afe.saturated);
}

static void test_afe_stale_while_moving(void)      /* SAF-FW-012 */
{
    uint32_t from, k;
    moving_homed(10000u);
    mu_afe_on = false;
    from = fake_cap_n;
    for (k = 0u; k < 260u && motion_active(); k++) {
        fake_run_ms(1u);
    }
    TEST_ASSERT_TRUE(k <= 252u);                  /* <= afe.timeout_ms + 1 ms after the last sample */
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_AFE_FAULT) != 0u);
    TEST_ASSERT_EQUAL_UINT16(SC_AFE_FAULT, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    h_expect_nack(h_cmd(CMD_FAULT_CLEAR, 1u, NULL, 0u), ST_E_CAUSE_ACTIVE, FAULT_AFE_FAULT);
    h_expect_nack(mu_move(1000, 1000u, 0u), ST_E_STATE, BLOCK_FAULT | BLOCK_AFE_STALE);
    fake_sample(fake_now_us(), 0);
    h_expect_ok(h_cmd(CMD_FAULT_CLEAR, 2u, NULL, 0u));
}

static void test_idle_disable(void)                /* SAF-FW-017, D-33 g */
{
    uint32_t from;
    h_set_param(PID_SAFETY_IDLE_DISABLE_S, PARAM_T_U16, 2u);
    mu_enable();
    g_fw.homed = true;
    from = fake_cap_n;
    TEST_ASSERT_TRUE(le_get16(&h_status().b[70]) <= 2u);
    mu_run(1990u);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    mu_run(20u);
    TEST_ASSERT_EQUAL_UINT8(MS_NOT_ENABLED, g_fw.motion_state);
    TEST_ASSERT_FALSE(g_fw.homed);
    TEST_ASSERT_EQUAL_UINT16(DD_IDLE, mu_ev_arg(mu_ev(EV_DRIVER_DISABLED, from)));
    /* loaded: stays enabled */
    mu_enable();
    mu_raw = 200000;                              /* >= release_band_raw 128 849 */
    mu_run(3000u);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    TEST_ASSERT_EQUAL_UINT16(0xFFFFu, le_get16(&h_status().b[70]));
    /* AFE stale: never */
    mu_raw = 0;
    mu_afe_on = false;
    mu_run(3000u);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
}

static void test_not_settled(void)                 /* FW-SW-004 */
{
    uint32_t from;
    mu_enable();
    g_fw.homed = true;
    fake_input_set(IN_PEND, false);               /* PEND never comes */
    from = fake_cap_n;
    h_expect_ok(mu_move(1000, 5000u, 0u));
    (void)mu_run_until_idle(2000u);
    mu_run(210u);
    TEST_ASSERT_TRUE(mu_ev(EV_NOT_SETTLED, from) >= 0);
}

static void test_afe_status_bits_and_settle(void)  /* FW-AFE-003, AFES_* (seam v1.2) */
{
    uint32_t from = fake_cap_n;
    uint32_t k, settling = 0u;
    int32_t d;
    mu_afe_on = false;
    h_expect_ok(h_cmd(CMD_STREAM_START, 1u, NULL, 0u));
    fake_sample_st(fake_now_us(), 100, AFES_SCK_OVERRUN);
    fake_run_ms(1u);
    TEST_ASSERT_TRUE(mu_ev(EV_AFE_REINIT, from) >= 0);
    TEST_ASSERT_EQUAL_UINT16(1u, le_get16(&h_status().b[38]));
    from = fake_cap_n;
    for (k = 0u; k < 6u; k++) {
        fake_run_ms(12u);
        fake_sample(fake_now_us(), 100);
    }
    d = fake_cap_find(ASYNC_DATA, -1, from);
    while (d >= 0) {
        settling += (le_get16(&fake_cap[d].payload[16]) & DS_AFE_SETTLING) != 0u;
        d = fake_cap_find(ASYNC_DATA, -1, (uint32_t)d + 1u);
    }
    TEST_ASSERT_EQUAL_UINT32(4u, settling);       /* afe.settle_discard */
    from = fake_cap_n;
    fake_sample_st(fake_now_us(), 100, AFES_MISSED_EDGE);
    fake_sample(fake_now_us() + 12500u, 100);
    d = fake_cap_find(ASYNC_DATA, -1, from);
    TEST_ASSERT_EQUAL_HEX8(DF_OVERRUN, fake_cap[d].payload[5] & DF_OVERRUN);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_estop_during_move);
    RUN_TEST(test_boot_with_inputs_active);
    RUN_TEST(test_limit_hit_and_release);
    RUN_TEST(test_both_limits_wiring);
    RUN_TEST(test_drv_power_loss_during_jog);
    RUN_TEST(test_estop_then_power_only_driver_power_event);
    RUN_TEST(test_k1_welded);
    RUN_TEST(test_k1_and_power_optional);
    RUN_TEST(test_alm_start_block);
    RUN_TEST(test_pause_button);
    RUN_TEST(test_load_limit_trip_and_regrow);
    RUN_TEST(test_afe_stale_while_moving);
    RUN_TEST(test_idle_disable);
    RUN_TEST(test_not_settled);
    RUN_TEST(test_afe_status_bits_and_settle);
    return UNITY_END();
}
