/* MOVE_UNTIL_LOAD (FW-MOT-006, D-44: M4 scope pulled forward): pure decision (mul.c) and the core
 * motion on the fake seams with a position-dependent load model (specimen spring, samples every 12 ms):
 * stop on the first sample beyond raw_stop for both cmp values (CLEAN halt in the sample ISR, no PUL
 * edge after the deciding sample), already beyond -> no pulse, bound -> MOVE_DONE BOUND exactly at the
 * bound, soft-limit bound inclusive, gating (HOMED, F-B-28, v_max_load cap, cmp range, busy), load limit
 * precedence, PC STOP, PAUSE + threshold during the deceleration, AFE stale, VALID unchanged, one
 * MOVE_DONE per motion, a repeated command never moves further; the Integrator's immediate-stop vectors
 * (motion_vectors.json `immediate_stops` and `threshold_in_controlled_stop`, ref_motion.py) with exact
 * pulse counts.
 * Verifies: FW-MOT-006, SAF-FW-002 (load-path stop in the sample ISR), SAF-FW-008 (load limit stays
 *           active during MOVE_UNTIL_LOAD), SAF-FW-012, FW-MOT-009 (v_max_load cap), SAF-FW-001
 */
#include <unity.h>

#include "hal_step.h"
#include "motion_util.h"
#include "mul.h"
#include "units.h"
#include "vec_motion.h"

void setUp(void) { mu_boot(); }
void tearDown(void) {}

/* ---------------- pure decision ---------------- */
static void test_pure_beyond(void)
{
    TEST_ASSERT_TRUE(mul_beyond(100, 100, CMP_GE));                /* equality is beyond */
    TEST_ASSERT_TRUE(mul_beyond(101, 100, CMP_GE));
    TEST_ASSERT_FALSE(mul_beyond(99, 100, CMP_GE));
    TEST_ASSERT_TRUE(mul_beyond(-100, -100, CMP_LE));
    TEST_ASSERT_TRUE(mul_beyond(-101, -100, CMP_LE));
    TEST_ASSERT_FALSE(mul_beyond(-99, -100, CMP_LE));
    TEST_ASSERT_TRUE(mul_beyond(PROTO_RAW_MAX, PROTO_RAW_MAX, CMP_GE));
    TEST_ASSERT_FALSE(mul_beyond(PROTO_RAW_MAX - 1, PROTO_RAW_MAX, CMP_GE));
    TEST_ASSERT_TRUE(mul_beyond(PROTO_RAW_MIN, PROTO_RAW_MIN, CMP_LE));
    TEST_ASSERT_FALSE(mul_beyond(PROTO_RAW_MIN + 1, PROTO_RAW_MIN, CMP_LE));
    TEST_ASSERT_TRUE(mul_beyond(0, 1000, 2u));                     /* undefined cmp: fail-safe */
}

static void test_pure_precheck_and_sample(void)
{
    TEST_ASSERT_FALSE(mul_precheck(false, 500, 100, CMP_GE));      /* no sample yet */
    TEST_ASSERT_FALSE(mul_precheck(true, PROTO_AFE_NO_DATA, 100, CMP_LE));
    TEST_ASSERT_TRUE(mul_precheck(true, 100, 100, CMP_GE));
    TEST_ASSERT_FALSE(mul_precheck(true, 99, 100, CMP_GE));
    TEST_ASSERT_TRUE(mul_precheck(true, -5, 0, CMP_LE));
    /* armed, not hit, running, no load trip, beyond -> stop */
    TEST_ASSERT_TRUE(mul_sample_stop(true, false, true, false, 200, 100, CMP_GE));
    TEST_ASSERT_FALSE(mul_sample_stop(false, false, true, false, 200, 100, CMP_GE));   /* not armed */
    TEST_ASSERT_FALSE(mul_sample_stop(true, true, true, false, 200, 100, CMP_GE));     /* recorded */
    TEST_ASSERT_FALSE(mul_sample_stop(true, false, false, false, 200, 100, CMP_GE));   /* timer stopped */
    TEST_ASSERT_FALSE(mul_sample_stop(true, false, true, true, 200, 100, CMP_GE));     /* load limit */
    TEST_ASSERT_FALSE(mul_sample_stop(true, false, true, false, 99, 100, CMP_GE));
    TEST_ASSERT_TRUE(mul_sample_stop(true, false, true, false, -200, -100, CMP_LE));
    TEST_ASSERT_EQUAL_INT8(1, mul_dir(10, 5));
    TEST_ASSERT_EQUAL_INT8(-1, mul_dir(-10, 5));
    TEST_ASSERT_EQUAL_INT8(0, mul_dir(5, 5));
    TEST_ASSERT_EQUAL_INT8(-1, mul_dir(INT32_MIN, INT32_MAX));
}

