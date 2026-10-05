/* Inputs and supervision in the 1 kHz control tick (FW_design §5.2, §5.3, §5.7; ICD §6.2, §6.4):
 * E-stop sense, START / END limits, PAUSE button, ALM / PEND / DRV_PWR (polled), boot-time input
 * state, wiring faults, driver monitor (power loss, K1 weld), link watchdog, AFE stale while moving,
 * idle disable, the FAULT_CLEAR cause conditions. The input EXTI callbacks (levels 0/1, after the
 * HAL fixed reaction) only post records; the tick folds them in this order: E-stop -> driver monitor
 * -> limits -> PAUSE -> timeouts. There is no STOP-button input (D-36, CR-01).
 * Polarity: E-stop sense, limits and DRV_PWR are fixed (SAF-FW-007: open / high = active, DRV_PWR
 * low = powered, a broken wire reads active / unpowered); PAUSE, ALM and PEND follow their
 * parameters.
 * Implements: SAF-FW-005 (latch part, backup detection), SAF-FW-006 (closed time), SAF-FW-007,
 *             SAF-FW-013, SAF-FW-014, SAF-FW-015 (+ VALID clear in every state, D-47 a), SAF-FW-017,
 *             SAF-FW-018 (boot ENA / latches),
 *             SAF-FW-023 (button), SAF-FW-024, SAF-FW-025, SAF-FW-026 (ALM state), FW-SW-001...005,
 *             FW-CMD-003 (cause conditions), FW-STR-006 (EVENTs)
 */
#include <string.h>

#include "fw.h"

#include "hal_inputs.h"
#include "hal_step.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "units.h"

static int32_t um_of(int32_t steps) { return units_steps_to_um(steps, g_fw.p.motion.steps_per_mm); }

static bool level_of(uint16_t raw, uint8_t bit) { return ((raw >> bit) & 1u) != 0u; }

static bool active_of(uint16_t raw, uint8_t bit)
{
    bool lvl = level_of(raw, bit);
    switch (bit) {
    case IO_PAUSE_BTN_BIT:
        return (g_fw.p.io.pause_active_level == (uint8_t)IO_PAUSE_ACTIVE_LEVEL_CLOSED_ACTIVE) ? !lvl : lvl;
    case IO_ALM_BIT:
        return (g_fw.p.drv.alm_active_level == (uint8_t)DRV_ALM_ACTIVE_LEVEL_HIGH_ACTIVE) ? lvl : !lvl;
    case IO_PEND_BIT:
        return (g_fw.p.drv.pend_active_level == (uint8_t)DRV_PEND_ACTIVE_LEVEL_HIGH_ACTIVE) ? lvl : !lvl;
    case IO_DRV_PWR_BIT:
        return !lvl;                                   /* K1 aux closed = low = powered (fixed) */
    default:
        return lvl;                                    /* E-stop open / limit active = high (fixed) */
    }
}

bool safety_active(uint8_t io_bit) { return active_of(g_fw.in.raw, io_bit); }
uint32_t safety_estop_closed_ms(void) { return g_fw.in.estop_closed.inactive_ms; }
bool safety_drv_power(void) { return drvmon_power(&g_fw.in.drv); }

bool safety_loaded(void)
{
    int64_t d;
    if (g_fw.afe.stale || g_fw.afe.raw_last == PROTO_AFE_NO_DATA) {
        return true;                                   /* unknown load = loaded (ICD §5.4) */
    }
    d = (int64_t)g_fw.afe.raw_last - (int64_t)g_fw.p.safety.zero_raw;
    if (d < 0) {
        d = -d;
    }
    return d >= (int64_t)g_fw.p.safety.release_band_raw;
}

uint16_t safety_fault_causes(void)
{
    uint16_t m = 0u;
    if (g_fw.afe.stale || g_fw.afe.saturated) {
        m |= FAULT_AFE_FAULT;
    }
    if (safety_active((uint8_t)IO_LIMIT_START_BIT) && safety_active((uint8_t)IO_LIMIT_END_BIT)) {
        m |= FAULT_LIMIT_WIRING;
    }
    if (drvmon_k1_cause(&g_fw.in.drv, safety_active((uint8_t)IO_ESTOP_OPEN_BIT))) {
        m |= FAULT_K1_WELDED;
    }
    return m;
}

void safety_inputs_config(void)
{
    hal_in_cfg_t c;
    c.pause_active_level = g_fw.p.io.pause_active_level;
    c.alm_active_level = g_fw.p.drv.alm_active_level;
    hal_inputs_config(&c);
}

