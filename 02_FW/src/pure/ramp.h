/* Exact square-root ramp with the "max rule", fractional carry and virtual indices (R4 §1.5,
 * FW_design §5.6.3). Normative definition: 00_System/tools/ref_motion.py (docstring) - the oracle
 * of 00_System/tools/vectors/motion_vectors.json (every period within +-1 tick, every sum within
 * +-N/1000 ticks). One generator serves every motion: position moves (trapezoid / triangle to an
 * end point), jogs with on-the-fly speed / accel / end-point changes, and controlled stops.
 *
 *   A(k) = Ca/(sqrt(k)+sqrt(k-1)), D(r) = Cd/(sqrt(r)+sqrt(r-1)), Ca = f sqrt(2/alpha), cmin = f/v
 *   position move, step i, r = N - i + 1:  c = max(A(i), cmin, D(r))
 *   accelerate from period c_last to a faster cruise: k0 = (f/c_last)^2/(2 alpha), c = max(A(k0+j), cmin)
 *   slow down to a lower cruise: R = (f/c_last)^2/(2 alpha_d), c = D(R - j) while R - j >= 1 and
 *       D < cmin, then cmin
 *   controlled stop: r0 = ceil((f/c_last)^2/(2 alpha_stop)), r = min(r_move, r0), c = max(c_last, D_s(r))
 *   (never faster than the current period: every later interval non-decreasing, SAF-FW-003)
 * The integer period is taken with one fractional carry over the whole motion (acc += c;
 * n = (u32)acc; acc -= n), so the mean speed and the total time are exact (TV-M: 468 000 000 ticks).
 * Virtual indices are real numbers (float); the decel-to-end index r is the integer steps left.
 *
 * Bookkeeping: `gen` periods handed out, `rem` still to generate; gen + rem = steps of the move.
 * The step ISR (level 2) owns the generator while the timer runs; the tick / thread modify it only
 * under CRIT_MOTION (FW_design §4.4). float32 only in ramp_next() (one VSQRT + one VDIV per active
 * term on the M4F, the decel term only within rguard steps of the end); the setup functions use
 * double (thread / tick context) so that the integer r0 equals the binary64 oracle.
 * Pure C11.
 * Implements: FW-MOT-003 (exact ramp, virtual index), FW-MOT-002 (step bookkeeping),
 *             SAF-FW-003 (controlled-stop sizing, non-decreasing intervals), FW-MOT-005 (on-the-fly)
 */
#ifndef PURE_RAMP_H
#define PURE_RAMP_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    double   f;                /* timer ticks per second */
    float    ca, cd, cr;       /* accel / decel-to-end / reduction constants (ticks) */
    float    cmin;             /* cruise period (ticks) */
    float    chw;              /* hardware minimum period (ticks) */
    float    ka;               /* next virtual accel index (real, >= 1) */
    float    sa, sa1;          /* sqrt(ka), sqrt(ka - 1) (cache: one VSQRT per accel step) */
    bool     acc_on;           /* the accel term may still bind */
    float    rv;               /* virtual reduction index (real) */
    bool     red;              /* slowing down to cmin */
    uint32_t rem;              /* steps still to generate */
    uint32_t rguard;           /* the decel term is evaluated only while rem <= rguard */
    float    sr, sr1;          /* sqrt(rem), sqrt(rem - 1) (valid while sr_ok) */
    bool     sr_ok;
    bool     mono;             /* never shorter than `last` (controlled stop) */
    float    last;             /* last generated period (float, before the carry) = c_last */
    float    prev;             /* the one before (= the running timer period while one is preloaded) */
    float    carry;
    uint32_t gen;              /* periods generated */
} ramp_t;

/* ---- constants (double, thread / tick context) ---- */
/** steps/s or steps/s^2 from µm/s or µm/s^2 (binary32 spm, ICD §0.1). */
double   ramp_steps_of(uint32_t um, float spm);
/** Cruise period in ticks for v steps/s (f = timer clock). */
float    ramp_period_of(double f, double v_steps_s);
/** Ca / Cd = f * sqrt(2 / alpha), alpha in steps/s^2. */
float    ramp_c_of(double f, double alpha);
/** Stop distance in steps from the period c at alpha: (f/c)^2 / (2 alpha). */
double   ramp_stop_dist(double f, float c, double alpha);

/* ---- generator ---- */
/** New move of n_steps (>= 1) from rest: accel alpha_a, decel-to-end alpha_d, cruise v. */
void     ramp_start(ramp_t *r, double f, double alpha_a, double alpha_d, double v_steps_s, float chw,
                    uint32_t n_steps);
/** Next period (integer ticks, >= 1); rem must be >= 1. */
uint32_t ramp_next(ramp_t *r);
/** Retarget: the move now has total_steps steps (from its start); rem = total - gen, >= 0. When the
 *  end moved away, acceleration may resume from the current speed. */
void     ramp_set_total(ramp_t *r, uint32_t total_steps);
/** New cruise speed / accel (jog refresh): faster -> accelerate from c_last (k0), slower -> slow
 *  down with alpha_d (virtual index R), equal -> cruise. */
void     ramp_set_speed(ramp_t *r, double v_steps_s, double alpha_a, double alpha_d);
/** Controlled stop at alpha_stop: rem = min(rem, max(1, ceil(d) - extra)) where d = the stop
 *  distance from c_last and `extra` = steps already committed in the timer and not regenerated;
 *  periods never shorter than max(c_last, floor_c). Returns rem. */
uint32_t ramp_stop(ramp_t *r, double alpha_stop, uint32_t extra, float floor_c);
/** Steps of the move (gen + rem). */
static inline uint32_t ramp_total(const ramp_t *r) { return r->gen + r->rem; }

/* ---- planner (R4 §1.5; previews, timeouts, tests) ---- */
typedef struct {
    bool     triangle;
    uint32_t n_acc, n_cruise, n_dec;
    double   v_peak;           /* steps/s */
    double   t_s;              /* move time */
} ramp_plan_t;
/** Trapezoid / triangle plan of n steps at v steps/s, accel a and decel d steps/s^2. */
ramp_plan_t ramp_plan(uint32_t n, double v, double a, double d);

#ifdef __cplusplus
}
#endif

#endif /* PURE_RAMP_H */
