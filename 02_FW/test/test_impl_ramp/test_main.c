/* Ramp generator against motion_vectors.json (ref_motion.py oracle, read in place by the pre-script):
 * every period within tolerance.period_ticks, the period count exact, the sum within
 * tolerance.sum_ticks; controlled-stop path rows; planner rows; R4 TV-M spot values; non-decreasing
 * intervals after a controlled stop; retarget bookkeeping.
 * Verifies: FW-MOT-003, FW-MOT-005 (on-the-fly speed changes), SAF-FW-003 (stop sizing, path choice)
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <unity.h>

#include "ramp.h"
#include "stepgen.h"
#include "vec_motion.h"

void setUp(void) {}
void tearDown(void) {}

static uint32_t worst_dev;

static void apply_event(ramp_t *r, const vec_motion_t *c, const vec_mev_t *e)
{
    double aa = ramp_steps_of(c->a, c->spm);
    double ad = ramp_steps_of(c->d, c->spm);
    double as = ramp_steps_of(c->a_stop, c->spm);
    if (e->kind == 0u || e->v_um_s == 0) {
        (void)ramp_stop(r, as, 0u, r->last);
    } else {
        ramp_set_speed(r, ramp_steps_of((uint32_t)abs(e->v_um_s), c->spm), aa, ad);
    }
}

static void replay(const vec_motion_t *c)
{
    ramp_t r;
    uint32_t n = 0u;
    uint64_t sum = 0u;
    uint8_t e = 0u;
    double aa = ramp_steps_of(c->a, c->spm);
    double ad = ramp_steps_of(c->d, c->spm);
    if (c->n_steps > 0u) {
        ramp_start(&r, (double)c->f_tick, aa, ad, ramp_steps_of(c->v, c->spm), 1.0f, c->n_steps);
    } else {
        TEST_ASSERT_TRUE_MESSAGE(c->n_ev > 0u && c->ev[0].after == 0u, c->name);
        ramp_start(&r, (double)c->f_tick, aa, ad, ramp_steps_of((uint32_t)abs(c->ev[0].v_um_s), c->spm), 1.0f,
                   0x7FFFFFFFu);
        e = 1u;
    }
    while (r.rem > 0u && n < c->n_periods + 16u) {
        uint32_t p;
        while (e < c->n_ev && c->ev[e].after == n) {
            apply_event(&r, c, &c->ev[e]);
            e++;
        }
        if (r.rem == 0u) {
            break;
        }
        p = ramp_next(&r);
        if (n < c->n_periods) {
            uint32_t ref = c->periods[n];
            uint32_t dev = (p > ref) ? p - ref : ref - p;
            if (dev > worst_dev) {
                worst_dev = dev;
            }
            if (dev > c->tol_p) {
                char msg[128];
                snprintf(msg, sizeof msg, "%s: period %lu = %lu, ref %lu", c->name, (unsigned long)n,
                         (unsigned long)p, (unsigned long)ref);
                TEST_FAIL_MESSAGE(msg);
            }
        }
        sum += p;
        n++;
    }
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(c->n_periods, n, c->name);
    {
        uint64_t d = (sum > c->sum) ? sum - c->sum : c->sum - sum;
        TEST_ASSERT_TRUE_MESSAGE(d <= c->tol_sum, c->name);
    }
}

static void test_motion_vectors(void)
{
    uint32_t i;
    worst_dev = 0u;
    for (i = 0u; i < VEC_MOTION_N; i++) {
        replay(&VEC_MOTION[i]);
    }
    TEST_ASSERT_TRUE(VEC_MOTION_N >= 9u);
    TEST_ASSERT_TRUE(worst_dev <= 1u);
}

static void test_tvm_spot_values(void)   /* R4 §12 TV-M (f 90 MHz, 640 steps/mm, 100 mm/s^2, 20 mm/s) */
{
    ramp_t r;
    uint32_t i, first[5], n7031 = 0u, n7032 = 0u, ramp_n = 0u;
    uint64_t sum = 0u;
    static const uint32_t tv[5] = {503115u, 208397u, 159909u, 134809u, 118770u};
    ramp_start(&r, 90e6, 64000.0, 64000.0, 12800.0, 1.0f, 64000u);
    for (i = 0u; i < 64000u; i++) {
        uint32_t p = ramp_next(&r);
        if (i < 5u) {
            first[i] = p;
        }
        sum += p;
        n7031 += (p == 7031u);
        n7032 += (p == 7032u);
        ramp_n += (p > 7032u);
    }
    for (i = 0u; i < 5u; i++) {
        TEST_ASSERT_UINT32_WITHIN(1u, tv[i], first[i]);
    }
    TEST_ASSERT_TRUE(sum >= 468000000u - 64u && sum <= 468000000u + 64u);
    TEST_ASSERT_UINT32_WITHIN(2u, 2559u, ramp_n);
    TEST_ASSERT_UINT32_WITHIN(4u, 46080u, n7031);
    TEST_ASSERT_UINT32_WITHIN(4u, 15361u, n7032);
}