/* ---------------- core: load model + sample watcher ---------------- */
#define SPEC_NONE  0
#define SPEC_PULL  1   /* raw = k * (pos - c) for pos > c (tension, raw rises with +x) */
#define SPEC_PUSH  2   /* raw = -k * (c - pos) for pos < c (compression approached from +x) */
#define SPEC_STEP  3   /* raw = k for pos >= c (jump) */

static int     s_spec;
static int32_t s_k, s_c, s_raw0;
static bool    s_samples_on;

typedef struct {
    bool     on, found;
    int32_t  raw_stop;
    uint8_t  cmp;
    int32_t  pos, raw;            /* deciding sample: latched position and value */
    uint32_t rises;               /* PUL rising edges at the deciding sample */
    uint32_t stop_calls;          /* hal_step_stop_now() calls made inside that sample callback */
} watch_t;
static watch_t W;

static int32_t spec_raw(int32_t pos)
{
    switch (s_spec) {
    case SPEC_PULL: return (pos > s_c) ? s_raw0 + s_k * (pos - s_c) : s_raw0;
    case SPEC_PUSH: return (pos < s_c) ? s_raw0 - s_k * (s_c - pos) : s_raw0;
    case SPEC_STEP: return (pos >= s_c) ? s_k : s_raw0;
    default: return s_raw0;
    }
}

static bool ref_beyond(int32_t raw, int32_t stop, uint8_t cmp)  /* independent of mul.c */
{
    return (cmp == CMP_GE) ? raw >= stop : raw <= stop;
}

static void ml_run(uint32_t ms)
{
    uint32_t i;
    for (i = 0u; i < ms; i++) {
        fake_run_ms(1u);
        if (s_samples_on && (fake_now_us() / 1000u) % 12u == 0u) {
            int32_t pos = fake_step_count();
            int32_t raw = spec_raw(pos);
            bool moving = hal_step_running();
            uint32_t rises = fake_rise_n, calls = fake_stop_now_calls;
            fake_sample(fake_now_us(), raw);
            if (W.on && !W.found && moving && ref_beyond(raw, W.raw_stop, W.cmp)) {
                W.found = true;
                W.pos = pos;
                W.raw = raw;
                W.rises = rises;
                W.stop_calls = fake_stop_now_calls - calls;
            }
        }
    }
}

static uint32_t ml_until_idle(uint32_t max_ms)
{
    uint32_t t;
    for (t = 0u; t < max_ms; t++) {
        ml_run(1u);
        if (!motion_active()) {
            break;
        }
    }
    ml_run(3u);                                                   /* fold + EVENTs */
    return t;
}

static const fake_frame_t *ml_cmd(int32_t bound, uint32_t v, uint32_t a, int32_t raw_stop, uint8_t cmp)
{
    uint8_t pl[17];
    le_put32(pl, (uint32_t)bound);
    le_put32(&pl[4], v);
    le_put32(&pl[8], a);
    le_put32(&pl[12], (uint32_t)raw_stop);
    pl[16] = cmp;
    return h_cmd(CMD_MOVE_UNTIL_LOAD, 0x64u, pl, 17u);
}

