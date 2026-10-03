/* Seam v1 - AFE (tools/README "Seam v1", FW_design §5.8, §8.1).
 * Target M1: hal/f446/afe_synth.c (synthetic samples at afe.rate_sps from TIM5 CC2 through the
 * EXTI4 vector, FEAT_AFE_SYNTHETIC); M2: hx711_shim.c. Twin: load model.
 * Implements: FW-AFE-001/002/005, FW-STR-002 (M1 synthetic), SYS-008
 */
#ifndef HAL_HX711_H
#define HAL_HX711_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct { uint32_t t_us; int32_t raw; int32_t pos_steps; uint8_t status; } afe_sample_t;
void hal_hx711_config(uint8_t gain_pulses, bool rate80);
void hal_hx711_powerdown(bool on);
void hal_hx711_kick(void);                                            /* missed-edge recovery */
void hal_hx711_hold(bool on);                                         /* NVM hold */
void on_afe_sample(const afe_sample_t *s);                            /* callback, level 3, after the latch */

#ifdef __cplusplus
}
#endif

#endif /* HAL_HX711_H */
