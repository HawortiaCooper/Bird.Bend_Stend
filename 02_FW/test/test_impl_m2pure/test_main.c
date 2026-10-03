/* M2 pure modules: CLEAN/TRUNCATE/stretch decisions (stepgen.h), FW load limit (loadlim), driver
 * monitor (drvmon: DRV_PWR 20 ms filter, K1 timer, ALM filter), input filters (inputs.h), homing
 * phase logic (homing), HX711 math.
 * Verifies: SAF-FW-002/004 (TC-SAF-FW-004-02 halt decision grid), SAF-FW-003 (extend-only stretch),
 *           SAF-FW-008/009/011 (TC-SAF-FW-008-02 trip samples, rails, regrow), SAF-FW-024/025,
 *           FW-SW-001/003/004/005, FW-HOM-001/002/004, FW-AFE-001/002/003
 */
#include <unity.h>

#include "drvmon.h"
#include "homing.h"
#include "hx711_math.h"
#include "inputs.h"
#include "loadlim.h"
#include "params_gen.h"
#include "proto_gen.h"
#include "stepgen.h"

void setUp(void) {}
void tearDown(void) {}

/* ---------------- stepgen ---------------- */
static void test_halt_decision_grid(void)       /* every CNT for PW 225 / 900 / 9000 (TC-SAF-FW-004-02) */
{
    static const uint32_t pws[3] = {225u, 900u, 9000u};
    uint32_t k, cnt, guard = 45u;               /* 0.5 us at 90 MHz */
    for (k = 0u; k < 3u; k++) {
        uint32_t arr = 20000u, ccr1 = arr + 1u - pws[k];
        for (cnt = 0u; cnt <= arr; cnt++) {
            bool complete = stepgen_halt_complete(cnt, ccr1, guard);
            TEST_ASSERT_EQUAL(cnt + guard >= ccr1, complete);
            TEST_ASSERT_EQUAL(cnt >= ccr1, stepgen_abort_cuts(cnt, ccr1));
        }
    }
}

static void test_stretch_extend_only(void)
{
    uint32_t pw = 900u, arr = 89999u, ccr1 = arr + 1u - pw, guard = 45u;
    TEST_ASSERT_TRUE(stepgen_stretch_ok(1000u, ccr1, arr, pw, guard, 120000u));
    TEST_ASSERT_FALSE(stepgen_stretch_ok(1000u, ccr1, arr, pw, guard, 80000u));    /* would shorten */
    TEST_ASSERT_FALSE(stepgen_stretch_ok(ccr1, ccr1, arr, pw, guard, 120000u));    /* pulse in flight */
    TEST_ASSERT_FALSE(stepgen_stretch_ok(ccr1 - 10u, ccr1, arr, pw, guard, 120000u)); /* in the guard */
}

