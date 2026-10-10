/* Validator E - §16 (FW v0.8.6): TIM2 register model + the TARGET step HAL hal/f446/step_tim2.c,
 * instantiated once per translation unit under a name prefix VP(): tu_core.c compiles the target entry
 * (HOST_TEST undefined: TIM2_IRQHandler -> step_isr_core(cnt)), tu_seam.c the seam entry (HOST_TEST:
 * TIM2_IRQHandler -> step_isr() -> step_isr_core(hal_step_count()), the twin / seam v1 form). Both call
 * the REAL core (core/motion.c) through the probes in test_main.c.
 * Timer model (as test_val_steprace): PWM mode 2, ARR / CCR1 shadows loaded at the update event
 * (CNT = ARR + 1 -> 0), OPM clears CEN at the update, UIF set at the update; the step ISR is dispatched
 * right after the update unless held (missed-update / FWR-05 late-latch scenarios). Origin: own model,
 * register prelude copied from A's test_impl_steptim @e600169 (trimmed).
 * Include this header once per TU after defining VP(n) (and HOST_TEST as wanted).
 */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

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

static uint32_t dwt_cycles(void) { return (uint32_t)(2u * m_t); }   /* 180 MHz core */

#define hal_ena_set             VP(hal_ena_set)
#define hal_step_init           VP(hal_step_init)
#define hal_step_set_dir        VP(hal_step_set_dir)
#define hal_step_start          VP(hal_step_start)
#define hal_step_set_period     VP(hal_step_set_period)
#define hal_step_set_period_now VP(hal_step_set_period_now)
#define hal_step_arm_last       VP(hal_step_arm_last)
#define hal_step_stop_now       VP(hal_step_stop_now)
#define hal_step_abort          VP(hal_step_abort)
#define hal_step_count          VP(hal_step_count)
#define hal_step_set_count      VP(hal_step_set_count)
#define hal_step_running        VP(hal_step_running)
#define hal_step_stop_gen       VP(hal_step_stop_gen)
#define g_meas_static           VP(g_meas_static)
#define TIM2_IRQHandler         VP(TIM2_IRQHandler)
#define step_estop_reaction     VP(step_estop_reaction)

#include "../../src/hal/f446/step_tim2.c"

#include "isrc_api.h"

/* ---- timer model ---- */
#define OC_MODE() (m_tim2.CCMR1 & TIM_CCMR1_OC1M)
static uint32_t m_arr_sh, m_ccr_sh;
static bool     m_out, m_pend, m_hold, m_in_isr;
static isrc_obs_t m_o;

static void out_eval(void)
{
    bool act = OC_MODE() == OC1M_PWM2 && m_tim2.CNT >= m_ccr_sh;
    if (act && !m_out) {
        if (m_o.rises < ISRC_RISE_LOG) {
            m_o.rise_t[m_o.rises] = m_t;
        }
        m_o.rises++;
        m_o.rise_at = m_t;
    } else if (!act && m_out) {
        if (m_t - m_o.rise_at < m_o.pw) {
            m_o.runts++;
        } else {
            m_o.done++;
        }
    }
    m_out = act;
}

static void tick(void)
{
    m_t++;
    if ((m_tim2.CR1 & TIM_CR1_CEN) != 0u) {
        m_tim2.CNT++;
        if (m_tim2.CNT == m_arr_sh + 1u) {            /* update event: the pulse ends here */
            m_o.upd++;
            m_tim2.CNT = 0u;
            m_tim2.SR |= TIM_SR_UIF;
            m_arr_sh = m_tim2.ARR;
            m_ccr_sh = m_tim2.CCR1;
            if ((m_tim2.CR1 & TIM_CR1_OPM) != 0u) {
                m_tim2.CR1 &= ~TIM_CR1_CEN;
            }
            m_pend = true;
        }
    }
    out_eval();
}

static void dispatch(void)
{
    if (m_pend && !m_hold && !m_in_isr && m_primask == 0u) {
        m_pend = false;
        m_in_isr = true;
        VP(TIM2_IRQHandler)();
        m_in_isr = false;
        out_eval();
    }
}

void VP(advance)(uint64_t n)                          /* time inside an ISR / a pre-empting handler */
{
    uint64_t i;
    for (i = 0u; i < n; i++) {
        tick();
    }
}

void VP(run)(uint64_t n)
{
    uint64_t i;
    for (i = 0u; i < n; i++) {
        tick();
        dispatch();
    }
}

void VP(hold)(bool on)
{
    m_hold = on;
    dispatch();
}

void VP(reset)(uint32_t c1, uint32_t c2, int32_t count0, int dir, uint32_t pw)
{
    hal_step_cfg_t cfg;
    cfg.pw_ticks = pw;
    cfg.dir_setup_ticks = 1800u;
    cfg.pul_invert = false;
    cfg.ena_invert = false;
    memset(&m_tim2, 0, sizeof m_tim2);
    memset(&m_gpioa, 0, sizeof m_gpioa);
    memset(&m_o, 0, sizeof m_o);
    m_o.pw = pw;
    m_t = 0u;
    m_out = m_pend = m_hold = m_in_isr = false;
    m_primask = 0u;
    s_count = 0;
    s_running = false;
    s_late = false;
    s_late_cur = 0u;
    s_stop_gen = 0u;
    s_init = false;
    VP(g_meas_static) = 0u;
    (void)VP(hal_step_init)(&cfg);
    VP(hal_step_set_dir)(dir);
    VP(hal_step_set_count)(count0);
    VP(hal_step_start)(c1);
    m_arr_sh = m_tim2.ARR;                            /* UG loaded the shadows at the start */
    m_ccr_sh = m_tim2.CCR1;
    m_tim2.SR &= ~TIM_SR_UIF;
    VP(hal_step_set_period)(c2);
}

const isrc_obs_t *VP(obs)(void)
{
    m_o.count = VP(hal_step_count)();
    m_o.running = VP(hal_step_running)();
    m_o.stop_gen = VP(hal_step_stop_gen)();
    m_o.cnt = m_tim2.CNT;
    m_o.arr = m_tim2.ARR;
    m_o.ccr1 = m_tim2.CCR1;
    m_o.cr1 = m_tim2.CR1;
    m_o.ccmr1 = m_tim2.CCMR1;
    m_o.t = m_t;
    return &m_o;
}

int32_t VP(count)(void) { return VP(hal_step_count)(); }
uint32_t VP(cnt_reg)(void) { return m_tim2.CNT; }
uint32_t VP(ccr_shadow)(void) { return m_ccr_sh; }
bool VP(uif)(void) { return (m_tim2.SR & TIM_SR_UIF) != 0u; }
bool VP(stop_now)(void) { return VP(hal_step_stop_now)(); }
bool VP(abort)(void) { return VP(hal_step_abort)(); }
void VP(estop)(void) { VP(step_estop_reaction)(); }
void VP(set_period)(uint32_t p) { VP(hal_step_set_period)(p); }
void VP(set_period_now)(uint32_t p) { VP(hal_step_set_period_now)(p); }
uint32_t VP(cur)(void) { return s_cur; }
uint32_t VP(pre)(void) { return s_pre; }
bool VP(late)(void) { return s_late; }
