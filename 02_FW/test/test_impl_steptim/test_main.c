/* Host harness of the TARGET step HAL hal/f446/step_tim2.c (DEF-M2-01): the unchanged source is
 * compiled against a register-level TIM2 model (PWM mode 2, ARR / CCR1 preload, update event at
 * CNT = ARR + 1 -> 0, OPM, UIF) instead of the CMSIS device. The model counts the pulses that reach
 * their update with the output still in PWM mode ("completed"), the rising edges and the pulses cut
 * by a forced-inactive output. The update ISR is called by the harness right after each update
 * unless a test holds it (a level 0/1 ISR or a PRIMASK section pre-empting the step ISR).
 * Invariants checked at every halt position: hal count == completed pulses, one stop_gen increment
 * per halt, CLEAN never cuts a pulse (no runt), TRUNCATE reports a cut pulse.
 * v0.8 (FW_design §9.8 / review FWR-01, FWR-02, FWR-05): the E-stop fixed reaction
 * step_estop_reaction() is equivalent to hal_step_abort() + hal_ena_set(false) at every CNT position;
 * idle halts bump the stop generation; a halt before the atomic arm is caught by the recheck before
 * the first edge; a stretch while an update is pending keeps the running-period bookkeeping.
 * Verifies: SAF-FW-002 (CLEAN halt), SAF-FW-004 (no uncounted pulse, DEF-M2-01), SAF-FW-005 a,
 *           NFR-007 (optimised E-stop path equivalent)
 */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "unity.h"

/* ---- register model (replaces f446.h / stm32f4xx.h for this translation unit) ---- */
#define HAL_F446_H 1
#define RAMFUNC

typedef struct {
    volatile uint32_t CR1, CR2, SMCR, DIER, SR, EGR, CCMR1, CCMR2, CCER, CNT, PSC, ARR, RCR, CCR1;
} TIM_TypeDef;
typedef struct {
    volatile uint32_t MODER, OTYPER, OSPEEDR, PUPDR, IDR, ODR, BSRR, LCKR, AFR[2];
} GPIO_TypeDef;
typedef struct { volatile uint32_t APB1ENR; } RCC_TypeDef;

static TIM_TypeDef  m_tim2;
static GPIO_TypeDef m_gpioa;
static RCC_TypeDef  m_rcc;
#define TIM2  (&m_tim2)
#define GPIOA (&m_gpioa)
#define RCC   (&m_rcc)

#define TIM_CR1_CEN        0x0001u
#define TIM_CR1_OPM        0x0008u
#define TIM_CR1_ARPE       0x0080u
#define TIM_SR_UIF         0x0001u
#define TIM_EGR_UG         0x0001u
#define TIM_DIER_UIE       0x0001u
#define TIM_CCMR1_OC1PE    0x0008u
#define TIM_CCMR1_OC1M     0x0070u
#define TIM_CCMR1_OC1M_0   0x0010u
#define TIM_CCMR1_OC1M_1   0x0020u
#define TIM_CCMR1_OC1M_2   0x0040u
#define TIM_CCER_CC1E      0x0001u
#define TIM_CCER_CC1P      0x0002u
#define RCC_APB1ENR_TIM2EN 0x0001u
#define TIM2_IRQn          28

static uint32_t m_primask;
static uint64_t m_t;                                  /* model time, timer ticks (90 MHz) */
static inline void NVIC_SetPriority(int irq, uint32_t p) { (void)irq; (void)p; }
static inline void NVIC_ClearPendingIRQ(int irq) { (void)irq; }
static inline void NVIC_EnableIRQ(int irq) { (void)irq; }
static inline uint32_t __get_PRIMASK(void) { return m_primask; }
static inline void __disable_irq(void) { m_primask = 1u; }
static inline void __set_PRIMASK(uint32_t v) { m_primask = v; }
static inline uint32_t dwt_cycles(void) { return (uint32_t)(2u * m_t); }   /* 180 MHz core */
static inline uint32_t time_timer_hz(void) { return 90000000u; }
static inline void gpio_mode(GPIO_TypeDef *g, uint32_t pin, uint32_t mode, uint32_t pupd, uint32_t speed)
{
    (void)g; (void)pin; (void)mode; (void)pupd; (void)speed;
}
static inline void gpio_af(GPIO_TypeDef *g, uint32_t pin, uint32_t af) { (void)g; (void)pin; (void)af; }
static inline void gpio_write(GPIO_TypeDef *g, uint32_t pin, bool high)
{
    g->BSRR = high ? (1u << pin) : (1u << (pin + 16u));
}
#define GPIO_MODE_IN_    0u
#define GPIO_MODE_OUT_   1u
#define GPIO_MODE_AF_    2u
#define GPIO_PUPD_NONE_  0u
#define GPIO_PUPD_UP_    1u
#define GPIO_SPEED_LOW_  0u
#define GPIO_SPEED_HIGH_ 2u