static void ml_setup(int spec, int32_t k, int32_t c, int32_t raw0)
{
    s_spec = spec;
    s_k = k;
    s_c = c;
    s_raw0 = raw0;
    s_samples_on = true;
    memset(&W, 0, sizeof W);
    mu_raw = spec_raw(0);
    mu_afe_on = true;
    mu_enable();                                                  /* mu_run samples during the settle */
    mu_afe_on = false;                                            /* from here: samples from ml_run only */
    ml_run(30u);
    g_fw.homed = true;
}

static void watch(int32_t raw_stop, uint8_t cmp)
{
    W.on = true;
    W.found = false;
    W.raw_stop = raw_stop;
    W.cmp = cmp;
}

static void set_valid(void)
{
    uint8_t pl[1] = {1u};
    h_expect_ok(h_cmd(CMD_SET_VALID, 0x65u, pl, 1u));
}

static void expect_threshold_stop(uint32_t from)
{
    int32_t i;
    TEST_ASSERT_TRUE_MESSAGE(W.found, "no sample beyond the threshold while moving");
    TEST_ASSERT_TRUE(W.stop_calls >= 1u);                         /* CLEAN halt inside the sample ISR */
    TEST_ASSERT_EQUAL_UINT32(W.rises, fake_rise_n);               /* no PUL edge after the deciding sample */
    TEST_ASSERT_TRUE(fake_step_count() - W.pos >= 0 && fake_step_count() - W.pos <= 1);   /* pulse in flight */
    TEST_ASSERT_FALSE(motion_active());
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    i = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_EQUAL_UINT16(MD_LOAD_THRESHOLD, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_INT32(fake_step_count(), mu_ev_val2(i));
    TEST_ASSERT_EQUAL_INT32(units_steps_to_um(fake_step_count(), 800u), mu_ev_val(i));
    TEST_ASSERT_TRUE(mu_ev(EV_STOPPED, from) < 0);                /* not a stop source */
    TEST_ASSERT_TRUE(mu_ev(EV_VALID_CLEARED, from) < 0);          /* VALID unchanged */
    TEST_ASSERT_EQUAL_HEX8(DF_VALID, h_status().b[8] & DF_VALID);
    TEST_ASSERT_TRUE(g_fw.homed);
    TEST_ASSERT_FALSE(g_fw.pos_uncertain);                        /* CLEAN: no truncated pulse */
}

static void test_ge_pull_stops_on_first_sample(void)   /* FW-MOT-006 cmp GE, +x */
{
    uint32_t from;
    ml_setup(SPEC_PULL, 50, 4000, 0);                             /* contact at 5 mm, 50 counts/step */
    set_valid();
    from = fake_cap_n;
    watch(100000, CMP_GE);
    h_expect_ok(ml_cmd(50000, 5000u, 0u, 100000, CMP_GE));
    TEST_ASSERT_EQUAL_UINT8(MS_MOVE_UNTIL_LOAD, g_fw.motion_state);
    TEST_ASSERT_EQUAL_INT32(50000, motion_target_um());           /* STATUS target_um = bound */
    TEST_ASSERT_EQUAL_INT(1, fake_dir_arg);
    TEST_ASSERT_TRUE(ml_until_idle(20000u) < 20000u);
    expect_threshold_stop(from);
    TEST_ASSERT_TRUE(W.pos >= 6000 && W.pos < 6000 + 60);         /* first sample past 100000 counts */
    /* a repeated command never moves further: already beyond -> no pulse */
    {
        uint32_t n = fake_rise_n, from2 = fake_cap_n;
        h_expect_ok(ml_cmd(50000, 5000u, 0u, 100000, CMP_GE));
        ml_run(30u);
        TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);
        TEST_ASSERT_FALSE(motion_active());
        TEST_ASSERT_EQUAL_UINT16(MD_LOAD_THRESHOLD, mu_ev_arg(mu_ev(EV_MOVE_DONE, from2)));
        TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from2));
    }
}

