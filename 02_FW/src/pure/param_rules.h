/* Parameter hard rules H1..H5 (ICD §11.4, FW_design §5.15). Pure C11; oracle =
 * check_vectors.json (rule_h* and the per-parameter vectors).
 *   H1 limits.soft_min_um < limits.soft_max_um
 *   H2 safety.load_raw_min < safety.load_raw_max
 *   H3 (u64) motion.max_step_rate_hz * (pulse_high_ns + pulse_low_min_ns) <= 1e9
 *   H4 motion.v_max_load_um_s <= motion.v_max_travel_um_s
 *   H5 afe.timeout_ms * sps(afe.rate_sps) >= 2000   (D-33a: timeout >= 2 conversion periods)
 * Implements: FW-CFG-003, SAF-FW-010, SAF-FW-012 (H5)
 */
#ifndef PURE_PARAM_RULES_H
#define PURE_PARAM_RULES_H

#include <stdbool.h>
#include <stdint.h>

#include "params_gen.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 0 = the rules hold after setting `id` := raw on top of *cur; else the id of the OTHER parameter
 *  of the violated rule (E_CONFIG detail). Unknown ids / ids without a rule -> 0. */
uint16_t param_rules_check_set(const params_t *cur, uint16_t id, uint32_t raw);

/** True if H1..H5 hold for a whole image (NVM boot / LOAD rule 5). */
bool param_rules_check_all(const params_t *p);

/** Conversions per second of an afe.rate_sps enum code (SPS10 -> 10, SPS80 -> 80). */
uint32_t param_rules_sps(uint8_t rate_sps);

#ifdef __cplusplus
}
#endif

#endif /* PURE_PARAM_RULES_H */
