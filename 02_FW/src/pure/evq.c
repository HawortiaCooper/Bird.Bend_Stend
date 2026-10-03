/* EVENT ring. Implements: FW-STR-006 */
#include "evq.h"

#include <string.h>

void evq_init(evq_t *q)
{
    memset(q, 0, sizeof *q);
}

void evq_push(evq_t *q, uint32_t t_us, uint16_t code, uint16_t arg, int32_t value, int32_t value2)
{
    uint8_t s = q->counter;
    q->counter = (uint8_t)(q->counter + 1u);
    if (q->count >= EVQ_SIZE) {
        if (q->overflows != UINT16_MAX) {
            q->overflows++;
        }
        return;
    }
    {
        uint8_t i = (uint8_t)((q->head + q->count) % EVQ_SIZE);
        q->ev[i].t_us = t_us;
        q->ev[i].code = code;
        q->ev[i].arg = arg;
        q->ev[i].value = value;
        q->ev[i].value2 = value2;
        q->seq[i] = s;
        q->count++;
    }
}

bool evq_pop(evq_t *q, event_t *ev, uint8_t *seq)
{
    if (q->count == 0u) {
        return false;
    }
    *ev = q->ev[q->head];
    *seq = q->seq[q->head];
    q->head = (uint8_t)((q->head + 1u) % EVQ_SIZE);
    q->count--;
    return true;
}
