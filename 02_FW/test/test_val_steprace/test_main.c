/* Validator E - code review FWR (02_FW/docs/FW_code_review.md): races between the stop primitives
 * (level 0/1 HAL fixed reactions) and the step-timer START sequence of the TARGET step HAL
 * hal/f446/step_tim2.c. The unchanged source is compiled against a register-level TIM2 model
 * (PWM mode 2, ARR / CCR1 preload, update event at CNT = ARR + 1 -> 0, OPM, UIF).
 * Model origin: Implementer A's test/test_impl_steptim/test_main.c @e600169 (register model copied
 * and trimmed; the injection hook and every test below are the validator's own).
 *
 * Injection: dwt_cycles() is the one call that hal_step_start() makes inside its start sequence. At
 * e600169 it lay between `s_running = true` and `TIM2->CR1 |= CEN` (lines 158-160; firmware.elf
 * 0x08011c16 / 1a / 1e..26), i.e. inside the race window of FWR-02. Since FW v0.8 (A's FWR-02 fix) it
 * is read just BEFORE the PRIMASK arm section, i.e. in the idle window of FWR-01: the injected halt
 * must then be visible to the start-then-recheck (stop_gen bumped) and the recheck must stop the
 * armed timer before its first edge. v0.8 harness adaptation (OI-FW-49): the E-stop injection calls
 * the target's step_estop_reaction() (what EXTI15_10 now runs), `s_late` / `s_late_cur` / `s_init`
 * are reset per case, and core_hw_start() follows hw_start() v0.8 (recheck right after the start,
 * before the preload).
 * Arming the hook makes a level-0/1 handler (E-stop TRUNCATE or limit CLEAN halt) run exactly at that
 * instruction boundary, as the NVIC can do on the target (hw_start() holds only CRIT_MOTION =
 * BASEPRI 0x20, which never masks levels 0/1).
 *
 * Expected (FW_design §5.3 "start-then-recheck", SAF-FW-002/004/005): a halt that happens after the
 * start sequence captured hal_step_stop_gen() must leave the timer stopped and the counter equal to
 * the completed pulses (no PUL edge, no phantom count).
 * Verifies: SAF-FW-002, SAF-FW-004, SAF-FW-005 (a) - review findings FWR-01 / FWR-02
 * TC: TC-SAF-FW-002-04 (FWR-01), TC-SAF-FW-004-04 (FWR-02)
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

/* injection: 0 = off, 1 = E-stop (abort), 2 = limit (stop_now); fires once at the next dwt_cycles() */
static int m_inject;
static void inject_now(void);
static uint32_t dwt_cycles(void)
{
    if (m_inject != 0) {
        inject_now();
    }
    return (uint32_t)(2u * m_t);                      /* 180 MHz core */
}

/* the target seam names would collide with the fake seams / core linked into env:native */
#define hal_ena_set             vtgt_hal_ena_set
#define hal_step_init           vtgt_hal_step_init
#define hal_step_set_dir        vtgt_hal_step_set_dir
#define hal_step_start          vtgt_hal_step_start
#define hal_step_set_period     vtgt_hal_step_set_period
#define hal_step_set_period_now vtgt_hal_step_set_period_now
#define hal_step_arm_last       vtgt_hal_step_arm_last
#define hal_step_stop_now       vtgt_hal_step_stop_now
#define hal_step_abort          vtgt_hal_step_abort
#define hal_step_count          vtgt_hal_step_count
#define hal_step_set_count      vtgt_hal_step_set_count
#define hal_step_running        vtgt_hal_step_running
#define hal_step_stop_gen       vtgt_hal_step_stop_gen
#define step_isr                vtgt_step_isr
#define g_meas_static           vtgt_g_meas_static
#define TIM2_IRQHandler         vtgt_TIM2_IRQHandler
#define step_estop_reaction     vtgt_step_estop_reaction

#include "../../src/hal/f446/step_tim2.c"

