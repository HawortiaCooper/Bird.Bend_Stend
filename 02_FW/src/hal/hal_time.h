/* Seam v1 - time base (tools/README "Seam v1", FW_design §5.1, §8.1).
 * Target: hal/f446/time_tim5.c (TIM5 1 MHz, CC1 = 1 kHz tick). Twin: virtual clock.
 * Implements: FW-TIM-001, SYS-008
 */
#ifndef HAL_TIME_H
#define HAL_TIME_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint32_t hal_time_us(void);   uint32_t hal_time_ms(void);
void     core_tick_1ms(void);                                         /* callback, level 4, every 1000 us */

#ifdef __cplusplus
}
#endif

#endif /* HAL_TIME_H */
