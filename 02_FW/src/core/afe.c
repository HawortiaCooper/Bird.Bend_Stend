/* AFE core (FW_design §5.8): the sample callback (level 3; HX711 on the target, load model in the
 * twin, synthetic source in the bring-up image), stale supervision and missed-edge recovery in the
 * tick, measured rate in the main loop, reconfiguration hooks.
 * Sample callback order (FW_design §5.8): FW load limit on EVERY sample in every state (stream on or
 * off; CLEAN halt in this ISR, LOAD_LIMIT latched before the DATA frame of the deciding sample is
 * built) -> MOVE_UNTIL_LOAD threshold (CLEAN halt in this ISR, FW-MOT-006) -> settle flag -> AFE
 * ring -> DATA frame. The tick folds the trip record into the EVENTs (FAULT_SET, STOPPED,
 * VALID_CLEARED) and stops the motion state.
 * Implements: SAF-FW-008, SAF-FW-009, SAF-FW-010 (next-sample effect), SAF-FW-011 (regrow),
 *             SAF-FW-012 (stale, AFE_FAULT while moving), FW-AFE-001...005 (core part: gain/rate
 *             apply, settle flagging, re-init, measured rate, data-ready timestamp + position from
 *             the HAL), FW-STR-002 (DATA built in the sample ISR), FW-STR-005, FW-NVM-003 (OVERRUN
 *             across the AFE hold), FW-TIM-001, FW-MOT-006 (per-sample threshold, load-path timing)
 */
#include "fw.h"

#include "hal_hx711.h"
#include "hal_outputs.h"
#include "hal_step.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "param_rules.h"
#include "units.h"

static void rearm(uint32_t now_us)
{
    g_fw.afe.stale_ref_us = now_us;              /* stale timer counts from the change */
    hx_settle_arm(&g_fw.afe.settle, g_fw.p.afe.settle_discard);
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
    hal_hx711_config(hx711_pulses(g_fw.p.afe.gain_channel), rate80);   /* next conversion (FW-AFE-002) */
}

void afe_loadlim_apply(void)
{
    const params_safety_t *s = &g_fw.p.safety;
    CRIT_BEGIN(HAL_CRIT_DATA);                   /* effective from the next sample (SAF-FW-010) */
    loadlim_config(&g_fw.afe.ll, s->load_raw_min, s->load_raw_max, s->load_trip_samples, s->load_regrow_raw);
    CRIT_END();
}

void afe_loadlim_cleared(void)
{
    CRIT_BEGIN(HAL_CRIT_DATA);
    loadlim_on_clear(&g_fw.afe.ll, (g_fw.afe.raw_last == PROTO_AFE_NO_DATA) ? 0 : g_fw.afe.raw_last,
                     g_fw.afe.saturated);
    CRIT_END();
}

void afe_init(void)
{
    const params_safety_t *s = &g_fw.p.safety;
    g_fw.afe.raw_last = PROTO_AFE_NO_DATA;
    g_fw.afe.stale = false;
    g_fw.afe.stale_reported = false;
    loadlim_init(&g_fw.afe.ll, s->load_raw_min, s->load_raw_max, s->load_trip_samples, s->load_regrow_raw);
    afe_reconfigure();                           /* settle armed: power-up (FW-AFE-003) */
}

/* level 3: the sample ISR (after the HAL latched t_us + position at data-ready entry, FW-AFE-005) */
void on_afe_sample(const afe_sample_t *s)
{
    afe_state_t *a = &g_fw.afe;
    bool settling, trip = false;
    bool sat = hx711_raw_at_rail(s->raw);
    /* 1. FW load limit: every sample, every state, stream on or off (SAF-FW-008/009) */
    if (loadlim_check(&a->ll, s->raw, sat)) {
        trip = true;
        (void)hal_step_stop_now();               /* CLEAN, <= 200 us after data-ready (SAF-FW-002) */
        if ((g_fw.lat.faults & FAULT_LOAD_LIMIT) == 0u) {
            g_fw.lat.faults = (uint16_t)(g_fw.lat.faults | FAULT_LOAD_LIMIT);   /* in this frame */
            if (!a->trip_rec) {
                a->trip_raw = s->raw;
                a->trip_t_us = s->t_us;
                a->trip_valid_was = valid_clear(&g_fw.valid, s->t_us);
                a->trip_rec = true;              /* FAULT_SET / STOPPED / VALID_CLEARED by the tick */
            }
        }
    }
    /* 1b. MOVE_UNTIL_LOAD: every sample while armed; the first one beyond raw_stop stops here
     *     (FW-MOT-006, SAF-FW-002 load path); a load-limit trip sample is the load limit's stop */
    motion_on_sample(s->raw, trip);
    /* 2. HAL status (seam v1.2 semantics, ICD Appendix B.17) */
    if ((s->status & AFES_SCK_OVERRUN) != 0u) {
        a->reinit_rec = true;                    /* power-down suspected: re-init in the tick */
    }
    if ((s->status & AFES_MISSED_EDGE) != 0u && g_fw.st.on) {
        g_fw.st.overrun = true;                  /* a conversion missed by the FW (ICD §7.6 bit 7) */
    }
    /* OVERRUN = conversions missed BY THE FW: across the FW's own AFE hold (NVM, DEF-M1-02/-04) */
    if (g_fw.st.hold_gap) {
        g_fw.st.hold_gap = false;
        if (g_fw.st.hold_stream_on && a->have_sample &&
            (uint32_t)(s->t_us - a->last_t_us) > a->period_us + a->period_us / 2u) {
            g_fw.st.overrun = true;
        }
    }
    settling = hx_settle_take(&a->settle);       /* still load-checked above (FW-AFE-003) */
    a->last_t_us = s->t_us;
    a->stale_ref_us = s->t_us;
    a->raw_last = s->raw;
    a->saturated = sat;
    a->have_sample = true;
    a->stale = false;                            /* EVENT AFE_STALE(0) is sent by the tick */
    a->samples++;
    a->ring_t[a->ring_w] = s->t_us;
    a->ring_w = (uint8_t)((a->ring_w + 1u) % AFE_RATE_PERIODS);
    if (g_fw.st.on) {
        stream_on_sample(s->t_us, s->raw, s->pos_steps, settling, sat);
    }
}

