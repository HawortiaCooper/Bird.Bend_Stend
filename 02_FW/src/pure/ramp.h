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
 * under CRIT_MOTION (FW_design §4.4). float32 only in ramp_next(); the setup functions use double
 * (thread / tick context) so that the integer r0 equals the binary64 oracle.
 * NFR-007 (FW_design §9.8 v0.8): ramp_next() is the bulk of the step ISR. Every square root it needs
 * is cached one call ahead, so one call executes at most two VDIV and two VSQRT (accel or reduction
 * term: VDIV + the VSQRT of its next index; decel term: VDIV; sqrt(rem - 1) for the next call: VSQRT):
 *   sa = sqrt(ka), sa1 = sqrt(ka - 1)       accel index (as before)
 *   srv = sqrt(rv), srv1 = sqrt(rv - 1)     reduction index (v0.8; was two VSQRT per call)
 *   sr = sqrt(rem), sr1 = sqrt(rem - 1)     always valid (v0.8: set by every function that changes rem
 *                                           outside ramp_next, kept by ramp_next; was a lazy
 *                                           two-VSQRT initialisation inside the ISR)
 * Each cached value is the same IEEE operation on the same operand as before (sqrtf is correctly
 * rounded), so every period is bit-identical to v0.7 (host check: FW_design §9.8). ramp_next_inl() is
 * the always-inlined body (step_isr() uses it, no call level); ramp_next() is the same code out of line.
 * Pure C11.
 * Implements: FW-MOT-003 (exact ramp, virtual index), FW-MOT-002 (step bookkeeping),
 *             SAF-FW-003 (controlled-stop sizing, non-decreasing intervals), FW-MOT-005 (on-the-fly)
 */
#ifndef PURE_RAMP_H
#define PURE_RAMP_H

#include <math.h>
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
    float    srv, srv1;        /* sqrt(rv), sqrt(rv - 1) (valid while red and rv >= 1) */
    bool     red;              /* slowing down to cmin */
    uint32_t rem;              /* steps still to generate */
    uint32_t rguard;           /* the decel term is evaluated only while rem <= rguard */
    float    sr, sr1;          /* sqrt(rem), sqrt(rem - 1) (0 for rem <= 1); always valid */
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

/* Edits split into the constants that do not depend on the ramp position (computed once, outside
 * CRIT_MOTION) and the position-dependent rest (FWR-10, FW_design §9.8 v0.8). ramp_set_speed() and
 * ramp_stop() are exactly these two steps, so the results are bit-identical. */
typedef struct {
    float cmin, ca, cr;
} ramp_speed_k_t;
/** Position-independent part of ramp_set_speed() (needs only r->f and r->chw). */
void     ramp_speed_k(const ramp_t *r, double v_steps_s, double alpha_a, double alpha_d, ramp_speed_k_t *k);
/** Position-dependent part of ramp_set_speed(). */
void     ramp_set_speed_k(ramp_t *r, const ramp_speed_k_t *k, double alpha_d);
/** ramp_stop() with cd = ramp_c_of(r->f, alpha_stop) precomputed. */
uint32_t ramp_stop_k(ramp_t *r, double alpha_stop, float cd, uint32_t extra, float floor_c);
/** Steps of the move (gen + rem). */
static inline uint32_t ramp_total(const ramp_t *r) { return r->gen + r->rem; }

#if defined(__GNUC__)
#define RAMP_INLINE static inline __attribute__((always_inline))
#else
#define RAMP_INLINE static inline
#endif

#define RAMP_U32_MAX_F 4294967040.0f   /* largest float below 2^32 */

/** ramp_next() body, always inlined (step ISR, NFR-007). Same semantics as ramp_next(). */
RAMP_INLINE uint32_t ramp_next_inl(ramp_t *r)
{
    float c = r->cmin;
    float acc;
    uint32_t n;
    uint32_t rem = r->rem;
    uint8_t bind = 0u;                       /* 1 = accel term, 2 = reduction term */

    if (r->red) {
        if (r->rv >= 1.0f) {
            float p = r->cr / (r->srv + r->srv1);
            if (p < r->cmin) {
                c = p;
                bind = 2u;
            } else {
                r->red = false;              /* reduction done: cruise */
            }
        } else {
            r->red = false;
        }
    } else if (r->acc_on) {
        float p = r->ca / (r->sa + r->sa1);
        if (p > c) {
            c = p;
            bind = 1u;
        } else {
            r->acc_on = false;               /* cruise reached: the accel term never binds again */
        }
    }
    if (rem != 0u && rem <= r->rguard) {
        float p = r->cd / (r->sr + r->sr1);
        if (p > c) {
            c = p;
            bind = 0u;                       /* decelerating to the end point */
        }
    }
    if (bind == 1u) {
        r->ka += 1.0f;
        r->sa1 = r->sa;
        r->sa = sqrtf(r->ka);
    } else if (bind == 2u) {
        r->rv -= 1.0f;
        r->srv = r->srv1;                    /* sqrt(rv) of the new index = the cached sqrt(rv - 1) */
        if (r->rv >= 1.0f) {
            r->srv1 = sqrtf(r->rv - 1.0f);
        }
    }
    if (c < r->chw) {
        c = r->chw;
    }
    if (r->mono && c < r->last) {
        c = r->last;
    }
    c = (c > RAMP_U32_MAX_F) ? RAMP_U32_MAX_F : c;
    r->prev = r->last;
    r->last = c;
    /* no clamp needed (v0.8.6, NFR-007): c <= RAMP_U32_MAX_F (= 2^32 - 256, float ulp 256 there) and
     * 0 <= carry < 1, so carry + c rounds to at most RAMP_U32_MAX_F; the v0.8.5 clamp never fired */
    acc = r->carry + c;
    n = (uint32_t)acc;
    r->carry = acc - (float)n;
    if (rem != 0u) {
        rem--;
        r->rem = rem;
        r->sr = r->sr1;
        r->sr1 = (rem > 1u) ? sqrtf((float)(rem - 1u)) : 0.0f;
    }
    r->gen++;
    return (n == 0u) ? 1u : n;
}

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
