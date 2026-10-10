/* Motion executor (FW_design §5.4 - §5.6; ICD §5.4, §5.5, §6.1, §6.5): ENABLE / DISABLE with the
 * ENA settle, MOVE_ABS, JOG (dead-man, end point, on-the-fly speed / bound change, reversal through
 * standstill), HOME (START only, pure homing.c), immediate (CLEAN) and controlled stops (ISR /
 * stretch / clean-halt paths, D-29 d / D-30, OI-ICD-07), the step ISR callback, motion completion
 * with exactly one MOVE_DONE per motion, NOT_SETTLED.
 *
 * Contexts: the thread calls the motion_* entry points under CRIT_TICK (cmd.c), the tick calls them
 * directly; step_isr() runs at level 2 and owns the ramp while the timer runs (changes from the
 * thread / tick under CRIT_MOTION). Every segment is an absolute end point in steps; the timer
 * counts in the HAL (hal_step_count) and is stopped in hardware by OPM at the last pulse, so a
 * segment is complete when hal_step_running() turns false (seen by the tick <= 1 ms later).
 *
 * DIR polarity (motion.dir_invert): the seam has no field for it; hal_step_set_dir() receives the
 * logical direction +-1, or +-2 when the DIR output must be inverted (the HAL counts by the sign) -
 * interim encoding, seam request SR-M2-01 to the Integrator (FW_design §9.7).
 * MOVE_UNTIL_LOAD (FW-MOT-006, D-44: M4 scope pulled forward): one segment from the position to the
 * absolute bound; the threshold compare is armed in the same CRIT_MOTION section that starts the timer
 * (after a last pre-check of the newest sample), decided per sample in the sample ISR
 * (motion_on_sample(), level 3: CLEAN halt + hit record, like the FW load limit) and folded into
 * MOVE_DONE LOAD_THRESHOLD by the tick (or by the next motion_stop()); the bound is a planned stop
 * (MOVE_DONE BOUND). LOAD_THRESHOLD is not a stop source: no STOPPED, VALID unchanged (ICD §5.4, §6.2).
 * Implements: FW-MOT-001 (start / DIR setup / PW via the HAL), FW-MOT-002, FW-MOT-003, FW-MOT-004,
 *             FW-MOT-005, FW-MOT-006, FW-MOT-007 (motion part), FW-MOT-008, FW-HOM-001, FW-HOM-002,
 *             FW-HOM-004,
 *             SAF-FW-001 (discard target, MOVE_DONE), SAF-FW-002 (CLEAN stops), SAF-FW-003
 *             (controlled-stop paths), SAF-FW-004 (step fault, POS_UNCERTAIN), SAF-FW-016 (dead-man),
 *             SAF-FW-017 (disable part), SAF-FW-024 (settle after power return), FW-SW-004 (NOT_SETTLED)
 */
#include <string.h>

#include "fw.h"

#include "hal_step.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "homing.h"
#include "mul.h"
#include "ramp.h"
#include "stepgen.h"
#include "units.h"

#define F_TICK_NOMINAL 90000000u   /* provisional for the first hal_step_init() (it returns f) */
#define UNEXPLAINED_STOP_TICKS 3u  /* timer stopped short of the end with no recorded cause */
#define RAMP_SYNC_SPIN 2000u       /* wait_step_isr() bound (> one 10 us step period at the 100 kHz cap;
                                      races are lost only at short periods), ends when the timer stops */

typedef struct {
    /* configuration derived from the parameters (idle only) */
    uint32_t f;                    /* timer ticks per second (hal_step_init) */
    uint32_t pw, dir_setup;        /* ticks */
    float    chw;                  /* hardware minimum period, ticks */
    uint32_t cfg_pw_ns, cfg_dir_us;
    /* motion */
    bool     active;               /* motion state 3..7 */
    bool     running;              /* the timer was started for the current segment */
    bool     parked;               /* start parked by the sniffed-stop hold */
    uint8_t  kind;                 /* MS_MOVE_ABS / MS_JOG / MS_HOMING */
    int8_t   dir;
    int32_t  start;                /* step count at the segment start */
    int32_t  target_um;            /* STATUS target_um (end point) */
    uint8_t  end_md;               /* MOVE_DONE reason of a natural end */
    bool     stopping;             /* a stop was requested (STOPPED sent if a cause was given) */
    bool     ctrl;                 /* ... as a controlled stop */
    uint8_t  stop_md;
    bool     last_armed;           /* the running period is the last one (OPM armed) */
    uint32_t gate_gen;             /* hal_step_stop_gen() before the gate check (FWR-01) */
    uint8_t  unexplained;
    ramp_t   ramp;
    uint32_t v_um_s, a_um_s2;      /* current segment speed / accel (jog refresh compares) */
    volatile int32_t isr_count;    /* step count seen by the last step_isr() */
    volatile bool    step_fault;   /* step_isr -> tick */
    /* jog */
    uint32_t jog_ms;
    int32_t  unhomed_origin;       /* D-43 b: un-homed travel window origin (steps), latched when the
                                      axis becomes un-homed */
    bool     jog_bounded;
    bool     reversing;            /* decelerating to standstill for a reversal */
    int32_t  rev_v;
    uint32_t rev_a;
    int32_t  rev_bound;
    /* homing */
    uint8_t  hphase;
    int32_t  horigin;
    bool     hreleased;
    bool     hstart_edge;
    int32_t  hedge_steps;
    bool     hend_edge;
    bool     was_homed;
    int32_t  hdrift_um;            /* EVENT HOMED value */
    /* enable */
    uint32_t settle_until_ms;
    uint32_t t_pwr_ms;
    bool     pwr_seen;
    /* NOT_SETTLED */
    bool     pend_wait;
    uint32_t pend_t0_ms;
} motion_t;

static motion_t M;

