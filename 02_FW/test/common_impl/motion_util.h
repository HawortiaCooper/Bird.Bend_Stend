/* M2 core-test helpers on the fake seams (header-only): boot with a long link timeout, run virtual
 * time with 80 Hz-like AFE samples, enable, commands with payloads, EVENT queries, PUL-edge analysis.
 * Verifies: (helpers for) FW-MOT-*, FW-HOM-*, SAF-FW-* core suites
 */
#ifndef MOTION_UTIL_H
#define MOTION_UTIL_H

#include "harness.h"
#include "ramp.h"

static int32_t mu_raw;                       /* load of the next samples (counts) */
static bool    mu_afe_on = true;

static inline void mu_run(uint32_t ms)
{
    uint32_t i;
    for (i = 0u; i < ms; i++) {
        fake_run_ms(1u);
        if (mu_afe_on && (fake_now_us() / 1000u) % 12u == 0u) {
            fake_sample(fake_now_us(), mu_raw);
        }
    }
}

static inline void mu_boot(void)
{
    h_boot(true);
    mu_raw = 0;
    mu_afe_on = true;
    h_set_param(PID_SAFETY_LINK_TIMEOUT_MS, PARAM_T_U16, 5000u);   /* SYS-002 L3 style: no watchdog */
    mu_run(20);
}

/* boot with drv.pwr_sense_enable = 1 (+ drv.k1_check_enable = k1) saved in the NVM (both
 * reboot-required; defaults 0 since ICD v0.7, D-41 / SRS OI-18) */
static inline void mu_boot_sense(bool k1)
{
    h_boot(true);
    h_set_param(PID_DRV_PWR_SENSE_ENABLE, PARAM_T_BOOL, 1u);
    h_set_param(PID_DRV_K1_CHECK_ENABLE, PARAM_T_BOOL, k1 ? 1u : 0u);
    h_expect_ok(h_cmd(CMD_SAVE_PARAMS, 0x5Fu, NULL, 0u));
    fake_run_ms(2u);
    h_reboot();
    TEST_ASSERT_TRUE(g_fw.boot_p.drv.pwr_sense_enable);
    TEST_ASSERT_EQUAL(k1, g_fw.boot_p.drv.k1_check_enable);
    mu_raw = 0;
    mu_afe_on = true;
    h_set_param(PID_SAFETY_LINK_TIMEOUT_MS, PARAM_T_U16, 5000u);
    mu_run(20);
}

static inline void mu_enable(void)
{
    h_expect_ok(h_cmd(CMD_ENABLE, 0x60u, NULL, 0u));
    mu_run(g_fw.p.motion.ena_settle_ms + 2u);
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
}

static inline const fake_frame_t *mu_move(int32_t target_um, uint32_t v, uint32_t a)
{
    uint8_t pl[12];
    le_put32(pl, (uint32_t)target_um);
    le_put32(&pl[4], v);
    le_put32(&pl[8], a);
    return h_cmd(CMD_MOVE_ABS, 0x61u, pl, 12u);
}

static inline const fake_frame_t *mu_jog(int32_t v, uint32_t a, int32_t bound)
{
    uint8_t pl[12];
    le_put32(pl, (uint32_t)v);
    le_put32(&pl[4], a);
    le_put32(&pl[8], (uint32_t)bound);
    return h_cmd(CMD_JOG, 0x62u, pl, 12u);
}

static inline const fake_frame_t *mu_stop(uint8_t mode)
{
    uint8_t pl[1];
    pl[0] = mode;
    return h_cmd(CMD_STOP, 0x63u, pl, 1u);
}

/* run until the motion ended (MOVE_DONE) or max_ms */
static inline uint32_t mu_run_until_idle(uint32_t max_ms)
{
    uint32_t t;
    for (t = 0u; t < max_ms; t++) {
        mu_run(1u);
        if (!motion_active()) {
            break;
        }
    }
    return t;
}

static inline int32_t mu_ev(uint16_t code, uint32_t from)
{
    events_flush();
    events_flush();
    events_flush();
    return fake_cap_event(code, from);
}
static inline uint16_t mu_ev_arg(int32_t i) { return le_get16(&fake_cap[i].payload[6]); }
static inline int32_t mu_ev_val(int32_t i) { return (int32_t)le_get32(&fake_cap[i].payload[8]); }
static inline int32_t mu_ev_val2(int32_t i) { return (int32_t)le_get32(&fake_cap[i].payload[12]); }

static inline uint32_t mu_ev_count(uint16_t code, uint32_t from)
{
    uint32_t n = 0u;
    int32_t i = mu_ev(code, from);
    while (i >= 0) {
        n++;
        i = fake_cap_event(code, (uint32_t)i + 1u);
    }
    return n;
}

/* PUL intervals after rising edge `from` are non-decreasing (+-1 tick of carry) */
static inline bool mu_intervals_non_decreasing(uint32_t from)
{
    uint32_t k;
    uint64_t prev = 0u;
    for (k = from + 1u; k < fake_rise_n && k < FAKE_RISE_LOG; k++) {
        uint64_t d = fake_rise_tk[k] - fake_rise_tk[k - 1u];
        if (prev != 0u && d + 1u < prev) {
            return false;
        }
        prev = d;
    }
    return true;
}

#endif /* MOTION_UTIL_H */
