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
 * Start / halt protocol (FW_design §5.3 v0.8, review FWR-01 / FWR-02 / FWR-04 / FWR-05):
 * - every stop primitive called while the timer is idle still increments the stop generation, so a
 *   halt between the core's gate check and the start is visible to start-then-recheck (FWR-01);
 * - hal_step_start() arms the output, `s_running` and CEN in one PRIMASK section: a level-0/1 halt
 *   lands either before it (idle: generation bumped, the core's recheck stops the timer before the
 *   first edge, which comes >= motion.dir_setup_us >= 5 us after CEN) or after it (a normal halt of
 *   a running timer); it can no longer be undone by a read-modify-write of CR1 (FWR-02);
 * - OPM is set under PRIMASK (step ISR, hal_step_arm_last; FWR-04);
 * - a preload written while an update is pending (its ISR masked by CRIT_MOTION) first latches the
 *   period that the update started (`s_late`), so the pending ISR and the stop decisions use the
 *   interval that is really running; hal_step_set_period_now() decides on that interval (FWR-05).
 * NFR-007 (FW_design §9.8 v0.8): the halt writes CCMR1 / CR1 as constants (no read-modify-write: the
 * only bits in use are OC1M + OC1PE and CEN + OPM + ARPE, and OC1PE / ARPE are cleared only inside
 * the PRIMASK window of hal_step_set_period_now(), which no halt can interrupt); the E-stop handler
 * uses step_estop_reaction() (TRUNCATE + ENA disabled, no cut decision, no PRIMASK); the update ISR
 * stores the count once and sets OPM for the last period under PRIMASK (a level-0/1 halt between
 * the read and the write of CR1 could otherwise re-enable the stopped counter: phantom count).
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

/* step ISR result (NFR-007 v0.8.6): the target calls the core's step_isr_core(count) (register
 * return, no hal_step_count() call); host harnesses of this file provide step_isr() only, so there
 * the same packed form is built from it */
#if defined(HOST_TEST)
static inline uint64_t step_next_of(int32_t count)
{
    step_next_t r = step_isr();
    (void)count;
    return (uint64_t)r.period | (r.last ? STEP_NEXT_LAST : 0u) | (r.stop ? STEP_NEXT_STOP : 0u);
}
#else
#define step_next_of(count) step_isr_core(count)
#endif

#ifndef RAMFUNC                                       /* host harness (test_impl_steptim) overrides */
#define RAMFUNC __attribute__((section(".RamFunc"), noinline, long_call))
#endif

#define OC1M_FORCE_INACTIVE (TIM_CCMR1_OC1M_2)                                        /* 100 */
#define OC1M_PWM2           (TIM_CCMR1_OC1M_2 | TIM_CCMR1_OC1M_1 | TIM_CCMR1_OC1M_0)   /* 111 */
#define CCMR1_INACTIVE      (OC1M_FORCE_INACTIVE | TIM_CCMR1_OC1PE)   /* CC1 output, preload, forced off */
#define CR1_STOPPED         (TIM_CR1_ARPE)                            /* CEN = OPM = 0, ARR preload */

/* the HAL state in ONE object, so the handlers reach every field from one base register (NFR-007,
 * FW_design §9.8 v0.8.6: one literal load instead of one per variable); the former names stay as
 * macros (host harnesses of this file use them) */
static struct {
    volatile int32_t  count;
    volatile int32_t  dir;
    volatile uint32_t stop_gen;
    volatile uint32_t cur;         /* running period (shadow), ticks */
    volatile uint32_t pre;         /* preloaded period, ticks */
    volatile uint32_t late_cur;    /* ... the period that a pending update started (FWR-05) */
    volatile uint32_t t_upd;       /* DWT at the previous update */
    uint32_t          pw, dir_setup, guard, f;
    uint32_t          ena_off;     /* BSRR word "driver disabled" (motion.ena_invert) */
    volatile bool     running;
    volatile bool     late;        /* an update is pending and the preload was rewritten */
    bool              ena_inv, init;
} s_t2 = {0, 1, 0u, 0u, 0u, 0u, 0u, 900u, 1800u, 135u, 90000000u, 1u << PIN_ENA_BIT, false, false, false, false};
#define s_count     s_t2.count
#define s_dir       s_t2.dir
#define s_running   s_t2.running
#define s_stop_gen  s_t2.stop_gen
#define s_cur       s_t2.cur
#define s_pre       s_t2.pre
#define s_late      s_t2.late
#define s_late_cur  s_t2.late_cur
#define s_t_upd     s_t2.t_upd
#define s_pw        s_t2.pw
#define s_dir_setup s_t2.dir_setup
#define s_guard     s_t2.guard
#define s_f         s_t2.f
#define s_ena_inv   s_t2.ena_inv
#define s_ena_off   s_t2.ena_off
#define s_init      s_t2.init
/* HW_MEAS STATIC_LEVEL hold (meas_f4.c sets it; always 0 in the release image): released at the next
 * motion start / ENA change (ICD v0.6 Appendix C op 8) */
