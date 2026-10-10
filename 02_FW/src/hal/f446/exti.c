/* Safety and operator inputs (FW_design §5.2; pinout §1.1, §1.3, §4): GPIO inputs with pull-ups, EXTI
 * lines 0 (START, PB0), 1 (END, PC1 / PB1), 6 (PAUSE, PB6) and 10 (E-stop sense, PA10), both edges;
 * ALM (PA8), PEND (PA9), DRV_PWR (PA7) are polled only (no EXTI, OBS-P1-10). There is no STOP-button
 * input (D-36: PC7 unused).
 * The level-0/1 handlers are RAM-resident and do the fixed reaction first, before the core callback:
 *   E-stop open (level 0, alone on EXTI15_10) -> step_estop_reaction() (TRUNCATE + ENA disabled);
 *   START / END active (level 1)              -> hal_step_stop_now() (CLEAN), own line masked;
 *   PAUSE active (level 1, EXTI9_5 line 6)    -> own line masked (the tick acts on the press).
 * E-stop core callback deferred (NFR-007, FW_design §9.8 v0.8): the level-0 handler only does the
 * fixed reaction, notes the edge level(s) and pends the EXTI3 vector (line 3 is not used as an input:
 * IMR bit 3 stays 0, the vector is reached only by this software pend; EXTI2 is the HANG ISR1 vector
 * of the measurement images) at level 1, which tail-chains
 * right after it and calls on_input_edge(0, level, t_us) - before any level >= 2 handler, the tick or
 * the thread can run, so the core record sees the same step count, motion state and latches as when
 * the call was made inside the level-0 handler. Several edges before the delivery (bounce while a
 * level-1 handler runs) are delivered as one open and / or one closed call: the record keeps the
 * first active edge and an "edge seen" flag only, so the result is the same. t_us is read at the
 * delivery (<= 1 us later than before, + a running level-1 handler).
 * A masked line is re-armed by the tick after the stable release (hal_inputs_rearm), so bounce gives
 * one interrupt per press. During a flash operation (idle only, vector table in RAM) the fixed
 * reaction still runs from RAM; the core callback (flash code) is skipped and the tick's level checks
 * pick the state up after the operation (E-stop open and not latched, PAUSE active and not pressed).
 * The shared EXTI9_5 handler clears only its own pending bit (R3 pitfall P10).
 * EXTI->IMR mask / re-arm of single lines through the bit-band alias (exti_imr_set, FWR-08).
 * Implements: FW-SW-001, FW-SW-002, FW-SW-003, SAF-FW-002 (limit path), SAF-FW-005 (a)(b),
 *             SAF-FW-007 (fixed polarity, pull-ups), NFR-007
 */
#include "f446.h"
#include "board_pins.h"
#include "hal_inputs.h"
#include "hal_step.h"
#include "hal_time.h"
#include "irq_prio.h"
#include "meas_dwt.h"
#include "params_gen.h"

#define RAMFUNC __attribute__((section(".RamFunc"), noinline, long_call))

#define L_START (1u << 0)
#define L_END   (1u << 1)
#define L_PAUSE (1u << 6)
#define L_ESTOP (1u << 10)

static volatile uint8_t s_pause_level = (uint8_t)IO_PAUSE_ACTIVE_LEVEL_CLOSED_ACTIVE;

#define ESTOP_EV_OPEN   0x01u                /* an open edge since the last delivery */
#define ESTOP_EV_CLOSED 0x02u                /* a closed edge since the last delivery */
#define ESTOP_CB_IRQn   EXTI3_IRQn           /* software-pended deferred E-stop callback (level 1) */
static volatile uint8_t s_estop_ev;          /* written by EXTI15_10 (level 0), taken by EXTI3 */

static inline bool pin(GPIO_TypeDef *g, uint32_t b) { return (g->IDR & (1u << b)) != 0u; }

