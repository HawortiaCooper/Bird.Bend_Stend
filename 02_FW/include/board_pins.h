/* NUCLEO-F446RE pin map (= 01_HW/pinout.md §1; register-level names, no Arduino pin numbers).
 * M2: PUL/DIR/ENA configured by hal_step_init() (boot step 6), inputs by exti_init() /
 * hx711_init().
 * D-36 / CR-01 (ICD v0.5): there is no STOP-button input; PC7 is the J-PUL-A loopback input of the
 * measurement header (CR-02).
 * Implements: FW-PLT-001, SYS-007
 */
#ifndef BOARD_PINS_H
#define BOARD_PINS_H

/* motor driver (M2) */
#define PIN_PUL_PORT     GPIOA   /* PA0  TIM2_CH1 AF1 */
#define PIN_PUL_BIT      0u
#define PIN_DIR_PORT     GPIOA   /* PA1 */
#define PIN_DIR_BIT      1u
#define PIN_ENA_PORT     GPIOA   /* PA4 (TC pin, push-pull only) */
#define PIN_ENA_BIT      4u
#define PIN_ALM_PORT     GPIOA   /* PA8  polled */
#define PIN_ALM_BIT      8u
#define PIN_PEND_PORT    GPIOA   /* PA9  polled */
#define PIN_PEND_BIT     9u
#define PIN_DRVPWR_PORT  GPIOA   /* PA7  polled, low = powered */
#define PIN_DRVPWR_BIT   7u

/* safety inputs (M2) */
#define PIN_ESTOP_PORT   GPIOA   /* PA10 EXTI10 -> EXTI15_10, level 0; high = E-stop open */
#define PIN_ESTOP_BIT    10u
#define PIN_START_PORT   GPIOB   /* PB0  EXTI0, level 1 */
#define PIN_START_BIT    0u
#ifdef PIN_END_PB1
#define PIN_END_PORT     GPIOB   /* PB1  fallback, EXTI1 */
#define PIN_END_BIT      1u
#else
#define PIN_END_PORT     GPIOC   /* PC1  (A4) EXTI1, level 1 */
#define PIN_END_BIT      1u
#endif
#define PIN_PAUSE_PORT   GPIOB   /* PB6  EXTI6 -> EXTI9_5, level 1 */
#define PIN_PAUSE_BIT    6u

/* HX711 (M2: real driver; M1: RATE only) */
#define PIN_DOUT_PORT    GPIOB   /* PB4  EXTI4, level 3 */
#define PIN_DOUT_BIT     4u
#define PIN_SCK_PORT     GPIOB   /* PB10 */
#define PIN_SCK_BIT      10u
#define PIN_RATE_PORT    GPIOB   /* PB5  high = 80 SPS */
#define PIN_RATE_BIT     5u

/* outputs */
#define PIN_TRIP_PORT    GPIOB   /* PB9  provision only, never high in release 1 */
#define PIN_TRIP_BIT     9u
#define PIN_LED_PORT     GPIOA   /* PA5  LD2 */
#define PIN_LED_BIT      5u

/* link: USART2 PA2 TX / PA3 RX, AF7 */
#define PIN_TX_BIT       2u
#define PIN_RX_BIT       3u
#define USART2_AF        7u

/* measurement header MH (CR-02, D-40 c; pinout §1.5): loopback / stimulus pins. Digital inputs
 * without pull in every build (never analog: 5 V taps may be fitted, REQ-A-M2-04); timer AF inputs
 * (PB8 = stimulus output) only in the HW_MEAS images. The v0.3 scope markers on PC8/PC9
 * (FW_DEBUG_PINS) are retired (no scope, D-35 G5; the pins are J-ENA / J-DIR now). */
#define PIN_MH_PUL_A_PORT GPIOC  /* PC7  TIM8_CH2 AF3  J-PUL-A */
#define PIN_MH_PUL_A_BIT  7u
#define PIN_MH_PUL_B_PORT GPIOB  /* PB7  TIM4_CH2 AF2  J-PUL-B (independent counter) */
#define PIN_MH_PUL_B_BIT  7u
#define PIN_MH_DIR_PORT   GPIOC  /* PC9  TIM8_CH4 AF3  J-DIR */
#define PIN_MH_DIR_BIT    9u
#define PIN_MH_ENA_PORT   GPIOC  /* PC8  TIM8_CH3 AF3  J-ENA */
#define PIN_MH_ENA_BIT    8u
#define PIN_MH_EVT_PORT   GPIOC  /* PC6  TIM8_CH1 AF3  J-EVT */
#define PIN_MH_EVT_BIT    6u
#define PIN_MH_AUX_PORT   GPIOA  /* PA11 TIM1_CH4 AF1  J-AUX */
#define PIN_MH_AUX_BIT    11u
#define PIN_MH_STIM_PORT  GPIOB  /* PB8  TIM10_CH1 AF3 J-STIM (output only in HW_MEAS) */
#define PIN_MH_STIM_BIT   8u

#endif /* BOARD_PINS_H */