/* ------------------------------------------------------------------ EXTI callback (levels 0/1) */
void on_input_edge(uint8_t id, bool level, uint32_t t_us)
{
    in_rec_t *r;
    bool act;
    if (id > (uint8_t)IO_DRV_PWR_BIT) {
        return;
    }
    r = &g_fw.in.rec[id];
    act = active_of(level ? (uint16_t)(1u << id) : 0u, id);
    if (act && !r->act_edge) {
        r->t_us = t_us;
        r->steps = hal_step_count();                   /* edge capture (homing, LIMIT_SET) */
        r->was_running = cmd_is_moving(g_fw.motion_state);
        r->act_edge = true;
    }
    r->edge = true;
}

static in_rec_t take(uint8_t id)
{
    in_rec_t c;
    CRIT_BEGIN(HAL_CRIT_HALT);                         /* <= 0.2 us snapshot (FW_design §4.4) */
    c.edge = g_fw.in.rec[id].edge;
    c.act_edge = g_fw.in.rec[id].act_edge;
    c.t_us = g_fw.in.rec[id].t_us;
    c.steps = g_fw.in.rec[id].steps;
    c.was_running = g_fw.in.rec[id].was_running;
    g_fw.in.rec[id].edge = false;
    g_fw.in.rec[id].act_edge = false;
    CRIT_END();
    return c;
}

static void vclear(uint16_t cause, uint32_t t_us)
{
    CRIT_BEGIN(HAL_CRIT_DATA);
    latch_valid_clear(&g_fw.valid, &g_fw.evq, cause, t_us);
    CRIT_END();
}

static bool fault(uint8_t bit, int32_t v, int32_t v2, uint32_t t_us)
{
    bool n;
    CRIT_BEGIN(HAL_CRIT_DATA);
    n = latch_fault(&g_fw.lat, &g_fw.evq, bit, v, v2, t_us);
    CRIT_END();
    return n;
}

/* ------------------------------------------------------------------ boot (FW_design §3.2 step 5/6) */
void safety_init(void)
{
    uint16_t raw = hal_inputs_raw();
    uint32_t t = hal_time_us();
    bool estop_open, pwr;
    uint8_t i;
    memset(&g_fw.in, 0, sizeof g_fw.in);
    g_fw.in.raw = raw;
    safety_inputs_config();
    estop_open = active_of(raw, (uint8_t)IO_ESTOP_OPEN_BIT);
    pwr = active_of(raw, (uint8_t)IO_DRV_PWR_BIT);
    g_fw.in.estop_closed.inactive_ms = 0u;             /* closed time counts from boot */
    for (i = 0u; i < 2u; i++) {
        relf_init(&g_fw.in.lim_rel[i], active_of(raw, (uint8_t)(IO_LIMIT_START_BIT + i)));
    }
    btn_init(&g_fw.in.pause, active_of(raw, (uint8_t)IO_PAUSE_BTN_BIT));   /* no press at boot */
    drvmon_init(&g_fw.in.drv, g_fw.boot_p.drv.pwr_sense_enable, g_fw.boot_p.drv.k1_check_enable, pwr,
                active_of(raw, (uint8_t)IO_ALM_BIT));
    g_fw.in.pend = active_of(raw, (uint8_t)IO_PEND_BIT);
    /* ENA boot rule (ICD §6.4, OI-FW-22): disabled if the E-stop is open or (sense on and power off),
     * else left at the "no current" level = holding (D-13) */
    if (estop_open || (g_fw.boot_p.drv.pwr_sense_enable && !pwr)) {
        motion_ena_out(false);
    }
    if (estop_open) {
        (void)latch_estop(&g_fw.lat, &g_fw.evq, 0, hal_step_count(), t);    /* SAF-FW-007 */
    }
    if (active_of(raw, (uint8_t)IO_LIMIT_START_BIT) && active_of(raw, (uint8_t)IO_LIMIT_END_BIT)) {
        (void)latch_fault(&g_fw.lat, &g_fw.evq, (uint8_t)FAULT_LIMIT_WIRING_BIT, 0, 0, t);
    }
}

/* ------------------------------------------------------------------ reactions */
static void estop_trip(const in_rec_t *r, uint32_t t_us)
{
    int32_t p = hal_step_count();
    bool moving = motion_active();
    motion_estop_hw();                                 /* HAL did it at the edge; backup path too */
    {
        bool n;
        CRIT_BEGIN(HAL_CRIT_DATA);
        n = latch_estop(&g_fw.lat, &g_fw.evq, um_of(p), p, t_us);
        CRIT_END();
        (void)n;
    }
    if (moving) {
        if (r->was_running || r->act_edge == false) {
            g_fw.pos_uncertain = true;                 /* TRUNCATE may have cut a pulse (SAF-FW-004) */
        }
        motion_stop((uint8_t)SC_ESTOP, false, (uint8_t)MD_STOPPED);
    }
    vclear(SC_ESTOP, r->act_edge ? r->t_us : t_us);
    motion_disable((uint8_t)DD_ESTOP);                 /* NOT_ENABLED, HOMED cleared (SAF-FW-005 c) */
}