/* the target seam names would collide with the fake seams / core linked into env:native */
#define hal_ena_set             tgt_hal_ena_set
#define hal_step_init           tgt_hal_step_init
#define hal_step_set_dir        tgt_hal_step_set_dir
#define hal_step_start          tgt_hal_step_start
#define hal_step_set_period     tgt_hal_step_set_period
#define hal_step_set_period_now tgt_hal_step_set_period_now
#define hal_step_arm_last       tgt_hal_step_arm_last
#define hal_step_stop_now       tgt_hal_step_stop_now
#define hal_step_abort          tgt_hal_step_abort
#define hal_step_count          tgt_hal_step_count
#define hal_step_set_count      tgt_hal_step_set_count
#define hal_step_running        tgt_hal_step_running
#define hal_step_stop_gen       tgt_hal_step_stop_gen
#define step_isr                tgt_step_isr
#define g_meas_static           tgt_g_meas_static

#include "../../src/hal/f446/step_tim2.c"

/* ---- timer model ---- */
#define PW 900u
#define OC_MODE() (m_tim2.CCMR1 & TIM_CCMR1_OC1M)

static uint32_t m_arr_sh, m_ccr_sh;                  /* shadow registers */
static bool     m_out;                               /* PUL output level (active) */
static uint32_t m_rise, m_done, m_cut;               /* rising edges, completed pulses, cut pulses */
static uint32_t m_rise_cnt;                          /* CNT at the last rising edge */
static bool     m_hold_isr;                          /* update ISR pending, not served */
static uint32_t m_isr_calls;

/* step_isr() model: periods from a table (varying, so the preload differs from the running one) */
static uint32_t m_per[4] = {3000u, 2600u, 3400u, 2200u};
static uint32_t m_isr_n, m_last_at;                  /* m_last_at: arm the last pulse at this call */

step_next_t step_isr(void)
{
    step_next_t r;
    memset(&r, 0, sizeof r);
    m_isr_n++;
    r.period = m_per[m_isr_n % 4u];
    r.last = (m_last_at != 0u && m_isr_n == m_last_at);
    return r;
}

static void out_eval(void)
{
    bool act = OC_MODE() == OC1M_PWM2 && (m_tim2.CR1 & TIM_CR1_CEN) != 0u && m_tim2.CNT >= m_ccr_sh;
    if (OC_MODE() == OC1M_PWM2 && (m_tim2.CR1 & TIM_CR1_CEN) == 0u) {
        act = m_out;                                  /* counter stopped: output holds */
    }
    if (act && !m_out) {
        m_rise++;
        m_rise_cnt = m_tim2.CNT;
    } else if (!act && m_out) {
        m_cut++;                                      /* fell before the update: cut */
    }
    m_out = act;
}

static void serve_isr(void)
{
    if (!m_hold_isr) {
        m_isr_calls++;
        TIM2_IRQHandler();
        out_eval();
    }
}

