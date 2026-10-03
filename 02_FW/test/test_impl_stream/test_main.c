/* DATA stream on the fake seams: one frame per conversion with the sample's t_us and frame_seq
 * (header SEQ = low byte), VALID by frame time, class-D congestion (frame dropped, frame_seq still
 * advances, OVERRUN on the next sent frame, tx_drops), missed conversion -> OVERRUN, stale ->
 * AFE_STALE + fallback frames at stream.fallback_hz, stream off keeps the AFE supervision,
 * frame_seq not reset by STREAM_STOP/START, measured rate in STATUS, settle flagging.
 * Verifies: FW-STR-001, FW-STR-002, FW-STR-003, FW-STR-004, FW-STR-005, FW-CMD-002, FW-AFE-004,
 *           FW-AFE-002 (settle), FW-NVM-003 (OVERRUN after a hold), IF-006, IF-007
 */
#include <unity.h>

#include "harness.h"

void setUp(void) { h_boot(true); }
void tearDown(void) {}

static uint32_t T;

static void sample(int32_t raw)
{
    T += 12500u;
    fake_set_time_us(T);
    fake_sample(T, raw);
}

static const fake_frame_t *last_data(void)
{
    int32_t i;
    for (i = (int32_t)fake_cap_n - 1; i >= 0; i--) {
        if (fake_cap[i].type == (uint8_t)ASYNC_DATA) {
            return &fake_cap[i];
        }
    }
    TEST_FAIL_MESSAGE("no DATA frame");
    return NULL;
}

static void start(void)
{
    T = fake_now_us();
    h_expect_ok(h_cmd(CMD_STREAM_START, 1u, NULL, 0u));
    fake_cap_clear();
}

static void test_one_frame_per_conversion(void)
{
    uint32_t i;
    start();
    for (i = 0u; i < 10u; i++) {
        const fake_frame_t *d;
        sample((int32_t)(1000 * (int32_t)i) - 4000);
        d = last_data();
        TEST_ASSERT_EQUAL_UINT16(PROTO_DATA_LEN, d->len);
        TEST_ASSERT_EQUAL_UINT32(T, le_get32(&d->payload[0]));
        TEST_ASSERT_EQUAL_UINT8(1u, d->payload[4]);
        TEST_ASSERT_EQUAL_INT32((int32_t)(1000 * (int32_t)i) - 4000, (int32_t)le_get32(&d->payload[6]));
        TEST_ASSERT_EQUAL_INT32(0, (int32_t)le_get32(&d->payload[10]));
        TEST_ASSERT_EQUAL_UINT16(i, le_get16(&d->payload[14]));
        TEST_ASSERT_EQUAL_UINT8((uint8_t)i, d->seq);
    }
    TEST_ASSERT_EQUAL_UINT32(10u, fake_cap_count(ASYNC_DATA));
    TEST_ASSERT_EQUAL_HEX8(SYSF_STREAM_ON, h_status().b[19] & SYSF_STREAM_ON);
}

static void test_settle_flag_after_boot(void)
{
    uint32_t i;
    start();
    for (i = 0u; i < 6u; i++) {
        sample(0);
        TEST_ASSERT_EQUAL((i < 4u) ? DS_AFE_SETTLING : 0u,
                          le_get16(&last_data()->payload[16]) & DS_AFE_SETTLING);   /* settle_discard 4 */
    }
}

static void test_valid_by_frame_time(void)
{
    uint8_t one[1] = {1u};
    start();
    sample(0);
    T += 5000u;
    fake_set_time_us(T);
    h_expect_ok(h_cmd(CMD_SET_VALID, 2u, one, 1u));                  /* t_apply = T */
    fake_sample(T - 1u, 0);                                          /* stamped before: old value */
    TEST_ASSERT_EQUAL_HEX8(0u, last_data()->payload[5] & DF_VALID);
    fake_sample(T, 0);                                               /* not earlier: new value */
    TEST_ASSERT_EQUAL_HEX8(DF_VALID, last_data()->payload[5] & DF_VALID);
}

static void test_congestion_drop_and_overrun(void)
{
    const fake_frame_t *d;
    start();
    fake_tx_auto(false);
    sample(1);
    sample(2);
    sample(3);                                                       /* class D full: dropped */
    TEST_ASSERT_EQUAL_UINT32(2u, fake_tx_drain(UINT32_MAX));
    TEST_ASSERT_EQUAL_HEX8(0u, fake_cap[1].payload[5] & DF_OVERRUN);
    sample(4);
    TEST_ASSERT_EQUAL_UINT32(1u, fake_tx_drain(UINT32_MAX));
    d = last_data();
    TEST_ASSERT_EQUAL_UINT16(3u, le_get16(&d->payload[14]));         /* gap: frame_seq 2 dropped */
    TEST_ASSERT_EQUAL_HEX8(DF_OVERRUN, d->payload[5] & DF_OVERRUN);
    sample(5);
    TEST_ASSERT_EQUAL_UINT32(1u, fake_tx_drain(UINT32_MAX));
    TEST_ASSERT_EQUAL_HEX8(0u, last_data()->payload[5] & DF_OVERRUN);
    fake_tx_auto(true);
    TEST_ASSERT_EQUAL_UINT32(1u, le_get32(&h_status().b[56]));       /* tx_drops */
}