static void power_lost(uint32_t t_us)
{
    if (motion_active()) {
        motion_stop((uint8_t)SC_DRV_POWER_LOST, false, (uint8_t)MD_STOPPED);
    }
    vclear(SC_DRV_POWER_LOST, t_us);
    motion_disable((uint8_t)DD_DRV_POWER_LOST);       /* event only if not already NOT_ENABLED */
}

static void limit_hit(uint8_t lim, int32_t steps, uint32_t t_us)
{
    uint8_t sc = (lim == (uint8_t)LIM_START) ? (uint8_t)SC_LIMIT_START : (uint8_t)SC_LIMIT_END;
    bool homing = motion_home_edge(lim, steps);
    bool moving = motion_active();
    bool n;
    if (homing && lim == (uint8_t)LIM_START) {
        return;                                        /* expected homing edge: no LIMIT latch */
    }
    CRIT_BEGIN(HAL_CRIT_DATA);
    n = latch_limit(&g_fw.lat, &g_fw.evq, lim, um_of(steps), steps, t_us);
    CRIT_END();
    (void)n;
    if (moving && !homing) {
        motion_stop(sc, false, (uint8_t)MD_STOPPED);   /* HAL stopped at the edge (<= 200 us) */
        vclear(sc, t_us);
    }
}

static void pause_press(uint32_t t_us)
{
    uint8_t r;
    fw_event((uint16_t)EV_PAUSE_BUTTON, 1u, 0, 0);
    CRIT_BEGIN(HAL_CRIT_DATA);
    r = latch_pause(&g_fw.lat, &g_fw.evq, (uint8_t)SRC_BUTTON, t_us);
    CRIT_END();
    if (r == LATCH_PAUSE_NEW) {
        motion_stop((uint8_t)SC_PAUSE_BUTTON, true, (uint8_t)MD_STOPPED);   /* controlled (SAF-FW-023) */
        vclear(SC_PAUSE_BUTTON, t_us);
    }
}

