/* TX class queues + wire order. Implements: FW-STR-002, FW-STR-004, IF-011, FW-CMD-001 */
#include "txsched.h"

void txq_init(txq_t *q, uint8_t *buf, uint16_t size)
{
    q->buf = buf;
    q->size = size;
    q->head = 0u;
    q->used = 0u;
}

uint16_t txq_free(const txq_t *q)
{
    uint16_t room = (uint16_t)(q->size - q->used);
    if (room < 2u) {
        return 0u;
    }
    room = (uint16_t)(room - 1u);
    return (room > 255u) ? 255u : room;
}

bool txq_push(txq_t *q, const uint8_t *frame, uint16_t n)
{
    uint16_t i;
    uint16_t w;
    if (n == 0u || n > 255u || n > txq_free(q)) {
        return false;
    }
    w = (uint16_t)((q->head + q->used) % q->size);
    q->buf[w] = (uint8_t)n;
    for (i = 0u; i < n; i++) {
        w = (uint16_t)((w + 1u) % q->size);
        q->buf[w] = frame[i];
    }
    q->used = (uint16_t)(q->used + n + 1u);
    return true;
}

uint16_t txq_pop(txq_t *q, uint8_t *out)
{
    uint16_t n, i, r;
    if (q->used == 0u) {
        return 0u;
    }
    r = q->head;
    n = q->buf[r];
    for (i = 0u; i < n; i++) {
        r = (uint16_t)((r + 1u) % q->size);
        out[i] = q->buf[r];
    }
    q->head = (uint16_t)((r + 1u) % q->size);
    q->used = (uint16_t)(q->used - n - 1u);
    return n;
}

uint16_t txsched_next(txq_t *d, txq_t *r, txq_t *e, uint8_t *out, uint8_t *cls)
{
    if (!txq_empty(d)) {
        *cls = 0u;
        return txq_pop(d, out);
    }
    if (!txq_empty(r)) {
        *cls = 1u;
        return txq_pop(r, out);
    }
    if (!txq_empty(e)) {
        *cls = 2u;
        return txq_pop(e, out);
    }
    return 0u;
}
