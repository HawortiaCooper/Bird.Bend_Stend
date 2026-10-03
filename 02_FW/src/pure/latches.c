/* Latch effects. Implements: SAF-FW-001, SAF-FW-005, SAF-FW-006, SAF-FW-013, SAF-FW-023,
 * FW-CMD-003, FW-MOT-007
 */
#include "latches.h"

#include <string.h>

#include "proto_gen.h"

void latch_init(latch_t *l)
{
    memset(l, 0, sizeof *l);
    l->halt_src = (uint8_t)SRC_NONE;
    l->pause_src = (uint8_t)SRC_NONE;
}

void latch_valid_clear(valid_t *v, evq_t *q, uint16_t cause, uint32_t t_us)
{
    if (valid_clear(v, t_us)) {
        evq_push(q, t_us, (uint16_t)EV_VALID_CLEARED, cause, 0, 0);
    }
}

bool latch_halt(latch_t *l, evq_t *q, uint32_t t_us)
{
    if (l->halt) {
        return false;                         /* repeats: no further event (CONFIRM class) */
    }
    l->halt = true;
    l->halt_src = (uint8_t)SRC_PC;
    evq_push(q, t_us, (uint16_t)EV_HALT_SET, (uint16_t)SRC_PC, 0, 0);
    return true;
}

uint8_t latch_pause(latch_t *l, evq_t *q, uint8_t src, uint32_t t_us)
{
    if (!l->paused) {
        l->paused = true;
        l->pause_src = src;
        evq_push(q, t_us, (uint16_t)EV_PAUSED, src, 0, 0);
        return LATCH_PAUSE_NEW;
    }
    if (src == (uint8_t)SRC_BUTTON) {
        evq_push(q, t_us, (uint16_t)EV_RESUME_REQUEST, 0u, 0, 0);
        return LATCH_PAUSE_RESUME_RQ;         /* the FW never resumes by itself */
    }
    return LATCH_PAUSE_ALREADY;
}

bool latch_fault(latch_t *l, evq_t *q, uint8_t bit, int32_t value, int32_t value2, uint32_t t_us)
{
    uint16_t m = (uint16_t)(1u << bit);
    if ((l->faults & m) != 0u) {
        return false;
    }
    l->faults = (uint16_t)(l->faults | m);
    evq_push(q, t_us, (uint16_t)EV_FAULT_SET, bit, value, value2);
    return true;
}

bool latch_estop(latch_t *l, evq_t *q, int32_t pos_um, int32_t pos_steps, uint32_t t_us)
{
    if (l->estop) {
        return false;
    }
    l->estop = true;
    evq_push(q, t_us, (uint16_t)EV_ESTOP_SET, 0u, pos_um, pos_steps);
    return true;
}

bool latch_limit(latch_t *l, evq_t *q, uint8_t id, int32_t pos_um, int32_t pos_steps, uint32_t t_us)
{
    bool *f = (id == (uint8_t)LIM_START) ? &l->limit_start : &l->limit_end;
    if (*f) {
        return false;
    }
    *f = true;
    evq_push(q, t_us, (uint16_t)EV_LIMIT_SET, id, pos_um, pos_steps);
    return true;
}

void latch_limit_clear(latch_t *l, evq_t *q, uint8_t id, uint32_t t_us)
{
    bool *f = (id == (uint8_t)LIM_START) ? &l->limit_start : &l->limit_end;
    if (*f) {
        *f = false;
        evq_push(q, t_us, (uint16_t)EV_LIMIT_CLEARED, id, 0, 0);
    }
}

void latch_on_stop(latch_t *l, valid_t *v, evq_t *q, uint8_t mode, uint32_t t_us)
{
    (void)l;
    latch_valid_clear(v, q, (mode == (uint8_t)STOPMODE_CONTROLLED) ? (uint16_t)SC_PC_STOP_CONTROLLED
                                                                   : (uint16_t)SC_PC_STOP, t_us);
}

void latch_on_halt(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us)
{
    (void)src;                                /* D-36: HALT source is always PC */
    (void)latch_halt(l, q, t_us);
    latch_valid_clear(v, q, (uint16_t)SC_PC_HALT, t_us);
}

void latch_on_pause(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us)
{
    if (latch_pause(l, q, src, t_us) == LATCH_PAUSE_RESUME_RQ) {
        return;                               /* a resume request is not a stop */
    }
    latch_valid_clear(v, q, (src == (uint8_t)SRC_BUTTON) ? (uint16_t)SC_PAUSE_BUTTON : (uint16_t)SC_PC_PAUSE,
                      t_us);
}

void latch_on_halt_clear(latch_t *l, evq_t *q, uint32_t t_us)
{
    if (l->halt) {
        l->halt = false;
        l->halt_src = (uint8_t)SRC_NONE;
        evq_push(q, t_us, (uint16_t)EV_HALT_CLEARED, 0u, 0, 0);
    }
    if (l->paused) {
        l->paused = false;
        l->pause_src = (uint8_t)SRC_NONE;
        evq_push(q, t_us, (uint16_t)EV_PAUSE_CLEARED, (uint16_t)PCLR_HALT_CLEAR, 0, 0);
    }
}

void latch_on_resume(latch_t *l, evq_t *q, uint32_t t_us)
{
    if (l->paused) {
        l->paused = false;
        l->pause_src = (uint8_t)SRC_NONE;
        evq_push(q, t_us, (uint16_t)EV_PAUSE_CLEARED, (uint16_t)PCLR_RESUME, 0, 0);
    }
}

uint16_t latch_on_fault_clear(latch_t *l, evq_t *q, uint32_t t_us)
{
    uint16_t m = l->faults;
    l->faults = 0u;
    if (m != 0u) {
        evq_push(q, t_us, (uint16_t)EV_FAULT_CLEARED, m, 0, 0);
    }
    return m;
}

void latch_on_estop_clear(latch_t *l, evq_t *q, uint32_t t_us)
{
    if (l->estop) {
        l->estop = false;
        evq_push(q, t_us, (uint16_t)EV_ESTOP_CLEARED, 0u, 0, 0);
    }
}

bool latch_paused_after(bool paused_before, uint8_t type, bool ok)
{
    if (!ok) {
        return paused_before;                 /* a NACK has no effect (ICD §4.1) */
    }
    if (type == (uint8_t)CMD_PAUSE) {
        return true;
    }
    if (type == (uint8_t)CMD_RESUME || type == (uint8_t)CMD_HALT_CLEAR) {
        return false;
    }
    return paused_before;                     /* nothing else clears PAUSED (D-30) */
}
