/* Latch effects of accepted commands (M1 subset). Implements: SAF-FW-001, SAF-FW-006, SAF-FW-022,
 * SAF-FW-023, FW-CMD-003, FW-MOT-007
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

static void clear_valid(valid_t *v, evq_t *q, uint16_t cause, uint32_t t_us)
{
    if (valid_clear(v, t_us)) {
        evq_push(q, t_us, (uint16_t)EV_VALID_CLEARED, cause, 0, 0);
    }
}

void latch_on_stop(latch_t *l, valid_t *v, evq_t *q, uint8_t mode, uint32_t t_us)
{
    (void)l;
    clear_valid(v, q, (mode == (uint8_t)STOPMODE_CONTROLLED) ? (uint16_t)SC_PC_STOP_CONTROLLED
                                                             : (uint16_t)SC_PC_STOP, t_us);
}

void latch_on_halt(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us)
{
    if (!l->halt) {
        l->halt = true;
        l->halt_src = src;
        evq_push(q, t_us, (uint16_t)EV_HALT_SET, src, 0, 0);
    }
    clear_valid(v, q, (src == (uint8_t)SRC_BUTTON) ? (uint16_t)SC_STOP_BUTTON : (uint16_t)SC_PC_HALT,
                t_us);
}

void latch_on_pause(latch_t *l, valid_t *v, evq_t *q, uint8_t src, uint32_t t_us)
{
    if (!l->paused) {
        l->paused = true;
        l->pause_src = src;
        evq_push(q, t_us, (uint16_t)EV_PAUSED, src, 0, 0);
    } else if (src == (uint8_t)SRC_BUTTON) {
        evq_push(q, t_us, (uint16_t)EV_RESUME_REQUEST, 0u, 0, 0);
        return;                               /* a resume request is not a stop */
    }
    clear_valid(v, q, (src == (uint8_t)SRC_BUTTON) ? (uint16_t)SC_PAUSE_BUTTON : (uint16_t)SC_PC_PAUSE,
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