/* MOVE_UNTIL_LOAD compare, shared with the sample ISR (level 3): configured by the thread before the
 * start, armed inside the CRIT_MOTION section that starts the timer (masks level 3), disarmed in
 * finish(); `hit` is written by the sample ISR (or by the start pre-check under CRIT_MOTION) and
 * cleared by the thread before a new start */
typedef struct {
    volatile bool    armed;
    volatile bool    hit;
    int32_t          raw_stop;
    uint8_t          cmp;
    volatile int32_t hit_raw;            /* deciding sample (diagnostics) */
} mul_isr_t;

static mul_isr_t MUL;

/* ------------------------------------------------------------------ helpers */
static int32_t pos_now(void) { return hal_step_count(); }

/* D-43 b (ICD v0.7 §5.4): every event that clears HOMED (boot, ENABLE, DISABLE / E-stop / idle /
 * driver power, STEP_FAULT, homing failure) latches the current position as the un-homed origin; a
 * jog, a stop or a jog restart never re-latches it */
static void unhome(void)
{
    g_fw.homed = false;
    M.unhomed_origin = pos_now();
}
static int32_t um_of(int32_t steps) { return units_steps_to_um(steps, g_fw.p.motion.steps_per_mm); }
static int32_t steps_of(int32_t um) { return units_um_to_steps(um, g_fw.p.motion.steps_per_mm); }

static void ev(uint16_t code, uint16_t arg, int32_t v, int32_t v2) { fw_event(code, arg, v, v2); }

static uint32_t ticks_of_ns(uint32_t ns)
{
    return (uint32_t)(((uint64_t)ns * M.f + 500000000ull) / 1000000000ull);
}

static void apply_step_cfg(bool force)
{
    const params_motion_t *pm = &g_fw.p.motion;
    hal_step_cfg_t c;
    uint32_t lo, hw_rate, hw_pl;
    if (!force && pm->pulse_high_ns == M.cfg_pw_ns && pm->dir_setup_us == M.cfg_dir_us) {
        goto derived;
    }
    M.cfg_pw_ns = pm->pulse_high_ns;
    M.cfg_dir_us = pm->dir_setup_us;
    M.pw = ticks_of_ns(pm->pulse_high_ns);
    M.dir_setup = (uint32_t)(((uint64_t)pm->dir_setup_us * M.f) / 1000000ull);
    c.pw_ticks = M.pw;
    c.dir_setup_ticks = M.dir_setup;
    c.pul_invert = g_fw.boot_p.motion.pul_invert;      /* reboot_required (DEF-M1-01) */
    c.ena_invert = g_fw.boot_p.motion.ena_invert;
    (void)hal_step_init(&c);
derived:
    lo = ticks_of_ns(pm->pulse_low_min_ns);
    hw_rate = (pm->max_step_rate_hz > 0u) ? (M.f + pm->max_step_rate_hz - 1u) / pm->max_step_rate_hz : M.f;
    hw_pl = M.pw + lo;
    M.chw = (float)((hw_rate > hw_pl) ? hw_rate : hw_pl);
}

void motion_ena_out(bool enabled)
{
    hal_ena_set(enabled);
    g_fw.ena_on = enabled;
}

void motion_init(void)
{
    hal_step_cfg_t c;
    memset(&M, 0, sizeof M);
    c.pw_ticks = 0u;
    c.dir_setup_ticks = 0u;
    c.pul_invert = g_fw.boot_p.motion.pul_invert;
    c.ena_invert = g_fw.boot_p.motion.ena_invert;
    M.f = F_TICK_NOMINAL;
    M.f = hal_step_init(&c);                           /* f from the bus clocks (FW-PLT-002) */
    if (M.f == 0u) {
        M.f = F_TICK_NOMINAL;
    }
    apply_step_cfg(true);
    M.isr_count = pos_now();
    unhome();                                          /* boot: un-homed origin = 0 (D-43 b) */
    g_fw.ena_on = true;                                /* reset level: no LED current = holding (D-13) */
}

bool motion_active(void) { return M.active; }
bool motion_stopping(void) { return M.active && M.stopping; }

/* FWR-01 (SAF-FW-002 / SAF-FW-005 a): the stop generation is captured BEFORE the decision to start
 * (cmd_execute(): before the command check; motion_tick(): right after reading the timer state and
 * before the segment-end decision, FWR-20 - a planned end or halt seen as "stopped" is accounted; a halt
 * whose record is not folded yet is deferred by safety_edges_pending(), FWR-18; a later halt bumps the
 * generation and hw_start() refuses the next segment, FWR-17).
 * Every core-initiated halt in a tick puts the motion into "stopping", so no segment of that tick is
 * started after it. A fixed reaction (E-stop / limit / load limit) after that
 * point bumps the generation even while the timer is idle, and hw_start() then stops the timer it
 * has just armed - before the first PUL edge. */
void motion_gate_capture(void) { M.gate_gen = hal_step_stop_gen(); }
int8_t motion_dir(void) { return (M.active && M.running) ? M.dir : 0; }

int32_t motion_unhomed_origin_um(void) { return um_of(M.unhomed_origin); }

int32_t motion_target_um(void)
{
    return M.active ? M.target_um : um_of(pos_now());
}

uint16_t motion_enabling_left_ms(uint32_t now_ms)
{
    int32_t d;
    if (g_fw.motion_state != (uint8_t)MS_ENABLING) {
        return 0u;
    }
    d = (int32_t)(M.settle_until_ms - now_ms);
    return (d <= 0) ? 0u : (uint16_t)((d > 0xFFFF) ? 0xFFFF : d);
}

/* ------------------------------------------------------------------ enable / disable */
static void settle_deadline(uint32_t now_ms)
{
    uint32_t base = now_ms;
    if (g_fw.boot_p.drv.pwr_sense_enable && M.pwr_seen && (int32_t)(M.t_pwr_ms - base) > 0) {
        base = M.t_pwr_ms;
    }
    M.settle_until_ms = base + g_fw.p.motion.ena_settle_ms;
}

