/* Origin: Thrust_Stand_HAW/02_FW/src/pure/stream_sched.h @37c8747 (copied, tags adapted). */
/* Fallback-frame schedule (FW_design §5.14, FW-STR-005; TS used it for the DATA stream): exact mean rate by a fractional accumulator, phase kept
 * across late polls, no burst after a stall (missed ticks are counted, not sent). Pure C11.
 * Implements: FW-STR-005
 */
#ifndef PURE_STREAM_SCHED_H
#define PURE_STREAM_SCHED_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t rate_hz;       /* 0 = stopped */
    uint32_t period_us;     /* floor(1e6 / rate) */
    uint32_t rem;           /* 1e6 mod rate */
    uint32_t frac;          /* accumulated remainder, 0..rate-1 */
    uint32_t due_us;        /* next scheduled tick (time_us, wraps) */
} stream_sched_t;

/** Start at `rate_hz` (> 0); the first tick is due immediately. */
void ss_start(stream_sched_t *s, uint16_t rate_hz, uint32_t now_us);
/** Change the rate while running: the next tick is one new period after now (no burst). */
void ss_set_rate(stream_sched_t *s, uint16_t rate_hz, uint32_t now_us);
void ss_stop(stream_sched_t *s);
static inline bool ss_running(const stream_sched_t *s) { return s->rate_hz != 0u; }
/** True if a tick is due; *skipped = scheduled ticks missed before it (late poll). At most one
 *  tick is reported per call and the next due time always lies in the future afterwards. */
bool ss_poll(stream_sched_t *s, uint32_t now_us, uint32_t *skipped);

#ifdef __cplusplus
}
#endif

#endif
