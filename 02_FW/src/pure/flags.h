/* Composition of the DATA `flags` (u8) / `status` (u16), STATUS `io` (u16) and `sys_flags` (u8)
 * from a state snapshot (ICD §7.2, §7.6; FW_design §5.14 table). Pure C11, one input per bit.
 * Implements: FW-STR-003, FW-CMD-004
 */
#ifndef PURE_FLAGS_H
#define PURE_FLAGS_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    /* DATA flags */
    bool valid, moving, homed, enabled, estop, halt, fault, overrun;
    /* DATA status */
    bool paused, limit_start, limit_end, load_limit, afe_stale, afe_saturated, afe_settling,
         afe_rate_mismatch, link_wdg, stop_btn, pause_btn, alm, pend, pos_uncertain, no_afe_data,
         drv_pwr;
} flags_in_t;

typedef struct {
    bool estop_open, limit_start, limit_end, stop_btn, pause_btn, alm, pend, drv_pwr, ena_disabled,
         rate_80;
} io_in_t;

uint8_t  flags_data(const flags_in_t *s);
uint16_t flags_status(const flags_in_t *s);
uint16_t flags_io(const io_in_t *s);
uint8_t  flags_sys(bool clk_fallback, bool cfg_dirty, bool stream_on, bool reboot_pending,
                   bool nvm_defaulted);

#ifdef __cplusplus
}
#endif

#endif /* PURE_FLAGS_H */
