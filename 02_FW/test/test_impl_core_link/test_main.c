/* Core link on the fake seams: boot EVENTs, exactly one response per valid command frame (SEQ
 * echoed), INFO/STATUS content, GET_ALL_PARAMS = the vectors' default pages, invalid TYPE counted
 * without response, flood with response back-pressure (no response lost, shortened or reordered),
 * dispatch budget, NVM HOLD blocks dispatch, latch commands, motion gating with the real inputs
 * (M2), REBOOT, sniffer hold.
 * Verifies: FW-CMD-001, FW-CMD-002, FW-CMD-004, FW-CFG-002, FW-CFG-004, FW-NVM-003, IF-005, IF-008,
 *           SAF-FW-018 (boot state), SAF-FW-023, SAF-FW-024 (DRV_UNPOWERED), NFR-008, DEF-P1-07
 */
#include <unity.h>

#include "harness.h"
#include "vec_frames.h"

void setUp(void) { h_boot(true); }
void tearDown(void) {}

static void test_boot_events_and_state(void)
{
    h_status_t s;
    int32_t i = fake_cap_event(EV_BOOT, 0u);
    int32_t j = fake_cap_event(EV_PARAMS_DEFAULTED, 0u);
    TEST_ASSERT_TRUE(i >= 0 && j > i);
    TEST_ASSERT_EQUAL_UINT8(0u, fake_cap[i].seq);                     /* first EVENT SEQ */
    TEST_ASSERT_EQUAL_UINT16(1u, le_get16(&fake_cap[i].payload[6]));  /* reset cause POWER_ON */
    TEST_ASSERT_EQUAL_UINT16(PDEF_NO_RECORD, le_get16(&fake_cap[j].payload[6]));
    s = h_status();
    TEST_ASSERT_EQUAL_UINT8(MS_NOT_ENABLED, s.b[9]);
    TEST_ASSERT_EQUAL_HEX8(0u, s.b[8] & (DF_VALID | DF_HOMED | DF_ENABLED | DF_MOVING));
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED, s.b[19]);   /* stream off */
    TEST_ASSERT_EQUAL_UINT8(RST_POWER_ON, s.b[18]);
    TEST_ASSERT_EQUAL_INT32(PROTO_AFE_NO_DATA, (int32_t)le_get32(&s.b[32]));
    TEST_ASSERT_EQUAL_UINT16(0xFFFFu, le_get16(&s.b[70]));
    TEST_ASSERT_EQUAL_UINT8(SRC_NONE, s.b[84]);
    TEST_ASSERT_EQUAL_UINT8(0u, s.b[85]);
    /* fake idle levels: E-stop closed, limits inactive, PAUSE released, PEND in position, powered */
    TEST_ASSERT_EQUAL_HEX16(IO_RATE_80 | IO_PEND | IO_DRV_PWR, le_get16(&s.b[14]));
    TEST_ASSERT_EQUAL_HEX16(DS_DRV_PWR | DS_PEND, le_get16(&s.b[10]));
    TEST_ASSERT_TRUE(fake_rate_pin);
    TEST_ASSERT_EQUAL_UINT8(25u, fake_hx_gain_pulses);
}

static void test_get_info(void)
{
    const fake_frame_t *r = h_cmd(CMD_GET_INFO, 1u, NULL, 0u);
    const uint8_t *b = &r->payload[1];
    h_expect_ok(r);
    TEST_ASSERT_EQUAL_UINT16(1u + PROTO_INFO_LEN, r->len);
    TEST_ASSERT_EQUAL_UINT8(1u, b[0]);
    TEST_ASSERT_EQUAL_UINT8(0u, b[1]);
    TEST_ASSERT_EQUAL_UINT8(1u, b[2]);
    TEST_ASSERT_EQUAL_UINT8(FW_VERSION_MAJOR, b[3]);
    TEST_ASSERT_EQUAL_UINT8(FW_VERSION_MINOR, b[4]);
    TEST_ASSERT_EQUAL_HEX32(PARAM_DICT_HASH, le_get32(&b[6]));
    TEST_ASSERT_EQUAL_HEX8(0x10u, b[10]);                              /* hal_uid */
    TEST_ASSERT_EQUAL_STRING("host", (const char *)&b[22]);
    TEST_ASSERT_EQUAL_UINT16(PARAM_COUNT, le_get16(&b[38]));
    TEST_ASSERT_EQUAL_HEX32(FEAT_AFE | FEAT_MOTION | FEAT_HOMING | FEAT_MOVE_UNTIL_LOAD | FEAT_NVM | FEAT_BUTTONS |
                            FEAT_DRV_SIGNALS, le_get32(&b[40]));   /* M2 + MOVE_UNTIL_LOAD (D-44) */
}

