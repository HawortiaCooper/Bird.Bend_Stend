/* Board bring-up (FW_design §3.2 boot steps 2/3/6/8/11/12 for M1) and the hal_outputs seam.
 * M1 leaves the motor-driver pins PA0 (PUL) / PA1 (DIR) / PA4 (ENA) in their reset state (Hi-Z: no
 * LED current = driver holding, D-13; no pulse possible: TIM2 is not configured) and samples no
 * input (M2). Outputs: LED PA5, RATE PB5 (ODR before MODER, 80 SPS default), TRIP PB9 low.
 * D-36: PC7 is not configured as a STOP input.
 * Implements: SAF-FW-018 (no PUL edge, ENA untouched at boot), FW-PLT-001, FW-AFE-002 (RATE pin)
 */
#include "f446.h"
#include "board_pins.h"
#include "hal_outputs.h"
#include "irq_prio.h"

void hal_rate_pin(bool high) { gpio_write(PIN_RATE_PORT, PIN_RATE_BIT, high); }
void hal_trip_relay(bool trip) { gpio_write(PIN_TRIP_PORT, PIN_TRIP_BIT, trip); }
void hal_led(bool on) { gpio_write(PIN_LED_PORT, PIN_LED_BIT, on); }

void board_init(void)
{
    sys_capture_reset_cause();
    sys_stack_paint();
    dwt_enable();
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;
    (void)RCC->AHB1ENR;
    /* outputs: level written before the pin becomes an output */
    gpio_write(PIN_LED_PORT, PIN_LED_BIT, false);
    gpio_mode(PIN_LED_PORT, PIN_LED_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_write(PIN_RATE_PORT, PIN_RATE_BIT, true);                 /* 80 SPS (= external pull-up) */
    gpio_mode(PIN_RATE_PORT, PIN_RATE_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_write(PIN_TRIP_PORT, PIN_TRIP_BIT, false);                /* never high in release 1 */
    gpio_mode(PIN_TRIP_PORT, PIN_TRIP_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
#if defined(FW_DEBUG_PINS) && FW_DEBUG_PINS
    gpio_write(GPIOC, PIN_DBG0_BIT, false);
    gpio_write(GPIOC, PIN_DBG1_BIT, false);
    gpio_mode(GPIOC, PIN_DBG0_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_HIGH_);
    gpio_mode(GPIOC, PIN_DBG1_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_HIGH_);
#endif
    time_init();                                                   /* TIM5 1 MHz, no IRQ yet */
    uart_init();                                                   /* RX DMA runs, IRQs later */
}

void board_start(void)
{
    uart_start_irqs();
    afe_synth_start();                                             /* CC2 + EXTI4 vector (level 3) */
    time_start_tick();                                             /* CC1 1 kHz tick (level 4) */
    iwdg_start();                                                  /* run window 32..90 ms */
}