static void inject_now(void)
{
    int k = m_inject;
    m_inject = 0;                                     /* once */
    if (k == 1) {
        vtgt_step_estop_reaction();                   /* EXTI15_10 E-stop fixed reaction (v0.8) */
    } else if (k == 3) {
        (void)vtgt_hal_step_abort();                  /* e600169 E-stop reaction (equivalence) */
        vtgt_hal_ena_set(false);
    } else {
        (void)vtgt_hal_step_stop_now();               /* EXTI0/1 limit fixed reaction (CLEAN) */
    }
}

/* ---- timer model ---- */
#define PW 900u
#define OC_MODE() (m_tim2.CCMR1 & TIM_CCMR1_OC1M)

static uint32_t m_arr_sh, m_ccr_sh;
static bool     m_out;
static uint32_t m_rise, m_done, m_upd;
static uint32_t m_isr_n, m_last_at;

step_next_t vtgt_step_isr(void)
{
    step_next_t r;
    memset(&r, 0, sizeof r);
    m_isr_n++;
    r.period = 3000u;
    r.last = (m_last_at != 0u && m_isr_n == m_last_at);
    return r;
}

static void out_eval(void)
{
    bool act = OC_MODE() == OC1M_PWM2 && (m_tim2.CR1 & TIM_CR1_CEN) != 0u && m_tim2.CNT >= m_ccr_sh;
    if (OC_MODE() == OC1M_PWM2 && (m_tim2.CR1 & TIM_CR1_CEN) == 0u) {
        act = m_out;
    }
    if (act && !m_out) {
        m_rise++;
    }
    m_out = act;
}