uint16_t motion_enable(uint32_t now_ms)
{
    if (g_fw.motion_state == (uint8_t)MS_NOT_ENABLED) {
        if (!g_fw.homed) {
            unhome();                                  /* ENABLE (after power-up): D-43 b */
        }
        motion_ena_out(true);
        g_fw.motion_state = (uint8_t)MS_ENABLING;
        settle_deadline(now_ms);
        if (motion_enabling_left_ms(now_ms) == 0u) {
            g_fw.motion_state = (uint8_t)MS_IDLE;      /* settle 0: enabled at once */
            ev((uint16_t)EV_DRIVER_ENABLED, 0u, 0, 0);
        }
    }
    return motion_enabling_left_ms(now_ms);            /* 0 if already enabled (no-op) */
}

void motion_power_returned(uint32_t now_ms)
{
    M.t_pwr_ms = now_ms;                               /* settle counts from the later of both */
    M.pwr_seen = true;
    if (g_fw.motion_state == (uint8_t)MS_ENABLING) {
        uint32_t keep = M.settle_until_ms;
        settle_deadline(now_ms);
        if ((int32_t)(keep - M.settle_until_ms) > 0) {
            M.settle_until_ms = keep;
        }
    }
}

void motion_disable(uint8_t dd_cause)
{
    /* DRIVER_DISABLED only if the state was not already NOT_ENABLED (ICD §6.2); a PC DISABLE that
     * releases the boot-time holding level (D-13) is reported as well */
    bool changed = g_fw.motion_state != (uint8_t)MS_NOT_ENABLED ||
                   (dd_cause == (uint8_t)DD_PC_DISABLE && g_fw.ena_on);
    motion_ena_out(false);
    g_fw.motion_state = (uint8_t)MS_NOT_ENABLED;      /* a running motion (already stopped by the
                                                          caller) completes as NOT_ENABLED */
    unhome();                                          /* position not held by the driver (D-11) */
    M.pend_wait = false;
    if (changed) {
        ev((uint16_t)EV_DRIVER_DISABLED, dd_cause, 0, 0);
    }
}

void motion_estop_hw(void)
{
    (void)hal_step_abort();                            /* TRUNCATE (SAF-FW-005 a) */
    motion_ena_out(false);                             /* SAF-FW-005 b */
}

/* ------------------------------------------------------------------ segments */
static double sps(uint32_t um) { return ramp_steps_of(um, g_fw.p.motion.steps_per_mm); }
static uint32_t acc_or_max(uint32_t a) { return (a == 0u) ? g_fw.p.motion.a_max_um_s2 : a; }

static void hw_start(void)
{
    uint32_t gen = M.gate_gen;                         /* captured before the gate check (FWR-01) */
    uint32_t c1, c2 = 0u;
    bool mul = M.kind == (uint8_t)MS_MOVE_UNTIL_LOAD;
    int dir_hw = g_fw.p.motion.dir_invert ? 2 * M.dir : M.dir;
    hal_step_set_dir(dir_hw);                          /* only while stopped (FW-MOT-001) */
    c1 = ramp_next(&M.ramp);
    if (M.ramp.rem != 0u) {
        c2 = ramp_next(&M.ramp);
    }
    CRIT_BEGIN(HAL_CRIT_MOTION);                       /* masks the sample ISR (level 3) as well */
    /* FW-MOT-006: the newest sample before the first pulse decides; no sample can slip between this
     * pre-check and the armed compare (both happen inside this section) */
    if (mul && mul_precheck(g_fw.afe.raw_last != PROTO_AFE_NO_DATA, g_fw.afe.raw_last, MUL.raw_stop, MUL.cmp)) {
        MUL.hit = true;                                /* already beyond: no pulse */
        MUL.hit_raw = g_fw.afe.raw_last;
    } else {
        MUL.armed = mul;
        M.isr_count = pos_now();
        M.last_armed = false;
        M.running = true;
        hal_step_start(c1);                            /* atomic arm; first edge >= dir_setup after it */
        if (hal_step_stop_gen() != gen) {
            /* start-then-recheck (FW_design §5.3): a fixed reaction since the gate check; CNT is
             * still far below the first compare, so this CLEAN halt emits no edge (FWR-01/02) */
            (void)hal_step_stop_now();
        } else if (c2 == 0u) {
            hal_step_arm_last();
            M.last_armed = true;
        } else {
            hal_step_set_period(c2);
        }
    }
    CRIT_END();
}

/* ---- ramp edits (NFR-007): computed on a copy outside CRIT_MOTION (double math, VSQRT), committed
 * under CRIT_MOTION (struct copy + at most two HAL register writes, <= ~0.5 us) only if the step ISR
 * did not advance the ramp in between; after 4 lost races the edit is done inside the section ---- */
#define RE_JOG          0u
#define RE_TOTAL        1u
#define RE_STOP_ISR     2u
#define RE_STOP_STRETCH 3u
typedef struct {
    uint8_t  kind;
    bool     set_speed;
    double   v, a, d;            /* steps/s, steps/s^2 */
    uint32_t total;
    float    floor_c;
    uint32_t c1, c2;             /* RE_STOP_STRETCH results */
    ramp_speed_k_t k;            /* position-independent constants, computed once (FWR-10) */
    float    cd;
} redit_t;

/* FWR-10: the soft-double constants (ramp_c_of: sqrt + div) do not depend on the ramp position;
 * they are computed once before the commit attempts, so neither an attempt nor the in-section
 * fallback evaluates them (f and chw are fixed for the move; the step ISR does not write them) */
static void redit_prep(redit_t *e)
{
    if (e->kind == RE_JOG && e->set_speed) {
        ramp_speed_k(&M.ramp, e->v, e->a, e->d, &e->k);
    }
    if (e->kind == RE_STOP_ISR || e->kind == RE_STOP_STRETCH) {
        e->cd = ramp_c_of(M.ramp.f, e->a);
    }
}

