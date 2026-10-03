/* µm <-> steps conversion and the step-rate speed cap (ICD §0.1 normative, OI-FW-20): with spm =
 * the binary32 value of motion.steps_per_mm, evaluated in IEEE-754 binary64 left to right,
 *   steps = round(um * spm / 1000),  um = round(steps * 1000 / spm),  cap = floor(rate * 1000 / spm)
 * (round = half away from zero, C99 round()). Oracle: vectors/units_vectors.json (exact).
 * Pure C11.
 * Implements: SYS-003, FW-MOT-002, FW-MOT-009
 */
#ifndef PURE_UNITS_H
#define PURE_UNITS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int32_t  units_um_to_steps(int32_t um, float spm);
int32_t  units_steps_to_um(int32_t steps, float spm);
/** floor(max_step_rate_hz * 1000 / spm) in µm/s (ICD §5.4 speed cap). */
uint32_t units_rate_cap_um_s(uint32_t max_step_rate_hz, float spm);

#ifdef __cplusplus
}
#endif

#endif /* PURE_UNITS_H */