/* production path GET_ALL_PARAMS with defaults == the vectors' default-table pages */
static void test_get_all_params_equals_vectors(void)
{
    uint32_t i, pages = 0u;
    for (i = 0u; i < VEC_RESPS_N; i++) {
        const vec_resp_t *v = &VEC_RESPS[i];
        const vec_frame_t *f = &VEC_FRAMES[v->idx];
        uint8_t pl[1];
        const fake_frame_t *r;
        if (v->kind != RB_PAGE) {
            continue;
        }
        pl[0] = v->page;
        r = h_cmd(CMD_GET_ALL_PARAMS, f->seq, pl, 1u);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(f->len, r->len, f->name);
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(f->payload, r->payload, f->len, f->name);
        pages++;
    }
    TEST_ASSERT_EQUAL_UINT32(3u, pages);
    {
        uint8_t pl[1] = {3u};
        h_expect_nack(h_cmd(CMD_GET_ALL_PARAMS, 9u, pl, 1u), ST_E_RANGE, 0u);
    }
}

static void test_one_response_per_frame_and_invalid_type(void)
{
    uint8_t junk[] = {0x00, 0x11, 0xA5};
    uint8_t pl[1] = {0u};
    uint32_t before;
    h_status_t s;
    fake_cap_clear();
    fake_cmd(CMD_PING, 10u, NULL, 0u);
    fake_rx(junk, sizeof junk);                               /* noise */
    fake_cmd(0x81u, 11u, pl, 1u);                             /* a response TYPE: invalid for the FW */
    fake_cmd(0xC0u, 12u, NULL, 0u);                           /* async TYPE */
    fake_cmd(0x3Fu, 13u, NULL, 0u);                           /* undefined command */
    fake_cmd(CMD_PING, 14u, pl, 1u);                          /* wrong LEN */
    fake_run_ms(30);                                          /* incl. the 20 ms inter-byte timeout */
    TEST_ASSERT_TRUE(fake_cap_find(0x81u, 10, 0u) >= 0);
    TEST_ASSERT_TRUE(fake_cap_find(0x81u, 11, 0u) < 0);
    TEST_ASSERT_EQUAL_UINT8(ST_E_UNKNOWN_CMD, fake_cap[fake_cap_find(0xBFu, 13, 0u)].payload[0]);
    TEST_ASSERT_EQUAL_UINT8(ST_E_LENGTH, fake_cap[fake_cap_find(0x81u, 14, 0u)].payload[0]);
    before = fake_cap_count(0x81u) + fake_cap_count(0xBFu);
    TEST_ASSERT_EQUAL_UINT32(3u, before);
    s = h_status();
    TEST_ASSERT_EQUAL_UINT32(2u, le_get32(&s.b[48]));         /* rx_frame_errors: 2 invalid TYPEs */
    TEST_ASSERT_EQUAL_UINT32(6u, le_get32(&s.b[40]));         /* 5 + GET_STATUS */
}

/* DEF-P1-07: a flood of commands with the line stalled: responses are never dropped; dispatch
 * stops while class R lacks room for the largest response, and every response arrives once, in
 * order, after the line drains */