static void test_missed_conversion_overrun(void)
{
    start();
    sample(0);
    T += 12500u;                                                     /* one conversion missing */
    sample(0);
    TEST_ASSERT_EQUAL_HEX8(DF_OVERRUN, last_data()->payload[5] & DF_OVERRUN);
    sample(0);
    TEST_ASSERT_EQUAL_HEX8(0u, last_data()->payload[5] & DF_OVERRUN);
}

static void test_stale_fallback_frames(void)
{
    uint32_t i, fb = 0u;
    int32_t ev;
    start();
    sample(0);
    fake_set_time_us(T);
    fake_run_ms(249);
    TEST_ASSERT_TRUE(fake_cap_event(EV_AFE_STALE, 0u) < 0);
    fake_run_ms(1);                                                  /* afe.timeout_ms = 250 */
    ev = fake_cap_event(EV_AFE_STALE, 0u);
    TEST_ASSERT_TRUE(ev >= 0);
    TEST_ASSERT_EQUAL_UINT16(1u, le_get16(&fake_cap[ev].payload[6]));
    fake_run_ms(1000);
    for (i = 0u; i < fake_cap_n; i++) {
        if (fake_cap[i].type == (uint8_t)ASYNC_DATA &&
            (int32_t)le_get32(&fake_cap[i].payload[6]) == PROTO_AFE_NO_DATA) {
            uint16_t st = le_get16(&fake_cap[i].payload[16]);
            TEST_ASSERT_EQUAL_HEX16(DS_NO_AFE_DATA | DS_AFE_STALE, st & (DS_NO_AFE_DATA | DS_AFE_STALE));
            fb++;
        }
    }
    TEST_ASSERT_TRUE(fb >= 10u && fb <= 11u);                        /* 10 Hz for ~1 s */
    TEST_ASSERT_EQUAL_HEX16(DS_AFE_STALE, le_get16(&h_status().b[10]) & DS_AFE_STALE);
    fake_cap_clear();
    T = fake_now_us();
    sample(7);                                                       /* fresh again */
    TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&last_data()->payload[16]) & DS_AFE_STALE);
    fake_run_ms(200);
    ev = fake_cap_event(EV_AFE_STALE, 0u);
    TEST_ASSERT_TRUE(ev >= 0);
    TEST_ASSERT_EQUAL_UINT16(0u, le_get16(&fake_cap[ev].payload[6]));
    for (i = 0u; i < fake_cap_n; i++) {                              /* no fallback after fresh */
        if (fake_cap[i].type == (uint8_t)ASYNC_DATA) {
            TEST_ASSERT_NOT_EQUAL(PROTO_AFE_NO_DATA, (int32_t)le_get32(&fake_cap[i].payload[6]));
        }
    }
}

static void test_stream_off_and_seq_not_reset(void)
{
    start();
    sample(0);
    sample(0);
    h_expect_ok(h_cmd(CMD_STREAM_STOP, 3u, NULL, 0u));
    fake_cap_clear();
    sample(123);
    TEST_ASSERT_EQUAL_UINT32(0u, fake_cap_count(ASYNC_DATA));
    TEST_ASSERT_EQUAL_INT32(123, (int32_t)le_get32(&h_status().b[32]));   /* supervision continues */
    h_expect_ok(h_cmd(CMD_STREAM_STOP, 4u, NULL, 0u));                    /* idempotent */
    h_expect_ok(h_cmd(CMD_STREAM_START, 5u, NULL, 0u));
    h_expect_ok(h_cmd(CMD_STREAM_START, 6u, NULL, 0u));
    sample(0);
    TEST_ASSERT_EQUAL_UINT16(2u, le_get16(&last_data()->payload[14]));    /* not reset */
}

static void test_measured_rate_in_status(void)
{
    uint32_t i;
    start();
    for (i = 0u; i < 20u; i++) {
        sample(0);
        app_loop();
    }
    TEST_ASSERT_EQUAL_UINT16(800u, le_get16(&h_status().b[36]));
    TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&h_status().b[10]) & DS_AFE_RATE_MISMATCH);
}

static void test_rate_change_reconfigures(void)
{
    h_set_param(PID_AFE_RATE_SPS, PARAM_T_ENUM, AFE_RATE_SPS_SPS10);
    TEST_ASSERT_FALSE(fake_hx_rate80);
    TEST_ASSERT_FALSE(fake_rate_pin);
    TEST_ASSERT_EQUAL_HEX16(0u, le_get16(&h_status().b[14]) & IO_RATE_80);
    h_set_param(PID_AFE_GAIN_CHANNEL, PARAM_T_ENUM, AFE_GAIN_CHANNEL_A64);
    TEST_ASSERT_EQUAL_UINT8(27u, fake_hx_gain_pulses);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_one_frame_per_conversion);
    RUN_TEST(test_settle_flag_after_boot);
    RUN_TEST(test_valid_by_frame_time);
    RUN_TEST(test_congestion_drop_and_overrun);
    RUN_TEST(test_missed_conversion_overrun);
    RUN_TEST(test_stale_fallback_frames);
    RUN_TEST(test_stream_off_and_seq_not_reset);
    RUN_TEST(test_measured_rate_in_status);
    RUN_TEST(test_rate_change_reconfigures);
    return UNITY_END();
}