volatile uint8_t g_meas_static;

/* DEF-M2-01 (SAF-FW-004): an update event (= a completed pulse) that happened before this halt but
 * whose ISR has not run yet (a level 0/1 ISR or a PRIMASK section pre-empted it) is counted here,
 * after the counter is stopped (no further update can follow), so the count is exact at once (the
 * E-stop / limit edge record taken right after the halt sees it); the pending NVIC request then
 * finds UIF clear and returns. Called with PRIMASK set or from the step ISR (UIF already cleared). */
RAMFUNC static void halt_hw(void)
{
    TIM2->CCMR1 = CCMR1_INACTIVE;                    /* PUL inactive at once (no runt) */
    TIM2->CR1 = CR1_STOPPED;                         /* CEN = OPM = 0 */
    if ((TIM2->SR & TIM_SR_UIF) != 0u) {
        TIM2->SR = ~TIM_SR_UIF;
        s_count += s_dir;                            /* completed before the halt: counted once */
    }
    s_running = false;
    s_stop_gen++;
}

/* compare value of the period the counter is running now: after an update whose ISR is still
 * pending the preloaded period is already running (DEF-M2-01: decisions on the right interval) */
static inline __attribute__((always_inline)) uint32_t per_running(void)
{
    if ((TIM2->SR & TIM_SR_UIF) != 0u) {
        return s_late ? s_late_cur : s_pre;
    }
    return s_cur;
}
static inline __attribute__((always_inline)) uint32_t ccr_running(void)
{
    return per_running() - s_pw;
}

/* FWR-05: before the preload is rewritten while an update is pending, remember the period that
 * update started (the pending ISR takes it as s_cur). Called with the step ISR masked. */
static inline __attribute__((always_inline)) void latch_running(void)
{
    if (!s_late && (TIM2->SR & TIM_SR_UIF) != 0u) {
        s_late_cur = s_pre;
        s_late = true;
    }
}

RAMFUNC void hal_ena_set(bool enabled)
{
    if (g_meas_static != 0u) {
        g_meas_static = 0u;
        TIM2->CCMR1 = CCMR1_INACTIVE;
    }
    /* LED current (pin high) = driver disabled; motion.ena_invert swaps (boot value) */
    bool high = (!enabled) != s_ena_inv;
    PIN_ENA_PORT->BSRR = high ? (1u << PIN_ENA_BIT) : (1u << (PIN_ENA_BIT + 16u));
}

/* E-stop fixed reaction (EXTI15_10_IRQHandler, level 0 only; FW_design §5.3, §9.8 v0.8): the same
 * effect as `(void)hal_step_abort(); hal_ena_set(false);` with the fewest bus accesses:
 * - PUL forced inactive first, unconditionally (a no-op while stopped; ends a STATIC_LEVEL hold of
 *   the measurement image like hal_ena_set() does);
 * - no cut decision (every caller of hal_step_abort() discards it; the E-stop clears HOMED anyway);
 * - no PRIMASK: nothing configurable pre-empts level 0, and an NMI / HardFault that does calls
 *   hal_step_abort(), whose halt_hw() finds the work done or does it (count at most once: UIF);
 * - ENA disabled by one precomputed BSRR word. */
