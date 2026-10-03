/* Homing phase logic, START switch only (FW_design §5.5; ICD §5.4; D-29 b). The core executes each
 * phase as one motion segment (an absolute end point in steps) and calls home_segment_end() when the
 * timer stopped; this module decides the next segment, the zero and the failure. Pure C11.
 *
 *  RELEASE      (START active at HOME): +x at v_slow toward origin + RELEASE_MAX; when START has been
 *               released stably the core moves the end to pos + backoff (home_release_end());
 *               ended released -> FAST_SEEK; still active at the end -> HOME_WIRING; a stop by a START
 *               bounce edge -> the same segment again (end unchanged).
 *  FAST_SEEK    -x at v_fast to pos - max_travel: START edge -> BACKOFF; END edge -> HOME_WIRING;
 *               planned end -> HOME_NOT_FOUND.
 *  BACKOFF      as RELEASE, then -> SLOW_APPROACH.
 *  SLOW_APPROACH -x at v_slow to pos - (backoff + SLOW_EXTRA): first START edge (position captured in
 *               the EXTI callback) -> zero set so that the edge lies at -offset, drift check ->
 *               MOVE_TO_ZERO; END edge -> HOME_WIRING; planned end -> HOME_NOT_FOUND.
 *  MOVE_TO_ZERO to 0 at v_fast -> DONE.
 * Any other stop (stop sources, latches, PC STOP) is handled by the core as HOME_FAILED ABORTED.
 * Implements: FW-HOM-001, FW-HOM-002, FW-HOM-004 (drift)
 */
#ifndef PURE_HOMING_H
#define PURE_HOMING_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int32_t release_max;      /* steps: PROTO_HOME_RELEASE_MAX_UM */
    int32_t backoff;          /* steps: home.backoff_um */
    int32_t slow_extra;       /* steps: PROTO_HOME_SLOW_EXTRA_UM */
    int32_t max_travel;       /* steps: home.max_travel_um */
} home_geo_t;

#define HOME_ACT_SEGMENT 0u   /* run the segment {dir, end_steps, slow} in `phase` */
#define HOME_ACT_ZERO    1u   /* set the zero (home_zero()), then run the MOVE_TO_ZERO segment */
#define HOME_ACT_DONE    2u   /* homing completed */
#define HOME_ACT_FAIL    3u   /* failure `fail` (HF_NOT_FOUND / HF_WIRING) */

typedef struct {
    uint8_t  action;
    uint8_t  phase;           /* HP_* of the next segment */
    int8_t   dir;             /* +1 / -1 */
    int32_t  end_steps;
    bool     slow;            /* home.v_slow_um_s (else home.v_fast_um_s) */
    uint8_t  fail;            /* HF_* */
} home_next_t;

typedef struct {
    int32_t  pos;             /* step count now (timer stopped) */
    int32_t  origin;          /* step count at the start of this phase */
    bool     start_edge;      /* the segment was ended by a START active edge */
    bool     end_edge;        /* the END switch became active during the segment */
    bool     released;        /* RELEASE / BACKOFF: START released stably during the segment */
    bool     planned_end;     /* the segment reached its planned end point */
} home_in_t;

/** First segment of HOME (after the pre-check): RELEASE if START is active, else FAST_SEEK. */
home_next_t home_begin(bool start_active, int32_t pos, const home_geo_t *g);
/** The segment of `phase` ended (timer stopped, no external stop source). */
home_next_t home_segment_end(uint8_t phase, const home_in_t *in, const home_geo_t *g);
/** RELEASE / BACKOFF: START released stably at pos -> new end point. */
static inline int32_t home_release_end(int32_t pos, const home_geo_t *g) { return pos + g->backoff; }

typedef struct {
    int32_t new_count;        /* hal_step_set_count() value at step count `pos` */
    int32_t drift_um;         /* captured edge (old coordinates) - (-offset); 0 if not homed before */
    bool    drift_fault;      /* |drift| > home.drift_tol_um while homed before (FW-HOM-004) */
} home_zero_t;
/** Zero so that the captured edge lies at -offset: new = pos - edge - offset_steps. Drift in µm
 *  (edge_um_old = the edge in the old coordinates, µm). */
home_zero_t home_zero(int32_t pos, int32_t edge_steps, int32_t offset_steps, bool was_homed,
                      int32_t edge_um_old, int32_t offset_um, uint32_t drift_tol_um);

#ifdef __cplusplus
}
#endif

#endif /* PURE_HOMING_H */
