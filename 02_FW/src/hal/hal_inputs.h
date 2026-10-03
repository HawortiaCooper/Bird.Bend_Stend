/* Seam v1.2 - inputs (tools/README "Seam v1", FW_design §5.2, §8.1). Ids = ICD IO bit indices 0..7
 * (IO_*_BIT in proto_gen.h; id 3 = retired STOP input, never used). Target: hal/f446/exti.c.
 * Core: core/safety.c (on_input_edge records, 1 kHz polling of ALM / PEND / DRV_PWR).
 * D-36 / CR-01 (ICD v0.5): hal_in_cfg_t.stop_active_level removed (no STOP-button input) - seam
 * change SR-M2-02 to the Integrator (README seam block + twin_seams.c mirror it).
 * Implements: FW-SW-001...004, SYS-008
 */
#ifndef HAL_INPUTS_H
#define HAL_INPUTS_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint16_t hal_inputs_raw(void);                                        /* electrical levels (1 = pin high) */
typedef struct { uint8_t pause_active_level, alm_active_level; } hal_in_cfg_t;
void     hal_inputs_config(const hal_in_cfg_t *c);                    /* polarity for the fixed reactions */
void     hal_inputs_rearm(uint8_t id);                                /* re-enable a self-masked line */
void     on_input_edge(uint8_t id, bool level, uint32_t t_us);        /* callback, level 0/1, AFTER the HAL's
                                                                         fixed reaction; deferred during flash ops */

#ifdef __cplusplus
}
#endif

#endif /* HAL_INPUTS_H */