/* load-limit trip record (sample ISR) -> EVENTs + motion part, order FAULT_SET, STOPPED,
 * VALID_CLEARED; tick, or the thread under CRIT_TICK before a FAULT_CLEAR */
void afe_fold_trip(void)
{
    afe_state_t *a = &g_fw.afe;
    bool trip;
    int32_t traw = 0;
    bool tvalid = false;
    uint32_t tt = 0u;
    CRIT_BEGIN(HAL_CRIT_DATA);
    trip = a->trip_rec;
    if (trip) {
        traw = a->trip_raw;
        tvalid = a->trip_valid_was;
        tt = a->trip_t_us;
        a->trip_rec = false;
    }
    CRIT_END();
    if (trip) {
        int32_t p = hal_step_count();
        fw_event((uint16_t)EV_FAULT_SET, (uint16_t)FAULT_LOAD_LIMIT_BIT, traw, p);
        motion_stop((uint8_t)SC_LOAD_LIMIT, false, (uint8_t)MD_STOPPED);
        if (tvalid) {
            CRIT_BEGIN(HAL_CRIT_TICK);
            evq_push(&g_fw.evq, tt, (uint16_t)EV_VALID_CLEARED, (uint16_t)SC_LOAD_LIMIT, 0, 0);
            CRIT_END();
        }
    }
}

void afe_tick(uint32_t now_us)
{
    afe_state_t *a = &g_fw.afe;
    bool stale, reinit;
    afe_fold_trip();
    CRIT_BEGIN(HAL_CRIT_DATA);
    reinit = a->reinit_rec;
    a->reinit_rec = false;
    CRIT_END();
    if (reinit) {                                /* FW-AFE-003: re-init + settle + counter */
        a->reinit_count++;
        fw_event((uint16_t)EV_AFE_REINIT, a->reinit_count, 0, 0);
        hal_hx711_config(hx711_pulses(g_fw.p.afe.gain_channel), g_fw.p.afe.rate_sps == (uint8_t)AFE_RATE_SPS_SPS80);
        CRIT_BEGIN(HAL_CRIT_DATA);
        rearm(now_us);
        CRIT_END();
    }
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
    /* SAF-FW-012: stale while moving -> immediate stop + AFE_FAULT */
    if (stale && motion_active() && (g_fw.lat.faults & FAULT_AFE_FAULT) == 0u) {
        int32_t p = hal_step_count();
        (void)hal_step_stop_now();
        CRIT_BEGIN(HAL_CRIT_DATA);
        (void)latch_fault(&g_fw.lat, &g_fw.evq, (uint8_t)FAULT_AFE_FAULT_BIT,
                          units_steps_to_um(p, g_fw.p.motion.steps_per_mm), p, now_us);
        CRIT_END();
        motion_stop((uint8_t)SC_AFE_FAULT, false, (uint8_t)MD_STOPPED);
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_valid_clear(&g_fw.valid, &g_fw.evq, (uint16_t)SC_AFE_FAULT, now_us);
        CRIT_END();
    }
    /* missed-edge recovery (FW_design §4.3 step 9): no sample for 2 periods -> kick once per period */
    if (a->have_sample && (uint32_t)(now_us - a->last_t_us) > 2u * a->period_us &&
        (uint32_t)(now_us / 1000u - a->kick_ms) >= a->period_us / 1000u) {
        a->kick_ms = now_us / 1000u;
        hal_hx711_kick();
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
    g_fw.afe.stale_ref_us = now;                 /* no stale verdict from the hold itself */
    g_fw.st.hold_gap = true;                     /* last_t_us kept: the hold gap -> OVERRUN */
    CRIT_END();
}