RAMFUNC void step_estop_reaction(void)
{
    TIM2->CCMR1 = CCMR1_INACTIVE;                    /* PUL inactive at once (no runt) */
    g_meas_static = 0u;
    if (s_running) {
        TIM2->CR1 = CR1_STOPPED;
        if ((TIM2->SR & TIM_SR_UIF) != 0u) {         /* completed before the halt (DEF-M2-01) */
            TIM2->SR = ~TIM_SR_UIF;
            s_count += s_dir;
        }
        s_running = false;
    }
    s_stop_gen++;                                    /* also while idle (FWR-01) */
    PIN_ENA_PORT->BSRR = s_ena_off;                  /* driver disabled (SAF-FW-005 b) */
}

uint32_t hal_step_init(const hal_step_cfg_t *cfg)
{
    uint32_t f = time_timer_hz();                    /* APB1 timer clock (FW-PLT-002) */
    s_f = f;
    s_pw = cfg->pw_ticks;
    s_dir_setup = cfg->dir_setup_ticks;
    /* 1.5 us (OBS-RC-7): >= 3x the CNT-read -> register-write path of the stop / stretch primitives
     * (~0.5 us conservative), and still < motion.pulse_low_min_ns minimum 2.5 us, so a counter at 0
     * (stopped by OPM) is below every compare even with the guard added */
    s_guard = (f / 2000000u) * 3u;
    if (!s_init) {
        s_init = true;
        s_ena_inv = cfg->ena_invert;
        s_ena_off = (!s_ena_inv) ? (1u << PIN_ENA_BIT) : (1u << (PIN_ENA_BIT + 16u));   /* = hal_ena_set(false) */
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
    uint32_t per, t, pm;
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
    s_late = false;
    TIM2->ARR = per - 1u;
    TIM2->CCR1 = per - s_pw;
    t = dwt_cycles();
    pm = __get_PRIMASK();
    __disable_irq();                                 /* arm atomically vs. a level-0/1 halt (FWR-02) */
    TIM2->CCMR1 = OC1M_PWM2 | TIM_CCMR1_OC1PE;
    s_running = true;
    s_t_upd = t;
    TIM2->CR1 = CR1_STOPPED | TIM_CR1_CEN;
    __set_PRIMASK(pm);
}

void hal_step_set_period(uint32_t ticks)
{
    latch_running();                                 /* FWR-05 (no-op unless an update is pending) */
    s_pre = ticks;
    TIM2->ARR = ticks - 1u;                          /* preload: after the running period */
    TIM2->CCR1 = ticks - s_pw;
}

void hal_step_set_period_now(uint32_t ticks)
{
    uint32_t sr, cr, run;
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    MDWT_T0(t0);
    /* one read each of SR / CR1 / CNT, constant CCMR1 writes (OC1M is PWM2 while running), CR1
     * restored from the value read: no update can occur in this window, because the stretch is done
     * only before the running period's pulse (CNT + guard < CCR1) (v0.8.4, OBS-RC-6 window figures) */
    if (s_running) {
        sr = TIM2->SR;
        cr = TIM2->CR1;
        run = ((sr & TIM_SR_UIF) != 0u) ? (s_late ? s_late_cur : s_pre) : s_cur;   /* FWR-05 */
        if ((cr & TIM_CR1_CEN) != 0u &&
            stepgen_stretch_ok(TIM2->CNT, run - s_pw, run - 1u, s_pw, s_guard, ticks)) {
            TIM2->CR1 = cr & ~TIM_CR1_ARPE;
            TIM2->CCMR1 = OC1M_PWM2;                     /* OC1PE off */
            TIM2->ARR = ticks - 1u;                      /* running interval extended (OI-ICD-07) */
            TIM2->CCR1 = ticks - s_pw;
            TIM2->CCMR1 = OC1M_PWM2 | TIM_CCMR1_OC1PE;
            TIM2->CR1 = cr;
            if ((sr & TIM_SR_UIF) != 0u) {
                s_late_cur = ticks;                      /* the pending ISR takes it as s_cur */
                s_late = true;
            } else {
                s_cur = ticks;
            }
            TIM2->ARR = s_pre - 1u;                      /* restore the preload */
            TIM2->CCR1 = s_pre - s_pw;
        }
    }
    MDWT_END(MDWT_SET_NOW, t0);
    __set_PRIMASK(pm);
}

void hal_step_arm_last(void)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();                                 /* no level-0/1 halt inside the read-modify-write */
    TIM2->CR1 |= TIM_CR1_OPM;                        /* the counter stops at the next update */
    __set_PRIMASK(pm);
}

