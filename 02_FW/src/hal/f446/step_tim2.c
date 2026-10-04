/* Step generator on TIM2 (FW_design §5.3, §5.6; pinout §2): 32-bit counter at the APB1 timer clock
 * (90 MHz, PSC 0), PWM mode 2 on CH1 (PA0, AF1): PUL active for the last PW ticks of each period, so
 * the pulse ends exactly at the update event; ARR / CCR1 preloaded (ARPE, OC1PE) -> one update IRQ
 * per completed pulse (level 2) counts the step and calls step_isr() for the period after next.
 * Stop primitives (RAM-resident, PRIMASK <= 0.2 us, decision = pure stepgen.h):
 *   hal_step_stop_now()  CLEAN: a pulse running or starting within the guard completes and the
 *                        counter stops in hardware at its update (OPM, counted); else force inactive.
 *   hal_step_abort()     TRUNCATE (E-stop, HardFault): force inactive at once; a cut pulse is not
 *                        counted (POS_UNCERTAIN is the core's business).
 *   Both count a pulse whose update event preceded the halt but whose ISR is still pending
 *   (DEF-M2-01, halt_hw()).
 * Twin semantics (tools/README hal_step row) are reproduced: hal_step_start() resets the preload
 * (the core preloads period 2 at once), the final update (OPM) only counts and does not call
 * step_isr(), set_period_now() stretches the running period only before its pulse.
 * Missed update (SAF-FW-004): an update ISR entered more than 1.5 periods after the previous one
 * (DWT) counts the estimated missed step as well, so the core's per-update count check raises
 * STEP_FAULT.
 * DIR: hal_step_set_dir(+-1) logical, +-2 = logical with the DIR output inverted (motion.dir_invert;
 * interim encoding, seam request SR-M2-01).
 * Implements: FW-MOT-001, FW-MOT-002 (count per completed pulse), FW-MOT-008 (ENA output),
 *             SAF-FW-002, SAF-FW-004, SAF-FW-005 (abort + ENA), SAF-FW-018 (PUL idle, ENA holding
 *             level until the core decides), NFR-007
 */
#include "f446.h"
#include "board_pins.h"
#include "hal_step.h"
#include "irq_prio.h"
#include "meas_dwt.h"
#include "stepgen.h"

#ifndef RAMFUNC                                       /* host harness (test_impl_steptim) overrides */
#define RAMFUNC __attribute__((section(".RamFunc"), noinline, long_call))
#endif

#define OC1M_FORCE_INACTIVE (TIM_CCMR1_OC1M_2)                                        /* 100 */
#define OC1M_PWM2           (TIM_CCMR1_OC1M_2 | TIM_CCMR1_OC1M_1 | TIM_CCMR1_OC1M_0)   /* 111 */

static volatile int32_t  s_count;
static volatile int32_t  s_dir = 1;
static volatile bool     s_running;
static volatile uint32_t s_stop_gen;
static volatile uint32_t s_cur;          /* running period (shadow), ticks */
static volatile uint32_t s_pre;          /* preloaded period, ticks */
static volatile uint32_t s_t_upd;        /* DWT at the previous update */
static uint32_t s_pw = 900u, s_dir_setup = 1800u, s_guard = 45u, s_f = 90000000u;
static bool     s_ena_inv;
static bool     s_init;
/* HW_MEAS STATIC_LEVEL hold (meas_f4.c sets it; always 0 in the release image): released at the next
 * motion start / ENA change (ICD v0.6 Appendix C op 8) */
volatile uint8_t g_meas_static;

static inline __attribute__((always_inline)) void oc1m(uint32_t m)
{
    TIM2->CCMR1 = (TIM2->CCMR1 & ~TIM_CCMR1_OC1M) | m;
}

/* DEF-M2-01 (SAF-FW-004): an update event (= a completed pulse) that happened before this halt but
 * whose ISR has not run yet (a level 0/1 ISR or a PRIMASK section pre-empted it) is counted here,
 * after the counter is stopped (no further update can follow), so the count is exact at once (the
 * E-stop / limit edge record taken right after the halt sees it); the pending NVIC request then
 * finds UIF clear and returns. Called with PRIMASK set or from the step ISR (UIF already cleared). */
RAMFUNC static void halt_hw(void)
{
    oc1m(OC1M_FORCE_INACTIVE);                       /* PUL inactive at once (no runt) */
    TIM2->CR1 &= ~(TIM_CR1_CEN | TIM_CR1_OPM);
    if ((TIM2->SR & TIM_SR_UIF) != 0u) {
        TIM2->SR = ~TIM_SR_UIF;
        s_count += s_dir;                            /* completed before the halt: counted once */
    }
    s_running = false;
    s_stop_gen++;
}

