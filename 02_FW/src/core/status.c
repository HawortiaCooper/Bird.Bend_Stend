/* State snapshots: DATA flags/status, GET_STATUS (86 B), GET_INFO (44 B) (FW_design §5.10, §5.14;
 * ICD §7.1, §7.2, §7.6). Bits come only from the state: latches, the polarity-corrected inputs of the
 * last tick sample (safety.c), the driver monitor (DRV_PWR filtered, ALM filtered), the motion state.
 * The retired STOP_BTN bits are always 0 (ICD v0.5, D-36). fw_flags() also runs in the sample ISR.
 * Implements: FW-CMD-004, FW-STR-003, FW-CFG-004, IF-008, NFR-005 (stack_free_min),
 *             NFR-006 (loop_max_us), FW-MOT-009 (v_limit_um_s)
 */
#include <string.h>

#include "fw.h"

#include "hal_outputs.h"
#include "hal_step.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "units.h"

extern const char fw_build_id[];              /* build_id.c: the only object that differs per build */

int32_t fw_pos_um(void)
{
    return units_steps_to_um(hal_step_count(), g_fw.p.motion.steps_per_mm);
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
    f->limit_start = g_fw.lat.limit_start || safety_active((uint8_t)IO_LIMIT_START_BIT);
    f->limit_end = g_fw.lat.limit_end || safety_active((uint8_t)IO_LIMIT_END_BIT);
    f->link_wdg = g_fw.in.link_wdg;
    f->pause_btn = g_fw.in.pause.pressed;
    f->alm = drvmon_alm(&g_fw.in.drv);
    f->pend = g_fw.in.pend;
    f->pos_uncertain = g_fw.pos_uncertain;
    f->drv_pwr = drvmon_power(&g_fw.in.drv);          /* filtered; 1 with sensing disabled (boot value) */
    f->estop = g_fw.lat.estop || safety_active((uint8_t)IO_ESTOP_OPEN_BIT);
    /* D-37 b: bits of a feature whose GET_INFO bit is 0 are sent as 0 (invalid), also DRV_PWR with
     * power sensing off */
    if ((FW_FEATURES & FEAT_DRV_SIGNALS) == 0u) {
        f->alm = false;
        f->pend = false;
        f->drv_pwr = false;
    }
    if ((FW_FEATURES & FEAT_BUTTONS) == 0u) {
        f->pause_btn = false;
    }
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
    io.estop_open = safety_active((uint8_t)IO_ESTOP_OPEN_BIT);
    io.limit_start = safety_active((uint8_t)IO_LIMIT_START_BIT);
    io.limit_end = safety_active((uint8_t)IO_LIMIT_END_BIT);
    io.pause_btn = safety_active((uint8_t)IO_PAUSE_BTN_BIT);
    io.alm = safety_active((uint8_t)IO_ALM_BIT);
    io.pend = safety_active((uint8_t)IO_PEND_BIT);
    io.drv_pwr = safety_active((uint8_t)IO_DRV_PWR_BIT);   /* raw 'powered' */
    io.ena_disabled = !g_fw.ena_on;
    if ((FW_FEATURES & FEAT_DRV_SIGNALS) == 0u) {    /* D-37 b */
        io.alm = false;
        io.pend = false;
        io.drv_pwr = false;
    }
    if ((FW_FEATURES & FEAT_BUTTONS) == 0u) {
        io.pause_btn = false;
    }
    io.rate_80 = g_fw.p.afe.rate_sps == (uint8_t)AFE_RATE_SPS_SPS80;
    s->io = flags_io(&io);
    s->home_phase = g_fw.home_phase;
    s->halt_src = g_fw.lat.halt_src;
    s->reset_cause = g_fw.reset_cause;
    s->sys_flags = flags_sys(g_fw.clk_fallback, g_fw.cfg_dirty, g_fw.st.on, params_rt_reboot_pending(),
                             g_fw.nvm_defaulted);
    s->pos_um = fw_pos_um();
    s->target_um = motion_target_um();           /* = pos_um when idle */
    s->pos_steps = hal_step_count();
    s->afe_raw_last = g_fw.afe.raw_last;
    s->afe_rate_dsps = afe_rate_dsps(&g_fw.afe.rate);
    s->afe_reinit_count = g_fw.afe.reinit_count;
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
    s->idle_disable_left_s = safety_idle_left_s();
    s->nvm_record_seq = g_fw.nvm_record_valid ? g_fw.nvm_record_seq : 0u;
    s->nvm_save_uptime_ms = g_fw.nvm_save_uptime_ms;
    s->v_limit_um_s = fw_v_limit();
    s->pause_src = g_fw.lat.pause_src;
}

void info_build(info_t *i)
{
    const char *b = fw_build_id;
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
    i->feature_mask = FW_FEATURES | (g_fw.hw_meas ? (uint32_t)FEAT_HW_MEAS : 0u);
}
