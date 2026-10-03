/* AFE core (FW_design §5.8): the sample callback (level 3, same path for the M1 synthetic source
 * and the M2 HX711), stale supervision in the tick, measured rate in the main loop,
 * reconfiguration hooks. M1 has no load limit / MOVE_UNTIL_LOAD (M2/M4).
 * Implements: FW-STR-002 (DATA built in the sample ISR), FW-TIM-001 (sample t_us = data-ready
 *             time from the HAL), FW-AFE-002 (gain/rate apply, settle flagging), FW-AFE-004,
 *             FW-STR-005 (stale -> fallback), SAF-FW-012 (stale detection; motion part M2),
 *             FW-NVM-003 (missed conversions -> OVERRUN)
 */
#include "fw.h"

#include "hal_hx711.h"
#include "hal_outputs.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "param_rules.h"

uint8_t afe_gain_pulses(uint8_t gain_channel)
{
    switch (gain_channel) {
    case AFE_GAIN_CHANNEL_B32: return 26u;
    case AFE_GAIN_CHANNEL_A64: return 27u;
    default:                   return 25u;      /* A128 */
    }
}

static void rearm(uint32_t now_us)
{
    g_fw.afe.stale_ref_us = now_us;              /* stale timer counts from the change */
    g_fw.afe.settle_left = g_fw.p.afe.settle_discard;
}

void afe_reconfigure(void)
{
    bool rate80 = g_fw.p.afe.rate_sps == (uint8_t)AFE_RATE_SPS_SPS80;
    uint32_t now = hal_time_us();
    CRIT_BEGIN(HAL_CRIT_DATA);
    g_fw.afe.period_us = 1000000u / param_rules_sps(g_fw.p.afe.rate_sps);
    rearm(now);
    g_fw.afe.have_sample = false;                /* no missed-conversion check across a change */
    CRIT_END();
    afe_rate_reset(&g_fw.afe.rate);
    hal_rate_pin(rate80);
    hal_hx711_config(afe_gain_pulses(g_fw.p.afe.gain_channel), rate80);
}

void afe_init(void)
{
    g_fw.afe.raw_last = PROTO_AFE_NO_DATA;
    g_fw.afe.stale = false;
    g_fw.afe.stale_reported = false;
    afe_reconfigure();
}

void on_afe_sample(const afe_sample_t *s)
{
    afe_state_t *a = &g_fw.afe;
    bool settling = false;
    bool sat = s->raw >= PROTO_RAW_MAX || s->raw <= PROTO_RAW_MIN;
    /* a conversion missed by the FW (NVM hold, late edge): delta > 1.5 periods -> OVERRUN */
    if (a->have_sample && (uint32_t)(s->t_us - a->last_t_us) > a->period_us + a->period_us / 2u) {
        g_fw.st.overrun = true;
    }
    if (a->settle_left != 0u) {
        a->settle_left = (uint8_t)(a->settle_left - 1u);
        settling = true;
    }
    a->last_t_us = s->t_us;
    a->stale_ref_us = s->t_us;
    a->raw_last = s->raw;
    a->saturated = sat;
    a->have_sample = true;
    a->stale = false;                            /* EVENT AFE_STALE(0) is sent by the tick */
    a->samples++;
    a->ring_t[a->ring_w] = s->t_us;
    a->ring_w = (uint8_t)((a->ring_w + 1u) % AFE_RATE_PERIODS);
    /* M2: load limit + MOVE_UNTIL_LOAD compare here, before the frame (FW_design §5.8) */
    if (g_fw.st.on) {
        stream_on_sample(s->t_us, s->raw, s->pos_steps, settling, sat);
    }
}

void afe_tick(uint32_t now_us)
{
    afe_state_t *a = &g_fw.afe;
    bool stale;
    if (g_fw.st.hold) {
        return;                                  /* NVM operation (idle only): no stale verdict */
    }
    CRIT_BEGIN(HAL_CRIT_DATA);                   /* the sample ISR clears `stale` */
    if (!a->stale && (uint32_t)(now_us - a->stale_ref_us) >= (uint32_t)g_fw.p.afe.timeout_ms * 1000u) {
        a->stale = true;
    }
    stale = a->stale;
    CRIT_END();
    if (stale != a->stale_reported) {
        a->stale_reported = stale;
        fw_event((uint16_t)EV_AFE_STALE, stale ? 1u : 0u, 0, 0);
    }
}

void afe_service(void)
{
    afe_state_t *a = &g_fw.afe;
    while (a->ring_r != a->ring_w) {
        afe_rate_push(&a->rate, a->ring_t[a->ring_r]);
        a->ring_r = (uint8_t)((a->ring_r + 1u) % AFE_RATE_PERIODS);
    }
    if (afe_rate_eval(&a->rate, param_rules_sps(g_fw.p.afe.rate_sps), g_fw.p.afe.rate_tol_pct)) {
        fw_event((uint16_t)EV_AFE_RATE_MISMATCH, a->rate.mismatch ? 1u : 0u,
                 (int32_t)afe_rate_dsps(&a->rate), 0);
    }
}

void afe_rearm_after_hold(void)
{
    uint32_t now = hal_time_us();
    CRIT_BEGIN(HAL_CRIT_DATA);
    g_fw.afe.stale_ref_us = now;                 /* last_t_us kept: the gap -> OVERRUN */
    CRIT_END();
}
