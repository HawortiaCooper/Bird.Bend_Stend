/* Latch state and the latch/clear effects (ICD §5.5, §6.2; FW_design §5.3).
 * HALT (source always PC since ICD v0.5 / D-36), PAUSED (+source, D-30), ESTOP, LIMIT_START /
 * LIMIT_END, FAULT mask (FAULT_CLEAR all-or-nothing, decided by cmd_check), VALID auto-clear with
 * cause, and the EVENTs. The motion part (STOPPED, MOVE_DONE) is added by the core around these
 * calls, in the order of FW_design §5.3: latch EVENT -> STOPPED -> VALID_CLEARED -> ... -> MOVE_DONE.
 * Every clear function is called only after cmd_check() accepted the command (a NACK has no effect).
 * Pure C11.
 * Origin: pattern of Thrust_Stand_HAW/02_FW/src/pure/safety_sm.* @37c8747 (rewritten).
 * Implements: SAF-FW-001 (VALID clear), SAF-FW-005 (ESTOP latch), SAF-FW-006 (ESTOP_CLEAR),
 *             SAF-FW-013 (LIMIT latch), SAF-FW-023 (PAUSED, RESUME, D-30/D-31), FW-CMD-003,
 *             FW-MOT-007 (HALT / idempotent stops)
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
    uint8_t  halt_src;      /* SRC_* (SRC_PC or SRC_NONE since v0.5) */
    bool     paused;
    uint8_t  pause_src;     /* SRC_* */
    bool     estop;
    bool     limit_start;   /* LIMIT_START latch (input state is separate) */
    bool     limit_end;
    uint16_t faults;        /* FAULT_* mask */
} latch_t;

/* latch_pause() results */
#define LATCH_PAUSE_ALREADY   0u   /* PC PAUSE while PAUSED: nothing */
#define LATCH_PAUSE_NEW       1u   /* PAUSED 0 -> 1, EVENT PAUSED sent */
#define LATCH_PAUSE_RESUME_RQ 2u   /* button press while PAUSED: EVENT RESUME_REQUEST only */

void latch_init(latch_t *l);

/* ---- building blocks (M2) ---- */
/** HALT latch (source PC), EVENT HALT_SET on the first HALT only; true if newly latched. */
bool    latch_halt(latch_t *l, evq_t *q, uint32_t t_us);
/** PAUSED from src (SRC_PC / SRC_BUTTON); returns LATCH_PAUSE_*. */
uint8_t latch_pause(latch_t *l, evq_t *q, uint8_t src, uint32_t t_us);
/** VALID automatic clear at t_us with cause SC_*; EVENT VALID_CLEARED if it was 1. */
void    latch_valid_clear(valid_t *v, evq_t *q, uint16_t cause, uint32_t t_us);
/** FAULT bit (FAULT_*_BIT index) latched with FAULT_SET(bit, value, value2) if not yet latched;
 *  true if newly latched. */
bool    latch_fault(latch_t *l, evq_t *q, uint8_t bit, int32_t value, int32_t value2, uint32_t t_us);
/** ESTOP latch + EVENT ESTOP_SET(pos) if not yet latched; true if newly latched. */
bool    latch_estop(latch_t *l, evq_t *q, int32_t pos_um, int32_t pos_steps, uint32_t t_us);
/** LIMIT_x latch (id LIM_START / LIM_END) + EVENT LIMIT_SET(id, pos) if not yet latched. */
bool    latch_limit(latch_t *l, evq_t *q, uint8_t id, int32_t pos_um, int32_t pos_steps, uint32_t t_us);
/** LIMIT_x auto-clear (input released >= io.release_ms, D-33 h) + EVENT LIMIT_CLEARED(id). */
void    latch_limit_clear(latch_t *l, evq_t *q, uint8_t id, uint32_t t_us);

/* ---- accepted clear commands ---- */
/** Accepted HALT_CLEAR: clears HALT (HALT_CLEARED) and PAUSED (PAUSE_CLEARED arg HALT_CLEAR). */
void latch_on_halt_clear(latch_t *l, evq_t *q, uint32_t t_us);
/** Accepted RESUME (D-31): clears only PAUSED (PAUSE_CLEARED arg RESUME); no-op if not PAUSED. */
void latch_on_resume(latch_t *l, evq_t *q, uint32_t t_us);
/** Accepted FAULT_CLEAR: clears every latched fault, FAULT_CLEARED(mask) if any; returns the mask
 *  (OK body). */
uint16_t latch_on_fault_clear(latch_t *l, evq_t *q, uint32_t t_us);
/** Accepted ESTOP_CLEAR: clears ESTOP (ESTOP_CLEARED) if latched. */
void latch_on_estop_clear(latch_t *l, evq_t *q, uint32_t t_us);

/* ---- M1 compositions without motion (kept for the M1 suites) ---- */
/** STOP(mode): not latched; VALID cleared (also when idle), cause PC_STOP / PC_STOP_CONTROLLED. */
void latch_on_stop(latch_t *l, valid_t *v, evq_t *q, uint8_t mode, uint32_t t_us);
/** HALT: latch_halt + VALID clear (PC_HALT). `src` is ignored (always PC since v0.5, D-36). */
void latch_on_halt(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us);
/** PAUSE from src: latch_pause + VALID clear (PAUSE_BUTTON / PC_PAUSE) unless it was only a
 *  RESUME_REQUEST. */
void latch_on_pause(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us);

/** PAUSED after an accepted (ok = true) or refused command of TYPE type (ICD §5.5, D-30/D-31):
 *  the oracle `paused_after` of check_vectors.json. */
bool latch_paused_after(bool paused_before, uint8_t type, bool ok);

#ifdef __cplusplus
}
#endif

#endif /* PURE_LATCHES_H */
