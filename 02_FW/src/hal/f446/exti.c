/* Safety and operator inputs (FW_design §5.2; pinout §1.1, §1.3, §4): GPIO inputs with pull-ups, EXTI
 * lines 0 (START, PB0), 1 (END, PC1 / PB1), 6 (PAUSE, PB6) and 10 (E-stop sense, PA10), both edges;
 * ALM (PA8), PEND (PA9), DRV_PWR (PA7) are polled only (no EXTI, OBS-P1-10). There is no STOP-button
 * input (D-36: PC7 unused).
 * The level-0/1 handlers are RAM-resident and do the fixed reaction first, before the core callback:
 *   E-stop open (level 0, alone on EXTI15_10) -> hal_step_abort() (TRUNCATE) + ENA disabled;
 *   START / END active (level 1)              -> hal_step_stop_now() (CLEAN), own line masked;
 *   PAUSE active (level 1, EXTI9_5 line 6)    -> own line masked (the tick acts on the press).
 * A masked line is re-armed by the tick after the stable release (hal_inputs_rearm), so bounce gives
 * one interrupt per press. During a flash operation (idle only, vector table in RAM) the fixed
 * reaction still runs from RAM; the core callback (flash code) is skipped and the tick's level checks
 * pick the state up after the operation (E-stop open and not latched, PAUSE active and not pressed).
 * The shared EXTI9_5 handler clears only its own pending bit (R3 pitfall P10).
 * Implements: FW-SW-001, FW-SW-002, FW-SW-003, SAF-FW-002 (limit path), SAF-FW-005 (a)(b),
 *             SAF-FW-007 (fixed polarity, pull-ups), NFR-007
 */
#include "f446.h"
#include "board_pins.h"
#include "hal_inputs.h"
#include "hal_step.h"
#include "hal_time.h"
#include "irq_prio.h"
#include "params_gen.h"

#define RAMFUNC __attribute__((section(".RamFunc"), noinline, long_call))

#define L_START (1u << 0)
#define L_END   (1u << 1)
#define L_PAUSE (1u << 6)
#define L_ESTOP (1u << 10)

static volatile uint8_t s_pause_level = (uint8_t)IO_PAUSE_ACTIVE_LEVEL_CLOSED_ACTIVE;

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
        EXTI->IMR |= m;
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
    NVIC_ClearPendingIRQ(EXTI0_IRQn);
    NVIC_ClearPendingIRQ(EXTI1_IRQn);
    NVIC_ClearPendingIRQ(EXTI9_5_IRQn);
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
    bool open;
    if ((EXTI->PR & L_ESTOP) == 0u) {
        return;
    }
    EXTI->PR = L_ESTOP;
    open = pin(PIN_ESTOP_PORT, PIN_ESTOP_BIT);
    if (open) {
        (void)hal_step_abort();                      /* <= 0.5 us after entry (SAF-FW-005 a) */
        hal_ena_set(false);                          /* (SAF-FW-005 b) */
    }
    callback(0u, open);
}

RAMFUNC static void limit_line(uint32_t m, uint8_t id, GPIO_TypeDef *g, uint32_t b)
{
    bool active;
    EXTI->PR = m;
    active = pin(g, b);
    if (active) {
        (void)hal_step_stop_now();                   /* CLEAN (SAF-FW-002) */
        EXTI->IMR &= ~m;                             /* first edge only; re-armed after release */
    }
    callback(id, active);
}

RAMFUNC void EXTI0_IRQHandler(void)
{
    if ((EXTI->PR & L_START) != 0u) {
        limit_line(L_START, 1u, PIN_START_PORT, PIN_START_BIT);
    }
}

RAMFUNC void EXTI1_IRQHandler(void)
{
    if ((EXTI->PR & L_END) != 0u) {
        limit_line(L_END, 2u, PIN_END_PORT, PIN_END_BIT);
    }
}

RAMFUNC void EXTI9_5_IRQHandler(void)
{
    bool lvl, active;
    if ((EXTI->PR & L_PAUSE) == 0u || (EXTI->IMR & L_PAUSE) == 0u) {
        return;                                      /* only our own, enabled line */
    }
    EXTI->PR = L_PAUSE;
    lvl = pin(PIN_PAUSE_PORT, PIN_PAUSE_BIT);
    active = (s_pause_level == (uint8_t)IO_PAUSE_ACTIVE_LEVEL_CLOSED_ACTIVE) ? !lvl : lvl;
    if (active) {
        EXTI->IMR &= ~L_PAUSE;
    }
    callback(4u, lvl);
}