/* one timer tick */
static void tick(void)
{
    out_eval();                                       /* a forced-inactive write since the last tick */
    if ((m_tim2.CR1 & TIM_CR1_CEN) == 0u) {
        m_t++;
        return;
    }
    m_t++;
    m_tim2.CNT++;
    if (m_tim2.CNT == m_arr_sh + 1u) {                /* update event: the pulse ends here */
        if (m_out) {
            m_done++;
            m_out = false;
        }
        m_tim2.CNT = 0u;
        m_tim2.SR |= TIM_SR_UIF;
        m_arr_sh = m_tim2.ARR;
        m_ccr_sh = m_tim2.CCR1;
        if ((m_tim2.CR1 & TIM_CR1_OPM) != 0u) {
            m_tim2.CR1 &= ~TIM_CR1_CEN;
        }
        serve_isr();
    } else {
        out_eval();
    }
}

static void run(uint32_t n)
{
    uint32_t i;
    for (i = 0u; i < n; i++) {
        tick();
    }
}

static void start(int dir)
{
    hal_step_cfg_t cfg = {PW, 1800u, false, false};
    (void)tgt_hal_step_init(&cfg);
    tgt_hal_step_set_dir(dir);
    tgt_hal_step_start(3000u);
    m_arr_sh = m_tim2.ARR;                            /* UG + the same preload write (start) */
    m_ccr_sh = m_tim2.CCR1;
    m_tim2.SR &= ~TIM_SR_UIF;
    out_eval();
}

static void reset_model(void)
{
    memset(&m_tim2, 0, sizeof m_tim2);
    m_t = 0u;
    m_out = false;
    m_rise = m_done = m_cut = 0u;
    m_hold_isr = false;
    m_isr_calls = 0u;
    m_isr_n = 0u;
    m_last_at = 0u;
    m_primask = 0u;
    s_count = 0;
    s_running = false;
    s_late = false;
    memset(&m_gpioa, 0, sizeof m_gpioa);
}

/* run until the k-th update event (ISR held for that one when hold) */
static void run_to_update(uint32_t k, bool hold)
{
    uint32_t guard = 0u;
    while (m_done + m_cut < k && guard++ < 1000000u) {
        if (m_done + m_cut == k - 1u) {
            m_hold_isr = hold;
        }
        tick();
    }
}

void setUp(void) { reset_model(); }
void tearDown(void) {}

/* plain run + last pulse: one count per completed pulse, one stop */
void test_run_last_counts_exact(void)
{
    uint32_t g;
    start(1);
    g = tgt_hal_step_stop_gen();
    m_last_at = 7u;
    run(100000u);
    TEST_ASSERT_FALSE(tgt_hal_step_running());
    TEST_ASSERT_EQUAL_UINT32(g + 1u, tgt_hal_step_stop_gen());
    TEST_ASSERT_EQUAL_UINT32(0u, m_cut);
    TEST_ASSERT_EQUAL_INT32((int32_t)m_done, tgt_hal_step_count());
    TEST_ASSERT_EQUAL_UINT32(m_rise, m_done);
}

/* DEF-M2-01: a halt between an update event and its (pre-empted) ISR keeps the count */
static void pending_case(bool abort, int dir)
{
    uint32_t g;
    bool r;
    int32_t want;
    start(dir);
    g = tgt_hal_step_stop_gen();
    run_to_update(5u, true);                          /* 5th pulse completed, its ISR pending */
    TEST_ASSERT_TRUE((m_tim2.SR & TIM_SR_UIF) != 0u);
    r = abort ? tgt_hal_step_abort() : tgt_hal_step_stop_now();
    want = dir * (int32_t)m_done;
    TEST_ASSERT_EQUAL_INT32(want, tgt_hal_step_count());   /* exact at once (edge records) */
    m_hold_isr = false;
    serve_isr();                                      /* the pending request is served now */
    run(20000u);
    TEST_ASSERT_FALSE(r);                             /* next pulse not started: nothing to cut */
    TEST_ASSERT_FALSE(tgt_hal_step_running());
    TEST_ASSERT_EQUAL_UINT32(g + 1u, tgt_hal_step_stop_gen());
    TEST_ASSERT_EQUAL_UINT32(0u, m_cut);
    TEST_ASSERT_EQUAL_UINT32(m_rise, m_done);
    TEST_ASSERT_EQUAL_INT32(dir * (int32_t)m_done, tgt_hal_step_count());
}
void test_abort_with_pending_update(void) { pending_case(true, 1); }
void test_stop_now_with_pending_update(void) { pending_case(false, 1); }
void test_abort_with_pending_update_reverse(void) { pending_case(true, -1); }

