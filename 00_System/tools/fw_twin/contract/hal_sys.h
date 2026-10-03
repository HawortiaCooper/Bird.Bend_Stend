/* Seam v1 CONTRACT COPY (00_System/tools/README.md "Seam v1" = FW_design v0.3 §8.1).
 * Owner of the real header: Implementer A (02_FW/src/hal/hal_sys.h). This copy is used by the
 * twin build ONLY while A's header is absent; build.py prefers 02_FW/src/hal/ and checks that
 * A's prototypes equal this contract (tools/fw_twin/build.py --check-seams). Never edit to
 * "fix" a mismatch: a seam change goes through FW_design §8.1 + tools/README (Integrator).
 * Implements: SYS-008 (twin seam), D-07 */
#ifndef HAL_SYS_H
#define HAL_SYS_H
#include <stdbool.h>
#include <stddef.h>
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
hal_crit_t hal_crit_enter(hal_crit_level_t level);   /* critical sections (seam v1.1, proposed by A) */
void       hal_crit_exit(hal_crit_t saved);
#define CRIT_BEGIN(level) do { const hal_crit_t crit_saved_ = hal_crit_enter(level)
#define CRIT_END()        hal_crit_exit(crit_saved_); } while (0)
#ifdef __cplusplus
}
#endif
#endif /* HAL_SYS_H */
