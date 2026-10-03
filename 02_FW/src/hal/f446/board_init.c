/* Board bring-up (FW_design §3.2 boot steps 2/3/5/8/10/11/12) and the hal_outputs seam.
 * The motor-driver pins PA0 (PUL) / PA1 (DIR) / PA4 (ENA) stay in their reset state (Hi-Z: no LED
 * current = driver holding, D-13) until hal_step_init() (boot step 6, from app_init after the
 * parameters are known); the inputs are configured here and their EXTI lines enabled in
 * board_start() after the core latched the boot-time input state. Outputs: LED PA5, RATE PB5 (ODR
 * before MODER, 80 SPS default), TRIP PB9 low. D-36: PC7 is not configured (no STOP input).
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
    /* measurement-header pins: digital input, no pull, in every build (REQ-A-M2-04) */
    gpio_mode(PIN_MH_PUL_A_PORT, PIN_MH_PUL_A_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_MH_PUL_B_PORT, PIN_MH_PUL_B_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_MH_DIR_PORT, PIN_MH_DIR_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_MH_ENA_PORT, PIN_MH_ENA_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_MH_EVT_PORT, PIN_MH_EVT_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_MH_AUX_PORT, PIN_MH_AUX_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    gpio_mode(PIN_MH_STIM_PORT, PIN_MH_STIM_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
    time_init();                                                   /* TIM5 1 MHz, no IRQ yet */
    exti_init();                                                   /* inputs: pull-ups, EXTI routing */
#if !(defined(FW_AFE_SYNTHETIC) && FW_AFE_SYNTHETIC)
    hx711_init();                                                  /* SCK low, DOUT input, EXTI4 */
#endif
    uart_init();                                                   /* RX DMA runs, IRQs later */
    /* PUL / DIR / ENA are configured by hal_step_init() from app_init() (boot step 6) */
}

/* measurement images (HW_MEAS, meas_f4.c) override this hook; the release image keeps it empty */
__attribute__((weak)) void meas_start(void)
{
}

void board_start(void)
{
    exti_start();                                                  /* E-stop 0, limits / PAUSE 1 */
    uart_start_irqs();
#if defined(FW_AFE_SYNTHETIC) && FW_AFE_SYNTHETIC
    afe_synth_start();                                             /* CC2 + EXTI4 vector (level 3) */
#else
    hx711_start();                                                 /* DOUT EXTI4 (level 3) */
#endif
    time_start_tick();                                             /* CC1 1 kHz tick (level 4) */
    iwdg_start();                                                  /* run window 32..90 ms */
    meas_start();                                                  /* HW_MEAS images only (CR-02) */
}
