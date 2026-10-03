/* Seam v1 CONTRACT COPY (00_System/tools/README.md "Seam v1" = FW_design v0.3 §8.1).
 * Owner of the real header: Implementer A (02_FW/src/hal/hal_hx711.h). This copy is used by the
 * twin build ONLY while A's header is absent; build.py prefers 02_FW/src/hal/ and checks that
 * A's prototypes equal this contract (tools/fw_twin/build.py --check-seams). Never edit to
 * "fix" a mismatch: a seam change goes through FW_design §8.1 + tools/README (Integrator).
 * Implements: SYS-008 (twin seam), D-07 */
#ifndef HAL_HX711_H
#define HAL_HX711_H
#include <stdbool.h>
#include <stddef.h>
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