static void test_le_push_negative_direction(void)       /* FW-MOT-006 cmp LE, -x */
{
    uint32_t from;
    ml_setup(SPEC_PUSH, 40, 30000, 0);                            /* contact at 37.5 mm from +x */
    h_expect_ok(mu_move(45000, 20000u, 0u));
    (void)ml_until_idle(20000u);
    TEST_ASSERT_EQUAL_INT32(36000, fake_step_count());
    set_valid();
    from = fake_cap_n;
    watch(-60000, CMP_LE);
    h_expect_ok(ml_cmd(20000, 4000u, 50000u, -60000, CMP_LE));
    TEST_ASSERT_EQUAL_INT(-1, fake_dir_arg);
    (void)ml_until_idle(30000u);
    expect_threshold_stop(from);
    TEST_ASSERT_TRUE(W.pos <= 28500 && W.pos > 28500 - 60);
}

static void test_bound_reached_planned_stop(void)       /* MOVE_DONE BOUND exactly at the bound */
{
    uint32_t from;
    int32_t i;
    ml_setup(SPEC_NONE, 0, 0, 1234);
    from = fake_cap_n;
    h_expect_ok(ml_cmd(20000, 10000u, 0u, 100000, CMP_GE));
    (void)ml_until_idle(10000u);
    TEST_ASSERT_EQUAL_INT32(16000, fake_step_count());
    TEST_ASSERT_EQUAL_UINT32(16000u, fake_rise_n);
    i = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_EQUAL_UINT16(MD_BOUND, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_INT32(20000, mu_ev_val(i));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    TEST_ASSERT_TRUE(mu_ev(EV_STOPPED, from) < 0);
    TEST_ASSERT_TRUE(mu_intervals_non_decreasing(15000u));        /* planned deceleration */
    /* soft limit as the bound is inclusive (D-32 / D-33 d); beyond it -> E_RANGE 0 */
    h_set_param(PID_LIMITS_SOFT_MAX_UM, PARAM_T_I32, 30000u);
    h_expect_nack(ml_cmd(30001, 10000u, 0u, 100000, CMP_GE), ST_E_RANGE, 0u);
    from = fake_cap_n;
    h_expect_ok(ml_cmd(30000, 10000u, 0u, 100000, CMP_GE));
    (void)ml_until_idle(10000u);
    TEST_ASSERT_EQUAL_INT32(24000, fake_step_count());
    TEST_ASSERT_EQUAL_UINT16(MD_BOUND, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
}

static void test_already_beyond_no_pulse(void)          /* pre-check of the last sample */
{
    uint32_t from;
    int32_t i;
    ml_setup(SPEC_NONE, 0, 0, 100000);                            /* raw == raw_stop: beyond (GE) */
    from = fake_cap_n;
    h_expect_ok(ml_cmd(20000, 10000u, 0u, 100000, CMP_GE));
    TEST_ASSERT_FALSE(motion_active());
    TEST_ASSERT_EQUAL_UINT8(MS_IDLE, g_fw.motion_state);
    ml_run(20u);
    TEST_ASSERT_EQUAL_UINT32(0u, fake_rise_n);
    i = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_EQUAL_UINT16(MD_LOAD_THRESHOLD, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_INT32(0, mu_ev_val2(i));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    /* LE: below the threshold already */
    s_raw0 = -5;
    ml_run(13u);
    from = fake_cap_n;
    h_expect_ok(ml_cmd(20000, 10000u, 0u, 0, CMP_LE));
    ml_run(20u);
    TEST_ASSERT_EQUAL_UINT32(0u, fake_rise_n);
    TEST_ASSERT_EQUAL_UINT16(MD_LOAD_THRESHOLD, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    /* not beyond: it moves */
    h_expect_ok(ml_cmd(20000, 10000u, 0u, -6, CMP_LE));
    ml_run(50u);
    TEST_ASSERT_TRUE(fake_rise_n > 0u);
}

static void test_gating(void)                           /* cmd_check part (ICD §5.4, F-B-28) */
{
    ml_setup(SPEC_NONE, 0, 0, 0);
    g_fw.homed = false;
    h_expect_nack(ml_cmd(20000, 1000u, 0u, 100, CMP_GE), ST_E_STATE, BLOCK_NOT_HOMED);
    g_fw.homed = true;
    h_expect_ok(mu_move(10000, 20000u, 0u));
    (void)ml_until_idle(5000u);
    h_expect_nack(ml_cmd(10000, 1000u, 0u, 100, CMP_GE), ST_E_RANGE, 0u);       /* bound = position */
    h_expect_nack(ml_cmd(400, 1000u, 0u, 100, CMP_GE), ST_E_RANGE, 0u);         /* < soft_min */
    h_expect_nack(ml_cmd(20000, 0u, 0u, 100, CMP_GE), ST_E_RANGE, 4u);
    h_expect_nack(ml_cmd(20000, 20001u, 0u, 100, CMP_GE), ST_E_RANGE, 4u);      /* v_max_load, unloaded */
    h_expect_nack(ml_cmd(20000, 1000u, 100001u, 100, CMP_GE), ST_E_RANGE, 8u);
    h_expect_nack(ml_cmd(20000, 1000u, 0u, PROTO_RAW_MAX + 1, CMP_GE), ST_E_RANGE, 12u);
    h_expect_nack(ml_cmd(20000, 1000u, 0u, 100, 2u), ST_E_RANGE, 16u);
    TEST_ASSERT_EQUAL_UINT32(8000u, fake_rise_n);                 /* NACKs: no side effect */
    h_expect_ok(ml_cmd(20000, 20000u, 0u, 100000, CMP_GE));       /* v = v_max_load accepted */
    h_expect_nack(ml_cmd(30000, 1000u, 0u, 100000, CMP_GE), ST_E_BUSY, BUSY_MOTION);
    h_expect_nack(mu_move(30000, 1000u, 0u), ST_E_BUSY, BUSY_MOTION);
    (void)ml_until_idle(5000u);
    TEST_ASSERT_EQUAL_INT32(16000, fake_step_count());
}

static void test_load_limit_has_precedence(void)        /* SAF-FW-008 stays active */
{
    uint32_t from;
    int32_t i;
    ml_setup(SPEC_STEP, 200000, 8000, 0);                         /* jump to 200000 counts at 10 mm */
    h_set_param(PID_SAFETY_LOAD_RAW_MAX, PARAM_T_I32, 150000u);
    from = fake_cap_n;
    watch(120000, CMP_GE);
    h_expect_ok(ml_cmd(50000, 5000u, 0u, 120000, CMP_GE));
    (void)ml_until_idle(20000u);
    TEST_ASSERT_TRUE(W.found);
    TEST_ASSERT_EQUAL_UINT32(W.rises, fake_rise_n);               /* stopped by the load limit, same ISR */
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_LOAD_LIMIT) != 0u);
    i = mu_ev(EV_FAULT_SET, from);
    TEST_ASSERT_EQUAL_UINT16(FAULT_LOAD_LIMIT_BIT, mu_ev_arg(i));
    TEST_ASSERT_EQUAL_UINT16(SC_LOAD_LIMIT, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
}

static void test_pc_stop_during_move(void)
{
    uint32_t from, n;
    ml_setup(SPEC_NONE, 0, 0, 0);
    h_expect_ok(ml_cmd(50000, 5000u, 0u, 100000, CMP_GE));
    ml_run(300u);
    from = fake_cap_n;
    h_expect_ok(mu_stop(0u));
    ml_run(5u);
    n = fake_rise_n;
    ml_run(200u);
    TEST_ASSERT_EQUAL_UINT32(n, fake_rise_n);
    TEST_ASSERT_EQUAL_UINT16(SC_PC_STOP, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    TEST_ASSERT_FALSE(motion_active());
}

static void test_pause_then_threshold_in_decel(void)    /* compare stays armed while STOPPING */
{
    uint32_t from;
    int32_t p0;
    ml_setup(SPEC_NONE, 0, 0, 0);
    h_set_param(PID_MOTION_A_STOP_UM_S2, PARAM_T_U32, 10000u);   /* decel 5 mm/s -> 0: ~1000 steps */
    h_expect_ok(ml_cmd(100000, 5000u, 0u, 25000, CMP_GE));
    ml_run(1000u);
    p0 = fake_step_count();
    s_spec = SPEC_PULL;                                           /* contact here, 50 counts/step */
    s_k = 50;
    s_c = p0;
    watch(25000, CMP_GE);
    from = fake_cap_n;
    h_expect_ok(h_cmd(CMD_PAUSE, 0x66u, NULL, 0u));
    TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    (void)ml_until_idle(3000u);
    TEST_ASSERT_TRUE(W.found);
    TEST_ASSERT_EQUAL_UINT32(W.rises, fake_rise_n);               /* CLEAN halt at the threshold */
    TEST_ASSERT_TRUE(fake_step_count() < p0 + 900);               /* deceleration cut short */
    TEST_ASSERT_EQUAL_UINT16(SC_PC_PAUSE, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));   /* first cause kept */
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
}

static void test_afe_stale_during_move(void)            /* SAF-FW-012: fallback frames are not samples */
{
    uint32_t from;
    ml_setup(SPEC_NONE, 0, 0, 0);
    h_expect_ok(ml_cmd(100000, 5000u, 0u, 100000, CMP_GE));
    ml_run(100u);
    from = fake_cap_n;
    s_samples_on = false;
    (void)ml_until_idle(1000u);
    TEST_ASSERT_TRUE((g_fw.lat.faults & FAULT_AFE_FAULT) != 0u);
    TEST_ASSERT_EQUAL_UINT16(SC_AFE_FAULT, mu_ev_arg(mu_ev(EV_STOPPED, from)));
    TEST_ASSERT_EQUAL_UINT16(MD_STOPPED, mu_ev_arg(mu_ev(EV_MOVE_DONE, from)));
}

/* Integrator vectors (motion_vectors.json, ref_motion.py):
 *  - `immediate_stops` (v0.7.2): a threshold sample taking effect after `after` steps of the base case
 *    stops with exactly `after` pulses (CLEAN; injected right after the update that completed step
 *    `after`, so no pulse is in flight);
 *  - `threshold_in_controlled_stop` (v0.7.2, OI-FW-43 b): the base case's controlled stop (STOP 1 at its
 *    a_stop) is applied first, the threshold sample during the deceleration cuts it with a CLEAN halt
 *    after `after` pulses; MOVE_DONE stays STOPPED (first cause kept). */
static void run_until_count(int32_t n)
{
    uint32_t us = 0u;
    while (fake_step_count() < n && us < 20000000u) {
        fake_advance_tk(90u);                                     /* 1 us */
        us++;
        if (fake_now_us() % 1000u == 0u) {
            core_tick_1ms();
            app_loop();
        }
        if (fake_now_us() % 12000u == 0u) {
            fake_sample(fake_now_us(), 0);                        /* below the threshold: keeps the AFE fresh */
        }
    }
    TEST_ASSERT_EQUAL_INT32(n, fake_step_count());
}

static void vector_stop_case(const vec_istop_t *x, bool with_events)
{
    const vec_motion_t *b = &VEC_MOTION[x->base];
    int32_t bound_um = (int32_t)((double)b->n_steps * 1000.0 / (double)b->spm + 0.5);
    uint32_t from, k;
    int32_t i;
    mu_boot();
    ml_setup(SPEC_NONE, 0, 0, (x->after == 0u) ? 500 : 0);     /* raw_stop 100: beyond only for after 0 */
    TEST_ASSERT_EQUAL_FLOAT(800.0f, b->spm);                     /* the fake runs at the default 800 */
    if (with_events && b->a_stop != 0u) {
        h_set_param(PID_MOTION_A_STOP_UM_S2, PARAM_T_U32, b->a_stop);
    }
    from = fake_cap_n;
    h_expect_ok(ml_cmd(bound_um, b->v, b->a, 100, CMP_GE));
    s_samples_on = false;
    for (k = 0u; with_events && k < b->n_ev; k++) {
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(0u, b->ev[k].kind, x->name);   /* controlled_stop only */
        run_until_count((int32_t)b->ev[k].after);
        h_expect_ok(mu_stop(1u));                                 /* STOP 1 = controlled, a_stop */
        TEST_ASSERT_EQUAL_UINT8(MS_STOPPING, g_fw.motion_state);
    }
    if (x->after != 0u) {
        run_until_count((int32_t)x->after);
        TEST_ASSERT_TRUE(hal_step_running());                     /* still moving / decelerating */
        fake_sample(fake_now_us(), 1000);                         /* the threshold sample */
    }
    s_samples_on = true;
    (void)ml_until_idle(20000u);
    TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)x->after, fake_step_count(), x->name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(x->after, fake_rise_n, x->name);
    i = mu_ev(EV_MOVE_DONE, from);
    TEST_ASSERT_TRUE(i >= 0);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(x->reason, mu_ev_arg(i), x->name);
    TEST_ASSERT_EQUAL_INT32((int32_t)x->after, mu_ev_val2(i));
    TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_MOVE_DONE, from));
    if (with_events && b->n_ev != 0u) {
        TEST_ASSERT_EQUAL_UINT16(SC_PC_STOP_CONTROLLED, mu_ev_arg(mu_ev(EV_STOPPED, from)));
        TEST_ASSERT_EQUAL_UINT32(1u, mu_ev_count(EV_STOPPED, from));
    }
}