static void test_flood_backpressure(void)
{
    uint32_t k, n = 0u;
    uint8_t expect = 0u;
    fake_tx_auto(false);
    fake_cap_clear();
    for (k = 0u; k < 120u; k++) {
        fake_cmd(CMD_GET_STATUS, (uint8_t)k, NULL, 0u);      /* 96-byte responses */
    }
    for (k = 0u; k < 20u; k++) {
        app_loop();
    }
    TEST_ASSERT_EQUAL_UINT32(0u, fake_cap_n);                /* nothing sent: line stalled */
    TEST_ASSERT_TRUE(hal_uart_tx_free(HAL_TX_RESP) < TX_R_RESERVE);
    for (k = 0u; k < 400u; k++) {
        (void)fake_tx_drain(1u);
        app_loop();
    }
    (void)fake_tx_drain(UINT32_MAX);
    for (k = 0u; k < fake_cap_n; k++) {
        if (fake_cap[k].type == (uint8_t)(CMD_GET_STATUS | PROTO_RESP_BIT)) {
            TEST_ASSERT_EQUAL_UINT8(expect, fake_cap[k].seq);
            TEST_ASSERT_EQUAL_UINT16(1u + PROTO_STATUS_LEN, fake_cap[k].len);
            expect++;
            n++;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(120u, n);
}

static void test_set_get_param_and_nack_no_effect(void)
{
    uint8_t pl[7];
    const fake_frame_t *r;
    h_set_param(PID_AFE_SETTLE_DISCARD, PARAM_T_U8, 8u);
    TEST_ASSERT_EQUAL_UINT32(8u, h_get_param_raw(PID_AFE_SETTLE_DISCARD));
    le_put16(pl, PID_SAFETY_LOAD_RAW_MAX);
    pl[2] = PARAM_T_I32;
    le_put32(&pl[3], 7151122u);                              /* above max: E_RANGE, never clamped */
    r = h_cmd(CMD_SET_PARAM, 3u, pl, 7u);
    h_expect_nack(r, ST_E_RANGE, PID_SAFETY_LOAD_RAW_MAX);
    TEST_ASSERT_EQUAL_UINT32(7022271u, h_get_param_raw(PID_SAFETY_LOAD_RAW_MAX));
    /* reboot_required -> REBOOT_PENDING while RAM != boot value */
    h_set_param(PID_MOTION_PUL_INVERT, PARAM_T_BOOL, 1u);
    TEST_ASSERT_EQUAL_HEX8(SYSF_REBOOT_PENDING, h_status().b[19] & SYSF_REBOOT_PENDING);
    h_set_param(PID_MOTION_PUL_INVERT, PARAM_T_BOOL, 0u);
    TEST_ASSERT_EQUAL_HEX8(0u, h_status().b[19] & SYSF_REBOOT_PENDING);
}

static void test_motion_gating_and_reboot_params(void)
{
    uint8_t mv[12] = {0};
    uint8_t jog0[12] = {0};
    /* ICD v0.7 / D-41: drv.pwr_sense_enable default 0 -> an unwired / "off" PA7 is ignored: DRV_PWR
     * reads 1, no DRV_UNPOWERED refusal */
    le_put32(mv, 100000u);
    le_put32(&mv[4], 1000u);
    h_expect_nack(h_cmd(CMD_MOVE_ABS, 1u, mv, 12u), ST_E_STATE, BLOCK_NOT_ENABLED | BLOCK_NOT_HOMED);
    fake_input_set(IO_DRV_PWR_BIT, true);                     /* input reads "off" */
    fake_run_ms(DRV_PWR_FILTER_MS + 1u);
    h_expect_nack(h_cmd(CMD_MOVE_ABS, 1u, mv, 12u), ST_E_STATE, BLOCK_NOT_ENABLED | BLOCK_NOT_HOMED);
    TEST_ASSERT_EQUAL_HEX16(DS_DRV_PWR, le_get16(&h_status().b[10]) & DS_DRV_PWR);
    h_expect_ok(h_cmd(CMD_JOG, 3u, jog0, 12u));               /* JOG 0: OK no-op */
    /* DEF-M1-01: drv.pwr_sense_enable is reboot_required - the old behaviour stays until SAVE +
     * REBOOT (REBOOT_PENDING meanwhile); the same holds for pul_invert / ena_invert */
    h_set_param(PID_DRV_PWR_SENSE_ENABLE, PARAM_T_BOOL, 1u);
    h_set_param(PID_MOTION_PUL_INVERT, PARAM_T_BOOL, 1u);
    h_set_param(PID_MOTION_ENA_INVERT, PARAM_T_BOOL, 1u);
    {
        h_status_t s = h_status();
        TEST_ASSERT_EQUAL_HEX8(SYSF_REBOOT_PENDING, s.b[19] & SYSF_REBOOT_PENDING);
        TEST_ASSERT_EQUAL_HEX16(DS_DRV_PWR, le_get16(&s.b[10]) & DS_DRV_PWR);
    }
    TEST_ASSERT_TRUE(g_fw.p.motion.pul_invert && !g_fw.boot_p.motion.pul_invert);
    TEST_ASSERT_FALSE(params_rt_effective()->motion.ena_invert);
    TEST_ASSERT_FALSE(params_rt_effective()->drv.pwr_sense_enable);
    h_expect_ok(h_cmd(CMD_SAVE_PARAMS, 8u, NULL, 0u));
    fake_run_ms(2);
    h_reboot();
    TEST_ASSERT_EQUAL_HEX8(0u, h_status().b[19] & SYSF_REBOOT_PENDING);
    TEST_ASSERT_TRUE(g_fw.boot_p.motion.pul_invert && g_fw.boot_p.motion.ena_invert);
    TEST_ASSERT_TRUE(g_fw.boot_p.drv.pwr_sense_enable);
    /* sensing enabled (optional 48 V presence input): input off -> DRV_UNPOWERED */
    fake_input_set(IO_DRV_PWR_BIT, true);
    fake_run_ms(DRV_PWR_FILTER_MS + 1u);
    TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&h_status().b[10]) & DS_DRV_PWR);
    h_expect_nack(h_cmd(CMD_MOVE_ABS, 1u, mv, 12u), ST_E_STATE,
                  BLOCK_NOT_ENABLED | BLOCK_NOT_HOMED | BLOCK_DRV_UNPOWERED);
    h_expect_nack(h_cmd(CMD_ENABLE, 2u, NULL, 0u), ST_E_STATE, BLOCK_DRV_UNPOWERED);
    h_expect_ok(h_cmd(CMD_JOG, 3u, jog0, 12u));
    fake_input_set(IO_DRV_PWR_BIT, false);                    /* powered */
    fake_run_ms(DRV_PWR_FILTER_MS + 1u);
    {
        const fake_frame_t *r = h_cmd(CMD_ENABLE, 4u, NULL, 0u);
        h_expect_ok(r);
        TEST_ASSERT_EQUAL_UINT16(500u, le_get16(&r->payload[1]));    /* settle left (FW-MOT-008) */
    }
    TEST_ASSERT_EQUAL_UINT8(MS_ENABLING, h_status().b[9]);
    h_expect_ok(h_cmd(CMD_DISABLE, 5u, NULL, 0u));
    TEST_ASSERT_EQUAL_UINT8(MS_NOT_ENABLED, h_status().b[9]);
}

