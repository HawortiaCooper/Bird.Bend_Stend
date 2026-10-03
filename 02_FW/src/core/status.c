/* State snapshots: DATA flags/status, GET_STATUS (86 B), GET_INFO (44 B) (FW_design §5.10, §5.14;
 * ICD §7.1, §7.2, §7.6). Bits come only from the state; M1 input-derived bits are 0 (not sampled),
 * DRV_PWR = 1 only with drv.pwr_sense_enable = false (power not confirmed otherwise).
 * Implements: FW-CMD-004, FW-STR-003, FW-CFG-004, IF-008, NFR-005 (stack_free_min),
 *             NFR-006 (loop_max_us), FW-MOT-009 (v_limit_um_s)
 */
#include <string.h>

#include "fw.h"

#include "hal_outputs.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "units.h"

#if defined(__has_include)
#if __has_include("build_info_gen.h")
#include "build_info_gen.h"
#endif
#endif
#ifndef FW_BUILD_DATE
#define FW_BUILD_DATE "host"
#endif

int32_t fw_pos_um(void)
{
    return units_steps_to_um(g_fw.pos_steps, g_fw.p.motion.steps_per_mm);
}

uint32_t fw_v_limit(void)
{
    cmd_ctx_t c;
    fw_cmd_ctx(&c);
    return cmd_v_limit(&c, cmd_loaded(&c), false);
}

void fw_flags(flags_in_t *f, uint32_t t_us)
{
    memset(f, 0, sizeof *f);
    f->valid = valid_at(&g_fw.valid, t_us);
    f->moving = cmd_is_moving(g_fw.motion_state);
    f->homed = g_fw.homed;
    f->enabled = g_fw.motion_state >= (uint8_t)MS_IDLE;
    f->estop = g_fw.lat.estop;
    f->halt = g_fw.lat.halt;
    f->fault = g_fw.lat.faults != 0u;
    f->overrun = false;
    f->paused = g_fw.lat.paused;
    f->load_limit = (g_fw.lat.faults & FAULT_LOAD_LIMIT) != 0u;
    f->afe_stale = g_fw.afe.stale;
    f->afe_saturated = g_fw.afe.saturated;
    f->afe_rate_mismatch = g_fw.afe.rate.mismatch;
    f->drv_pwr = !g_fw.p.drv.pwr_sense_enable;   /* M1: not sensed */
}

void status_build(status_t *s)
{
    flags_in_t fi;
    io_in_t io;
    uint32_t ok, crc, ferr;
    uint32_t now_ms = hal_time_ms();
    uint32_t age;
    memset(s, 0, sizeof *s);
    s->uptime_ms = now_ms - g_fw.boot_ms;
    s->t_us = hal_time_us();
    fw_flags(&fi, s->t_us);
    fi.afe_saturated = g_fw.afe.saturated;
    s->flags = flags_data(&fi);
    s->status = flags_status(&fi);
    s->motion_state = g_fw.motion_state;
    s->faults = g_fw.lat.faults;
    memset(&io, 0, sizeof io);
    io.drv_pwr = false;                          /* raw input not sampled in M1 */
    io.ena_disabled = false;                     /* ENA untouched (reset level = holding, D-13) */
    io.rate_80 = g_fw.p.afe.rate_sps == (uint8_t)AFE_RATE_SPS_SPS80;
    s->io = flags_io(&io);
    s->home_phase = g_fw.home_phase;
    s->halt_src = g_fw.lat.halt_src;
    s->reset_cause = g_fw.reset_cause;
    s->sys_flags = flags_sys(g_fw.clk_fallback, g_fw.cfg_dirty, g_fw.st.on, params_rt_reboot_pending(),
                             g_fw.nvm_defaulted);
    s->pos_um = fw_pos_um();
    s->target_um = s->pos_um;                    /* idle */
    s->pos_steps = g_fw.pos_steps;
    s->afe_raw_last = g_fw.afe.raw_last;
    s->afe_rate_dsps = afe_rate_dsps(&g_fw.afe.rate);
    s->afe_reinit_count = 0u;
    link_counters(&ok, &crc, &ferr);
    s->rx_frames_ok = ok;
    s->rx_crc_errors = crc;
    s->rx_frame_errors = ferr;
    s->rx_overruns = hal_uart_rx_overruns();
    s->tx_drops = g_fw.st.tx_drops;
    s->event_overflows = g_fw.evq.overflows;
    s->loop_max_us = g_fw.loop_max_us;
    age = now_ms - g_fw.last_cmd_rx_ms;
    s->link_age_ms = (age > 0xFFFFu) ? 0xFFFFu : (uint16_t)age;
    s->stack_free_min = hal_stack_free_min();
    s->nvm_save_ms = g_fw.nvm_save_ms;
    s->idle_disable_left_s = 0xFFFFu;            /* not counting: driver not enabled */
    s->nvm_record_seq = g_fw.nvm_record_valid ? g_fw.nvm_record_seq : 0u;
    s->nvm_save_uptime_ms = g_fw.nvm_save_uptime_ms;
    s->v_limit_um_s = fw_v_limit();
    s->pause_src = g_fw.lat.pause_src;
}

void info_build(info_t *i)
{
    const char *b = FW_BUILD_DATE;
    size_t n = strlen(b);
    memset(i, 0, sizeof *i);
    i->proto_major = (uint8_t)PROTO_MAJOR;
    i->proto_minor = (uint8_t)PROTO_MINOR;
    i->payload_version = (uint8_t)PROTO_PAYLOAD_VERSION;
    i->fw_version[0] = (uint8_t)FW_VERSION_MAJOR;
    i->fw_version[1] = (uint8_t)FW_VERSION_MINOR;
    i->fw_version[2] = (uint8_t)FW_VERSION_PATCH;
    i->param_dict_hash = (uint32_t)PARAM_DICT_HASH;
    hal_uid(i->uid);
    memcpy(i->build, b, (n > sizeof i->build) ? sizeof i->build : n);
    i->param_count = (uint16_t)PARAM_COUNT;
    i->feature_mask = FW_FEATURES;
}