static void test_immediate_stop_vectors(void)
{
    uint32_t k;
    for (k = 0u; k < VEC_ISTOP_N; k++) {
        vector_stop_case(&VEC_ISTOP[k], false);
    }
    TEST_ASSERT_TRUE(VEC_ISTOP_N >= 3u);
}

static void test_threshold_in_controlled_stop_vectors(void)
{
    uint32_t k;
    for (k = 0u; k < VEC_TICS_N; k++) {
        vector_stop_case(&VEC_TICS[k], true);
    }
    TEST_ASSERT_TRUE(VEC_TICS_N >= 1u);
}

static void test_feature_bit(void)
{
    TEST_ASSERT_TRUE((FW_FEATURES & FEAT_MOVE_UNTIL_LOAD) != 0u);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_pure_beyond);
    RUN_TEST(test_pure_precheck_and_sample);
    RUN_TEST(test_ge_pull_stops_on_first_sample);
    RUN_TEST(test_le_push_negative_direction);
    RUN_TEST(test_bound_reached_planned_stop);
    RUN_TEST(test_already_beyond_no_pulse);
    RUN_TEST(test_gating);
    RUN_TEST(test_load_limit_has_precedence);
    RUN_TEST(test_pc_stop_during_move);
    RUN_TEST(test_pause_then_threshold_in_decel);
    RUN_TEST(test_afe_stale_during_move);
    RUN_TEST(test_immediate_stop_vectors);
    RUN_TEST(test_threshold_in_controlled_stop_vectors);
    RUN_TEST(test_feature_bit);
    return UNITY_END();
}
