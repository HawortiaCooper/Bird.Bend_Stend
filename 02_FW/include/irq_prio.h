/* NVIC priority plan and BASEPRI values (01_HW/pinout.md §4, FW_design §4.1). Grouping 4
 * (16 pre-emption levels, set by the core's premain()); 4 priority bits -> BASEPRI = level << 4.
 * Implements: FW-PLT-001, NFR-007
 */
#ifndef IRQ_PRIO_H
#define IRQ_PRIO_H

#define PRIO_ESTOP      0u   /* EXTI15_10 (E-stop sense PA10) alone - M2 */
#define PRIO_INPUTS     1u   /* EXTI0, EXTI1, EXTI9_5 (limits, PAUSE) - M2 */
#define PRIO_STEP       2u   /* TIM2 update - M2 */
#define PRIO_AFE        3u   /* EXTI4 (HX711 DOUT; M1: software-pended by the synthetic source) */
#define PRIO_TICK       4u   /* TIM5 (CC1 1 kHz control tick, CC2 synthetic AFE schedule) */
#define PRIO_LINK       5u   /* DMA1 Stream5/6, USART2, SysTick (TICK_INT_PRIORITY = 5) */

#define BASEPRI_OF(level) ((uint32_t)(level) << 4)

#define BASEPRI_MOTION  BASEPRI_OF(2u)   /* CRIT_MOTION / CRIT_AFE / CRIT_NVM: masks levels >= 2 */
#define BASEPRI_DATA    BASEPRI_OF(3u)   /* CRIT_DATA: masks levels >= 3 */
#define BASEPRI_TICK    BASEPRI_OF(4u)   /* CRIT_TICK: masks levels >= 4 */

#endif /* IRQ_PRIO_H */