uint16_t hal_inputs_raw(void)
{
    uint16_t v = 0u;
    v |= pin(PIN_ESTOP_PORT, PIN_ESTOP_BIT) ? (uint16_t)(1u << 0) : 0u;
    v |= pin(PIN_START_PORT, PIN_START_BIT) ? (uint16_t)(1u << 1) : 0u;
    v |= pin(PIN_END_PORT, PIN_END_BIT) ? (uint16_t)(1u << 2) : 0u;
    v |= pin(PIN_PAUSE_PORT, PIN_PAUSE_BIT) ? (uint16_t)(1u << 4) : 0u;
    v |= pin(PIN_ALM_PORT, PIN_ALM_BIT) ? (uint16_t)(1u << 5) : 0u;
    v |= pin(PIN_PEND_PORT, PIN_PEND_BIT) ? (uint16_t)(1u << 6) : 0u;
    v |= pin(PIN_DRVPWR_PORT, PIN_DRVPWR_BIT) ? (uint16_t)(1u << 7) : 0u;
    return v;
}

void hal_inputs_config(const hal_in_cfg_t *c)
{
    s_pause_level = c->pause_active_level;          /* ALM is polled: its polarity is the core's */
}

void hal_inputs_rearm(uint8_t id)
{
    uint32_t m = (id == 1u) ? L_START : (id == 2u) ? L_END : (id == 4u) ? L_PAUSE : 0u;
    if (m != 0u) {
        EXTI->PR = m;
        exti_imr_set((uint32_t)__builtin_ctz(m), true);   /* bit-band, no RMW (FWR-08) */
    }
}

static void exticr(uint32_t line, uint32_t port)   /* port: 0 = A, 1 = B, 2 = C */
{
    volatile uint32_t *r = &SYSCFG->EXTICR[line >> 2];
    uint32_t sh = 4u * (line & 3u);
    *r = (*r & ~(0xFu << sh)) | (port << sh);
}

/* board_init(): pins as inputs with pull-ups (external pull-ups / RC per wiring.md), EXTI routing */
void exti_init(void)
{
    gpio_mode(PIN_ESTOP_PORT, PIN_ESTOP_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_START_PORT, PIN_START_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_END_PORT, PIN_END_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_PAUSE_PORT, PIN_PAUSE_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_ALM_PORT, PIN_ALM_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_PEND_PORT, PIN_PEND_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_DRVPWR_PORT, PIN_DRVPWR_BIT, GPIO_MODE_IN_, GPIO_PUPD_UP_, GPIO_SPEED_LOW_);
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;
    (void)RCC->APB2ENR;
    exticr(0u, 1u);                                  /* PB0 */
#ifdef PIN_END_PB1
    exticr(1u, 1u);                                  /* PB1 fallback */
#else
    exticr(1u, 2u);                                  /* PC1 */
#endif
    exticr(6u, 1u);                                  /* PB6 */
    exticr(10u, 0u);                                 /* PA10 */
    EXTI->RTSR |= L_START | L_END | L_PAUSE | L_ESTOP;
    EXTI->FTSR |= L_START | L_END | L_PAUSE | L_ESTOP;
    EXTI->IMR &= ~(L_START | L_END | L_PAUSE | L_ESTOP);
    NVIC_SetPriority(EXTI15_10_IRQn, PRIO_ESTOP);
    NVIC_SetPriority(ESTOP_CB_IRQn, PRIO_INPUTS);
    NVIC_SetPriority(EXTI0_IRQn, PRIO_INPUTS);
    NVIC_SetPriority(EXTI1_IRQn, PRIO_INPUTS);
    NVIC_SetPriority(EXTI9_5_IRQn, PRIO_INPUTS);
}

/* board_start(): pending bits cleared, lines enabled (the boot latches already cover active inputs) */
void exti_start(void)
{
    EXTI->PR = L_START | L_END | L_PAUSE | L_ESTOP;
    EXTI->IMR |= L_START | L_END | L_PAUSE | L_ESTOP;
    NVIC_ClearPendingIRQ(EXTI15_10_IRQn);
    NVIC_ClearPendingIRQ(ESTOP_CB_IRQn);
    NVIC_ClearPendingIRQ(EXTI0_IRQn);
    NVIC_ClearPendingIRQ(EXTI1_IRQn);
    NVIC_ClearPendingIRQ(EXTI9_5_IRQn);
    NVIC_EnableIRQ(ESTOP_CB_IRQn);
    NVIC_EnableIRQ(EXTI15_10_IRQn);
    NVIC_EnableIRQ(EXTI0_IRQn);
    NVIC_EnableIRQ(EXTI1_IRQn);
    NVIC_EnableIRQ(EXTI9_5_IRQn);
}

