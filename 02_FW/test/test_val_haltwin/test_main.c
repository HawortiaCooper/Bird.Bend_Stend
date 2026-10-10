/* Validator E - code review re-checks §14 / §15 (02_FW/docs/FW_code_review.md, FW v0.8.4 / v0.8.5):
 * timing windows of the TARGET stop / stretch primitives in hal/f446/step_tim2.c, on an ACCESS-TIMED
 * register model.
 *
 * Model: every TIM2 register access first lets the timer run for m_acc timer ticks (90 MHz), then
 * the access happens. So the counter moves between the CNT read of a decision and the register write
 * that acts on it, as on the target (APB1 access ~12 core cycles = 6 timer ticks nominal; instruction
 * cost is not modelled, so 6 ticks per access is optimistic and larger costs stand for slower paths).
 * Timer model: PWM mode 2 (active while CNT >= CCR1), ARR / CCR1 shadow registers loaded at the update
 * event (CNT = ARR + 1 -> 0) while ARPE / OC1PE are set; a write while the preload is off goes to the
 * shadow at once; OPM clears CEN at the update; UIF set at the update. The step ISR (level 2) runs
 * when the caller's masking ends (after the primitive returns), never inside a PRIMASK window.
 * The guard is taken from the target (s_guard after hal_step_init()), not assumed.
 * Model origin: own model; register prelude as in test_val_steprace (copied from A's test_impl_steptim
 * @e600169, trimmed).
 *
 * Verifies: SAF-FW-002 (CLEAN halt: no PUL edge later than 200 us after a limit edge), SAF-FW-004 (no
 * runt, count exact), FW_design §5.3 ("the last PUL edge after a CLEAN stop comes <= pulse_high +
 * guard after the call"), OI-ICD-07 stretch (FW_design §5.6.4)
 * TC: TC-SAF-FW-002-05 (FWR-21, CLEAN halt update window), TC-SAF-FW-004-05 (OBS-RC-7, guard margin)
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "unity.h"

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
static TIM_TypeDef *tim2_acc(void);
#define TIM2  (tim2_acc())
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

static uint32_t dwt_cycles(void) { return (uint32_t)(2u * m_t); }   /* 180 MHz core */

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

/* ---- access-timed timer model ---- */
#define PW 900u                                       /* pulse_high 10 us */
static uint32_t m_acc = 6u;                           /* ticks per TIM2 access */
static uint32_t m_arr_sh, m_ccr_sh, m_arr_seen, m_ccr_seen;
static bool     m_out, m_pend, m_in_isr;
static uint32_t m_done, m_upd, m_runt;
static uint64_t m_rise_t, m_last_edge_t;
static uint32_t m_isr_period;                         /* every step ISR preloads this period */

step_next_t vtgt_step_isr(void)
{
    step_next_t r;
    memset(&r, 0, sizeof r);
    r.period = m_isr_period;
    return r;
}

/* a write since the last look goes to the shadow at once while the preload is off */
static void sync_regs(void)
{
    if (m_tim2.ARR != m_arr_seen) {
        m_arr_seen = m_tim2.ARR;
        if ((m_tim2.CR1 & TIM_CR1_ARPE) == 0u) {
            m_arr_sh = m_tim2.ARR;
        }
    }
    if (m_tim2.CCR1 != m_ccr_seen) {
        m_ccr_seen = m_tim2.CCR1;
        if ((m_tim2.CCMR1 & TIM_CCMR1_OC1PE) == 0u) {
            m_ccr_sh = m_tim2.CCR1;
        }
    }
    if ((m_tim2.EGR & TIM_EGR_UG) != 0u) {            /* UG: reload the shadows, CNT = 0 */
        m_tim2.EGR = 0u;
        m_tim2.CNT = 0u;
        m_arr_sh = m_tim2.ARR;
        m_ccr_sh = m_tim2.CCR1;
        m_tim2.SR |= TIM_SR_UIF;
    }
}

static void out_eval(void)
{
    uint32_t oc = m_tim2.CCMR1 & TIM_CCMR1_OC1M;
    bool act;
    if (oc == OC1M_PWM2) {
        act = m_tim2.CNT >= m_ccr_sh;
    } else {
        act = oc == (TIM_CCMR1_OC1M_2 | TIM_CCMR1_OC1M_0);   /* forced active (STATIC_LEVEL) */
    }
    if (act && !m_out) {
        m_rise_t = m_t;
        m_last_edge_t = m_t;
    } else if (!act && m_out) {
        m_last_edge_t = m_t;
        if (m_t - m_rise_t < PW) {
            m_runt++;                                 /* pulse shorter than pulse_high */
        } else {
            m_done++;
        }
    }
    m_out = act;
}