/* the last (OPM) update stopped the counter, its ISR pre-empted by E-stop / limit: counted once */
void test_halt_after_final_opm_update_pending(void)
{
    uint32_t pass;
    for (pass = 0u; pass < 2u; pass++) {
        uint32_t g;
        reset_model();
        start(1);
        g = tgt_hal_step_stop_gen();
        m_last_at = 3u;                               /* 4 pulses, the 4th stops the counter */
        run_to_update(4u, true);
        TEST_ASSERT_TRUE((m_tim2.CR1 & TIM_CR1_CEN) == 0u);
        TEST_ASSERT_TRUE(tgt_hal_step_running());     /* ISR not served yet */
        TEST_ASSERT_FALSE(pass == 0u ? tgt_hal_step_abort() : tgt_hal_step_stop_now());
        m_hold_isr = false;
        serve_isr();
        TEST_ASSERT_FALSE(tgt_hal_step_running());
        TEST_ASSERT_EQUAL_UINT32(g + 1u, tgt_hal_step_stop_gen());
        TEST_ASSERT_EQUAL_UINT32(4u, m_done);
        TEST_ASSERT_EQUAL_INT32(4, tgt_hal_step_count());
    }
}

/* every halt position over two periods, with and without a pending update ISR (U every CNT) */
static void sweep(bool abort)
{
    uint32_t off, n = 0u;
    for (off = 0u; off < 6000u; off += 7u) {
        uint32_t hold;
        for (hold = 0u; hold < 2u; hold++) {
            uint32_t g, cut0;
            bool r;
            reset_model();
            start(1);
            g = tgt_hal_step_stop_gen();
            run_to_update(3u, hold != 0u);
            if (hold == 0u) {
                run(off);                             /* anywhere in the following periods */
            } else if (off != 0u) {
                continue;                             /* pending only right at the update */
            }
            cut0 = m_cut;
            r = abort ? tgt_hal_step_abort() : tgt_hal_step_stop_now();
            out_eval();
            if (abort) {
                TEST_ASSERT_EQUAL(r, m_cut != cut0);      /* reports exactly the cut pulse */
            } else {
                TEST_ASSERT_EQUAL_UINT32(cut0, m_cut);    /* CLEAN: never a runt */
            }
            m_hold_isr = false;
            if ((m_tim2.SR & TIM_SR_UIF) != 0u) {
                serve_isr();
            }
            run(20000u);
            TEST_ASSERT_FALSE(tgt_hal_step_running());
            TEST_ASSERT_EQUAL_UINT32(g + 1u, tgt_hal_step_stop_gen());
            TEST_ASSERT_EQUAL_INT32((int32_t)m_done, tgt_hal_step_count());
            if (!abort) {
                TEST_ASSERT_EQUAL_UINT32(0u, m_cut);
                TEST_ASSERT_EQUAL_UINT32(m_rise, m_done);
            }
            n++;
        }
    }
    TEST_ASSERT_TRUE(n > 850u);                       /* anti-skip */
}
void test_sweep_stop_now(void) { sweep(false); }
void test_sweep_abort(void) { sweep(true); }

/* v0.8: step_estop_reaction() == hal_step_abort() + hal_ena_set(false), every 7th CNT position over
 * two periods, with and without a pending update ISR (the same deterministic model run twice) */
typedef struct {
    uint32_t done, cut, rise, gen, ccmr1, cr1, sr, bsrr;
    int32_t  count;
    bool     running;
} obs_t;