/* ------------------------------------------------------------------ tick */
void safety_tick(uint32_t now_ms, uint32_t now_us)
{
    uint16_t raw = hal_inputs_raw();
    uint8_t rel = g_fw.p.io.release_ms;
    in_rec_t r;
    bool estop_open, a;
    uint8_t i;
    g_fw.in.raw = raw;

    /* 1. E-stop sense (SAF-FW-005/006) */
    r = take((uint8_t)IO_ESTOP_OPEN_BIT);
    estop_open = active_of(raw, (uint8_t)IO_ESTOP_OPEN_BIT);
    if (r.act_edge || (estop_open && (!g_fw.lat.estop || g_fw.ena_on || motion_active()))) {
        estop_trip(&r, now_us);
    }
    (void)relf_sample(&g_fw.in.estop_closed, estop_open, r.edge, 0xFFFFu);

    /* 2. driver monitor (SAF-FW-024/025/026, FW-SW-004/005) */
    {
        drvmon_ev_t e = drvmon_sample(&g_fw.in.drv, active_of(raw, (uint8_t)IO_DRV_PWR_BIT), estop_open,
                                      active_of(raw, (uint8_t)IO_ALM_BIT), g_fw.p.drv.k1_weld_ms, rel);
        if (e.pwr_changed) {
            fw_event((uint16_t)EV_DRIVER_POWER, g_fw.in.drv.pwr ? 1u : 0u, 0, 0);
        }
        if (e.pwr_lost) {
            power_lost(now_us);
        }
        if (e.pwr_returned) {
            motion_power_returned(now_ms);
        }
        if (e.alm_changed) {
            fw_event((uint16_t)EV_ALM_CHANGED, drvmon_alm(&g_fw.in.drv) ? 1u : 0u, 0, 0);
        }
        if (e.k1_fault) {
            (void)fault((uint8_t)FAULT_K1_WELDED_BIT, (int32_t)g_fw.in.drv.k1_ms, 0, now_us);
        }
    }
    g_fw.in.pend = active_of(raw, (uint8_t)IO_PEND_BIT);

    /* 3. limits (SAF-FW-013/014, FW-SW-001) */
    for (i = 0u; i < 2u; i++) {
        uint8_t bit = (uint8_t)(IO_LIMIT_START_BIT + i);
        uint8_t lim = (i == 0u) ? (uint8_t)LIM_START : (uint8_t)LIM_END;
        int8_t toward = (i == 0u) ? -1 : 1;
        bool latched = (i == 0u) ? g_fw.lat.limit_start : g_fw.lat.limit_end;
        r = take(bit);
        a = active_of(raw, bit);
        if (r.act_edge) {
            limit_hit(lim, r.steps, r.t_us);
        } else if (a && !latched && motion_dir() == toward) {
            (void)hal_step_stop_now();                 /* backup: edge missed (line masked) */
            limit_hit(lim, hal_step_count(), now_us);
        }
        if (relf_sample(&g_fw.in.lim_rel[i], a, r.edge, rel)) {
            CRIT_BEGIN(HAL_CRIT_DATA);
            latch_limit_clear(&g_fw.lat, &g_fw.evq, lim, now_us);   /* released >= io.release_ms (D-33 h) */
            CRIT_END();
            hal_inputs_rearm(bit);
        }
    }
    if (safety_active((uint8_t)IO_LIMIT_START_BIT) && safety_active((uint8_t)IO_LIMIT_END_BIT) &&
        (g_fw.lat.faults & FAULT_LIMIT_WIRING) == 0u) {
        int32_t p = hal_step_count();
        (void)fault((uint8_t)FAULT_LIMIT_WIRING_BIT, um_of(p), p, now_us);
        if (motion_active()) {
            motion_stop((uint8_t)SC_LIMIT_WIRING, false, (uint8_t)MD_STOPPED);
            vclear(SC_LIMIT_WIRING, now_us);
        }
    }

    /* 4. PAUSE button (SAF-FW-023, FW-SW-003) */
    r = take((uint8_t)IO_PAUSE_BTN_BIT);
    a = active_of(raw, (uint8_t)IO_PAUSE_BTN_BIT);
    if (r.act_edge && btn_edge(&g_fw.in.pause) == BTN_PRESSED) {
        pause_press(r.t_us);
    }
    switch (btn_sample(&g_fw.in.pause, a, r.edge, rel)) {
    case BTN_PRESSED:
        pause_press(now_us);                           /* backup: edge missed */
        break;
    case BTN_RELEASED:
        fw_event((uint16_t)EV_PAUSE_BUTTON, 0u, 0, 0);
        hal_inputs_rearm((uint8_t)IO_PAUSE_BTN_BIT);
        break;
    default:
        break;
    }

    /* 5. link watchdog (SAF-FW-015) */
    if (g_fw.in.link_wdg && g_fw.cmd_rx_count != g_fw.in.link_wdg_rx) {
        g_fw.in.link_wdg = false;                      /* next valid command frame */
        fw_event((uint16_t)EV_LINK_RESTORED, 0u, 0, 0);
    }
    if (!g_fw.in.link_wdg && cmd_is_moving(g_fw.motion_state) &&
        (uint32_t)(now_ms - g_fw.last_cmd_rx_ms) >= g_fw.p.safety.link_timeout_ms) {
        g_fw.in.link_wdg = true;
        g_fw.in.link_wdg_rx = g_fw.cmd_rx_count;
        fw_event((uint16_t)EV_LINK_WDG, 0u, 0, 0);
        motion_stop((uint8_t)SC_LINK_WDG, true, (uint8_t)MD_STOPPED);
        vclear(SC_LINK_WDG, now_us);
    }
    /* D-47 a: link silence clears VALID in EVERY motion state (idle, capture, NOT_ENABLED), reported
     * once by VALID_CLEARED (arg LINK_WDG) on the 1 -> 0 change; the controlled stop, the LINK_WDG
     * status and its EVENTs stay "only while moving" (above) */
    if ((uint32_t)(now_ms - g_fw.last_cmd_rx_ms) >= g_fw.p.safety.link_timeout_ms) {
        vclear(SC_LINK_WDG, now_us);                   /* no-op while VALID is already 0 */
    }

    /* 6. idle disable (SAF-FW-017; not while the AFE is stale, D-33 g) */
    if (g_fw.p.safety.idle_disable_s != 0u && g_fw.motion_state == (uint8_t)MS_IDLE && !g_fw.afe.stale &&
        !safety_loaded()) {
        g_fw.in.idle_counting = true;
        if (++g_fw.in.idle_ms >= (uint32_t)g_fw.p.safety.idle_disable_s * 1000u) {
            g_fw.in.idle_ms = 0u;
            g_fw.in.idle_counting = false;
            motion_disable((uint8_t)DD_IDLE);
        }
    } else {
        g_fw.in.idle_ms = 0u;
        g_fw.in.idle_counting = false;
    }
}

uint16_t safety_idle_left_s(void)
{
    uint32_t lim = (uint32_t)g_fw.p.safety.idle_disable_s * 1000u;
    uint32_t left;
    if (!g_fw.in.idle_counting || lim == 0u || g_fw.in.idle_ms >= lim) {
        return 0xFFFFu;
    }
    left = (lim - g_fw.in.idle_ms + 999u) / 1000u;
    return (left >= 0xFFFFu) ? 0xFFFEu : (uint16_t)left;
}
