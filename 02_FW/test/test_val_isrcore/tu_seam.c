/* Validator E - §16: the SEAM entry of the same step HAL (FW v0.8.6): HOST_TEST defined, so
 * TIM2_IRQHandler calls step_isr() (step_tim2.c step_next_of, host branch). step_isr() here is the body
 * of core/motion.c's seam-v1 wrapper, v0.8.6:
 *     uint64_t v = step_isr_core(hal_step_count());
 *     n.period = (uint32_t)v; n.last = (v & STEP_NEXT_LAST) != 0u; n.stop = (v & STEP_NEXT_STOP) != 0u;
 * with hal_step_count() = this unit's HAL count, read inside val_probe_seam() after the injection point
 * (= the read timing of v0.8.5 and of the twin). The real core is called by the probe.
 * Verifies: SAF-FW-004, FW-MOT-002 - TC-FW-MOT-002-03 */
#ifndef HOST_TEST
#define HOST_TEST 1
#endif
#define VP(n) vseam_##n
#define step_isr vseam_step_isr
#include "isrc_model.h"

step_next_t vseam_step_isr(void)
{
    step_next_t n;
    uint64_t v = val_probe_seam(vseam_hal_step_count);
    n.period = (uint32_t)v;
    n.last = (v & STEP_NEXT_LAST) != 0u;
    n.stop = (v & STEP_NEXT_STOP) != 0u;
    return n;
}
