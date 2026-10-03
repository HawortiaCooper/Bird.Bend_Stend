/* Exact square-root ramp generator (R4 §1.5; oracle: tools/ref_motion.py). Implements: FW-MOT-003,
 * FW-MOT-002, SAF-FW-003, FW-MOT-005
 */
#include "ramp.h"

#include <math.h>

#define U32_MAX_F 4294967040.0f        /* largest float below 2^32 */
#define C_MAX     4.0e9                /* longest period handled (ticks) */

double ramp_steps_of(uint32_t um, float spm)
{
    return (double)um * (double)spm / 1000.0;
}

float ramp_period_of(double f, double v_steps_s)
{
    double c = (v_steps_s > 0.0) ? f / v_steps_s : C_MAX;
    return (float)((c > C_MAX) ? C_MAX : c);
}

float ramp_c_of(double f, double alpha)
{
    double c = (alpha > 0.0) ? f * sqrt(2.0 / alpha) : C_MAX;
    return (float)((c > C_MAX) ? C_MAX : c);
}

double ramp_stop_dist(double f, float c, double alpha)
{
    double v;
    if (c <= 0.0f || alpha <= 0.0) {
        return 0.0;
    }
    v = f / (double)c;
    return v * v / (2.0 * alpha);
}

static uint32_t sat_u32(double x)
{
    if (x <= 0.0) {
        return 0u;
    }
    return (x >= 4294967295.0) ? UINT32_MAX : (uint32_t)x;
}

static void set_rguard(ramp_t *r)
{
    /* D(rem) >= chw needs rem <~ Cd^2 / (4 chw^2) + 1 */
    double g = ((double)r->cd * (double)r->cd) / (4.0 * (double)r->chw * (double)r->chw) + 2.0;
    r->rguard = sat_u32(g);
    r->sr_ok = false;
}

void ramp_start(ramp_t *r, double f, double alpha_a, double alpha_d, double v_steps_s, float chw,
                uint32_t n_steps)
{
    r->f = f;
    r->ca = ramp_c_of(f, alpha_a);
    r->cd = ramp_c_of(f, alpha_d);
    r->cr = r->cd;
    r->chw = (chw < 1.0f) ? 1.0f : chw;
    r->cmin = ramp_period_of(f, v_steps_s);
    if (r->cmin < r->chw) {
        r->cmin = r->chw;
    }
    r->ka = 1.0f;
    r->acc_on = true;
    r->rv = 0.0f;
    r->red = false;
    r->rem = n_steps;
    set_rguard(r);
    r->mono = false;
    r->last = 0.0f;
    r->prev = 0.0f;
    r->carry = 0.0f;
    r->gen = 0u;
}