static void tick(void)
{
    out_eval();
    m_t++;
    if ((m_tim2.CR1 & TIM_CR1_CEN) == 0u) {
        return;
    }
    m_tim2.CNT++;
    if (m_tim2.CNT == m_arr_sh + 1u) {                /* update event: the pulse ends here */
        if (m_out) {
            m_done++;
            m_out = false;
        }
        m_upd++;
        m_tim2.CNT = 0u;
        m_tim2.SR |= TIM_SR_UIF;
        m_arr_sh = m_tim2.ARR;
        m_ccr_sh = m_tim2.CCR1;
        if ((m_tim2.CR1 & TIM_CR1_OPM) != 0u) {
            m_tim2.CR1 &= ~TIM_CR1_CEN;
        }
        vtgt_TIM2_IRQHandler();
        out_eval();
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

static bool m_ena_inv;
static void reset_model(void)
{
    hal_step_cfg_t cfg = {PW, 1800u, false, false};
    cfg.ena_invert = m_ena_inv;
    memset(&m_tim2, 0, sizeof m_tim2);
    memset(&m_gpioa, 0, sizeof m_gpioa);
    m_t = 0u;
    m_out = false;
    m_rise = m_done = m_upd = 0u;
    m_isr_n = 0u;
    m_last_at = 0u;
    m_primask = 0u;
    m_inject = 0;
    s_count = 0;
    s_running = false;
    s_late = false;
    s_late_cur = 0u;
    s_init = false;                                   /* ena_invert is taken at the first init only */
    vtgt_g_meas_static = 0u;
    (void)vtgt_hal_step_init(&cfg);
    vtgt_hal_step_set_dir(1);
}

/* hw_start() protocol of core/motion.c v0.8: the stop generation is captured before the gate check
 * (motion_gate_capture), the timer armed atomically, the recheck runs right after the start and
 * before the preload of period 2 */
static void core_hw_start(void)
{
    uint32_t gen = vtgt_hal_step_stop_gen();          /* gate capture */
    vtgt_hal_step_start(3000u);
    m_arr_sh = m_tim2.ARR;                            /* UG loaded the shadows at the start */
    m_ccr_sh = m_tim2.CCR1;
    m_tim2.SR &= ~TIM_SR_UIF;
    if (vtgt_hal_step_stop_gen() != gen) {
        (void)vtgt_hal_step_stop_now();               /* start-then-recheck */
    } else {
        vtgt_hal_step_set_period(3000u);
    }
    out_eval();
}

void setUp(void) { reset_model(); }
void tearDown(void) {}

/* FWR-01 (a): a limit / E-stop edge whose fixed reaction runs after hw_start() captured stop_gen but
 * before the timer is marked running (gate check -> start window): the halt is a no-op AND the
 * recheck cannot see it, so the move starts after the stop.
 * TC: review FWR-01 (SAF-FW-002 limit path <= 200 us, SAF-FW-005 a <= 100 us) */
static void halt_before_start(bool abort)
{
    uint32_t gen = vtgt_hal_step_stop_gen();
    if (abort) {
        (void)vtgt_hal_step_abort();                  /* E-stop edge in the window (axis idle) */
    } else {
        (void)vtgt_hal_step_stop_now();               /* limit edge in the window (axis idle) */
    }
    /* the start-then-recheck protocol needs the halt to be visible to the caller */
    TEST_ASSERT_NOT_EQUAL_UINT32_MESSAGE(gen, vtgt_hal_step_stop_gen(),
                                         "halt while idle not visible to start-then-recheck (FWR-01)");
}
void test_fwr01_limit_halt_in_start_window_visible(void) { halt_before_start(false); }
void test_fwr01_estop_halt_in_start_window_visible(void) { halt_before_start(true); }

/* FWR-02: the halt runs between `s_running = true` and `CR1 |= CEN` inside hal_step_start(): the
 * read-modify-write re-sets CEN after halt_hw() cleared it; the counter then runs with the output
 * forced inactive, s_running == false, and every update ISR counts a step that never reached the
 * driver (phantom count); the recheck's hal_step_stop_now() is a no-op because s_running is false. */
static void halt_inside_start(int kind)
{
    m_last_at = 6u;                                   /* a 7-step move */
    m_inject = kind;                                  /* fires at the dwt_cycles() in hal_step_start */
    core_hw_start();
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, m_inject, "injection did not fire");
    run(60000u);                                      /* > 7 periods of 3000 ticks */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, m_rise, "PUL edge after the halt");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, m_upd, "step timer kept running after the halt (FWR-02)");
    TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)m_done, vtgt_hal_step_count(),
                                    "phantom steps counted without a pulse (FWR-02, SAF-FW-004)");
}
void test_fwr02_estop_inside_start(void) { halt_inside_start(1); }
void test_fwr02_limit_inside_start(void) { halt_inside_start(2); }

/* control: the same sequence without an injection runs the 7 steps, count == completed pulses */
void test_control_plain_start(void)
{
    m_last_at = 6u;
    core_hw_start();
    run(60000u);
    TEST_ASSERT_FALSE(vtgt_hal_step_running());
    TEST_ASSERT_EQUAL_UINT32(7u, m_done);
    TEST_ASSERT_EQUAL_INT32(7, vtgt_hal_step_count());
}

/* ---------------------------------------------------------------- re-check v0.8 (OI-FW-49)
 * Independent equivalence of the v0.8 E-stop reaction with the e600169 reaction (hal_step_abort() +
 * hal_ena_set(false)) in the states A's sweep does not cover: last period armed (OPM), a CLEAN halt
 * in progress (OPM set by stop_now during a pulse), a STATIC_LEVEL hold while idle, and
 * motion.ena_invert = 1. The deterministic model is run twice per case (same position). */
typedef struct {
    uint32_t done, cut, rise, upd, ccmr1_oc, cen, bsrr;
    int32_t  count;
    bool     running, meas;
    uint32_t gen;
} vobs_t;

