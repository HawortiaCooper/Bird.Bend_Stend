/* Seam v1 - step generator / ENA (tools/README "Seam v1", FW_design §5.6, §8.1).
 * Target: hal/f446/step_tim2.c. Core: core/motion.c (step_isr() callback, ramp, segments).
 * hal_step_set_dir(dir): +-1 logical direction; +-2 = logical direction with the DIR output inverted
 * (motion.dir_invert) - interim encoding, seam request SR-M2-01 (the HAL counts by the sign).
 * Implements: FW-MOT-001, SAF-FW-002/004, SYS-008
 */
#ifndef HAL_STEP_H
#define HAL_STEP_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct { uint32_t pw_ticks, dir_setup_ticks; bool pul_invert, ena_invert; } hal_step_cfg_t;
uint32_t hal_step_init(const hal_step_cfg_t *cfg);                    /* returns f_tick (90 MHz target) */
void     hal_step_set_dir(int dir);                                   /* only while stopped */
void     hal_step_start(uint32_t first_period_ticks);                 /* first edge >= dir_setup after the call */
void     hal_step_set_period(uint32_t ticks);                         /* preload: period after the running one */
void     hal_step_set_period_now(uint32_t ticks);                     /* stretch the running period (§5.6.4) */
void     hal_step_arm_last(void);                                     /* OPM: stop at the end of this period */
bool     hal_step_stop_now(void);                                     /* CLEAN, no runt; true = pulse completes */
bool     hal_step_abort(void);                                        /* TRUNCATE; true = pulse cut (uncertain) */
int32_t  hal_step_count(void);                                        /* signed position counter, steps */
void     hal_step_set_count(int32_t steps);                           /* homing zero shift, only while stopped */
bool     hal_step_running(void);
uint32_t hal_step_stop_gen(void);                                     /* start-then-recheck counter */
void     hal_ena_set(bool enabled);                                   /* polarity from hal_step_init */
typedef struct { uint32_t period; bool last; bool stop; } step_next_t;
step_next_t step_isr(void);                                           /* callback, level 2, per completed pulse */

#ifdef __cplusplus
}
#endif

#endif /* HAL_STEP_H */