static void redit_apply(ramp_t *r, redit_t *e)
{
    switch (e->kind) {
    case RE_JOG:
        if (e->set_speed) {
            ramp_set_speed_k(r, &e->k, e->d);                /* on the fly (FW-MOT-005) */
        }
        ramp_set_total(r, e->total);
        break;
    case RE_TOTAL:
        ramp_set_total(r, e->total);
        break;
    case RE_STOP_ISR:
        (void)ramp_stop_k(r, e->a, e->cd, 1u, e->floor_c);    /* next ISR preloads c_dec1 */
        break;
    default:
        /* stretch (OI-ICD-07): the running and the preloaded period are regenerated, extend-only */
        r->gen = (r->gen >= 2u) ? r->gen - 2u : 0u;
        r->rem += 2u;
        (void)ramp_stop_k(r, e->a, e->cd, 0u, e->floor_c);
        e->c1 = ramp_next(r);
        e->c2 = (r->rem != 0u) ? ramp_next(r) : 0u;
        break;
    }
}

static void redit_hw(const redit_t *e)       /* inside CRIT_MOTION */
{
    if (e->kind == RE_STOP_STRETCH) {
        hal_step_set_period_now(e->c1);
        if (e->c2 != 0u) {
            hal_step_set_period(e->c2);
        } else {
            hal_step_arm_last();
            M.last_armed = true;
        }
    }
}

/* wait (bounded) until the step ISR has advanced the ramp past generation g: the next copy then
 * starts right after an update and has a whole step period for the edit (FWR-10) */
static void wait_step_isr(uint32_t g)
{
    const volatile uint32_t *gen = &M.ramp.gen;
    uint32_t n;
    for (n = 0u; n < RAMP_SYNC_SPIN && *gen == g && hal_step_running(); n++) {
    }
}

static void ramp_commit(redit_t *e)
{
    uint8_t k;
    bool done = false;
    uint32_t g, g_lost = 0u;
    redit_prep(e);
    for (k = 0u; k < 4u && !done; k++) {
        ramp_t c;
        bool la;
        if (k != 0u) {
            wait_step_isr(g_lost);                    /* lost a race: start right after the next ISR */
        }
        CRIT_BEGIN(HAL_CRIT_MOTION);
        c = M.ramp;
        la = M.last_armed;
        CRIT_END();
        g = c.gen;
        if (e->kind == RE_STOP_STRETCH && la) {
            return;                                           /* the running pulse is the last one */
        }
        redit_apply(&c, e);
        CRIT_BEGIN(HAL_CRIT_MOTION);
        if (M.ramp.gen == g && M.last_armed == la) {
            M.ramp = c;
            redit_hw(e);
            done = true;
        } else {
            g_lost = M.ramp.gen;
        }
        CRIT_END();
    }
    if (!done) {                                       /* rare: position-dependent part only */
        CRIT_BEGIN(HAL_CRIT_MOTION);
        if (!(e->kind == RE_STOP_STRETCH && M.last_armed)) {
            redit_apply(&M.ramp, e);
            redit_hw(e);
        }
        CRIT_END();
    }
}

/* start a segment of n steps in dir; returns false if there is nothing to move (n == 0) */
static bool seg_start(int8_t dir, uint32_t n, uint32_t v_um_s, uint32_t a_um_s2)
{
    if (n == 0u) {
        return false;
    }
    apply_step_cfg(false);
    M.dir = dir;
    M.start = pos_now();
    M.stopping = false;
    M.ctrl = false;
    M.unexplained = 0u;
    M.parked = false;
    M.running = false;
    M.step_fault = false;
    M.v_um_s = v_um_s;
    M.a_um_s2 = a_um_s2;
    ramp_start(&M.ramp, (double)M.f, sps(a_um_s2), sps(a_um_s2), sps(v_um_s), M.chw, n);
    M.pend_wait = false;
    if (!link_motion_start_allowed()) {
        M.parked = true;                               /* sniffed-stop hold (FW_design §5.9.3) */
        return true;
    }
    hw_start();
    return true;
}

static uint32_t span(int32_t from, int32_t to, int8_t dir)
{
    int64_t d = ((int64_t)to - (int64_t)from) * (int64_t)dir;
    return (d <= 0) ? 0u : (d > 0x7FFFFFFF ? 0x7FFFFFFFu : (uint32_t)d);
}

static void finish(uint8_t md)
{
    int32_t p = pos_now();
    MUL.armed = false;                                 /* no compare outside a running MOVE_UNTIL_LOAD */
    M.active = false;
    M.running = false;
    M.parked = false;
    M.reversing = false;
    if (cmd_is_moving(g_fw.motion_state)) {
        g_fw.motion_state = (uint8_t)MS_IDLE;          /* else already NOT_ENABLED (E-stop, power) */
    }
    ev((uint16_t)EV_MOVE_DONE, md, um_of(p), p);       /* exactly one per motion (ICD §5.4) */
    if (g_fw.motion_state == (uint8_t)MS_IDLE && g_fw.p.drv.pend_timeout_ms != 0u) {
        M.pend_wait = true;
        M.pend_t0_ms = hal_time_ms();
    }
}

/* ------------------------------------------------------------------ commands */
void motion_move_abs(int32_t target_um, uint32_t v_um_s, uint32_t a_um_s2)
{
    int32_t tgt = steps_of(target_um);
    int32_t p = pos_now();
    int8_t dir = (tgt >= p) ? 1 : -1;
    M.kind = (uint8_t)MS_MOVE_ABS;
    M.target_um = target_um;
    M.end_md = (uint8_t)MD_TARGET;
    if (!seg_start(dir, span(p, tgt, dir), v_um_s, acc_or_max(a_um_s2))) {
        ev((uint16_t)EV_MOVE_DONE, (uint16_t)MD_TARGET, um_of(p), p);   /* at the target: no pulse */
        return;
    }
    M.active = true;
    g_fw.motion_state = (uint8_t)MS_MOVE_ABS;
}