/* OBS-M1-08: every PARAM_F_REBOOT parameter of the generated table is overlaid with its boot value
 * (no hand list); non-reboot parameters come from RAM */
static void test_effective_overlay_from_generated_flags(void)
{
    uint16_t i, n = 0u;
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        const param_meta_t *m = &PARAM_TABLE[i];
        uint32_t alt = (m->max_raw != m->def_raw) ? m->max_raw : m->min_raw;
        param_set_raw(&g_fw.p, m, alt);
        if ((m->flags & PARAM_F_REBOOT) != 0u) {
            n++;
            TEST_ASSERT_EQUAL_HEX32(m->def_raw, param_get_raw(params_rt_effective(), m));
            TEST_ASSERT_TRUE(params_rt_reboot_pending());
        } else {
            TEST_ASSERT_EQUAL_HEX32(alt, param_get_raw(params_rt_effective(), m));
        }
        param_set_raw(&g_fw.p, m, m->def_raw);
        TEST_ASSERT_FALSE(params_rt_reboot_pending());
    }
    TEST_ASSERT_EQUAL_UINT16(4u, n);                  /* + drv.k1_check_enable (ICD v0.7) */
}

static void test_halt_pause_resume_flow(void)
{
    h_status_t s;
    uint8_t mv[12] = {0};
    uint32_t from = fake_cap_n;
    h_expect_ok(h_cmd(CMD_HALT, 1u, NULL, 0u));
    h_expect_ok(h_cmd(CMD_HALT, 2u, NULL, 0u));                /* CONFIRM repeat: no 2nd event */
    h_expect_ok(h_cmd(CMD_PAUSE, 3u, NULL, 0u));
    events_flush();
    TEST_ASSERT_TRUE(fake_cap_event(EV_HALT_SET, from) >= 0);
    TEST_ASSERT_TRUE(fake_cap_event(EV_HALT_SET, (uint32_t)fake_cap_event(EV_HALT_SET, from) + 1u) < 0);
    TEST_ASSERT_TRUE(fake_cap_event(EV_PAUSED, from) >= 0);
    s = h_status();
    TEST_ASSERT_EQUAL_HEX8(DF_HALT, s.b[8] & DF_HALT);
    TEST_ASSERT_EQUAL_HEX16(DS_PAUSED, le_get16(&s.b[10]) & DS_PAUSED);
    TEST_ASSERT_EQUAL_UINT8(SRC_PC, s.b[17]);
    TEST_ASSERT_EQUAL_UINT8(SRC_PC, s.b[84]);
    h_expect_nack(h_cmd(CMD_RESUME, 4u, NULL, 0u), ST_E_STATE, BLOCK_HALT);   /* D-31 */
    TEST_ASSERT_EQUAL_UINT8(SRC_PC, h_status().b[84]);                        /* NACK: no effect */
    le_put32(mv, 100000u);
    le_put32(&mv[4], 1000u);
    h_expect_nack(h_cmd(CMD_MOVE_ABS, 5u, mv, 12u), ST_E_STATE,
                  BLOCK_HALT | BLOCK_NOT_ENABLED | BLOCK_NOT_HOMED | BLOCK_PAUSED);
    h_expect_ok(h_cmd(CMD_HALT_CLEAR, 6u, NULL, 0u));                         /* HALT + PAUSED */
    s = h_status();
    TEST_ASSERT_EQUAL_HEX8(0u, s.b[8] & DF_HALT);
    TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&s.b[10]) & DS_PAUSED);
    h_expect_ok(h_cmd(CMD_PAUSE, 7u, NULL, 0u));
    h_expect_ok(h_cmd(CMD_RESUME, 8u, NULL, 0u));
    TEST_ASSERT_EQUAL_UINT8(SRC_NONE, h_status().b[84]);
    events_flush();
    TEST_ASSERT_TRUE(fake_cap_event(EV_PAUSE_CLEARED, from) >= 0);
    h_expect_ok(h_cmd(CMD_ESTOP_CLEAR, 9u, NULL, 0u));
    {
        const fake_frame_t *r = h_cmd(CMD_FAULT_CLEAR, 10u, NULL, 0u);
        h_expect_ok(r);
        TEST_ASSERT_EQUAL_UINT16(3u, r->len);
        TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&r->payload[1]));
    }
}

