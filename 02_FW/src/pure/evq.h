/* EVENT ring (FW_design §5.14, ICD §2.2/§7.4): 16 entries; the u8 event counter is the EVENT header
 * SEQ and advances also for events dropped by overflow (EVENT SEQ gap = lost events,
 * `event_overflows` in STATUS). Not thread-safe: the core serialises producers (CRIT_TICK).
 * Origin: pattern of Thrust_Stand_HAW/02_FW/src/link/events.* @37c8747 (rewritten in C).
 * Implements: FW-STR-006, IF-011 (drop order: EVENT after DATA)
 */
#ifndef PURE_EVQ_H
#define PURE_EVQ_H

#include <stdbool.h>
#include <stdint.h>

#include "payload.h"

#ifdef __cplusplus
extern "C" {
#endif

#define EVQ_SIZE 16u

typedef struct {
    event_t  ev[EVQ_SIZE];
    uint8_t  seq[EVQ_SIZE];
    uint8_t  head;          /* oldest entry */
    uint8_t  count;
    uint8_t  counter;       /* next EVENT SEQ */
    uint16_t overflows;     /* saturating */
} evq_t;

void evq_init(evq_t *q);
/** Queue an event (SEQ = counter++); when full the event is dropped and counted. */
void evq_push(evq_t *q, uint32_t t_us, uint16_t code, uint16_t arg, int32_t value, int32_t value2);
/** Oldest event and its SEQ; false if empty. */
bool evq_pop(evq_t *q, event_t *ev, uint8_t *seq);
static inline uint8_t evq_count(const evq_t *q) { return q->count; }

#ifdef __cplusplus
}
#endif

#endif /* PURE_EVQ_H */