RAMFUNC bool hal_step_stop_now(void)
{
    uint32_t pm = __get_PRIMASK();
    bool complete = false;
    __disable_irq();
    MDWT_T0(t0);
    if (s_running) {
        /* a counter already stopped by OPM (final update, its ISR pending) reads CNT = 0, which is
         * below every compare (>= pulse_low > guard): the CLEAN decision takes halt_hw() as with the
         * former CEN test, without that APB1 read (v0.8.4, OBS-RC-6) */
        uint32_t cnt = TIM2->CNT;
        if (stepgen_halt_complete(cnt, ccr_running(), s_guard)) {
            uint32_t cr = TIM2->CR1;
            if ((cr & TIM_CR1_OPM) == 0u) {
                /* no OPM yet, so no update can clear CEN before this write; with OPM already armed
                 * nothing is written (a read-modify-write could re-set a CEN cleared by an update) */
                TIM2->CR1 = cr | TIM_CR1_OPM;        /* pulse completes, counted, then stops */
                if (TIM2->CNT < cnt) {
                    /* FWR-21: the pulse ended (update, CNT wrapped) between the CNT read and the OPM
                     * write, so OPM would end the NEXT period one full pulse later: stop the counter
                     * now. CNT is far below the new compare (no pulse started, output inactive); the
                     * pending update ISR counts the completed pulse and finishes the stop. */
                    TIM2->CR1 = CR1_STOPPED;
                }
            }
            complete = true;
        } else {
            halt_hw();
        }
    } else {
        s_stop_gen++;                                /* idle: visible to start-then-recheck (FWR-01) */
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
        cut = stepgen_abort_cuts(TIM2->CNT, ccr_running());   /* CNT = 0 after an OPM stop: no cut */
        halt_hw();
    } else {
        s_stop_gen++;                                /* idle: visible to start-then-recheck (FWR-01) */
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
    uint32_t el, cur, pm, per;
    int32_t d, cnt;
    uint64_t r;
    if ((TIM2->SR & TIM_SR_UIF) == 0u) {
        return;                                      /* already counted by halt_hw() (DEF-M2-01) */
    }
    TIM2->SR = ~TIM_SR_UIF;
    el = now - s_t_upd;
    s_t_upd = now;
    cur = s_cur;
    d = s_dir;
    cnt = s_count + d;                               /* one update = one completed pulse */
    if (cur < 0x40000000u && el > 3u * cur) {        /* > 1.5 periods (2 cycles per tick) */
        cnt += d;                                    /* missed update: the core flags STEP_FAULT */
    }
    s_count = cnt;
    if ((TIM2->CR1 & TIM_CR1_CEN) == 0u) {           /* stopped by OPM (last / CLEAN halt) */
        TIM2->CCMR1 = CCMR1_INACTIVE;
        TIM2->CR1 = CR1_STOPPED;                     /* OPM = 0 (CEN already 0) */
        s_late = false;
        if (s_running) {                             /* halt_hw() already counted and stopped */
            s_running = false;
            s_stop_gen++;
        }
    } else {
        {
            bool late = s_late;                      /* the period this update started (FWR-05), */
            uint32_t lc = s_late_cur, pre = s_pre;   /* selected without a branch */
            s_cur = late ? lc : pre;
        }
        s_late = false;
        r = step_next_of(cnt);                       /* precedence stop > last > period, as v0.8 */
        per = (uint32_t)r;
        if (__builtin_expect((r >> 32) == 0u, 1)) {
            if (per != 0u) {
                s_pre = per;                         /* hal_step_set_period() */
                TIM2->ARR = per - 1u;
                TIM2->CCR1 = per - s_pw;
            }
        } else if ((r & STEP_NEXT_STOP) != 0u) {
            halt_hw();
        } else {                                     /* STEP_NEXT_LAST */
            pm = __get_PRIMASK();
            __disable_irq();                         /* a halt must not be undone by this RMW */
            TIM2->CR1 |= TIM_CR1_OPM;
            __set_PRIMASK(pm);
        }
    }
    MDWT_VAL(MDWT_ISR_STEP, dwt_cycles() - now);      /* HW_MEAS_DWT only (OI-FW-37) */
}