static void tick(void)
{
    sync_regs();
    m_t++;
    if ((m_tim2.CR1 & TIM_CR1_CEN) != 0u) {
        m_tim2.CNT++;
        if (m_tim2.CNT == m_arr_sh + 1u) {            /* update event: the pulse ends here */
            m_upd++;
            m_tim2.CNT = 0u;
            m_tim2.SR |= TIM_SR_UIF;
            m_arr_sh = m_tim2.ARR;
            m_ccr_sh = m_tim2.CCR1;
            m_arr_seen = m_tim2.ARR;
            m_ccr_seen = m_tim2.CCR1;
            if ((m_tim2.CR1 & TIM_CR1_OPM) != 0u) {
                m_tim2.CR1 &= ~TIM_CR1_CEN;
            }
            m_pend = true;
        }
    }
    out_eval();
}

static TIM_TypeDef *tim2_acc(void)
{
    uint32_t k;
    sync_regs();                                      /* the previous access has taken effect */
    for (k = 0u; k < m_acc; k++) {
        tick();
    }
    return &m_tim2;
}

static void dispatch(void)
{
    if (m_pend && m_primask == 0u && !m_in_isr) {
        m_pend = false;
        m_in_isr = true;
        vtgt_TIM2_IRQHandler();
        m_in_isr = false;
    }
}

static void run(uint64_t n)
{
    uint64_t i;
    for (i = 0u; i < n; i++) {
        tick();
        dispatch();
    }
}

static void reset_model(uint32_t period)
{
    hal_step_cfg_t cfg = {PW, 1800u, false, false};
    memset(&m_tim2, 0, sizeof m_tim2);
    memset(&m_gpioa, 0, sizeof m_gpioa);
    m_t = 0u;
    m_out = m_pend = m_in_isr = false;
    m_done = m_upd = m_runt = 0u;
    m_rise_t = m_last_edge_t = 0u;
    m_arr_sh = m_ccr_sh = m_arr_seen = m_ccr_seen = 0u;
    m_primask = 0u;
    m_isr_period = period;
    s_count = 0;
    s_running = false;
    s_late = false;
    s_late_cur = 0u;
    s_init = false;
    vtgt_g_meas_static = 0u;
    (void)vtgt_hal_step_init(&cfg);
    vtgt_hal_step_set_dir(1);
    vtgt_hal_step_start(period);
    vtgt_hal_step_set_period(period);
    dispatch();
}

/* run until the 3rd update, then until CNT == target (counter running) */
static void run_to_cnt(uint32_t target)
{
    while (m_upd < 3u) {
        run(1u);
    }
    while (m_tim2.CNT != target) {
        run(1u);
    }
}

/* one CLEAN halt case: the call starts when CNT = ARR - d (0 = last tick of the pulse);
 * returns the time from the call to the last PUL edge, in timer ticks */
static uint64_t clean_case(uint32_t period, uint32_t d, uint32_t *extra, int32_t *count_err)
{
    uint64_t t_call;
    uint32_t done0;
    reset_model(period);
    run_to_cnt(period - 1u - d);
    t_call = m_t;
    done0 = m_done + (m_out ? 1u : 0u);               /* the running pulse completes (CLEAN) */
    (void)vtgt_hal_step_stop_now();                   /* EXTI0/1 limit fixed reaction (level 1) */
    dispatch();
    run(3u * (uint64_t)period);
    *extra = m_done - done0;
    *count_err = vtgt_hal_step_count() - (int32_t)m_done;
    return (m_last_edge_t > t_call) ? m_last_edge_t - t_call : 0u;
}


/* ---- guard margin (OBS-RC-7, §15 of the review) --------------------------------------------------
 * A decision taken at CNT = CCR1 - guard - 1 (the last CNT that still chooses "force inactive" /
 * "stretch") is runt-free only if the CNT read -> acting write path is shorter than the guard. In the
 * model that path is k accesses x the per-access cost c (k = accesses after the CNT read up to the
 * acting write: CLEAN halt_hw path k = 2 (SR read, CCMR1 write), stretch k = 4 (CR1, CCMR1, ARR, CCR1
 * writes)). Criterion (validator's, not A's OI-FW-52 proposal):
 *   (1) anti-vacuity: the model MUST produce a runt once k x c exceeds the guard (else the test could
 *       not see a guard that is too small);
 *   (2) no runt while k x c <= guard (the decision logic leaves the guard as the only margin);
 *   (3) margin: runt-free at 2 x the conservative target path (the static model is uncalibrated until
 *       HG-18, OI-FW-46). Conservative target paths from the disassembly (§14 / §15 of the review):
 *       stretch CNT read -> CCR1 write ~91 cycles = 46 ticks; CLEAN CNT read -> CCMR1 write (halt_hw)
 *       ~0.42 us = 38 ticks. On v0.8.4 (guard 45 ticks) (3) fails for the stretch: OBS-RC-7. */
