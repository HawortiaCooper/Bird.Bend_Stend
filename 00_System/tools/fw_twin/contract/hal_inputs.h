/* Seam v1 CONTRACT COPY (00_System/tools/README.md "Seam v1" = FW_design v0.3 §8.1).
 * Owner of the real header: Implementer A (02_FW/src/hal/hal_inputs.h). This copy is used by the
 * twin build ONLY while A's header is absent; build.py prefers 02_FW/src/hal/ and checks that
 * A's prototypes equal this contract (tools/fw_twin/build.py --check-seams). Never edit to
 * "fix" a mismatch: a seam change goes through FW_design §8.1 + tools/README (Integrator).
 * Implements: SYS-008 (twin seam), D-07 */
#ifndef HAL_INPUTS_H
#define HAL_INPUTS_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* ids = ICD IO bit indices 0..7 */
uint16_t hal_inputs_raw(void);                                        /* electrical levels (1 = pin high) */
typedef struct { uint8_t stop_active_level, pause_active_level, alm_active_level; } hal_in_cfg_t;
void     hal_inputs_config(const hal_in_cfg_t *c);                    /* polarity for the fixed reactions */
void     hal_inputs_rearm(uint8_t id);                                /* re-enable a self-masked line */
void     on_input_edge(uint8_t id, bool level, uint32_t t_us);        /* callback, level 0/1, AFTER the fixed reaction */
#ifdef __cplusplus
}
#endif
#endif /* HAL_INPUTS_H */
