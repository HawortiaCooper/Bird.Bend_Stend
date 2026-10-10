/* Validator E - code review re-check §16 (02_FW/docs/FW_code_review.md, FW v0.8.6, D-55):
 * (a) the TARGET step-ISR entry (TIM2_IRQHandler -> step_isr_core(cnt), packed 64-bit result) against
 *     the SEAM entry the twin runs (TIM2_IRQHandler -> step_isr() -> step_isr_core(hal_step_count())),
 *     with the same inputs: the same target HAL source (step_tim2.c) is compiled twice on a TIM2
 *     register model (tu_core.c without HOST_TEST, tu_seam.c with it), both drive the REAL core
 *     (core/motion.c step_isr_core, set up by a real MOVE_ABS on A's fake seams), and every observable is
 *     compared: the core calls (count argument, packed result), PUL rise times, completed pulses,
 *     runts, update events, final count, stop generation, timer registers. Scenarios: plain jog to its
 *     target (LAST), missed update (count estimate -> STOP precedence), FWR-05 late latch (branch-free
 *     select), CLEAN / TRUNCATE halts between ISRs (FWR-21 window included) and with the ISR pending
 *     (DEF-M2-01), halts pre-empting the handler before the core is consulted (count read timing).
 * (b) the removed acc clamp of ramp_next_inl(): exhaustive float check of the proof.
 * Verifies: FW-MOT-002, FW-MOT-003 (ramp float path), SAF-FW-004, NFR-007 (v0.8.6 change, no behaviour
 *           change)
 * TC: TC-FW-MOT-002-03 (entry equivalence), TC-FW-MOT-003-03 (acc clamp)
 */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unity.h>

#include "motion_util.h"
#include "hal_step.h"
#include "stepgen.h"
#include "isrc_api.h"

/* ---------------------------------------------------------------- probes */
#define LOG_N 8192u
typedef struct {
    int32_t  arg;
    uint64_t ret;
} call_t;
static call_t   g_log[LOG_N];
static uint32_t g_calls, g_hook_at, g_cnt_mismatch;
static void (*g_hook)(void);
static int32_t (*g_cnt_of)(void);

static void run_hook(void)
{
    g_calls++;
    if (g_hook != NULL && g_calls == g_hook_at) {
        g_hook();
    }
}

static uint64_t logged(int32_t c, uint64_t v)
{
    if (g_calls - 1u < LOG_N) {
        g_log[g_calls - 1u].arg = c;
        g_log[g_calls - 1u].ret = v;
    }
    return v;
}

/* target entry: the count is passed in; it must equal the HAL count at the call (read timing) */
uint64_t val_probe_core(int32_t count)
{
    if (count != g_cnt_of()) {
        g_cnt_mismatch++;
    }
    run_hook();                                       /* a pre-empting halt after the count was taken */
    return logged(count, step_isr_core(count));
}

/* seam entry: step_isr() reads hal_step_count() itself, i.e. after a pre-empting halt */
uint64_t val_probe_seam(int32_t (*count_now)(void))
{
    int32_t c;
    run_hook();
    c = count_now();
    return logged(c, step_isr_core(c));
}

/* ---------------------------------------------------------------- the two HAL instances */
typedef struct {
    void (*reset)(uint32_t, uint32_t, int32_t, int, uint32_t);
    void (*run)(uint64_t);
    void (*advance)(uint64_t);
    void (*hold)(bool);
    const isrc_obs_t *(*obs)(void);
    int32_t (*count)(void);
    uint32_t (*cnt_reg)(void);
    uint32_t (*ccr_sh)(void);
    bool (*uif)(void);
    bool (*stop_now)(void);
    bool (*abort)(void);
    void (*estop)(void);
    void (*set_period)(uint32_t);
    uint32_t (*cur)(void);
    uint32_t (*pre)(void);
    bool (*late)(void);
} hal_if_t;
static const hal_if_t HAL_IF[2] = {
    {vcore_reset, vcore_run, vcore_advance, vcore_hold, vcore_obs, vcore_count, vcore_cnt_reg, vcore_ccr_shadow, vcore_uif,
     vcore_stop_now, vcore_abort, vcore_estop, vcore_set_period, vcore_cur, vcore_pre, vcore_late},
    {vseam_reset, vseam_run, vseam_advance, vseam_hold, vseam_obs, vseam_count, vseam_cnt_reg, vseam_ccr_shadow, vseam_uif,
     vseam_stop_now, vseam_abort, vseam_estop, vseam_set_period, vseam_cur, vseam_pre, vseam_late},
};
static const hal_if_t *H;