/* ---------------- loadlim ---------------- */
static void test_loadlim_trip_rails_regrow(void)
{
    loadlim_t l;
    loadlim_init(&l, -7022271, 7022271, 1u, 128849);
    TEST_ASSERT_FALSE(loadlim_check(&l, 7022271, false));               /* at the threshold: ok */
    TEST_ASSERT_TRUE(loadlim_check(&l, 7022272, false));                /* raw_max + 1 */
    TEST_ASSERT_TRUE(loadlim_check(&l, -7022272, false));
    loadlim_init(&l, -7151121, 7151121, 1u, 128849);                   /* thresholds at the caps */
    TEST_ASSERT_TRUE(loadlim_check(&l, 8388607, true));                 /* rail (SAF-FW-009) */
    TEST_ASSERT_TRUE(loadlim_check(&l, -8388608, true));
    /* 2 consecutive samples */
    loadlim_init(&l, -1000, 1000, 2u, 100);
    TEST_ASSERT_FALSE(loadlim_check(&l, 1001, false));
    TEST_ASSERT_FALSE(loadlim_check(&l, 0, false));                     /* alternating: never */
    TEST_ASSERT_FALSE(loadlim_check(&l, 1001, false));
    TEST_ASSERT_TRUE(loadlim_check(&l, 1001, false));
    /* regrow after FAULT_CLEAR with the load still beyond (SAF-FW-011) */
    loadlim_init(&l, -1000, 1000, 1u, 100);
    TEST_ASSERT_TRUE(loadlim_check(&l, 1500, false));
    loadlim_on_clear(&l, 1500, false);
    TEST_ASSERT_FALSE(loadlim_check(&l, 1450, false));                  /* unloading */
    TEST_ASSERT_FALSE(loadlim_check(&l, 1600, false));                  /* grew by 100: not > regrow */
    TEST_ASSERT_TRUE(loadlim_check(&l, 1601, false));                   /* > regrow: immediate */
    loadlim_on_clear(&l, 1601, false);
    TEST_ASSERT_FALSE(loadlim_check(&l, 900, false));                   /* back inside: mode ends */
    TEST_ASSERT_TRUE(loadlim_check(&l, 1001, false));                   /* normal rule again */
    loadlim_on_clear(&l, 0, false);                                     /* cleared while inside */
    TEST_ASSERT_TRUE(loadlim_check(&l, 1001, false));
    /* thresholds changed between samples: effective from the next sample (SAF-FW-010) */
    loadlim_init(&l, -1000, 1000, 1u, 100);
    TEST_ASSERT_FALSE(loadlim_check(&l, 900, false));
    loadlim_config(&l, -800, 800, 1u, 100);
    TEST_ASSERT_TRUE(loadlim_check(&l, 900, false));
}

/* ---------------- drvmon ---------------- */
static void test_drvmon_filter_and_k1(void)
{
    drvmon_t m;
    drvmon_ev_t e;
    uint32_t i;
    drvmon_init(&m, true, true, false);
    for (i = 0u; i < 19u; i++) {                                        /* 19 ms toggle: ignored */
        e = drvmon_sample(&m, false, false, false, 200u, 20u);
        TEST_ASSERT_FALSE(e.pwr_changed);
    }
    e = drvmon_sample(&m, true, false, false, 200u, 20u);
    TEST_ASSERT_TRUE(drvmon_power(&m));
    for (i = 1u; i <= 20u; i++) {                                       /* loss accepted at 20 ms */
        e = drvmon_sample(&m, false, false, false, 200u, 20u);
        TEST_ASSERT_EQUAL(i == 20u, e.pwr_lost);
    }
    TEST_ASSERT_FALSE(drvmon_power(&m));
    /* K1 weld: E-stop open with power held on (k1 = 200): no fault at 150 ms, fault at 201 */
    drvmon_init(&m, true, true, false);
    for (i = 1u; i <= 201u; i++) {
        e = drvmon_sample(&m, true, true, false, 200u, 20u);
        TEST_ASSERT_EQUAL(i == 201u, e.k1_fault);
        if (i == 150u) {
            TEST_ASSERT_TRUE(drvmon_k1_cause(&m, true));
        }
    }
    e = drvmon_sample(&m, true, true, false, 200u, 20u);
    TEST_ASSERT_FALSE(e.k1_fault);                                      /* once per episode */
    /* normal case: power drops 60 ms after the E-stop -> no fault */
    drvmon_init(&m, true, true, false);
    for (i = 1u; i <= 300u; i++) {
        e = drvmon_sample(&m, i < 60u, true, false, 200u, 20u);
        TEST_ASSERT_FALSE(e.k1_fault);
    }
    /* sensing disabled: power always present, never K1 */
    drvmon_init(&m, false, false, false);
    for (i = 0u; i < 300u; i++) {
        e = drvmon_sample(&m, false, true, false, 100u, 20u);
        TEST_ASSERT_FALSE(e.k1_fault || e.pwr_changed);
    }
    TEST_ASSERT_TRUE(drvmon_power(&m));
}