RAMFUNC static void callback(uint8_t id, bool level)
{
    if (!flash_op_active()) {
        on_input_edge(id, level, TIM5->CNT);         /* core (flash): skipped during flash ops */
    }
}

RAMFUNC void EXTI15_10_IRQHandler(void)
{
    MDWT_T0(t0);
    if ((EXTI->PR & L_ESTOP) == 0u) {
        return;
    }
    EXTI->PR = L_ESTOP;
    if (pin(PIN_ESTOP_PORT, PIN_ESTOP_BIT)) {
        step_estop_reaction();                       /* TRUNCATE (SAF-FW-005 a) + ENA disabled (b) */
        s_estop_ev |= ESTOP_EV_OPEN;
    } else {
        s_estop_ev |= ESTOP_EV_CLOSED;
    }
    NVIC->ISPR[(uint32_t)ESTOP_CB_IRQn >> 5] = 1u << ((uint32_t)ESTOP_CB_IRQn & 31u);   /* core: EXTI3 */
    MDWT_END(MDWT_ISR_ESTOP, t0);
}

/* deferred E-stop core callback (level 1, software-pended by the handler above; RAM like every
 * level-0/1 handler, the vector table is in RAM during a flash operation) */
RAMFUNC void EXTI3_IRQHandler(void)
{
    uint8_t ev;
    uint32_t pm;
    MDWT_T0(t0);
    pm = __get_PRIMASK();
    __disable_irq();                                 /* take vs. the level-0 writer (<= 10 cycles) */
    ev = s_estop_ev;
    s_estop_ev = 0u;
    __set_PRIMASK(pm);
    if ((ev & ESTOP_EV_OPEN) != 0u) {
        callback(0u, true);
    }
    if ((ev & ESTOP_EV_CLOSED) != 0u) {
        callback(0u, false);
    }
    MDWT_END(MDWT_ISR_ESTOP_CB, t0);
}

RAMFUNC static void limit_line(uint32_t m, uint8_t id, GPIO_TypeDef *g, uint32_t b)
{
    bool active;
    EXTI->PR = m;
    active = pin(g, b);
    if (active) {
        (void)hal_step_stop_now();                   /* CLEAN (SAF-FW-002) */
        exti_imr_set((uint32_t)__builtin_ctz(m), false);   /* first edge only; re-armed after release */
    }
    callback(id, active);
}

RAMFUNC void EXTI0_IRQHandler(void)
{
    MDWT_T0(t0);
    if ((EXTI->PR & L_START) != 0u) {
        limit_line(L_START, 1u, PIN_START_PORT, PIN_START_BIT);
    }
    MDWT_END(MDWT_ISR_LIM_START, t0);
}

RAMFUNC void EXTI1_IRQHandler(void)
{
    MDWT_T0(t0);
    if ((EXTI->PR & L_END) != 0u) {
        limit_line(L_END, 2u, PIN_END_PORT, PIN_END_BIT);
    }
    MDWT_END(MDWT_ISR_LIM_END, t0);
}

RAMFUNC void EXTI9_5_IRQHandler(void)
{
    bool lvl, active;
    MDWT_T0(t0);
    if ((EXTI->PR & L_PAUSE) == 0u || (EXTI->IMR & L_PAUSE) == 0u) {
        return;                                      /* only our own, enabled line */
    }
    EXTI->PR = L_PAUSE;
    lvl = pin(PIN_PAUSE_PORT, PIN_PAUSE_BIT);
    active = (s_pause_level == (uint8_t)IO_PAUSE_ACTIVE_LEVEL_CLOSED_ACTIVE) ? !lvl : lvl;
    if (active) {
        exti_imr_set(6u, false);                     /* L_PAUSE, bit-band (FWR-08) */
    }
    callback(4u, lvl);
    MDWT_END(MDWT_ISR_PAUSE, t0);
}
