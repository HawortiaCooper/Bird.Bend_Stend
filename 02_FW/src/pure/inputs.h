/* Input filters for the 1 kHz control tick (FW_design §5.2, §5.7): act on the first active edge, debounce
 * only the release (R4 §3.4). Pure C11, header-only.
 *  - relf_t: "released stably" counter: an active sample or an edge seen since the previous sample
 *    restarts it; released once inactive for >= release_ms consecutive samples. Used for the limit
 *    latch auto-clear (D-33 h), the E-stop closed time (io.estop_release_ms) and the EXTI re-arm.
 *  - btn_t:  press/release state of a push button or of the ALM line: pressed at the first active
 *    edge or sample (only when armed, i.e. after a stable release), released after a stable release
 *    of release_ms. A chattering input therefore produces at most one press per release_ms
 *    (OBS-P1-10); the twin, whose lines are never masked, gets the same behaviour.
 * Implements: FW-SW-001 (first edge, release debounce), FW-SW-002 (E-stop closed time),
 *             FW-SW-003 (PAUSE button press/release), FW-SW-004 (ALM filter), SAF-FW-013 (release)
 */
#ifndef PURE_INPUTS_H
#define PURE_INPUTS_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t inactive_ms;        /* consecutive inactive samples without an edge (saturating) */
} relf_t;

static inline void relf_init(relf_t *f, bool active)
{
    f->inactive_ms = active ? 0u : 0xFFFFu;   /* boot: an inactive input counts as released */
}

/** One sample; returns true on the sample at which the release becomes stable. */
static inline bool relf_sample(relf_t *f, bool active, bool edge, uint16_t release_ms)
{
    uint16_t before = f->inactive_ms;
    if (active || edge) {
        f->inactive_ms = 0u;
        return false;
    }
    if (f->inactive_ms != 0xFFFFu) {
        f->inactive_ms++;
    }
    return before < release_ms && f->inactive_ms >= release_ms;
}

static inline bool relf_released(const relf_t *f, uint16_t release_ms)
{
    return f->inactive_ms >= release_ms;
}

typedef struct {
    bool   pressed;              /* debounced state (true from the press until a stable release) */
    relf_t rel;
} btn_t;

#define BTN_NONE     0u
#define BTN_PRESSED  1u
#define BTN_RELEASED 2u

static inline void btn_init(btn_t *b, bool active)
{
    b->pressed = active;          /* active at boot: no press event until released once */
    relf_init(&b->rel, active);
}

/** Active edge (EXTI callback, folded in the tick): BTN_PRESSED if this is a new press. */
static inline uint8_t btn_edge(btn_t *b)
{
    if (b->pressed) {
        return BTN_NONE;          /* bounce / chatter within the same press */
    }
    b->pressed = true;
    b->rel.inactive_ms = 0u;
    return BTN_PRESSED;
}

/** Tick sample of the (polarity-corrected) level; `edge` = any edge since the last sample. */
static inline uint8_t btn_sample(btn_t *b, bool active, bool edge, uint16_t release_ms)
{
    if (active && !b->pressed) {
        b->pressed = true;        /* first active sample (polled input / missed edge) */
        b->rel.inactive_ms = 0u;
        return BTN_PRESSED;
    }
    if (relf_sample(&b->rel, active, edge, release_ms) && b->pressed) {
        b->pressed = false;
        return BTN_RELEASED;
    }
    return BTN_NONE;
}

#ifdef __cplusplus
}
#endif

#endif /* PURE_INPUTS_H */