static void test_set_valid_response_time(void)
{
    uint8_t pl[1] = {1u};
    const fake_frame_t *r;
    fake_set_time_us(7000123u);
    r = h_cmd(CMD_SET_VALID, 66u, pl, 1u);
    h_expect_ok(r);
    TEST_ASSERT_EQUAL_UINT32(7000123u, le_get32(&r->payload[1]));
    TEST_ASSERT_EQUAL_HEX8(DF_VALID, h_status().b[8] & DF_VALID);
    pl[0] = 2u;
    h_expect_nack(h_cmd(CMD_SET_VALID, 67u, pl, 1u), ST_E_RANGE, 0u);
    h_expect_nack(h_cmd(CMD_STOP, 68u, pl, 1u), ST_E_RANGE, 0u);     /* STOP mode 2 */
    TEST_ASSERT_EQUAL_HEX8(DF_VALID, h_status().b[8] & DF_VALID);   /* NACKed STOP: no effect */
    pl[0] = 0u;
    h_expect_ok(h_cmd(CMD_STOP, 70u, pl, 1u));
    TEST_ASSERT_EQUAL_HEX8(0u, h_status().b[8] & DF_VALID);          /* STOP clears VALID */
}

static void test_reboot(void)
{
    uint8_t pl[4];
    le_put32(pl, PROTO_REBOOT_MAGIC);
    h_expect_ok(h_cmd(CMD_REBOOT, 5u, pl, 4u));
    app_loop();
    TEST_ASSERT_TRUE(fake_reset_requested);
    fake_reset_requested = false;
    le_put32(pl, 0x12345678u);
    h_expect_nack(h_cmd(CMD_REBOOT, 6u, pl, 4u), ST_E_RANGE, 0u);
    app_loop();
    TEST_ASSERT_FALSE(fake_reset_requested);
}