/* FW-MOT-006 (ICD §5.4): accepted by cmd_check (HOMED, bound inside the soft limits and != position,
 * v <= v_limit with v_max_load, raw_stop / cmp in range, BLOCK mask incl. LIMIT toward the switch) */
void motion_move_until_load(int32_t bound_um, uint32_t v_um_s, uint32_t a_um_s2, int32_t raw_stop, uint8_t cmp)
{
    int32_t b = steps_of(bound_um);
    int32_t p = pos_now();
    int8_t dir = mul_dir(b, p);
    CRIT_BEGIN(HAL_CRIT_DATA);                         /* the sample ISR reads MUL */
    MUL.armed = false;
    MUL.hit = false;
    MUL.raw_stop = raw_stop;
    MUL.cmp = cmp;
    CRIT_END();
    M.kind = (uint8_t)MS_MOVE_UNTIL_LOAD;
    M.target_um = bound_um;
    M.end_md = (uint8_t)MD_BOUND;
    if (dir == 0 || !seg_start(dir, span(p, b, dir), v_um_s, acc_or_max(a_um_s2))) {
        /* the bound rounds to the current step (bound != position in um, F-B-28): already at the
         * bound, but the last sample is compared first (a repeated command never moves further) */
        bool beyond = mul_precheck(g_fw.afe.raw_last != PROTO_AFE_NO_DATA, g_fw.afe.raw_last, raw_stop, cmp);
        ev((uint16_t)EV_MOVE_DONE, (uint16_t)(beyond ? MD_LOAD_THRESHOLD : MD_BOUND), um_of(p), p);
        return;
    }
    if (!M.running && !M.parked) {                     /* pre-check in hw_start: already beyond */
        MUL.hit = false;
        ev((uint16_t)EV_MOVE_DONE, (uint16_t)MD_LOAD_THRESHOLD, um_of(p), p);   /* no pulse */
        return;
    }
    M.active = true;
    g_fw.motion_state = (uint8_t)MS_MOVE_UNTIL_LOAD;
}

/* level 3, from on_afe_sample() right after the FW load limit (FW_design §5.8): the first sample
 * beyond raw_stop -> CLEAN halt in this ISR (SAF-FW-002 load path, <= 200 us after data-ready) */
void motion_on_sample(int32_t raw, bool load_trip)
{
    if (mul_sample_stop(MUL.armed, MUL.hit, hal_step_running(), load_trip, raw, MUL.raw_stop, MUL.cmp)) {
        (void)hal_step_stop_now();
        MUL.hit_raw = raw;
        MUL.hit = true;                                /* folded by the tick: MOVE_DONE LOAD_THRESHOLD */
    }
}

/* threshold record -> the running MOVE_UNTIL_LOAD ends with LOAD_THRESHOLD (no STOPPED, VALID kept);
 * a stop already in progress keeps its own reason (STOPPED) */
static void mul_fold(void)
{
    if (M.active && M.kind == (uint8_t)MS_MOVE_UNTIL_LOAD && MUL.hit && !M.stopping) {
        M.stopping = true;
        M.ctrl = false;
        M.reversing = false;
        M.stop_md = (uint8_t)MD_LOAD_THRESHOLD;
    }
}

static int32_t jog_end_steps(int8_t dir, int32_t bound_um, bool *bounded)
{
    const params_t *p = &g_fw.p;
    *bounded = bound_um != PROTO_JOG_NO_BOUND;
    if (*bounded) {
        return steps_of(bound_um);
    }
    if (g_fw.homed) {
        return steps_of(dir > 0 ? p->limits.soft_max_um : p->limits.soft_min_um);
    }
    {
        int64_t e = (int64_t)M.unhomed_origin + (int64_t)dir * (int64_t)steps_of((int32_t)p->home.max_travel_um);
        return (e > INT32_MAX) ? INT32_MAX : (e < INT32_MIN ? INT32_MIN : (int32_t)e);
    }
}

static void jog_begin(int32_t v_um_s, uint32_t a_um_s2, int32_t bound_um)
{
    int8_t dir = (v_um_s > 0) ? 1 : -1;
    uint32_t v = (uint32_t)((v_um_s > 0) ? v_um_s : -(int64_t)v_um_s);
    bool bounded;
    int32_t end, p = pos_now();
    end = jog_end_steps(dir, bound_um, &bounded);
    M.kind = (uint8_t)MS_JOG;
    M.jog_bounded = bounded;
    M.end_md = bounded ? (uint8_t)MD_BOUND : (uint8_t)MD_SOFT_LIMIT;
    M.target_um = um_of(end);
    M.reversing = false;
    if (!seg_start(dir, span(p, end, dir), v, acc_or_max(a_um_s2))) {
        M.active = false;
        if (cmd_is_moving(g_fw.motion_state)) {
            g_fw.motion_state = (uint8_t)MS_IDLE;
        }
        ev((uint16_t)EV_MOVE_DONE, M.end_md, um_of(p), p);    /* already at the end point */
        return;
    }
    M.active = true;
    g_fw.motion_state = (uint8_t)MS_JOG;
}

static void ctrl_stop_apply(double alpha_stop);

