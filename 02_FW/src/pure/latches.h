/* Latch state and the latch/clear effects of accepted commands (ICD §5.5, §6.2; FW_design §5.3).
 * M1 subset (no motion): HALT (+source), PAUSED (+source, D-30), ESTOP (cleared by ESTOP_CLEAR;
 * set by the E-stop input from M2), FAULT mask (FAULT_CLEAR all-or-nothing; faults are set from
 * M2), VALID auto-clear with cause, and the EVENTs in the order of FW_design §5.3. M2 adds the
 * stop/motion part (STOPPED, MOVE_DONE) around these calls.
 * Every function is called only after cmd_check() accepted the command (a NACK has no effect).
 * Pure C11.
 * Origin: pattern of Thrust_Stand_HAW/02_FW/src/pure/safety_sm.* @37c8747 (rewritten).
 * Implements: SAF-FW-001 (VALID clear), SAF-FW-006 (ESTOP_CLEAR), SAF-FW-022 (HALT/HALT_CLEAR),
 *             SAF-FW-023 (PAUSED, RESUME, D-30/D-31), FW-CMD-003, FW-MOT-007 (idempotent stops)
 */
#ifndef PURE_LATCHES_H
#define PURE_LATCHES_H

#include <stdbool.h>
#include <stdint.h>

#include "evq.h"
#include "valid.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool     halt;
    uint8_t  halt_src;      /* SRC_* */
    bool     paused;
    uint8_t  pause_src;     /* SRC_* */
    bool     estop;
    uint16_t faults;        /* FAULT_* mask */
} latch_t;

void latch_init(latch_t *l);

/** STOP(mode): not latched; VALID cleared (also when idle), cause PC_STOP / PC_STOP_CONTROLLED. */
void latch_on_stop(latch_t *l, valid_t *v, evq_t *q, uint8_t mode, uint32_t t_us);
/** HALT from src (SRC_PC / SRC_BUTTON): HALT latch, HALT_SET on the first HALT only (source kept
 *  if already latched), VALID cleared. */
void latch_on_halt(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us);
/** PAUSE from src: PAUSED + EVENT PAUSED on 0 -> 1 only, VALID cleared. A button press while
 *  PAUSED -> RESUME_REQUEST only (the FW never resumes). PC PAUSE while PAUSED -> no event. */
void latch_on_pause(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us);
/** Accepted HALT_CLEAR: clears HALT (HALT_CLEARED) and PAUSED (PAUSE_CLEARED arg HALT_CLEAR). */
void latch_on_halt_clear(latch_t *l, evq_t *q, uint32_t t_us);
/** Accepted RESUME (D-31): clears only PAUSED (PAUSE_CLEARED arg RESUME); no-op if not PAUSED. */
void latch_on_resume(latch_t *l, evq_t *q, uint32_t t_us);
/** Accepted FAULT_CLEAR: clears every latched fault, FAULT_CLEARED(mask) if any; returns the mask
 *  (OK body). */
uint16_t latch_on_fault_clear(latch_t *l, evq_t *q, uint32_t t_us);
/** Accepted ESTOP_CLEAR: clears ESTOP (ESTOP_CLEARED) if latched. */
void latch_on_estop_clear(latch_t *l, evq_t *q, uint32_t t_us);

/** PAUSED after an accepted (ok = true) or refused command of TYPE type (ICD §5.5, D-30/D-31):
 *  the oracle `paused_after` of check_vectors.json. */
bool latch_paused_after(bool paused_before, uint8_t type, bool ok);

#ifdef __cplusplus
}
#endif

#endif /* PURE_LATCHES_H */
