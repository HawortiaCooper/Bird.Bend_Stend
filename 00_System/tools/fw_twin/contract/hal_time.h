/* Seam v1 CONTRACT COPY (00_System/tools/README.md "Seam v1" = FW_design v0.3 §8.1).
 * Owner of the real header: Implementer A (02_FW/src/hal/hal_time.h). This copy is used by the
 * twin build ONLY while A's header is absent; build.py prefers 02_FW/src/hal/ and checks that
 * A's prototypes equal this contract (tools/fw_twin/build.py --check-seams). Never edit to
 * "fix" a mismatch: a seam change goes through FW_design §8.1 + tools/README (Integrator).
 * Implements: SYS-008 (twin seam), D-07 */
#ifndef HAL_TIME_H
#define HAL_TIME_H
#include <stdbool.h>
#include <stddef.h>
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
