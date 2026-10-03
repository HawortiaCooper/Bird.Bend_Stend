/* NUCLEO-F446RE pin map (= 01_HW/pinout.md §1; register-level names, no Arduino pin numbers).
 * M1 drives only LED, RATE and TRIP; the motor outputs (PUL/DIR/ENA) stay in their reset state
 * (Hi-Z = no LED current = driver holding, D-13) and no input is sampled (M2, FW_design §9).
 * D-36 (2026-10-03): there is no separate holding STOP button - PC7 is no longer reserved for a
 * STOP input (the generated names io.stop_active_level / STOP_BTN stay until CR-01 / ICD v0.5).
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

/* debug markers (FW_DEBUG_PINS builds only) */
#define PIN_DBG0_BIT     8u      /* PC8 */
#define PIN_DBG1_BIT     9u      /* PC9 */

#endif /* BOARD_PINS_H */
