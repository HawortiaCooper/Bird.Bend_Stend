/* DATA / STATUS bit composition. DS_STOP_BTN / IO_STOP_BTN are retired (ICD v0.5, D-36): always 0.
 * Implements: FW-STR-003, FW-CMD-004 */
#include "flags.h"

#include "proto_gen.h"

#define B8(c, m)  ((c) ? (uint8_t)(m) : 0u)
#define B16(c, m) ((c) ? (uint16_t)(m) : 0u)

uint8_t flags_data(const flags_in_t *s)
{
    return (uint8_t)(B8(s->valid, DF_VALID) | B8(s->moving, DF_MOVING) | B8(s->homed, DF_HOMED) |
                     B8(s->enabled, DF_ENABLED) | B8(s->estop, DF_ESTOP) | B8(s->halt, DF_HALT) |
                     B8(s->fault, DF_FAULT) | B8(s->overrun, DF_OVERRUN));
}

uint16_t flags_status(const flags_in_t *s)
{
    return (uint16_t)(B16(s->paused, DS_PAUSED) | B16(s->limit_start, DS_LIMIT_START) |
                      B16(s->limit_end, DS_LIMIT_END) | B16(s->load_limit, DS_LOAD_LIMIT) |
                      B16(s->afe_stale, DS_AFE_STALE) | B16(s->afe_saturated, DS_AFE_SATURATED) |
                      B16(s->afe_settling, DS_AFE_SETTLING) |
                      B16(s->afe_rate_mismatch, DS_AFE_RATE_MISMATCH) | B16(s->link_wdg, DS_LINK_WDG) |
                      B16(s->pause_btn, DS_PAUSE_BTN) |
                      B16(s->alm, DS_ALM) | B16(s->pend, DS_PEND) |
                      B16(s->pos_uncertain, DS_POS_UNCERTAIN) | B16(s->no_afe_data, DS_NO_AFE_DATA) |
                      B16(s->drv_pwr, DS_DRV_PWR));
}

uint16_t flags_io(const io_in_t *s)
{
    return (uint16_t)(B16(s->estop_open, IO_ESTOP_OPEN) | B16(s->limit_start, IO_LIMIT_START) |
                      B16(s->limit_end, IO_LIMIT_END) |
                      B16(s->pause_btn, IO_PAUSE_BTN) | B16(s->alm, IO_ALM) | B16(s->pend, IO_PEND) |
                      B16(s->drv_pwr, IO_DRV_PWR) | B16(s->ena_disabled, IO_ENA_DISABLED) |
                      B16(s->rate_80, IO_RATE_80));
}

uint8_t flags_sys(bool clk_fallback, bool cfg_dirty, bool stream_on, bool reboot_pending,
                  bool nvm_defaulted)
{
    return (uint8_t)(B8(clk_fallback, SYSF_CLK_FALLBACK) | B8(cfg_dirty, SYSF_CFG_DIRTY) |
                     B8(stream_on, SYSF_STREAM_ON) | B8(reboot_pending, SYSF_REBOOT_PENDING) |
                     B8(nvm_defaulted, SYSF_NVM_DEFAULTED));
}
