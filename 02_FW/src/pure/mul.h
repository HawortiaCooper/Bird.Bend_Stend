/* MOVE_UNTIL_LOAD decisions (FW_design §5.4.4, §5.8; ICD v0.7.1 §5.4; SRS FW-MOT-006). Pure C11.
 *
 *  - Comparison `cmp` (ICD Appendix B.7): CMP_GE = stop when raw >= raw_stop, CMP_LE = stop when
 *    raw <= raw_stop. An undefined cmp value (refused by cmd_check with E_RANGE 16, never armed)
 *    counts as "beyond" (fail-safe: no motion).
 *  - Pre-check: the last sample before the first pulse is compared; already beyond -> MOVE_DONE
 *    LOAD_THRESHOLD without motion. No sample yet (PROTO_AFE_NO_DATA) -> not beyond (an AFE that
 *    never delivers goes stale and the move ends in AFE_FAULT, SAF-FW-012; a stale AFE refuses the
 *    command anyway, BLOCK AFE_STALE).
 *  - Per-sample decision (sample ISR, level 3, after the FW load limit): the first sample beyond
 *    raw_stop while the compare is armed and the step timer runs -> immediate (CLEAN) stop with the
 *    load-path timing of SAF-FW-002, reason LOAD_THRESHOLD. A sample that trips the FW load limit is
 *    not a threshold hit (the load limit is the stop cause: STOPPED LOAD_LIMIT, MOVE_DONE STOPPED). A
 *    sample after the timer already stopped (bound reached, another immediate stop) decides nothing:
 *    the motion ended for that reason.
 * Implements: FW-MOT-006 (decision part)
 */
#ifndef PURE_MUL_H
#define PURE_MUL_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** raw is beyond the threshold in the sense of cmp (CMP_GE / CMP_LE). */
bool mul_beyond(int32_t raw, int32_t raw_stop, uint8_t cmp);

/** Pre-check before the first pulse: true = already beyond -> MOVE_DONE LOAD_THRESHOLD, no pulse.
 *  `raw_last` = PROTO_AFE_NO_DATA (or have_sample false) -> false. */
bool mul_precheck(bool have_sample, int32_t raw_last, int32_t raw_stop, uint8_t cmp);

/** One HX711 sample during the move: true = stop now (CLEAN) and record LOAD_THRESHOLD.
 *  armed: the compare of the running MOVE_UNTIL_LOAD is armed; hit: already recorded;
 *  running: the step timer runs; load_trip: this sample tripped the FW load limit. */
bool mul_sample_stop(bool armed, bool hit, bool running, bool load_trip, int32_t raw, int32_t raw_stop,
                     uint8_t cmp);

/** Direction of the approach: sign(bound - pos) in steps (+1 / -1; 0 when equal). */
int8_t mul_dir(int32_t bound_steps, int32_t pos_steps);

#ifdef __cplusplus
}
#endif

#endif /* PURE_MUL_H */