typedef struct {
    isrc_obs_t o;
    call_t     log[LOG_N];
    uint32_t   calls, cnt_mismatch;
    int32_t    c0;                                     /* count at the start */
    uint32_t   aux[4];                                 /* scenario-specific observations */
} trace_t;
static trace_t T[2];

#define PW_T 1125u                                    /* pulse_high default 12.5 us at 90 MHz */
#define C0   20000u                                   /* first two periods (the core consumed its own) */

void setUp(void) {}
void tearDown(void) {}

/* a real MOVE_ABS on the fake seams (as test_impl_motion: HOMED forced): M.running, ramp planned,
 * isr_count = the fake count; 1000 -> 1300 um = 240 steps at 5 mm/s, default accel */
static int32_t core_setup(void)
{
    mu_boot();
    mu_enable();
    g_fw.homed = true;
    hal_step_set_count(800);                          /* the fake: at 1000 um (inside soft_min 500 um) */
    h_expect_ok(mu_move(1300, 5000u, 0u));
    TEST_ASSERT_TRUE(motion_active());
    TEST_ASSERT_TRUE(hal_step_running());             /* the fake timer: armed, never advanced here */
    return fake_step_count();
}

static void run_until_stopped(uint64_t max)
{
    uint64_t t;
    for (t = 0u; t < max && H->obs()->running; t += 1000u) {
        H->run(1000u);
    }
    H->run(20000u);
}

static void run_to_upd(uint32_t n)
{
    while (H->obs()->upd < n && H->obs()->running) {
        H->run(1u);
    }
}

typedef void (*scen_fn)(trace_t *tr);

static void run_entry(int which, scen_fn f, void (*hook)(void), uint32_t hook_at)
{
    trace_t *tr = &T[which];
    int32_t c0;
    memset(tr, 0, sizeof *tr);
    c0 = core_setup();
    tr->c0 = c0;
    H = &HAL_IF[which];
    g_cnt_of = H->count;
    g_calls = 0u;
    g_cnt_mismatch = 0u;
    g_hook = hook;
    g_hook_at = hook_at;
    memset(g_log, 0, sizeof g_log);
    H->reset(C0, C0, c0, 1, PW_T);
    f(tr);
    tr->o = *H->obs();
    tr->calls = g_calls;
    tr->cnt_mismatch = g_cnt_mismatch;
    memcpy(tr->log, g_log, sizeof g_log);
    g_hook = NULL;
}

/* trunc: a TRUNCATE halt (abort / E-stop reaction) may cut one pulse: not counted, POS_UNCERTAIN in the
 * core (SAF-FW-004 / TC-SAF-FW-004-01: +-1 only with TRUNCATE) */
