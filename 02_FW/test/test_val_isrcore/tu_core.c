/* Validator E - §16: the TARGET entry of the step HAL (FW v0.8.6): HOST_TEST undefined in this unit, so
 * TIM2_IRQHandler calls step_isr_core(cnt) directly (step_tim2.c step_next_of, non-host branch); the
 * call is routed to val_probe_core() (test_main.c), which calls the real core/motion.c step_isr_core().
 * Verifies: SAF-FW-004, FW-MOT-002 (target step ISR path) - TC-FW-MOT-002-03 */
#undef HOST_TEST
#define VP(n) vcore_##n
#define step_isr_core val_probe_core
#include "isrc_model.h"
