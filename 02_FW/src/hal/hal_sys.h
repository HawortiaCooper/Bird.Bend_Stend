/* Seam v1 - system (tools/README "Seam v1", FW_design §5.1, §5.13, §8.1) + critical sections.
 * Target: hal/f446/sys_f4.c, iwdg.c, clock.c. Twin: IWDG model, process restart.
 *
 * Critical sections: tools/README names CRIT_HALT, CRIT_AFE, CRIT_MOTION, CRIT_DATA, CRIT_TICK
 * ("no-ops / mutex in the twin") without a C signature. Seam v1 fixes the API here (proposed to
 * the Integrator for the README, FW_design §8.1):
 *   hal_crit_t hal_crit_enter(hal_crit_level_t level);  void hal_crit_exit(hal_crit_t saved);
 * Levels (pinout §4): HALT = PRIMASK, AFE / MOTION = BASEPRI 0x20, DATA = 0x30, TICK = 0x40.
 * Usage: CRIT_BEGIN(HAL_CRIT_DATA); ... CRIT_END();   (at most one per block scope)
 * Implements: SAF-FW-018/019, FW-CFG-004, NFR-005, NFR-007, SYS-008
 */
#ifndef HAL_SYS_H
#define HAL_SYS_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void hal_wdg_kick(void);  void hal_wdg_set_timeout(uint32_t ms);
uint8_t hal_reset_cause(void);            /* RST_* (proto_gen.h) */
void hal_reset(void);  void hal_uid(uint8_t uid[12]);  bool hal_clk_fallback(void);
uint16_t hal_stack_free_min(void);

typedef enum {
    HAL_CRIT_HALT = 0,     /* PRIMASK: everything, <= 0.2 us */
    HAL_CRIT_AFE = 1,      /* BASEPRI 0x20 */
    HAL_CRIT_MOTION = 2,   /* BASEPRI 0x20 */
    HAL_CRIT_DATA = 3,     /* BASEPRI 0x30 */
    HAL_CRIT_TICK = 4      /* BASEPRI 0x40 */
} hal_crit_level_t;
typedef uint32_t hal_crit_t;
hal_crit_t hal_crit_enter(hal_crit_level_t level);
void       hal_crit_exit(hal_crit_t saved);

#define CRIT_BEGIN(level) do { const hal_crit_t crit_saved_ = hal_crit_enter(level)
#define CRIT_END()        hal_crit_exit(crit_saved_); } while (0)

#ifdef __cplusplus
}
#endif

#endif /* HAL_SYS_H */