static bool s_trunc;
static void compare(const char *name, bool expect_same_calls)
{
    char msg[160];
    uint32_t i, n;
    const trace_t *a = &T[0], *b = &T[1];
    snprintf(msg, sizeof msg, "%s: target entry passed a count != the HAL count", name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, a->cnt_mismatch, msg);
    for (i = 0u; i < 2u; i++) {                       /* invariants of each entry */
        snprintf(msg, sizeof msg, "%s / %s: runt", name, i ? "seam" : "target");
        if (s_trunc) {
            int32_t d = (int32_t)(T[i].o.done + T[i].o.runts) - (T[i].o.count - T[i].c0);
            TEST_ASSERT_TRUE_MESSAGE(T[i].o.runts <= 1u, msg);
            snprintf(msg, sizeof msg, "%s / %s: count off by more than the cut pulse", name, i ? "seam" : "target");
            TEST_ASSERT_TRUE_MESSAGE(d == 0 || d == 1, msg);
            continue;
        }
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, T[i].o.runts, msg);
        snprintf(msg, sizeof msg, "%s / %s: count != completed pulses (SAF-FW-004)", name, i ? "seam" : "target");
        TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)T[i].o.done, T[i].o.count - T[i].c0, msg);
    }
    if (!expect_same_calls) {
        return;
    }
    snprintf(msg, sizeof msg, "%s: number of core calls", name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(a->calls, b->calls, msg);
    n = (a->calls < LOG_N) ? a->calls : LOG_N;
    for (i = 0u; i < n; i++) {
        snprintf(msg, sizeof msg, "%s: core call %u count argument", name, (unsigned)i);
        TEST_ASSERT_EQUAL_INT32_MESSAGE(a->log[i].arg, b->log[i].arg, msg);
        snprintf(msg, sizeof msg, "%s: core call %u packed result", name, (unsigned)i);
        TEST_ASSERT_TRUE_MESSAGE(a->log[i].ret == b->log[i].ret, msg);
    }
    snprintf(msg, sizeof msg, "%s: PUL rises", name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(a->o.rises, b->o.rises, msg);
    n = (a->o.rises < ISRC_RISE_LOG) ? a->o.rises : ISRC_RISE_LOG;
    for (i = 0u; i < n; i++) {
        snprintf(msg, sizeof msg, "%s: PUL rise %u time", name, (unsigned)i);
        TEST_ASSERT_TRUE_MESSAGE(a->o.rise_t[i] == b->o.rise_t[i], msg);
    }
    TEST_ASSERT_EQUAL_UINT32(a->o.done, b->o.done);
    TEST_ASSERT_EQUAL_UINT32(a->o.upd, b->o.upd);
    TEST_ASSERT_EQUAL_INT32(a->o.count, b->o.count);
    TEST_ASSERT_EQUAL(a->o.running, b->o.running);
    TEST_ASSERT_EQUAL_UINT32(a->o.stop_gen, b->o.stop_gen);
    TEST_ASSERT_EQUAL_HEX32(a->o.cr1, b->o.cr1);
    TEST_ASSERT_EQUAL_HEX32(a->o.ccmr1, b->o.ccmr1);
    TEST_ASSERT_EQUAL_UINT32(a->o.arr, b->o.arr);
    TEST_ASSERT_EQUAL_UINT32(a->o.ccr1, b->o.ccr1);
    TEST_ASSERT_EQUAL_UINT32(a->o.cnt, b->o.cnt);
    for (i = 0u; i < 4u; i++) {
        TEST_ASSERT_EQUAL_UINT32(a->aux[i], b->aux[i]);
    }
}

static void both(const char *name, scen_fn f, void (*hook)(void), uint32_t hook_at)
{
    run_entry(0, f, hook, hook_at);
    run_entry(1, f, hook, hook_at);
    compare(name, true);
}

static uint64_t last_ret(const trace_t *tr) { return tr->log[(tr->calls ? tr->calls : 1u) - 1u].ret; }

/* ---------------------------------------------------------------- (a) scenarios */
static void sc_plain(trace_t *tr)
{
    run_until_stopped(200000000u);
    tr->aux[0] = (uint32_t)H->obs()->running;
}
void test_entries_equal_plain_jog_to_bound(void)
{
    both("plain", sc_plain, NULL, 0u);
    TEST_ASSERT_FALSE(T[0].o.running);
    TEST_ASSERT_TRUE_MESSAGE(T[0].calls > 100u, "anti-skip: the real ramp ran");
    TEST_ASSERT_TRUE_MESSAGE(last_ret(&T[0]) == STEP_NEXT_LAST, "the move ends with LAST");
    TEST_ASSERT_EQUAL_UINT32(T[0].calls + 1u, T[0].o.upd);            /* + the final OPM update (no call) */
}

/* missed update: the ISR held over 2 updates -> the HAL estimates +1 -> core count fault -> STOP */
static uint32_t s_k;
static void sc_missed(trace_t *tr)
{
    run_to_upd(s_k);
    H->hold(true);
    run_to_upd(s_k + 2u);
    H->run(50u);
    H->hold(false);
    run_until_stopped(20000000u);
    tr->aux[0] = (uint32_t)H->obs()->running;
}
void test_entries_equal_missed_update_stop(void)
{
    for (s_k = 3u; s_k < 60u; s_k += 7u) {
        both("missed update", sc_missed, NULL, 0u);
        TEST_ASSERT_TRUE_MESSAGE(last_ret(&T[0]) == STEP_NEXT_STOP, "missed update -> STOP");
        TEST_ASSERT_FALSE(T[0].o.running);
    }
}

/* FWR-05 late latch: an update pending (ISR masked), the preload rewritten -> s_late; the ISR then
 * takes the period that update started (the branch-free select) */
static void sc_late(trace_t *tr)
{
    uint32_t old_pre;
    run_to_upd(s_k);
    H->hold(true);
    run_to_upd(s_k + 1u);                             /* update k+1 pending */
    old_pre = H->pre();
    H->set_period(old_pre + 3333u);                   /* tick-side preload while the ISR is masked */
    tr->aux[0] = (uint32_t)H->late();
    H->hold(false);                                   /* ISR: s_cur = late ? late_cur : pre */
    tr->aux[1] = (H->cur() == old_pre) ? 1u : 0u;
    tr->aux[2] = (uint32_t)H->late();
    run_until_stopped(200000000u);
}
void test_entries_equal_fwr05_late_latch(void)
{
    for (s_k = 4u; s_k < 40u; s_k += 9u) {
        both("FWR-05 late latch", sc_late, NULL, 0u);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(1u, T[0].aux[0], "late latch armed");
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(1u, T[0].aux[1], "the ISR took the period the update started");
        TEST_ASSERT_EQUAL_UINT32(0u, T[0].aux[2]);
    }
}

/* CLEAN / TRUNCATE halts between ISRs at every phase class of the period (FWR-21 window included) */
static uint32_t s_off;
static int s_kind;
static char s_name[96];
static void sc_halt_between(trace_t *tr)
{
    uint32_t target;
    run_to_upd(s_k);
    H->run(10u);
    target = H->ccr_sh() + PW_T - s_off;              /* running ARR + 1 - s_off (1 = last tick of the pulse) */
    while (H->cnt_reg() != target && H->obs()->running) {
        H->run(1u);
    }
    if (s_kind == 0) {
        tr->aux[0] = (uint32_t)H->stop_now();
    } else if (s_kind == 1) {
        tr->aux[0] = (uint32_t)H->abort();
    } else {
        H->estop();
    }
    run_until_stopped(20000000u);
}
void test_entries_equal_halts_between_isrs(void)
{
    static const uint32_t OFF[] = {1u, 2u, 30u, 200u, 1124u, 1125u, 1126u, 1300u, 1500u, 5000u};
    uint32_t i;
    for (s_kind = 0; s_kind < 3; s_kind++) {
        for (i = 0u; i < sizeof OFF / sizeof OFF[0]; i++) {
            s_off = OFF[i];
            s_k = 6u + i;
            snprintf(s_name, sizeof s_name, "halt between ISRs kind %d off %u", s_kind, (unsigned)s_off);
            s_trunc = s_kind != 0;
            both(s_name, sc_halt_between, NULL, 0u);
            s_trunc = false;
            TEST_ASSERT_FALSE(T[0].o.running);
        }
    }
}

/* DEF-M2-01: the halt lands while the update ISR is pending; the ISR then finds UIF clear */
static void sc_halt_pending(trace_t *tr)
{
    run_to_upd(s_k);
    H->hold(true);
    run_to_upd(s_k + 1u);
    H->run(s_off);
    tr->aux[0] = (s_kind == 0) ? (uint32_t)H->stop_now() : (uint32_t)H->abort();
    H->hold(false);
    run_until_stopped(20000000u);
}
void test_entries_equal_halt_with_isr_pending(void)
{
    for (s_kind = 0; s_kind < 2; s_kind++) {
        for (s_off = 5u; s_off < 3000u; s_off += 997u) {
            s_k = 5u;
            both("halt with ISR pending", sc_halt_pending, NULL, 0u);
            TEST_ASSERT_FALSE(T[0].o.running);
        }
    }
}

/* a level-0/1 halt pre-empting the step ISR before the core is consulted (target: after the count
 * was passed; seam / v0.8.5: before hal_step_count() is read) - the count read timing */
static void hk_estop(void) { H->estop(); }
static void hk_stop_now(void) { (void)H->stop_now(); }
static void hk_abort(void) { (void)H->abort(); }
static void sc_plain_short(trace_t *tr)
{
    run_until_stopped(30000000u);
    tr->aux[0] = (uint32_t)H->obs()->running;
}
void test_entries_equal_halt_preempting_handler(void)
{
    void (*const hk[3])(void) = {hk_estop, hk_stop_now, hk_abort};
    uint32_t i, at;
    for (i = 0u; i < 3u; i++) {
        for (at = 1u; at < 40u; at += 13u) {
            both("halt pre-empting the handler", sc_plain_short, hk[i], at);
            TEST_ASSERT_FALSE(T[0].o.running);
            TEST_ASSERT_EQUAL_UINT32(at, T[0].calls);  /* no core call after the halt */
        }
    }
}

/* characterisation outside the timing budget: the handler is pre-empted for more than one period
 * (a 2nd update before the halt) - not reachable with D-52 budgets (<= 5 us vs a >= 25 us period).
 * The target entry passes the pre-halt count (no core fault), the seam entry reads the count halt_hw()
 * corrected (core fault -> STOP). Both keep the count exact; only the STEP_FAULT verdict differs. */
static void hk_long(void)
{
    H->advance(H->obs()->arr + 50u);                  /* > one period inside the ISR */
    H->estop();
}
void test_characterise_handler_preempted_beyond_budget(void)
{
    char msg[160];
    run_entry(0, sc_plain_short, hk_long, 10u);
    run_entry(1, sc_plain_short, hk_long, 10u);
    compare("beyond budget", false);
    snprintf(msg, sizeof msg, "beyond-budget pre-emption: target call 10 -> 0x%llx (arg %ld), seam -> 0x%llx (arg %ld)",
             (unsigned long long)T[0].log[9].ret, (long)T[0].log[9].arg, (unsigned long long)T[1].log[9].ret,
             (long)T[1].log[9].arg);
    TEST_MESSAGE(msg);
    TEST_ASSERT_TRUE(T[1].log[9].ret == STEP_NEXT_STOP);
    TEST_ASSERT_EQUAL_INT32(T[0].log[9].arg + 1, T[1].log[9].arg);
}

/* ---------------------------------------------------------------- (b) acc clamp removal */
/* ramp_next_inl v0.8.6: c <= RAMP_U32_MAX_F (= 2^32 - 256) and 0 <= carry < 1 => carry + c <=
 * RAMP_U32_MAX_F in float arithmetic, so the removed clamp `if (acc > RAMP_U32_MAX_F) acc = MAX` never
 * changed acc; and the carry update keeps 0 <= carry < 1. Exhaustive over every float c in
 * [2^22, RAMP_U32_MAX_F] with the largest carry below 1 (rounding is monotone in carry, so it bounds
 * every carry), plus every 61st float in [1, 2^22) with a set of carries. */
static uint32_t acc_check(float c, float carry)
{
    volatile float vc = c, vk = carry;                /* no constant folding / excess precision */
    float acc = vk + vc;
    float clamped = (acc > RAMP_U32_MAX_F) ? RAMP_U32_MAX_F : acc;
    uint32_t n = (uint32_t)acc;
    float k2 = acc - (float)n;
    return (clamped != acc) | ((k2 < 0.0f || k2 >= 1.0f) ? 2u : 0u);
}
void test_acc_clamp_unreachable_exhaustive(void)
{
    const float kmax = nextafterf(1.0f, 0.0f);
    static const float K[] = {0.0f, 1e-7f, 0.25f, 0.5f, 0.75f, 0.9999f};
    float c;
    uint64_t n = 0u;
    uint32_t bad = 0u, i;
    for (c = 4194304.0f; c <= RAMP_U32_MAX_F; c = nextafterf(c, INFINITY)) {
        bad |= acc_check(c, kmax);
        n++;
        if (c == RAMP_U32_MAX_F) {
            break;
        }
    }
    for (c = 1.0f; c < 4194304.0f; ) {
        bad |= acc_check(c, kmax);
        for (i = 0u; i < sizeof K / sizeof K[0]; i++) {
            bad |= acc_check(c, K[i]);
        }
        for (i = 0u; i < 61u; i++) {
            c = nextafterf(c, INFINITY);
        }
        n++;
    }
    TEST_ASSERT_TRUE_MESSAGE(n > 80000000u, "anti-skip");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, bad & 1u, "carry + c exceeded RAMP_U32_MAX_F: the clamp was reachable");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, bad & 2u, "carry left [0, 1)");
    TEST_ASSERT_TRUE(RAMP_U32_MAX_F == 4294967040.0f);
    TEST_ASSERT_TRUE(nextafterf(RAMP_U32_MAX_F, INFINITY) == 4294967296.0f);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_acc_clamp_unreachable_exhaustive);
    RUN_TEST(test_entries_equal_plain_jog_to_bound);
    RUN_TEST(test_entries_equal_missed_update_stop);
    RUN_TEST(test_entries_equal_fwr05_late_latch);
    RUN_TEST(test_entries_equal_halts_between_isrs);
    RUN_TEST(test_entries_equal_halt_with_isr_pending);
    RUN_TEST(test_entries_equal_halt_preempting_handler);
    RUN_TEST(test_characterise_handler_preempted_beyond_budget);
    return UNITY_END();
}
