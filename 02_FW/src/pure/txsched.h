/* TX class queues and wire order (FW_design §5.9.4, ICD §2.4): each class is a byte ring of whole
 * frames ([u8 len][frame]); at every frame boundary the next frame is taken in the order
 * DATA > responses > EVENT. A push is all-or-nothing (drop accounting is the producer's: DATA ->
 * tx_drops + OVERRUN, EVENT -> stays in the EVENT ring; responses are admitted only with room,
 * DEF-P1-07). Used by the target HAL (uart2_dma.c), the host fakes and the twin. Not thread-safe:
 * the HAL serialises pushes and the DMA-complete pick under CRIT_DATA. Pure C11.
 * Implements: FW-STR-002, FW-STR-004 (drop policy), IF-011, FW-CMD-001 (responses never dropped)
 */
#ifndef PURE_TXSCHED_H
#define PURE_TXSCHED_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t *buf;
    uint16_t size;
    uint16_t head;          /* first byte of the oldest frame record */
    uint16_t used;          /* bytes in use */
} txq_t;

void     txq_init(txq_t *q, uint8_t *buf, uint16_t size);
/** Queue a whole frame (1..255 B); false (nothing queued) if it does not fit. */
bool     txq_push(txq_t *q, const uint8_t *frame, uint16_t n);
/** Largest frame that a push accepts now (0 if none). */
uint16_t txq_free(const txq_t *q);
/** Oldest frame into out (>= 255 B); returns its size, 0 if empty. */
uint16_t txq_pop(txq_t *q, uint8_t *out);
static inline bool txq_empty(const txq_t *q) { return q->used == 0u; }

/** Wire order D > R > E: pop the next frame into out; *cls = 0 (D), 1 (R), 2 (E); 0 if all empty. */
uint16_t txsched_next(txq_t *d, txq_t *r, txq_t *e, uint8_t *out, uint8_t *cls);

#ifdef __cplusplus
}
#endif

#endif /* PURE_TXSCHED_H */