static void test_alm_filter_chatter(void)       /* OBS-P1-10: chatter -> <= 1 pair per release_ms */
{
    drvmon_t m;
    uint32_t i, changes = 0u;
    drvmon_init(&m, true, true, false);
    for (i = 0u; i < 1000u; i++) {                                      /* 1 kHz sampled 50 % chatter */
        drvmon_ev_t e = drvmon_sample(&m, true, false, (i & 1u) != 0u, 200u, 20u);
        changes += e.alm_changed ? 1u : 0u;
    }
    TEST_ASSERT_EQUAL_UINT32(1u, changes);                              /* active at the first sample */
    for (i = 0u; i < 20u; i++) {
        drvmon_ev_t e = drvmon_sample(&m, true, false, false, 200u, 20u);
        TEST_ASSERT_EQUAL(i == 19u, e.alm_changed);                     /* inactive after 20 ms stable */
    }
}

/* ---------------- inputs ---------------- */
static void test_release_filter_and_button(void)
{
    relf_t f;
    btn_t b;
    uint32_t i;
    relf_init(&f, true);
    for (i = 1u; i <= 20u; i++) {
        TEST_ASSERT_EQUAL(i == 20u, relf_sample(&f, false, false, 20u));   /* latch kept at 19 ms */
    }
    TEST_ASSERT_TRUE(relf_released(&f, 20u));
    (void)relf_sample(&f, false, true, 20u);                            /* an edge between samples */
    TEST_ASSERT_FALSE(relf_released(&f, 20u));
    btn_init(&b, false);
    TEST_ASSERT_EQUAL_UINT8(BTN_PRESSED, btn_edge(&b));
    TEST_ASSERT_EQUAL_UINT8(BTN_NONE, btn_edge(&b));                    /* bounce: same press */
    for (i = 0u; i < 19u; i++) {
        TEST_ASSERT_EQUAL_UINT8(BTN_NONE, btn_sample(&b, false, false, 20u));
    }
    TEST_ASSERT_EQUAL_UINT8(BTN_RELEASED, btn_sample(&b, false, false, 20u));
    btn_init(&b, true);                                                 /* held at boot: no press */
    TEST_ASSERT_EQUAL_UINT8(BTN_NONE, btn_sample(&b, true, false, 20u));
}

/* ---------------- homing ---------------- */
static void test_homing_phases(void)
{
    home_geo_t g = {8000, 1600, 8000, 288000};
    home_next_t n;
    home_in_t in = {0};
    n = home_begin(false, 0, &g);
    TEST_ASSERT_EQUAL_UINT8(HP_FAST_SEEK, n.phase);
    TEST_ASSERT_EQUAL_INT32(-288000, n.end_steps);
    TEST_ASSERT_EQUAL_INT8(-1, n.dir);
    n = home_begin(true, 100, &g);
    TEST_ASSERT_EQUAL_UINT8(HP_RELEASE, n.phase);
    TEST_ASSERT_EQUAL_INT32(8100, n.end_steps);
    TEST_ASSERT_TRUE(n.slow);
    /* FAST_SEEK: START edge -> BACKOFF; END edge -> WIRING; planned end -> NOT_FOUND */
    in.pos = -1200; in.start_edge = true;
    n = home_segment_end(HP_FAST_SEEK, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HP_BACKOFF, n.phase);
    TEST_ASSERT_EQUAL_INT32(-1200 + 8000, n.end_steps);
    in.start_edge = false; in.end_edge = true;
    n = home_segment_end(HP_FAST_SEEK, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HOME_ACT_FAIL, n.action);
    TEST_ASSERT_EQUAL_UINT8(HF_WIRING, n.fail);
    in.end_edge = false; in.planned_end = true;
    n = home_segment_end(HP_FAST_SEEK, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HF_NOT_FOUND, n.fail);
    /* BACKOFF: released -> SLOW_APPROACH over backoff + 10 mm; not released -> WIRING; bounce -> again */
    in.pos = 400; in.origin = -1200; in.planned_end = true; in.released = true;
    n = home_segment_end(HP_BACKOFF, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HP_SLOW_APPROACH, n.phase);
    TEST_ASSERT_EQUAL_INT32(400 - 1600 - 8000, n.end_steps);
    in.released = false;
    n = home_segment_end(HP_BACKOFF, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HF_WIRING, n.fail);
    in.planned_end = false; in.start_edge = true;
    n = home_segment_end(HP_BACKOFF, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HOME_ACT_SEGMENT, n.action);
    TEST_ASSERT_EQUAL_UINT8(HP_BACKOFF, n.phase);
    TEST_ASSERT_EQUAL_INT32(-1200 + 8000, n.end_steps);
    /* SLOW_APPROACH: edge -> zero; none -> NOT_FOUND */
    in.start_edge = true;
    n = home_segment_end(HP_SLOW_APPROACH, &in, &g);
    TEST_ASSERT_EQUAL_UINT8(HOME_ACT_ZERO, n.action);
    TEST_ASSERT_EQUAL_UINT8(HP_MOVE_TO_ZERO, n.phase);
    in.start_edge = false;
    TEST_ASSERT_EQUAL_UINT8(HF_NOT_FOUND, home_segment_end(HP_SLOW_APPROACH, &in, &g).fail);
    TEST_ASSERT_EQUAL_UINT8(HOME_ACT_DONE, home_segment_end(HP_MOVE_TO_ZERO, &in, &g).action);
}

