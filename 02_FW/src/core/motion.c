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
 * Implements: FW-MOT-001 (start / DIR setup / PW via the HAL), FW-MOT-002, FW-MOT-003, FW-MOT-004,
 *             FW-MOT-005, FW-MOT-007 (motion part), FW-MOT-008, FW-HOM-001, FW-HOM-002, FW-HOM-004,
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
#include "ramp.h"
#include "stepgen.h"
#include "units.h"

#define F_TICK_NOMINAL 90000000u   /* provisional for the first hal_step_init() (it returns f) */
#define UNEXPLAINED_STOP_TICKS 3u  /* timer stopped short of the end with no recorded cause */

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
    uint8_t  unexplained;
    ramp_t   ramp;
    uint32_t v_um_s, a_um_s2;      /* current segment speed / accel (jog refresh compares) */
    volatile int32_t isr_count;    /* step count seen by the last step_isr() */
    volatile bool    step_fault;   /* step_isr -> tick */
    /* jog */
    uint32_t jog_ms;
    int32_t  jog_origin;           /* un-homed: travel bound origin (steps) */
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

/* ------------------------------------------------------------------ helpers */
static int32_t pos_now(void) { return hal_step_count(); }
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
    g_fw.ena_on = true;                                /* reset level: no LED current = holding (D-13) */
}

bool motion_active(void) { return M.active; }
int8_t motion_dir(void) { return (M.active && M.running) ? M.dir : 0; }

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
    g_fw.homed = false;                                /* position not held by the driver (D-11) */
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
    uint32_t gen = hal_step_stop_gen();
    uint32_t c1, c2 = 0u;
    int dir_hw = g_fw.p.motion.dir_invert ? 2 * M.dir : M.dir;
    hal_step_set_dir(dir_hw);                          /* only while stopped (FW-MOT-001) */
    c1 = ramp_next(&M.ramp);
    if (M.ramp.rem != 0u) {
        c2 = ramp_next(&M.ramp);
    }
    CRIT_BEGIN(HAL_CRIT_MOTION);
    M.isr_count = pos_now();
    M.last_armed = false;
    M.running = true;
    hal_step_start(c1);                                /* first edge >= dir_setup after this call */
    if (c2 == 0u) {
        hal_step_arm_last();
        M.last_armed = true;
    } else {
        hal_step_set_period(c2);
    }
    CRIT_END();
    if (hal_step_stop_gen() != gen) {
        (void)hal_step_stop_now();                     /* start-then-recheck: a fixed reaction raced it */
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
        int64_t e = (int64_t)M.jog_origin + (int64_t)dir * (int64_t)steps_of((int32_t)p->home.max_travel_um);
        return (e > INT32_MAX) ? INT32_MAX : (e < INT32_MIN ? INT32_MIN : (int32_t)e);
    }
}

static void jog_begin(int32_t v_um_s, uint32_t a_um_s2, int32_t bound_um)
{
    int8_t dir = (v_um_s > 0) ? 1 : -1;
    uint32_t v = (uint32_t)((v_um_s > 0) ? v_um_s : -(int64_t)v_um_s);
    bool bounded;
    int32_t end, p = pos_now();
    if (!g_fw.homed) {
        M.jog_origin = p;                              /* un-homed bound: from this jog's start point */
    }
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
            if (!M.reversing) {
                M.reversing = true;
                if (M.parked) {
                    M.parked = false;
                    M.running = false;
                } else {
                    ctrl_stop_apply(sps(a));
                }
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
            CRIT_BEGIN(HAL_CRIT_MOTION);
            if (!M.ramp.mono) {
                if (new_speed) {
                    ramp_set_speed(&M.ramp, sps(v), sps(a), sps(a));   /* on the fly (FW-MOT-005) */
                }
                ramp_set_total(&M.ramp, span(M.start, end, dir));
            }
            CRIT_END();
        }
    }
}

void motion_home(void)
{
    home_geo_t g;
    home_next_t n;
    int32_t p = pos_now();
    const params_home_t *h = &g_fw.p.home;
    g.release_max = steps_of((int32_t)PROTO_HOME_RELEASE_MAX_UM);
    g.backoff = steps_of((int32_t)h->backoff_um);
    g.slow_extra = steps_of((int32_t)PROTO_HOME_SLOW_EXTRA_UM);
    g.max_travel = steps_of((int32_t)h->max_travel_um);
    M.was_homed = g_fw.homed;
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
    (void)seg_start(n.dir, span(p, n.end_steps, n.dir), n.slow ? h->v_slow_um_s : h->v_fast_um_s, h->a_um_s2);
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
    CRIT_BEGIN(HAL_CRIT_MOTION);
    if (M.last_armed) {
        M.ramp.mono = true;                            /* the running pulse is the last one anyway */
    } else if (path == STOPPATH_ISR) {
        (void)ramp_stop(&M.ramp, alpha_stop, 1u, M.ramp.last);   /* next ISR preloads c_dec1 */
    } else {
        /* stretch (OI-ICD-07): replace the running and the preloaded period, extend-only */
        uint32_t c1, c2 = 0u;
        M.ramp.gen = (M.ramp.gen >= 2u) ? M.ramp.gen - 2u : 0u;
        M.ramp.rem += 2u;
        (void)ramp_stop(&M.ramp, alpha_stop, 0u, run);
        c1 = ramp_next(&M.ramp);
        if (M.ramp.rem != 0u) {
            c2 = ramp_next(&M.ramp);
        }
        hal_step_set_period_now(c1);
        if (c2 != 0u) {
            hal_step_set_period(c2);
        } else {
            hal_step_arm_last();
            M.last_armed = true;
        }
    }
    CRIT_END();
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
            g_fw.homed = false;
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
step_next_t step_isr(void)
{
    step_next_t n;
    int32_t c = hal_step_count();
    n.period = 0u;
    n.last = false;
    n.stop = false;
    if (!M.running) {
        n.stop = true;
        return n;
    }
    if (c - M.isr_count != (int32_t)M.dir) {
        M.step_fault = true;                           /* missed update / count fault (SAF-FW-004) */
        n.stop = true;
        return n;
    }
    M.isr_count = c;
    if (M.ramp.rem == 0u) {
        n.last = true;                                 /* the period that just started is the last */
        M.last_armed = true;
        return n;
    }
    n.period = ramp_next(&M.ramp);
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
    g_fw.homed = false;
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
    g.release_max = steps_of((int32_t)PROTO_HOME_RELEASE_MAX_UM);
    g.backoff = steps_of((int32_t)h->backoff_um);
    g.slow_extra = steps_of((int32_t)PROTO_HOME_SLOW_EXTRA_UM);
    g.max_travel = steps_of((int32_t)h->max_travel_um);
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
        g_fw.homed = false;
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
        g_fw.homed = false;
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
        CRIT_BEGIN(HAL_CRIT_MOTION);
        ramp_set_total(&M.ramp, span(M.start, end, M.dir));
        CRIT_END();
    }
    if (M.active && M.running && !hal_step_running()) {
        segment_ended(now_ms);
    } else {
        M.unexplained = 0u;
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