/* compare value of the period the counter is running now: after an update whose ISR is still
 * pending the preloaded period is already running (DEF-M2-01: decisions on the right interval) */
static inline __attribute__((always_inline)) uint32_t ccr_running(void)
{
    uint32_t c = ((TIM2->SR & TIM_SR_UIF) != 0u) ? s_pre : s_cur;
    return c - s_pw;
}

RAMFUNC void hal_ena_set(bool enabled)
{
    if (g_meas_static != 0u) {
        g_meas_static = 0u;
        oc1m(OC1M_FORCE_INACTIVE);
    }
    /* LED current (pin high) = driver disabled; motion.ena_invert swaps (boot value) */
    bool high = (!enabled) != s_ena_inv;
    PIN_ENA_PORT->BSRR = high ? (1u << PIN_ENA_BIT) : (1u << (PIN_ENA_BIT + 16u));
}

uint32_t hal_step_init(const hal_step_cfg_t *cfg)
{
    uint32_t f = time_timer_hz();                    /* APB1 timer clock (FW-PLT-002) */
    s_f = f;
    s_pw = cfg->pw_ticks;
    s_dir_setup = cfg->dir_setup_ticks;
    s_guard = f / 2000000u;                          /* 0.5 us */
    if (!s_init) {
        s_init = true;
        s_ena_inv = cfg->ena_invert;
        RCC->APB1ENR |= RCC_APB1ENR_TIM2EN;
        (void)RCC->APB1ENR;
        TIM2->CR1 = TIM_CR1_ARPE;
        TIM2->PSC = 0u;
        TIM2->ARR = 0xFFFFFFFFu;
        TIM2->CCMR1 = OC1M_FORCE_INACTIVE | TIM_CCMR1_OC1PE;      /* CC1 output, preload */
        TIM2->CCER = TIM_CCER_CC1E | (cfg->pul_invert ? TIM_CCER_CC1P : 0u);
        TIM2->EGR = TIM_EGR_UG;
        TIM2->SR = 0u;
        TIM2->DIER = TIM_DIER_UIE;
        NVIC_SetPriority(TIM2_IRQn, PRIO_STEP);
        NVIC_ClearPendingIRQ(TIM2_IRQn);
        NVIC_EnableIRQ(TIM2_IRQn);
        /* pins: ODR before MODER (pinout §1.1): ENA at the "no current" level = holding (D-13), DIR 0,
         * PUL to AF1 only after OC1 is forced inactive */
        hal_ena_set(true);
        gpio_mode(PIN_ENA_PORT, PIN_ENA_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
        gpio_write(PIN_DIR_PORT, PIN_DIR_BIT, false);
        gpio_mode(PIN_DIR_PORT, PIN_DIR_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
        gpio_af(PIN_PUL_PORT, PIN_PUL_BIT, 1u);
        gpio_mode(PIN_PUL_PORT, PIN_PUL_BIT, GPIO_MODE_AF_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    }
    return f;
}

void hal_step_set_dir(int dir)
{
    bool inv = dir == 2 || dir == -2;
    if (s_running) {
        return;                                      /* only while stopped (FW-MOT-001) */
    }
    s_dir = (dir > 0) ? 1 : -1;
    gpio_write(PIN_DIR_PORT, PIN_DIR_BIT, (dir > 0) != inv);
}

void hal_step_start(uint32_t first)
{
    uint32_t ccr = (first > s_pw) ? first - s_pw : 1u;
    uint32_t per;
    g_meas_static = 0u;                              /* PWM2 below replaces any STATIC_LEVEL */
    if (ccr < s_dir_setup) {
        ccr = s_dir_setup;                           /* first edge >= dir_setup after CEN */
    }
    per = ccr + s_pw;
    TIM2->CR1 &= ~(TIM_CR1_CEN | TIM_CR1_OPM);
    TIM2->CNT = 0u;
    TIM2->ARR = per - 1u;
    TIM2->CCR1 = ccr;
    TIM2->EGR = TIM_EGR_UG;                          /* load the shadows */
    TIM2->SR = ~TIM_SR_UIF;
    s_cur = per;
    s_pre = per;                                     /* repeats until the core preloads period 2 */
    TIM2->ARR = per - 1u;
    TIM2->CCR1 = per - s_pw;
    oc1m(OC1M_PWM2);
    s_running = true;
    s_t_upd = dwt_cycles();
    TIM2->CR1 |= TIM_CR1_CEN;
}

void hal_step_set_period(uint32_t ticks)
{
    s_pre = ticks;
    TIM2->ARR = ticks - 1u;                          /* preload: after the running period */
    TIM2->CCR1 = ticks - s_pw;
}

void hal_step_set_period_now(uint32_t ticks)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    MDWT_T0(t0);
    if (s_running && stepgen_stretch_ok(TIM2->CNT, s_cur - s_pw, s_cur - 1u, s_pw, s_guard, ticks)) {
        TIM2->CR1 &= ~TIM_CR1_ARPE;
        TIM2->CCMR1 &= ~TIM_CCMR1_OC1PE;
        TIM2->ARR = ticks - 1u;                      /* running interval extended (OI-ICD-07) */
        TIM2->CCR1 = ticks - s_pw;
        TIM2->CCMR1 |= TIM_CCMR1_OC1PE;
        TIM2->CR1 |= TIM_CR1_ARPE;
        s_cur = ticks;
        TIM2->ARR = s_pre - 1u;                      /* restore the preload */
        TIM2->CCR1 = s_pre - s_pw;
    }
    MDWT_END(MDWT_SET_NOW, t0);
    __set_PRIMASK(pm);
}

void hal_step_arm_last(void)
{
    TIM2->CR1 |= TIM_CR1_OPM;                        /* the counter stops at the next update */
}

RAMFUNC bool hal_step_stop_now(void)
{
    uint32_t pm = __get_PRIMASK();
    bool complete = false;
    __disable_irq();
    MDWT_T0(t0);
    if (s_running) {
        if ((TIM2->CR1 & TIM_CR1_CEN) != 0u &&       /* not already stopped by OPM */
            stepgen_halt_complete(TIM2->CNT, ccr_running(), s_guard)) {
            TIM2->CR1 |= TIM_CR1_OPM;                /* pulse completes, counted, then stops */
            complete = true;
        } else {
            halt_hw();
        }
    }
    MDWT_END(MDWT_STOP_NOW, t0);
    __set_PRIMASK(pm);
    return complete;
}

RAMFUNC bool hal_step_abort(void)
{
    uint32_t pm = __get_PRIMASK();
    bool cut = false;
    __disable_irq();
    MDWT_T0(t0);
    if (s_running) {
        cut = (TIM2->CR1 & TIM_CR1_CEN) != 0u && stepgen_abort_cuts(TIM2->CNT, ccr_running());
        halt_hw();
    }
    MDWT_END(MDWT_ABORT, t0);
    __set_PRIMASK(pm);
    return cut;
}

int32_t hal_step_count(void) { return s_count; }

void hal_step_set_count(int32_t steps)
{
    if (!s_running) {
        s_count = steps;
    }
}

bool hal_step_running(void) { return s_running; }
uint32_t hal_step_stop_gen(void) { return s_stop_gen; }

void TIM2_IRQHandler(void)
{
    uint32_t now = dwt_cycles();
    uint32_t el = now - s_t_upd;
    step_next_t r;
    if ((TIM2->SR & TIM_SR_UIF) == 0u) {
        return;
    }
    TIM2->SR = ~TIM_SR_UIF;
    s_t_upd = now;
    if (s_cur < 0x40000000u && el > 3u * s_cur) {    /* > 1.5 periods (2 cycles per tick) */
        s_count += s_dir;                            /* missed update: the core flags STEP_FAULT */
    }
    s_count += s_dir;                                /* one update = one completed pulse */
    if ((TIM2->CR1 & TIM_CR1_CEN) == 0u) {           /* stopped by OPM (last / CLEAN halt) */
        oc1m(OC1M_FORCE_INACTIVE);
        TIM2->CR1 &= ~TIM_CR1_OPM;
        if (s_running) {                             /* halt_hw() already counted and stopped */
            s_running = false;
            s_stop_gen++;
        }
    } else {
        s_cur = s_pre;                               /* the preloaded period is running now */
        r = step_isr();
        if (r.stop) {
            halt_hw();
        } else {
            if (r.period != 0u) {
                hal_step_set_period(r.period);
            }
            if (r.last) {
                TIM2->CR1 |= TIM_CR1_OPM;
            }
        }
    }
    MDWT_VAL(MDWT_ISR_STEP, dwt_cycles() - now);      /* HW_MEAS_DWT only (OI-FW-37) */
}