void motion_jog(int32_t v_um_s, uint32_t a_um_s2, int32_t bound_um, uint32_t now_ms)
{
    uint32_t a = acc_or_max(a_um_s2);
    if (v_um_s == 0) {
        if (M.active && M.kind == (uint8_t)MS_JOG) {
            motion_stop((uint8_t)SC_NONE, true, (uint8_t)MD_JOG_ZERO);   /* JOG 0: a_stop */
        }
        return;                                        /* not jogging: OK no-op */
    }
    M.jog_ms = now_ms;                                 /* dead-man refresh (SAF-FW-016) */
    if (!(M.active && M.kind == (uint8_t)MS_JOG)) {
        jog_begin(v_um_s, a, bound_um);
        return;
    }
    {
        int8_t dir = (v_um_s > 0) ? 1 : -1;
        uint32_t v = (uint32_t)((v_um_s > 0) ? v_um_s : -(int64_t)v_um_s);
        if (M.reversing || dir != M.dir) {
            /* reversal: decelerate to standstill with the jog accel, then restart (DIR setup kept) */
            M.rev_v = v_um_s;
            M.rev_a = a;
            M.rev_bound = bound_um;
            if (M.parked) {
                /* FWR-07: a parked start emitted no pulse: re-plan it in the new direction (it
                 * parks again while the sniffed-stop hold is active); the motion continues */
                M.active = false;
                jog_begin(v_um_s, a, bound_um);
                return;
            }
            if (!M.reversing) {
                M.reversing = true;
                ctrl_stop_apply(sps(a));
            }
            return;
        }
        {
            bool bounded;
            int32_t end = jog_end_steps(dir, bound_um, &bounded);
            bool new_speed = v != M.v_um_s || a != M.a_um_s2;
            M.jog_bounded = bounded;
            M.end_md = bounded ? (uint8_t)MD_BOUND : (uint8_t)MD_SOFT_LIMIT;
            M.target_um = um_of(end);
            M.v_um_s = v;
            M.a_um_s2 = a;
            if (!M.ramp.mono) {
                redit_t e;
                e.kind = RE_JOG;
                e.set_speed = new_speed;
                e.v = sps(v);
                e.a = sps(a);
                e.d = e.a;
                e.total = span(M.start, end, dir);
                ramp_commit(&e);
            }
        }
    }
}

static void home_segment_done(int32_t p);

/* homing geometry; un-homed at HOME (D-43 b): the un-homed travel window bounds every segment */
static void home_geo(home_geo_t *g)
{
    const params_home_t *h = &g_fw.p.home;
    int64_t w = (int64_t)steps_of((int32_t)h->max_travel_um);
    int64_t lo = (int64_t)M.unhomed_origin - w, hi = (int64_t)M.unhomed_origin + w;
    g->release_max = steps_of((int32_t)PROTO_HOME_RELEASE_MAX_UM);
    g->backoff = steps_of((int32_t)h->backoff_um);
    g->slow_extra = steps_of((int32_t)PROTO_HOME_SLOW_EXTRA_UM);
    g->max_travel = (int32_t)w;
    g->bounded = !M.was_homed;
    g->lo = (lo < INT32_MIN) ? INT32_MIN : (int32_t)lo;
    g->hi = (hi > INT32_MAX) ? INT32_MAX : (int32_t)hi;
}

void motion_home(void)
{
    home_geo_t g;
    home_next_t n;
    int32_t p = pos_now();
    const params_home_t *h = &g_fw.p.home;
    M.was_homed = g_fw.homed;
    home_geo(&g);
    M.hdrift_um = 0;
    M.kind = (uint8_t)MS_HOMING;
    M.end_md = (uint8_t)MD_TARGET;
    M.hstart_edge = false;
    M.hend_edge = false;
    M.hreleased = false;
    n = home_begin(safety_active((uint8_t)IO_LIMIT_START_BIT), p, &g);
    M.hphase = n.phase;
    M.horigin = p;
    g_fw.home_phase = n.phase;
    M.target_um = um_of(n.end_steps);
    M.active = true;
    g_fw.motion_state = (uint8_t)MS_HOMING;
    if (!seg_start(n.dir, span(p, n.end_steps, n.dir), n.slow ? h->v_slow_um_s : h->v_fast_um_s, h->a_um_s2)) {
        M.start = p;                                   /* zero-length first segment (at the window */
        home_segment_done(p);                          /* bound, D-43 b): NOT_FOUND / WIRING at once */
    }
}

/* ------------------------------------------------------------------ stops */
static void ctrl_stop_apply(double alpha_stop)
{
    uint8_t path;
    float run = (M.ramp.prev > 0.0f) ? M.ramp.prev : M.ramp.last;
    if (!M.running) {
        return;
    }
    path = stepgen_ctrl_path((uint32_t)run, ramp_stop_dist((double)M.f, run, alpha_stop), M.f);
    if (path == STOPPATH_HALT) {
        (void)hal_step_stop_now();                     /* clean halt, no STOPPING (ICD §6.5) */
        return;
    }
    {
        redit_t e;
        e.kind = (path == STOPPATH_ISR) ? RE_STOP_ISR : RE_STOP_STRETCH;
        e.set_speed = false;
        e.v = 0.0;
        e.a = alpha_stop;
        e.d = alpha_stop;
        e.total = 0u;
        e.floor_c = (path == STOPPATH_ISR) ? M.ramp.last : run;
        e.c1 = 0u;
        e.c2 = 0u;
        if (M.last_armed) {
            CRIT_BEGIN(HAL_CRIT_MOTION);
            M.ramp.mono = true;                        /* the running pulse is the last one anyway */
            CRIT_END();
        } else {
            ramp_commit(&e);
        }
    }
    if (!M.reversing) {
        g_fw.motion_state = (uint8_t)MS_STOPPING;
    }
}

void motion_stop(uint8_t cause, bool controlled, uint8_t md)
{
    int32_t p;
    if (!M.active) {
        return;
    }
    mul_fold();                                        /* a threshold hit decided earlier ended it */
    p = pos_now();
    if (!M.stopping || M.reversing) {
        M.reversing = false;
        M.stopping = true;
        M.stop_md = md;
        if (cause != (uint8_t)SC_NONE) {
            ev((uint16_t)EV_STOPPED, cause, um_of(p), p);
        }
        if (M.kind == (uint8_t)MS_HOMING && cause != (uint8_t)SC_HOME_FAIL) {
            ev((uint16_t)EV_HOME_FAILED, (uint16_t)HF_ABORTED, um_of(p), p);   /* no latch of its own */
            unhome();
            g_fw.home_phase = (uint8_t)HP_DONE;
        }
        M.ctrl = controlled;
        if (M.parked || !M.running) {
            finish(md);                                /* a parked start never emitted a pulse */
            return;
        }
        if (controlled) {
            ctrl_stop_apply(sps(g_fw.p.motion.a_stop_um_s2));
            return;
        }
    } else if (controlled || !M.running) {
        return;                                        /* idempotent (same or weaker stop) */
    }
    M.ctrl = false;
    (void)hal_step_stop_now();                         /* CLEAN (SAF-FW-002/004) */
}