static vobs_t vcase(int st, uint32_t off, bool reaction, bool inv)
{
    vobs_t o;
    uint32_t g0;
    m_ena_inv = inv;
    reset_model();
    if (st == 3) {                                    /* idle STATIC_LEVEL hold (measurement image) */
        vtgt_g_meas_static = 1u;
        m_tim2.CCMR1 = OC1M_FORCE_INACTIVE | TIM_CCMR1_OC1M_0 | TIM_CCMR1_OC1PE;   /* forced active */
    } else {
        m_last_at = (st == 1) ? 2u : 0u;              /* st 1: the 3rd period is the last one (OPM) */
        core_hw_start();
        while (m_upd < 2u) {
            tick();
        }
        run(off);
        if (st == 2) {                                /* CLEAN halt chose OPM: a pulse is completing */
            while (!vtgt_hal_step_stop_now() && vtgt_hal_step_running()) {
                /* not in a pulse yet: the halt was immediate - retry the case one tick later */
                m_ena_inv = inv;
                reset_model();
                core_hw_start();
                while (m_upd < 2u) {
                    tick();
                }
                run(++off);
            }
        }
    }
    g0 = vtgt_hal_step_stop_gen();
    m_gpioa.BSRR = 0u;
    m_inject = reaction ? 1 : 3;
    inject_now();
    out_eval();
    o.ccmr1_oc = OC_MODE();
    o.cen = m_tim2.CR1 & TIM_CR1_CEN;
    o.bsrr = m_gpioa.BSRR;
    o.meas = vtgt_g_meas_static != 0u;
    o.gen = vtgt_hal_step_stop_gen() - g0;
    o.count = vtgt_hal_step_count();
    run(30000u);
    o.done = m_done;
    o.cut = 0u;
    o.rise = m_rise;
    o.upd = m_upd;
    o.running = vtgt_hal_step_running();
    return o;
}

void test_recheck_estop_reaction_equivalence_extra_states(void)
{
    int st;
    uint32_t n = 0u;
    for (st = 1; st <= 3; st++) {
        uint32_t off;
        int inv;
        for (inv = 0; inv < 2; inv++) {
            for (off = 0u; off < 3000u; off += (st == 3) ? 3000u : 13u) {
                vobs_t a = vcase(st, off, false, inv != 0);
                vobs_t b = vcase(st, off, true, inv != 0);
                TEST_ASSERT_EQUAL_UINT32(a.done, b.done);
                TEST_ASSERT_EQUAL_UINT32(a.rise, b.rise);
                TEST_ASSERT_EQUAL_UINT32(a.upd, b.upd);
                TEST_ASSERT_EQUAL_UINT32(a.ccmr1_oc, b.ccmr1_oc);
                TEST_ASSERT_EQUAL_UINT32(OC1M_FORCE_INACTIVE, b.ccmr1_oc);
                TEST_ASSERT_EQUAL_UINT32(0u, b.cen);
                TEST_ASSERT_EQUAL_UINT32(a.bsrr, b.bsrr);
                TEST_ASSERT_EQUAL_UINT32(inv ? (1u << (PIN_ENA_BIT + 16u)) : (1u << PIN_ENA_BIT), b.bsrr);
                TEST_ASSERT_EQUAL_INT32(a.count, b.count);
                TEST_ASSERT_EQUAL_INT32((int32_t)b.done, b.count);       /* SAF-FW-004 */
                TEST_ASSERT_FALSE(b.running);
                TEST_ASSERT_FALSE(b.meas);
                TEST_ASSERT_EQUAL_UINT32(1u, b.gen);                     /* one bump (also idle) */
                TEST_ASSERT_TRUE(a.gen >= 1u);
                n++;
            }
        }
    }
    m_ena_inv = false;
    TEST_ASSERT_TRUE(n > 900u);                                          /* anti-skip */
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_recheck_estop_reaction_equivalence_extra_states);
    RUN_TEST(test_control_plain_start);
    RUN_TEST(test_fwr01_limit_halt_in_start_window_visible);
    RUN_TEST(test_fwr01_estop_halt_in_start_window_visible);
    RUN_TEST(test_fwr02_estop_inside_start);
    RUN_TEST(test_fwr02_limit_inside_start);
    return UNITY_END();
}
