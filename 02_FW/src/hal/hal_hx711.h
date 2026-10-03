/* Seam v1 - AFE (tools/README "Seam v1", FW_design §5.8, §8.1).
 * Target: hal/f446/hx711_f4.c (HX711 on PB4/PB10/PB5, D-39 port of Thrust_Stand_HAW @37c8747);
 * bring-up image nucleo_f446re_synth: hal/f446/afe_synth.c (FEAT_AFE_SYNTHETIC). Twin: load model.
 * status bits = AFES_* (proto_gen.h, seam v1.2: SCK_OVERRUN, MISSED_EDGE).
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
