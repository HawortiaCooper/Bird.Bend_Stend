/* Seam v1 - inputs (tools/README "Seam v1", FW_design §5.2, §8.1). Ids = ICD IO bit indices 0..7
 * (IO_*_BIT in proto_gen.h). Target: hal/f446/exti.c (M2). M1: no input is sampled; the core
 * defines on_input_edge() as an M1 stub (core/m1_stubs.c) and calls no hal_inputs_*.
 * D-36: the STOP button input is retired by CR-01 (ICD v0.5); stop_active_level stays in seam v1
 * until then (generated names unchanged, no STOP input path implemented).
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
typedef struct { uint8_t stop_active_level, pause_active_level, alm_active_level; } hal_in_cfg_t;
void     hal_inputs_config(const hal_in_cfg_t *c);                    /* polarity for the fixed reactions */
void     hal_inputs_rearm(uint8_t id);                                /* re-enable a self-masked line */
void     on_input_edge(uint8_t id, bool level, uint32_t t_us);        /* callback, level 0/1, AFTER the HAL's
                                                                         fixed reaction; deferred during flash ops */

#ifdef __cplusplus
}
#endif

#endif /* HAL_INPUTS_H */