static void test_home_zero_and_drift(void)     /* FW-HOM-004: 0.1 mm no fault, 0.3 mm HOME_DRIFT */
{
    home_zero_t z = home_zero(-1150, -1200, 800, false, -1500, 1000, 200u);
    TEST_ASSERT_EQUAL_INT32(-1150 + 1200 - 800, z.new_count);   /* the edge lies at -offset */
    TEST_ASSERT_EQUAL_INT32(0, z.drift_um);
    TEST_ASSERT_FALSE(z.drift_fault);
    z = home_zero(0, -880, 800, true, -1100, 1000, 200u);       /* edge 100 µm off */
    TEST_ASSERT_EQUAL_INT32(-100, z.drift_um);
    TEST_ASSERT_FALSE(z.drift_fault);
    z = home_zero(0, -1040, 800, true, -1300, 1000, 200u);      /* 300 µm */
    TEST_ASSERT_EQUAL_INT32(-300, z.drift_um);
    TEST_ASSERT_TRUE(z.drift_fault);
}

/* ---------------- HX711 math (TS reference values, D-39) ---------------- */
static void test_hx711_math(void)
{
    static const uint32_t in[] = {0x000000u, 0x000001u, 0x7FFFFFu, 0x800000u, 0xFFFFFFu, 0x800001u, 0x123456u,
                                  0xEDCBAAu};
    static const int32_t out[] = {0, 1, 8388607, -8388608, -1, -8388607, 1193046, -1193046};
    hx_settle_t s;
    unsigned i;
    for (i = 0u; i < 8u; i++) {
        TEST_ASSERT_EQUAL_INT32(out[i], hx711_sign_extend(in[i]));
    }
    TEST_ASSERT_TRUE(hx711_is_saturated(0x7FFFFFu));
    TEST_ASSERT_TRUE(hx711_is_saturated(0x800000u));
    TEST_ASSERT_FALSE(hx711_is_saturated(0x7FFFFEu));
    TEST_ASSERT_TRUE(hx711_raw_at_rail(-8388608));
    TEST_ASSERT_FALSE(hx711_raw_at_rail(8388606));
    TEST_ASSERT_EQUAL_UINT8(25u, hx711_pulses(AFE_GAIN_CHANNEL_A128));
    TEST_ASSERT_EQUAL_UINT8(26u, hx711_pulses(AFE_GAIN_CHANNEL_B32));
    TEST_ASSERT_EQUAL_UINT8(27u, hx711_pulses(AFE_GAIN_CHANNEL_A64));
    hx_settle_arm(&s, 4u);
    for (i = 0u; i < 6u; i++) {
        TEST_ASSERT_EQUAL(i < 4u, hx_settle_take(&s));      /* exactly afe.settle_discard (FW-AFE-003) */
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_halt_decision_grid);
    RUN_TEST(test_stretch_extend_only);
    RUN_TEST(test_loadlim_trip_rails_regrow);
    RUN_TEST(test_drvmon_filter_and_k1);
    RUN_TEST(test_alm_filter_chatter);
    RUN_TEST(test_release_filter_and_button);
    RUN_TEST(test_homing_phases);
    RUN_TEST(test_home_zero_and_drift);
    RUN_TEST(test_hx711_math);
    return UNITY_END();
}