void motion_hold_resolved(uint8_t cause)
{
    if (M.active && M.parked) {
        motion_stop(cause, false, (uint8_t)MD_STOPPED);
    }
}

bool motion_home_edge(uint8_t lim_id, int32_t steps)
{
    if (!M.active || M.kind != (uint8_t)MS_HOMING || M.stopping) {
        return false;
    }
    if (lim_id == (uint8_t)LIM_END) {
        if (M.hphase != (uint8_t)HP_MOVE_TO_ZERO) {
            M.hend_edge = true;                        /* -> HOME_WIRING at the segment end */
            return true;
        }
        return false;
    }
    switch (M.hphase) {
    case HP_FAST_SEEK:
    case HP_SLOW_APPROACH:
        if (!M.hstart_edge) {
            M.hstart_edge = true;
            M.hedge_steps = steps;                     /* captured in the EXTI callback (FW-HOM-001) */
        }
        return true;
    case HP_RELEASE:
    case HP_BACKOFF:
        M.hstart_edge = true;                          /* bounce of the switch being left */
        return true;
    default:
        return false;
    }
}

/* ------------------------------------------------------------------ step ISR (level 2) */
/* NFR-007 (FW_design §9.8 v0.8 / v0.8.6): the ramp body is inlined here (ramp_next_inl, no call
 * level); the count comes from the caller and the result is packed for a register return (target HAL
 * fast path, stepgen.h); step_isr() below is the seam v1 entry with identical behaviour */
uint64_t step_isr_core(int32_t c)
{
    if (!M.running) {
        return STEP_NEXT_STOP;
    }
    if (c - M.isr_count != (int32_t)M.dir) {
        M.step_fault = true;                           /* missed update / count fault (SAF-FW-004) */
        return STEP_NEXT_STOP;
    }
    M.isr_count = c;
    if (M.ramp.rem == 0u) {
        M.last_armed = true;                           /* the period that just started is the last */
        return STEP_NEXT_LAST;
    }
    return (uint64_t)ramp_next_inl(&M.ramp);
}

step_next_t step_isr(void)
{
    step_next_t n;
    uint64_t v = step_isr_core(hal_step_count());
    n.period = (uint32_t)v;
    n.last = (v & STEP_NEXT_LAST) != 0u;
    n.stop = (v & STEP_NEXT_STOP) != 0u;
    return n;
}

/* ------------------------------------------------------------------ tick */
static void home_fail(uint8_t why, int32_t p)
{
    uint8_t bit = (why == (uint8_t)HF_WIRING) ? (uint8_t)FAULT_HOME_WIRING_BIT : (uint8_t)FAULT_HOME_NOT_FOUND_BIT;
    uint32_t t = hal_time_us();
    CRIT_BEGIN(HAL_CRIT_DATA);
    (void)latch_fault(&g_fw.lat, &g_fw.evq, bit, um_of(p), p, t);
    CRIT_END();
    ev((uint16_t)EV_HOME_FAILED, why, um_of(p), p);
    unhome();
    g_fw.home_phase = (uint8_t)HP_DONE;
    M.stopping = true;
    ev((uint16_t)EV_STOPPED, (uint16_t)SC_HOME_FAIL, um_of(p), p);
    CRIT_BEGIN(HAL_CRIT_DATA);
    latch_valid_clear(&g_fw.valid, &g_fw.evq, (uint16_t)SC_HOME_FAIL, t);
    CRIT_END();
    finish((uint8_t)MD_STOPPED);
}

static void home_segment_done(int32_t p)
{
    home_geo_t g;
    home_in_t in;
    home_next_t n;
    const params_home_t *h = &g_fw.p.home;
    home_geo(&g);
    in.pos = p;
    in.origin = M.horigin;
    in.start_edge = M.hstart_edge;
    in.end_edge = M.hend_edge;
    in.released = M.hreleased;
    in.planned_end = p == M.start + (int32_t)M.dir * (int32_t)ramp_total(&M.ramp);
    n = home_segment_end(M.hphase, &in, &g);
    if (n.action == HOME_ACT_FAIL) {
        home_fail(n.fail, p);
        return;
    }
    if (n.action == HOME_ACT_DONE) {
        g_fw.homed = true;
        g_fw.pos_uncertain = false;
        g_fw.home_phase = (uint8_t)HP_DONE;
        ev((uint16_t)EV_HOMED, 0u, M.hdrift_um, 0);   /* value = drift µm, 0 if not homed before */
        finish((uint8_t)MD_TARGET);
        return;
    }
    if (n.action == HOME_ACT_ZERO) {
        int32_t off_steps = steps_of((int32_t)h->offset_um);
        home_zero_t z = home_zero(p, M.hedge_steps, off_steps, M.was_homed, um_of(M.hedge_steps),
                                  (int32_t)h->offset_um, h->drift_tol_um);
        hal_step_set_count(z.new_count);               /* timer stopped: edge now at -offset */
        M.isr_count = z.new_count;
        if (z.drift_fault) {
            uint32_t t = hal_time_us();
            CRIT_BEGIN(HAL_CRIT_DATA);
            (void)latch_fault(&g_fw.lat, &g_fw.evq, (uint8_t)FAULT_HOME_DRIFT_BIT, z.drift_um, 0, t);
            CRIT_END();
        }
        M.hdrift_um = z.drift_um;
        p = z.new_count;
        n.dir = (p <= 0) ? 1 : -1;
        n.end_steps = 0;
    }
    if (n.phase != M.hphase) {
        M.horigin = p;                                 /* same phase again (bounce): origin kept */
    }
    M.hphase = n.phase;
    M.hstart_edge = false;
    M.hend_edge = false;
    M.hreleased = false;
    g_fw.home_phase = n.phase;
    M.target_um = um_of(n.end_steps);
    if (!seg_start(n.dir, span(p, n.end_steps, n.dir), n.slow ? h->v_slow_um_s : h->v_fast_um_s, h->a_um_s2)) {
        home_segment_done(p);                          /* zero-length segment (e.g. offset 0) */
    }
}