static obs_t estop_case(uint32_t off, bool hold, bool reaction)
{
    obs_t o;
    uint32_t g0;
    reset_model();
    start(1);
    g0 = tgt_hal_step_stop_gen();
    run_to_update(3u, hold);
    if (!hold) {
        run(off);
    }
    m_gpioa.BSRR = 0u;
    if (reaction) {
        step_estop_reaction();
    } else {
        (void)tgt_hal_step_abort();
        tgt_hal_ena_set(false);
    }
    out_eval();
    o.ccmr1 = m_tim2.CCMR1;
    o.cr1 = m_tim2.CR1;
    o.sr = m_tim2.SR;
    o.bsrr = m_gpioa.BSRR;
    o.gen = tgt_hal_step_stop_gen() - g0;             /* one per halt */
    o.count = tgt_hal_step_count();
    m_hold_isr = false;
    if ((m_tim2.SR & TIM_SR_UIF) != 0u) {
        serve_isr();
    }
    run(20000u);
    o.done = m_done;
    o.cut = m_cut;
    o.rise = m_rise;
    o.running = tgt_hal_step_running();
    TEST_ASSERT_EQUAL_INT32((int32_t)m_done, tgt_hal_step_count());   /* SAF-FW-004 */
    return o;
}

void test_estop_reaction_equals_abort_and_ena(void)
{
    uint32_t off, n = 0u;
    for (off = 0u; off < 6000u; off += 7u) {
        uint32_t hold;
        for (hold = 0u; hold < 2u; hold++) {
            obs_t a, b;
            if (hold != 0u && off != 0u) {
                continue;
            }
            a = estop_case(off, hold != 0u, false);
            b = estop_case(off, hold != 0u, true);
            TEST_ASSERT_EQUAL_UINT32(a.done, b.done);
            TEST_ASSERT_EQUAL_UINT32(a.cut, b.cut);
            TEST_ASSERT_EQUAL_UINT32(a.rise, b.rise);
            TEST_ASSERT_EQUAL_UINT32(a.gen, b.gen);
            TEST_ASSERT_EQUAL_UINT32(a.ccmr1, b.ccmr1);
            TEST_ASSERT_EQUAL_UINT32(a.cr1, b.cr1);
            TEST_ASSERT_EQUAL_UINT32(a.sr, b.sr);
            TEST_ASSERT_EQUAL_UINT32(a.bsrr, b.bsrr);
            TEST_ASSERT_EQUAL_INT32(a.count, b.count);
            TEST_ASSERT_EQUAL(a.running, b.running);
            TEST_ASSERT_FALSE(b.running);
            TEST_ASSERT_EQUAL_UINT32(1u << PIN_ENA_BIT, b.bsrr);   /* ENA -> disabled (ena_invert 0) */
            n++;
        }
    }
    TEST_ASSERT_TRUE(n > 850u);
}

/* idle: every stop primitive bumps the generation (FWR-01); the reaction also releases a
 * STATIC_LEVEL hold and drives ENA like hal_ena_set(false) */
void test_idle_halts_bump_generation(void)
{
    uint32_t g;
    hal_step_cfg_t cfg = {PW, 1800u, false, false};
    (void)tgt_hal_step_init(&cfg);
    g = tgt_hal_step_stop_gen();
    (void)tgt_hal_step_stop_now();
    TEST_ASSERT_EQUAL_UINT32(g + 1u, tgt_hal_step_stop_gen());
    (void)tgt_hal_step_abort();
    TEST_ASSERT_EQUAL_UINT32(g + 2u, tgt_hal_step_stop_gen());
    tgt_g_meas_static = 1u;
    m_tim2.CCMR1 = OC1M_FORCE_INACTIVE | TIM_CCMR1_OC1M_0 | TIM_CCMR1_OC1PE;   /* forced active */
    step_estop_reaction();
    TEST_ASSERT_EQUAL_UINT32(g + 3u, tgt_hal_step_stop_gen());
    TEST_ASSERT_EQUAL_UINT8(0u, tgt_g_meas_static);
    TEST_ASSERT_EQUAL_UINT32(OC1M_FORCE_INACTIVE, OC_MODE());
    TEST_ASSERT_EQUAL_UINT32(1u << PIN_ENA_BIT, m_gpioa.BSRR);
    TEST_ASSERT_FALSE(tgt_hal_step_running());
}

/* FWR-01 / FWR-02 core protocol (hw_start): a halt after the gate capture and before the atomic arm
 * leaves the timer armed; the recheck stops it before the first edge (dir_setup = 1800 ticks) */