/* HALT sniffed in the tick before dispatch: the motion-start gate closes until the dispatcher
 * reaches the HALT; the link age is refreshed by the sniffer */
static void test_sniffer_hold_and_link_age(void)
{
    uint8_t mv[12] = {0};
    fake_run_ms(300);
    TEST_ASSERT_TRUE(le_get16(&h_status().b[64]) <= 1u);     /* GET_STATUS itself refreshed it */
    fake_run_ms(300);
    fake_cmd(CMD_MOVE_ABS, 1u, mv, 12u);
    fake_cmd(CMD_HALT, 2u, NULL, 0u);
    fake_set_time_us(fake_now_us() + 1000u);
    core_tick_1ms();                                         /* sniffer runs before the main loop */
    TEST_ASSERT_FALSE(link_motion_start_allowed());
    TEST_ASSERT_EQUAL_UINT32(hal_time_ms(), g_fw.last_cmd_rx_ms);
    app_loop();                                              /* MOVE_ABS then HALT dispatched */
    TEST_ASSERT_TRUE(link_motion_start_allowed());
    TEST_ASSERT_TRUE(fake_cap_find(CMD_HALT | PROTO_RESP_BIT, 2, 0u) >= 0);
}

/* SAVE stops dispatching until the NVM operation finished: the PING behind it is answered after */
static void test_nvm_hold_blocks_dispatch(void)
{
    int32_t a, b;
    fake_cap_clear();
    fake_cmd(CMD_SAVE_PARAMS, 1u, NULL, 0u);
    fake_cmd(CMD_PING, 2u, NULL, 0u);
    app_loop();                                              /* SAVE accepted, HOLD */
    TEST_ASSERT_TRUE(nvm_busy());
    TEST_ASSERT_TRUE(fake_hx_hold);
    TEST_ASSERT_TRUE(fake_cap_find(0x81u, 2, 0u) < 0);
    fake_run_ms(10);
    a = fake_cap_find(CMD_SAVE_PARAMS | PROTO_RESP_BIT, 1, 0u);
    b = fake_cap_find(0x81u, 2, 0u);
    TEST_ASSERT_TRUE(a >= 0 && b > a);
    TEST_ASSERT_FALSE(fake_hx_hold);
    TEST_ASSERT_EQUAL_UINT32(WDG_RUN_TIMEOUT_MS, fake_wdg_timeout_ms);
}

static void test_watchdog_kicked_only_with_tick(void)
{
    uint32_t k0 = fake_wdg_kicks;
    uint32_t i;
    for (i = 0u; i < 10u; i++) {
        app_loop();                                          /* tick not running */
    }
    TEST_ASSERT_EQUAL_UINT32(k0, fake_wdg_kicks);
    fake_run_ms(5);
    TEST_ASSERT_EQUAL_UINT32(k0 + 5u, fake_wdg_kicks);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_boot_events_and_state);
    RUN_TEST(test_get_info);
    RUN_TEST(test_get_all_params_equals_vectors);
    RUN_TEST(test_one_response_per_frame_and_invalid_type);
    RUN_TEST(test_flood_backpressure);
    RUN_TEST(test_set_get_param_and_nack_no_effect);
    RUN_TEST(test_motion_gating_and_reboot_params);
    RUN_TEST(test_effective_overlay_from_generated_flags);
    RUN_TEST(test_halt_pause_resume_flow);
    RUN_TEST(test_set_valid_response_time);
    RUN_TEST(test_reboot);
    RUN_TEST(test_sniffer_hold_and_link_age);
    RUN_TEST(test_nvm_hold_blocks_dispatch);
    RUN_TEST(test_watchdog_kicked_only_with_tick);
    return UNITY_END();
}