uint32_t ramp_next(ramp_t *r)
{
    float c = r->cmin;
    float acc;
    uint32_t n;
    uint8_t bind = 0u;                       /* 1 = accel term, 2 = reduction term */

    if (r->red) {
        if (r->rv >= 1.0f) {
            float p = r->cr / (sqrtf(r->rv) + sqrtf(r->rv - 1.0f));
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
        float p = r->ca / (sqrtf(r->ka) + sqrtf(r->ka - 1.0f));
        if (p > c) {
            c = p;
            bind = 1u;
        } else {
            r->acc_on = false;               /* cruise reached: the accel term never binds again */
        }
    }
    if (r->rem != 0u && r->rem <= r->rguard) {
        float p;
        if (!r->sr_ok) {
            r->sr = sqrtf((float)r->rem);
            r->sr1 = sqrtf((float)(r->rem - 1u));
            r->sr_ok = true;
        }
        p = r->cd / (r->sr + r->sr1);
        if (p > c) {
            c = p;
            bind = 0u;                       /* decelerating to the end point */
        }
    }
    if (bind == 1u) {
        r->ka += 1.0f;
    } else if (bind == 2u) {
        r->rv -= 1.0f;
    }
    if (c < r->chw) {
        c = r->chw;
    }
    if (r->mono && c < r->last) {
        c = r->last;
    }
    if (c > U32_MAX_F) {
        c = U32_MAX_F;
    }
    r->prev = r->last;
    r->last = c;
    acc = r->carry + c;
    if (acc > U32_MAX_F) {
        acc = U32_MAX_F;
    }
    n = (uint32_t)acc;
    r->carry = acc - (float)n;
    if (r->rem != 0u) {
        r->rem--;
        if (r->sr_ok) {
            r->sr = r->sr1;
            r->sr1 = (r->rem > 1u) ? sqrtf((float)(r->rem - 1u)) : 0.0f;
        }
    }
    r->gen++;
    return (n == 0u) ? 1u : n;
}

static void accel_from_last(ramp_t *r)
{
    /* k0 = (f / c_last)^2 / (2 alpha) = Ca^2 / (4 c_last^2); next index k0 + 1 */
    double k0 = ((double)r->ca * (double)r->ca) / (4.0 * (double)r->last * (double)r->last);
    r->ka = (float)(k0 + 1.0);
    r->acc_on = true;
    r->red = false;
}

void ramp_set_total(ramp_t *r, uint32_t total_steps)
{
    uint32_t old = r->rem;
    r->rem = (total_steps > r->gen) ? total_steps - r->gen : 0u;
    r->sr_ok = false;
    if (r->rem > old && !r->mono && !r->red && r->last > 0.0f && r->last > r->cmin) {
        accel_from_last(r);                  /* the end moved away: may accelerate again */
    }
}

void ramp_set_speed(ramp_t *r, double v_steps_s, double alpha_a, double alpha_d)
{
    float cmin = ramp_period_of(r->f, v_steps_s);
    if (cmin < r->chw) {
        cmin = r->chw;
    }
    r->ca = ramp_c_of(r->f, alpha_a);
    r->cr = ramp_c_of(r->f, alpha_d);
    r->cmin = cmin;
    r->sr_ok = false;
    if (r->last <= 0.0f) {                   /* not started yet: from rest */
        r->ka = 1.0f;
        r->acc_on = true;
        r->red = false;
        return;
    }
    if (cmin < r->last) {                    /* faster: accelerate from the current speed */
        accel_from_last(r);
    } else if (cmin > r->last) {             /* slower: virtual decel index R from the current speed */
        double R = ramp_stop_dist(r->f, r->last, alpha_d);
        r->rv = (float)R;
        r->red = R >= 1.0;
        r->acc_on = false;
    } else {
        r->acc_on = false;                   /* equal: cruise */
        r->red = false;
    }
}

uint32_t ramp_stop(ramp_t *r, double alpha_stop, uint32_t extra, float floor_c)
{
    double d = ramp_stop_dist(r->f, (r->last > 0.0f) ? r->last : r->chw, alpha_stop);
    uint32_t r0 = sat_u32(ceil(d));
    r0 = (r0 > extra) ? r0 - extra : 0u;
    if (r0 < 1u) {
        r0 = 1u;
    }
    if (r0 < r->rem) {
        r->rem = r0;                         /* r = min(r_move, r0) */
    }
    if (floor_c > r->last) {
        r->last = floor_c;                   /* never faster than the running period */
    }
    r->cd = ramp_c_of(r->f, alpha_stop);
    r->rguard = UINT32_MAX;                  /* D_s binds from now on */
    r->sr_ok = false;
    r->mono = true;
    r->acc_on = false;
    r->red = false;
    r->cmin = r->chw;                        /* c = max(c_last, D_s(r)) */
    return r->rem;
}

static uint32_t round_half_away(double x)
{
    return (x <= 0.0) ? 0u : (uint32_t)floor(x + 0.5);
}

ramp_plan_t ramp_plan(uint32_t n, double v, double a, double d)
{
    ramp_plan_t p;
    double n_acc = v * v / (2.0 * a);
    double n_dec = v * v / (2.0 * d);
    if (n_acc + n_dec <= (double)n) {
        p.triangle = false;
        p.n_acc = round_half_away(n_acc);
        p.n_dec = round_half_away(n_dec);
        p.n_cruise = n - p.n_acc - p.n_dec;
        p.v_peak = v;
        p.t_s = v / a + v / d + ((double)n - n_acc - n_dec) / v;
    } else {
        double na = (double)n * d / (a + d);
        p.triangle = true;
        p.n_acc = round_half_away(na);
        p.n_dec = n - p.n_acc;
        p.n_cruise = 0u;
        p.v_peak = sqrt(2.0 * a * na);
        p.t_s = p.v_peak / a + p.v_peak / d;
    }
    return p;
}