static void test_stop_never_faster(void)       /* SAF-FW-003: intervals non-decreasing after the commit */
{
    ramp_t r;
    uint32_t i, prev = 0u;
    ramp_start(&r, 90e6, 80000.0, 80000.0, 24000.0, 1800.0f, 80000u);
    for (i = 0u; i < 300u; i++) {
        (void)ramp_next(&r);                    /* still accelerating */
    }
    (void)ramp_stop(&r, 800000.0, 0u, r.last);
    while (r.rem > 0u) {
        uint32_t p = ramp_next(&r);
        TEST_ASSERT_TRUE(p + 1u >= prev);       /* +-1 tick from the carry */
        prev = p;
    }
    TEST_ASSERT_TRUE(r.gen < 400u);
}

static void test_stop_distance(void)
{
    ramp_t r;
    double d;
    uint32_t i;
    ramp_start(&r, 90e6, 64000.0, 64000.0, 12800.0, 1.0f, 64000u);
    for (i = 0u; i < 30000u; i++) {
        (void)ramp_next(&r);
    }
    d = ramp_stop_dist(90e6, r.last, 64000.0);
    TEST_ASSERT_TRUE(fabs(d - 1280.0) < 1e-6);
    TEST_ASSERT_EQUAL_UINT32(1280u, ramp_stop(&r, 64000.0, 0u, r.last));
    TEST_ASSERT_EQUAL_UINT32(1279u + 0u, ramp_stop(&r, 64000.0, 1u, r.last) - 0u);   /* extra deducted */
}

static void test_retarget_total(void)
{
    ramp_t r;
    uint32_t i;
    ramp_start(&r, 90e6, 80000.0, 80000.0, 1600.0, 1800.0f, 10000u);
    for (i = 0u; i < 100u; i++) {
        (void)ramp_next(&r);
    }
    ramp_set_total(&r, 150u);
    TEST_ASSERT_EQUAL_UINT32(50u, r.rem);
    ramp_set_total(&r, 90u);                    /* end already passed: nothing more to generate */
    TEST_ASSERT_EQUAL_UINT32(0u, r.rem);
    TEST_ASSERT_EQUAL_UINT32(100u, ramp_total(&r));
}

static void test_stop_paths(void)               /* ICD §6.5, D-29 d / D-30 */
{
    uint32_t i;
    for (i = 0u; i < VEC_SPATH_N; i++) {
        const vec_spath_t *x = &VEC_SPATH[i];
        double d = ramp_stop_dist(90e6, (float)x->p_ticks, ramp_steps_of(x->a_stop, x->spm));
        TEST_ASSERT_TRUE(fabs(d - x->d_steps) <= 1e-9 * (1.0 + x->d_steps));
        TEST_ASSERT_EQUAL_UINT8(x->path, stepgen_ctrl_path(x->p_ticks, d, 90000000u));
    }
}

static void test_planner(void)
{
    uint32_t i;
    for (i = 0u; i < VEC_PLAN_N; i++) {
        const vec_plan_t *x = &VEC_PLAN[i];
        ramp_plan_t p = ramp_plan(x->n, x->v, x->a, x->d);
        TEST_ASSERT_EQUAL(x->tri, p.triangle);
        TEST_ASSERT_EQUAL_UINT32(x->n_acc, p.n_acc);
        TEST_ASSERT_EQUAL_UINT32(x->n_cruise, p.n_cruise);
        TEST_ASSERT_EQUAL_UINT32(x->n_dec, p.n_dec);
        TEST_ASSERT_TRUE(fabs(p.v_peak - x->v_peak) < 1e-6);
        TEST_ASSERT_TRUE(fabs(p.t_s - x->t) < 1e-9);
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_motion_vectors);
    RUN_TEST(test_tvm_spot_values);
    RUN_TEST(test_stop_never_faster);
    RUN_TEST(test_stop_distance);
    RUN_TEST(test_retarget_total);
    RUN_TEST(test_stop_paths);
    RUN_TEST(test_planner);
    return UNITY_END();
}
