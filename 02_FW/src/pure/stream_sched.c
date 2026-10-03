/* Origin: Thrust_Stand_HAW/02_FW/src/pure/stream_sched.c @37c8747 (copied, tags adapted). */
/* Fallback-frame schedule. Implements: FW-STR-005 */
#include "stream_sched.h"

#define SS_REBASE_US 1000000u   /* > 1 s late (cannot happen outside a debugger halt): re-base */

static void set_rate(stream_sched_t *s, uint16_t rate_hz)
{
    s->rate_hz = rate_hz;
    s->period_us = 1000000u / rate_hz;
    s->rem = 1000000u % rate_hz;
    s->frac = 0u;
}

static void advance(stream_sched_t *s)
{
    s->due_us += s->period_us;
    s->frac += s->rem;
    if (s->frac >= s->rate_hz) {
        s->frac -= s->rate_hz;
        s->due_us += 1u;
    }
}

void ss_start(stream_sched_t *s, uint16_t rate_hz, uint32_t now_us)
{
    if (rate_hz == 0u) {
        ss_stop(s);
        return;
    }
    set_rate(s, rate_hz);
    s->due_us = now_us;
}

void ss_set_rate(stream_sched_t *s, uint16_t rate_hz, uint32_t now_us)
{
    if (rate_hz == 0u) {
        ss_stop(s);
        return;
    }
    set_rate(s, rate_hz);
    s->due_us = now_us;
    advance(s);
}

void ss_stop(stream_sched_t *s)
{
    s->rate_hz = 0u;
    s->period_us = 0u;
    s->rem = 0u;
    s->frac = 0u;
    s->due_us = 0u;
}

bool ss_poll(stream_sched_t *s, uint32_t now_us, uint32_t *skipped)
{
    uint32_t late;
    *skipped = 0u;
    if (s->rate_hz == 0u || (int32_t)(now_us - s->due_us) < 0) {
        return false;
    }
    late = now_us - s->due_us;
    if (late > SS_REBASE_US) {
        *skipped = late / s->period_us;
        s->due_us = now_us;
        s->frac = 0u;
        advance(s);
        return true;
    }
    advance(s);                                   /* the tick reported now */
    while ((int32_t)(now_us - s->due_us) >= 0) {  /* further ticks already due: missed */
        (*skipped)++;
        advance(s);
    }
    return true;
}
