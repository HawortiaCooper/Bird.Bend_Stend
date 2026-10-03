/* Seam v1 - simple outputs (tools/README "Seam v1", FW_design §8.1).
 * Target: hal/f446/board_init.c (RATE PB5, TRIP PB9, LED PA5).
 * Implements: FW-AFE-002 (RATE pin), SYS-008
 */
#ifndef HAL_OUTPUTS_H
#define HAL_OUTPUTS_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

void hal_rate_pin(bool high);  void hal_trip_relay(bool trip /* never true in release 1 */);  void hal_led(bool on);

#ifdef __cplusplus
}
#endif

#endif /* HAL_OUTPUTS_H */