#define CONS_STRETCH_TICKS 46u
#define CONS_HALT_TICKS    38u
#define K_STRETCH          4u
#define K_HALT             2u

static uint32_t guard_ticks(void)
{
    reset_model(112500u);
    return s_guard;                                   /* the target's value after hal_step_init() */
}

/* stretch (OI-ICD-07): set_period_now() decides on CNT + guard < CCR1 and then writes CR1, CCMR1, ARR,
 * CCR1; if CNT passes the old CCR1 before the CCR1 write, PUL goes active at the old compare and back
 * inactive at the new one: a runt (SAF-FW-004). nx offsets below the decision boundary. */
static uint32_t stretch_runts(uint32_t acc, uint32_t nx, int32_t *count_err)
{
    const uint32_t period = 112500u;
    const uint32_t ccr = period - PW;
    uint32_t x, runts = 0u, g = guard_ticks();
    *count_err = 0;
    for (x = 0u; x < nx; x++) {
        m_acc = acc;
        reset_model(period);
        run_to_cnt(ccr - g - 1u - x - 3u * acc);      /* SR, CR1, CNT: the read returns ccr - g - 1 - x */
        vtgt_hal_step_set_period_now(2u * period);
        dispatch();
        run(3u * (uint64_t)period);
        runts += m_runt;
        if (vtgt_hal_step_count() != (int32_t)m_done) {
            *count_err = vtgt_hal_step_count() - (int32_t)m_done;
        }
    }
    return runts;
}

/* CLEAN halt at the boundary (CNT + guard < CCR1 at the read -> halt_hw(), force inactive): the pulse
 * must not start before the CCMR1 write. The CNT read is the first TIM2 access of stop_now (v0.8.5). */
static uint32_t clean_prepulse_runts(uint32_t acc, uint32_t ny)
{
    const uint32_t period = 112500u;
    uint32_t y, runts = 0u, g = guard_ticks();
    for (y = 0u; y < ny; y++) {
        uint32_t extra;
        int32_t cerr;
        uint64_t late;
        m_acc = acc;
        late = clean_case(period, PW + g + y + acc, &extra, &cerr);   /* the read returns ccr - g - 1 - y */
        TEST_ASSERT_EQUAL_INT32(0, cerr);
        /* should the compiler read SR before CNT, the decision may take the OPM path: the imminent
         * pulse then completes in full, within pulse_high + guard (+ slack) of the call */
        TEST_ASSERT_TRUE(extra <= 1u);
        TEST_ASSERT_TRUE(late <= (uint64_t)PW + g + 1u + y + 64u + 4u * acc);
        runts += m_runt;
    }
    return runts;
}

void test_stretch_no_runt_nominal_bus(void)
{
    int32_t cerr;
    TEST_ASSERT_EQUAL_UINT32(0u, stretch_runts(6u, 40u, &cerr));
    TEST_ASSERT_EQUAL_INT32(0, cerr);
}

void test_clean_halt_before_pulse_no_runt_nominal_bus(void)
{
    TEST_ASSERT_EQUAL_UINT32(0u, clean_prepulse_runts(6u, 40u));
}

typedef uint32_t (*runt_fn)(uint32_t acc);
static uint32_t stretch_at(uint32_t acc) { int32_t e; uint32_t r = stretch_runts(acc, 1u, &e); TEST_ASSERT_EQUAL_INT32(0, e); return r; }
static uint32_t halt_at(uint32_t acc) { return clean_prepulse_runts(acc, 1u); }