void test_halt_before_arm_caught_by_recheck(void)
{
    uint32_t pass;
    for (pass = 0u; pass < 2u; pass++) {
        uint32_t gen;
        hal_step_cfg_t cfg = {PW, 1800u, false, false};
        reset_model();
        (void)tgt_hal_step_init(&cfg);
        tgt_hal_step_set_dir(1);
        gen = tgt_hal_step_stop_gen();                 /* gate capture */
        if (pass == 0u) {
            step_estop_reaction();                     /* E-stop edge in the window (idle) */
        } else {
            (void)tgt_hal_step_stop_now();             /* limit edge in the window (idle) */
        }
        tgt_hal_step_start(3000u);
        m_arr_sh = m_tim2.ARR;
        m_ccr_sh = m_tim2.CCR1;
        m_tim2.SR &= ~TIM_SR_UIF;
        TEST_ASSERT_TRUE(tgt_hal_step_running());      /* armed atomically */
        run(10u);                                      /* a few ticks of pre-emption */
        TEST_ASSERT_NOT_EQUAL_UINT32(gen, tgt_hal_step_stop_gen());
        (void)tgt_hal_step_stop_now();                 /* recheck */
        run(20000u);
        TEST_ASSERT_FALSE(tgt_hal_step_running());
        TEST_ASSERT_EQUAL_UINT32(0u, m_rise);
        TEST_ASSERT_EQUAL_INT32(0, tgt_hal_step_count());
    }
}

/* FWR-05: a stretch while an update is pending (its ISR masked by CRIT_MOTION) extends the period
 * that update started, and the pending ISR takes it as the running period: a CLEAN stop inside that
 * period's pulse completes it (no runt, counted) */
void test_stretch_with_pending_update(void)
{
    uint32_t g;
    const uint32_t c1 = 6000u, c2 = 6400u;
    start(1);
    run_to_update(3u, true);                           /* update 3 pending: s_pre's period running */
    TEST_ASSERT_TRUE((m_tim2.SR & TIM_SR_UIF) != 0u);
    tgt_hal_step_set_period_now(c1);
    TEST_ASSERT_TRUE(s_late);
    TEST_ASSERT_EQUAL_UINT32(c1, s_late_cur);
    m_arr_sh = c1 - 1u;                                /* the model applies the direct (ARPE = 0) write */
    m_ccr_sh = c1 - PW;
    tgt_hal_step_set_period(c2);                       /* the core's next preload */
    TEST_ASSERT_EQUAL_UINT32(c1, s_late_cur);          /* not overwritten by the preload */
    m_hold_isr = false;
    serve_isr();
    TEST_ASSERT_EQUAL_UINT32(c1, s_cur);               /* bookkeeping = the interval really running */
    TEST_ASSERT_FALSE(s_late);
    while (m_tim2.CNT < c1 - PW + 10u) {               /* into c1's pulse */
        tick();
    }
    g = tgt_hal_step_stop_gen();
    TEST_ASSERT_TRUE(tgt_hal_step_stop_now());         /* CLEAN: the running pulse completes */
    run(20000u);
    TEST_ASSERT_EQUAL_UINT32(0u, m_cut);               /* no runt */
    TEST_ASSERT_EQUAL_INT32((int32_t)m_done, tgt_hal_step_count());
    TEST_ASSERT_EQUAL_UINT32(g + 1u, tgt_hal_step_stop_gen());
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_estop_reaction_equals_abort_and_ena);
    RUN_TEST(test_idle_halts_bump_generation);
    RUN_TEST(test_halt_before_arm_caught_by_recheck);
    RUN_TEST(test_stretch_with_pending_update);
    RUN_TEST(test_run_last_counts_exact);
    RUN_TEST(test_abort_with_pending_update);
    RUN_TEST(test_stop_now_with_pending_update);
    RUN_TEST(test_abort_with_pending_update_reverse);
    RUN_TEST(test_halt_after_final_opm_update_pending);
    RUN_TEST(test_sweep_stop_now);
    RUN_TEST(test_sweep_abort);
    return UNITY_END();
}
