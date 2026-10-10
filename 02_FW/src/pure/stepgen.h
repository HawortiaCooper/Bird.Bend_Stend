/* Step-generator decisions (FW_design §5.3, §5.6.4): the CLEAN-halt decision from the timer
 * registers (inlined into the RAM-resident HAL stop primitive), the period-stretch decision and the
 * controlled-stop path choice (D-29 d / D-30, ICD §6.5). Header-only, static inline, pure C11.
 * Timer model: PWM mode 2, PUL active while CNT >= CCR1 = ARR + 1 - PW, the update event (end of the
 * pulse) at CNT = ARR + 1 -> 0.
 * Implements: SAF-FW-002 (CLEAN halt), SAF-FW-003 (path choice, extend-only stretch),
 *             SAF-FW-004 (no runt, no uncounted pulse)
 */
#ifndef PURE_STEPGEN_H
#define PURE_STEPGEN_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* CLEAN halt: true -> a pulse is running or starts within `guard` ticks: let it complete and stop in
 * hardware at its update (OPM, counted by the update ISR); false -> force the output inactive and
 * stop the counter now (no pulse started, nothing to count). */
static inline bool stepgen_halt_complete(uint32_t cnt, uint32_t ccr1, uint32_t guard)
{
    return (ccr1 <= guard) || (cnt + guard >= ccr1);
}

/* TRUNCATE (E-stop): true -> a pulse was in flight and is cut (not counted: POS_UNCERTAIN). */
static inline bool stepgen_abort_cuts(uint32_t cnt, uint32_t ccr1)
{
    return cnt >= ccr1;
}

/* Period stretch (OI-ICD-07): the running period may be replaced by new_c only if its pulse has not
 * started (CNT < CCR1 - guard) and the new compare still lies ahead of CNT; the stretch never
 * shortens (new_c >= the running period is required by the caller, checked here too). */
static inline bool stepgen_stretch_ok(uint32_t cnt, uint32_t ccr1, uint32_t arr, uint32_t pw,
                                      uint32_t guard, uint32_t new_c)
{
    uint32_t new_ccr1 = (new_c > pw) ? new_c - pw : 0u;
    if (new_c < arr + 1u) {
        return false;                         /* would shorten the running interval */
    }
    if (cnt + guard >= ccr1) {
        return false;                         /* pulse in flight / imminent: preload path */
    }
    return cnt + guard < new_ccr1;
}

/* Step ISR fast path (NFR-007, FW_design §9.8 v0.8.6): core/motion.c step_isr_core(count) is the body
 * of step_isr() (seam v1) with the HAL's new count passed in and the result packed into 64 bits, so it
 * is returned in registers; the target HAL (hal/f446/step_tim2.c) calls it directly, the twin keeps
 * calling step_isr(). Low 32 bits = period (0 with LAST / STOP). Not a seam v1 function. */
#define STEP_NEXT_LAST ((uint64_t)1u << 32)
#define STEP_NEXT_STOP ((uint64_t)1u << 33)
uint64_t step_isr_core(int32_t count);

/* Controlled-stop path (FW_design §5.6.4, ICD §6.5; oracle: motion_vectors.json ctrl_stop_paths).
 * p_ticks = running step period, f = ticks per second, d_steps = planned stop distance
 * v^2/(2 a_stop) with v = f / p_ticks (ramp_stop_dist, binary64). */
#define STOPPATH_ISR     0u   /* P <= 1 ms: the next update ISR applies the deceleration */
#define STOPPATH_HALT    1u   /* P > 2 ms and d <= 1 step: CLEAN halt (no STOPPING state) */
#define STOPPATH_STRETCH 2u   /* otherwise: reprogram the running period to the first decel period */
static inline uint8_t stepgen_ctrl_path(uint32_t p_ticks, double d_steps, uint32_t f)
{
    uint32_t ms = f / 1000u;
    if (p_ticks > 2u * ms && d_steps <= 1.0) {
        return STOPPATH_HALT;
    }
    if (p_ticks <= ms) {
        return STOPPATH_ISR;
    }
    return STOPPATH_STRETCH;
}

#ifdef __cplusplus
}
#endif

#endif /* PURE_STEPGEN_H */