static void guard_margin(const char *name, runt_fn f, uint32_t k, uint32_t cons_ticks)
{
    uint32_t g = guard_ticks();
    uint32_t c_cons = (cons_ticks + k - 1u) / k;      /* per-access cost equivalent to the target path */
    uint32_t c_beyond = g / k + 2u;                   /* k x c_beyond > g + k: a runt must appear */
    uint32_t c, thr = 0u;
    char msg[200];
    for (c = 6u; c <= c_beyond; c++) {
        if (f(c) != 0u) {
            thr = c;
            break;
        }
    }
    snprintf(msg, sizeof msg,
             "%s: guard %u ticks; first runt at %u ticks/access (path %u ticks); conservative target path %u "
             "ticks -> margin factor %.2f (required >= 2)",
             name, (unsigned)g, (unsigned)thr, (unsigned)(k * thr), (unsigned)cons_ticks,
             (double)(k * thr) / (double)cons_ticks);
    TEST_MESSAGE(msg);
    TEST_ASSERT_TRUE_MESSAGE(thr != 0u, "anti-vacuity: no runt even with the path beyond the guard");
    TEST_ASSERT_TRUE_MESSAGE(k * thr > g, "runt with a read -> write path inside the guard (decision logic)");
    TEST_ASSERT_TRUE_MESSAGE(thr >= 2u * c_cons, msg);
}

/* TC-SAF-FW-004-05 (b) */
void test_stretch_guard_margin(void) { guard_margin("stretch", stretch_at, K_STRETCH, CONS_STRETCH_TICKS); }
void test_clean_halt_guard_margin(void) { guard_margin("CLEAN halt_hw", halt_at, K_HALT, CONS_HALT_TICKS); }

/* TC-SAF-FW-002-05. SAF-FW-002 / FW_design §5.3: a CLEAN halt whose running pulse ENDS between the CNT
 * read and the OPM write (the update reloads period N+1 and clears no CEN, OPM is armed only for N+1):
 * without the FWR-21 fix period N+1 runs to the end and emits one more pulse a full period after the
 * call (~1.25 ms at 1 mm/s with 800 steps/mm; budget 200 us). Run at the nominal (6) and the
 * conservative-equivalent (12) cost per access. FAILS on v0.8.3 / v0.8.4. */
static void late_pulse_case(uint32_t acc)
{
    const uint32_t period = 112500u;                  /* 1.25 ms */
    uint32_t g = guard_ticks();
    const uint64_t bound = PW + g + 64u;              /* pulse_high + guard (+ 0.7 us slack) */
    uint32_t d, bad = 0u, first_bad = 0xFFFFFFFFu;
    uint64_t worst = 0u;
    char msg[180];
    for (d = 0u; d < 96u; d++) {
        uint32_t extra;
        int32_t cerr;
        uint64_t late;
        m_acc = acc;
        late = clean_case(period, d, &extra, &cerr);
        TEST_ASSERT_EQUAL_INT32_MESSAGE(0, cerr, "count != completed pulses (SAF-FW-004)");
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, m_runt, "runt pulse");
        if (late > bound || extra != 0u) {
            bad++;
            if (first_bad == 0xFFFFFFFFu) {
                first_bad = d;
            }
            if (late > worst) {
                worst = late;
            }
        }
    }
    snprintf(msg, sizeof msg,
             "%u ticks/access: %u of 96 CNT offsets (first ARR-%u): one more pulse, last PUL edge %.1f us "
             "after the CLEAN halt (bound %.1f us, SAF-FW-002 200 us)",
             (unsigned)acc, (unsigned)bad, (unsigned)first_bad, (double)worst / 90.0, (double)bound / 90.0);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, bad, msg);
}
void test_clean_halt_pulse_ending_in_window_no_late_pulse(void) { late_pulse_case(6u); }
void test_clean_halt_pulse_ending_in_window_conservative_bus(void) { late_pulse_case(12u); }

/* control: a CLEAN halt in the middle of the pulse completes that pulse only */
void test_control_clean_halt_mid_pulse(void)
{
    const uint32_t period = 112500u;
    uint32_t d;
    m_acc = 6u;
    for (d = 100u; d < PW - 60u; d += 37u) {
        uint32_t extra;
        int32_t cerr;
        uint64_t late = clean_case(period, d, &extra, &cerr);
        TEST_ASSERT_EQUAL_INT32(0, cerr);
        TEST_ASSERT_EQUAL_UINT32(0u, extra);
        TEST_ASSERT_EQUAL_UINT32(0u, m_runt);
        TEST_ASSERT_TRUE(late <= (uint64_t)d + 2u);
    }
}

void setUp(void) { m_acc = 6u; }
void tearDown(void) {}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_control_clean_halt_mid_pulse);
    RUN_TEST(test_stretch_no_runt_nominal_bus);
    RUN_TEST(test_clean_halt_before_pulse_no_runt_nominal_bus);
    RUN_TEST(test_stretch_guard_margin);
    RUN_TEST(test_clean_halt_guard_margin);
    RUN_TEST(test_clean_halt_pulse_ending_in_window_no_late_pulse);
    RUN_TEST(test_clean_halt_pulse_ending_in_window_conservative_bus);
    return UNITY_END();
}