static void segment_ended(uint32_t now_ms)
{
    int32_t p = pos_now();
    (void)now_ms;
    if (M.stopping) {
        finish(M.stop_md);
        return;
    }
    if (M.reversing) {
        M.active = false;                              /* standstill: start the new direction */
        M.reversing = false;
        jog_begin(M.rev_v, M.rev_a, M.rev_bound);
        return;
    }
    if (M.kind == (uint8_t)MS_HOMING) {
        home_segment_done(p);
        return;
    }
    if (p == M.start + (int32_t)M.dir * (int32_t)ramp_total(&M.ramp)) {
        finish(M.end_md);                              /* natural end (TARGET / BOUND / SOFT_LIMIT) */
        return;
    }
    /* stopped short of the end without a recorded cause: the record is folded next tick */
    if (++M.unexplained >= UNEXPLAINED_STOP_TICKS) {
        uint32_t t = hal_time_us();
        CRIT_BEGIN(HAL_CRIT_DATA);
        (void)latch_fault(&g_fw.lat, &g_fw.evq, (uint8_t)FAULT_STEP_FAULT_BIT, um_of(p), p, t);
        CRIT_END();
        unhome();
        g_fw.pos_uncertain = true;
        motion_stop((uint8_t)SC_STEP_FAULT, false, (uint8_t)MD_STOPPED);
        finish((uint8_t)MD_STOPPED);
    }
}

void motion_tick(uint32_t now_ms)
{
    if (M.step_fault) {                                /* SAF-FW-004 */
        int32_t p = pos_now();
        uint32_t t = hal_time_us();
        M.step_fault = false;
        CRIT_BEGIN(HAL_CRIT_DATA);
        (void)latch_fault(&g_fw.lat, &g_fw.evq, (uint8_t)FAULT_STEP_FAULT_BIT, um_of(p), p, t);
        CRIT_END();
        unhome();
        g_fw.pos_uncertain = true;
        motion_stop((uint8_t)SC_STEP_FAULT, false, (uint8_t)MD_STOPPED);
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_valid_clear(&g_fw.valid, &g_fw.evq, (uint16_t)SC_STEP_FAULT, t);
        CRIT_END();
    }
    if (M.active && M.parked && link_motion_start_allowed()) {
        motion_stop(link_hold_cause(), false, (uint8_t)MD_STOPPED);   /* safety net (hold resolved) */
    }
    if (M.active && M.kind == (uint8_t)MS_HOMING && !M.stopping &&
        (M.hphase == (uint8_t)HP_RELEASE || M.hphase == (uint8_t)HP_BACKOFF) && !M.hreleased &&
        !safety_active((uint8_t)IO_LIMIT_START_BIT) &&
        relf_released(&g_fw.in.lim_rel[0], g_fw.p.io.release_ms)) {
        int32_t p = pos_now();
        int32_t end = p + steps_of((int32_t)g_fw.p.home.backoff_um);
        M.hreleased = true;                            /* START released stably: back off further */
        {
            redit_t e;
            e.kind = RE_TOTAL;
            e.set_speed = false;
            e.v = 0.0;
            e.a = 0.0;
            e.d = 0.0;
            e.total = span(M.start, end, M.dir);
            e.floor_c = 0.0f;
            e.c1 = 0u;
            e.c2 = 0u;
            ramp_commit(&e);
        }
    }
    mul_fold();                                        /* FW-MOT-006 hit record (sample ISR) */
    {
        /* FWR-20: read the timer state FIRST, then capture the stop generation, then look for pending
         * records. A planned last-period end or a halt before the capture is accounted (a segment end
         * seen here can never be killed by its own bump); a halt with a record between the take and
         * the capture leaves the record pending -> FWR-18 deferral; a halt after the capture makes
         * hw_start() refuse the next segment (FWR-01 / FWR-17) and its record folds next tick. */
        bool stopped = M.active && M.running && !hal_step_running();
        motion_gate_capture();
        if (stopped && safety_edges_pending()) {
            /* FWR-18: decide the segment end - homing edge, limit stop, restart - at the next tick */
        } else if (stopped) {
            segment_ended(now_ms);
        } else {
            M.unexplained = 0u;
        }
    }
    if (g_fw.motion_state == (uint8_t)MS_ENABLING && motion_enabling_left_ms(now_ms) == 0u) {
        g_fw.motion_state = (uint8_t)MS_IDLE;          /* FW-MOT-008: no PUL/DIR edge before */
        ev((uint16_t)EV_DRIVER_ENABLED, 0u, 0, 0);
    }
    if (M.active && M.kind == (uint8_t)MS_JOG && !M.stopping &&
        (uint32_t)(now_ms - M.jog_ms) >= g_fw.p.motion.jog_timeout_ms) {
        motion_stop((uint8_t)SC_JOG_DEADMAN, true, (uint8_t)MD_STOPPED);   /* VALID unchanged */
    }
    if (M.pend_wait) {                                 /* FW-SW-004: NOT_SETTLED (warning only) */
        uint32_t el = now_ms - M.pend_t0_ms;
        if (g_fw.in.pend || g_fw.motion_state != (uint8_t)MS_IDLE || drvmon_alm(&g_fw.in.drv) ||
            !safety_drv_power()) {
            M.pend_wait = false;
        } else if (el >= g_fw.p.drv.pend_timeout_ms) {
            M.pend_wait = false;
            ev((uint16_t)EV_NOT_SETTLED, 0u, (int32_t)el, 0);
        }
    }
}
