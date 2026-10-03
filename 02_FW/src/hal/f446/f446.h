/* NUCLEO-F446RE target HAL internals (not a seam): board bring-up order and helpers shared by the
 * hal/f446 files. Only hal/f446 touches registers (FW_design §1 principle 7).
 * Implements: FW-PLT-001, FW-PLT-002
 */
#ifndef HAL_F446_H
#define HAL_F446_H

#include <stdbool.h>
#include <stdint.h>

#include "stm32f4xx.h"

#ifdef __cplusplus
extern "C" {
#endif

/* main.cpp: board_init() -> app_init() -> board_start() */
void board_init(void);           /* reset cause, stack paint, DWT, NVIC levels, GPIO, TIM5, USART2/DMA */
void board_start(void);          /* enable the IRQs (inputs, tick, AFE, link), start the AFE + IWDG */

/* clock.c */
bool clock_hsi_fallback(void);
/* time_tim5.c */
void time_init(void);
void time_start_tick(void);
uint32_t time_timer_hz(void);
/* afe_synth.c (bring-up image FW_AFE_SYNTHETIC only; called from the TIM5 ISR on CC2) */
void afe_synth_start(void);
void afe_synth_on_cc2(uint32_t t_us);
/* hx711_f4.c (HX711 on PB4 / PB10 / PB5) */
void hx711_init(void);
void hx711_start(void);
/* exti.c (E-stop, limits, PAUSE) */
void exti_init(void);
void exti_start(void);
/* flash_f4.c: an erase / program is running (input callbacks skipped, FW_design §5.11) */
extern volatile bool g_flash_op;
static inline bool flash_op_active(void) { return g_flash_op; }
/* uart2_dma.c */
void uart_init(void);
void uart_start_irqs(void);
/* iwdg.c */
void iwdg_start(void);
/* sys_f4.c */
void sys_capture_reset_cause(void);
void sys_stack_paint(void);

static inline void dwt_enable(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0u;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static inline uint32_t dwt_cycles(void) { return DWT->CYCCNT; }

/* GPIO pin configuration by read-modify-write of the pin's own bits only (PA13/PA14 untouched) */
static inline void gpio_mode(GPIO_TypeDef *g, uint32_t pin, uint32_t mode, uint32_t pupd, uint32_t speed)
{
    g->OSPEEDR = (g->OSPEEDR & ~(3u << (2u * pin))) | (speed << (2u * pin));
    g->PUPDR = (g->PUPDR & ~(3u << (2u * pin))) | (pupd << (2u * pin));
    g->OTYPER &= ~(1u << pin);
    g->MODER = (g->MODER & ~(3u << (2u * pin))) | (mode << (2u * pin));
}

static inline void gpio_af(GPIO_TypeDef *g, uint32_t pin, uint32_t af)
{
    volatile uint32_t *afr = &g->AFR[pin >> 3];
    uint32_t sh = 4u * (pin & 7u);
    *afr = (*afr & ~(0xFu << sh)) | (af << sh);
}

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

#ifdef __cplusplus
}
#endif

#endif /* HAL_F446_H */
