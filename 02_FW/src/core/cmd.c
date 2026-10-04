/* Command handlers (FW_design §5.10, ICD §5): full pure check first (cmd_check, ICD §4.4) on a
 * context filled from the live state, a NACK has no effect; accepted commands are executed here.
 * M2: ENABLE / DISABLE / HOME / MOVE_ABS / JOG and the motion part of STOP / HALT / PAUSE run the
 * motion executor (under CRIT_TICK: serialised with the control tick). MOVE_UNTIL_LOAD is M4:
 * accepted commands answer E_INTERNAL NOT_IN_BUILD with no side effect (FEAT_MOVE_UNTIL_LOAD = 0).
 * EVENT order of a stop (FW_design §5.3): latch EVENT -> STOPPED -> VALID_CLEARED -> MOVE_DONE.
 * Implements: FW-CMD-001, FW-CMD-002, FW-CMD-003, FW-CMD-004, FW-CFG-002, FW-CFG-003, FW-CFG-004,
 *             FW-NVM-001, FW-STR-001, FW-MOT-004, FW-MOT-005, FW-MOT-007, FW-MOT-008, FW-HOM-001,
 *             SAF-FW-001 (VALID clear), SAF-FW-006, SAF-FW-011 (regrow reference), SAF-FW-020,
 *             SAF-FW-021, SAF-FW-023, IF-005, IF-008, D-30, D-31
 */
#include "fw.h"

#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "le.h"
#include "units.h"

static bool     s_reboot_pending;
static uint32_t s_reboot_at_ms;

void fw_cmd_ctx(cmd_ctx_t *c)
{
    c->p = params_rt_effective();                /* reboot_required at boot value */
    c->motion_state = g_fw.motion_state;
    c->enabling_left_ms = motion_enabling_left_ms(hal_time_ms());
    c->homed = g_fw.homed;
    c->pos_um = fw_pos_um();
    c->estop_latched = g_fw.lat.estop;
    c->estop_input_open = safety_active((uint8_t)IO_ESTOP_OPEN_BIT);
    c->estop_closed_ms = safety_estop_closed_ms();
    c->halt_latched = g_fw.lat.halt;
    c->faults = g_fw.lat.faults;
    c->fault_causes = safety_fault_causes();
    c->limit_start = g_fw.lat.limit_start || safety_active((uint8_t)IO_LIMIT_START_BIT);
    c->limit_end = g_fw.lat.limit_end || safety_active((uint8_t)IO_LIMIT_END_BIT);
    c->afe_stale = g_fw.afe.stale;
    c->afe_saturated = g_fw.afe.saturated;
    c->raw = (g_fw.afe.raw_last != PROTO_AFE_NO_DATA) ? g_fw.afe.raw_last : 0;
    c->drv_power = safety_drv_power();           /* filtered (true with sensing disabled) */
    c->alm_active = drvmon_alm(&g_fw.in.drv);
    c->nvm_record_valid = g_fw.nvm_record_valid;
    c->paused = g_fw.lat.paused;
    c->hw_meas = g_fw.hw_meas;
    c->unhomed_origin_um = motion_unhomed_origin_um();
}

static void ok_empty(uint8_t type, uint8_t seq)
{
    link_respond(type, seq, ST_OK, NULL, 0u);
}

static void do_set_param(uint8_t type, uint8_t seq, const cmd_req_t *r)
{
    const param_meta_t *m = param_find(r->u.set_param.id);
    uint32_t raw = 0u;
    uint8_t body[PROTO_PARAM_ENTRY_LEN];
    if (m == NULL || param_validate_set(m, r->u.set_param.type, r->u.set_param.wire, &raw) != PARAM_ST_OK) {
        link_nack(type, seq, ST_E_INTERNAL, INTERNAL_INVARIANT);   /* cannot happen after the check */
        return;
    }
    CRIT_BEGIN(HAL_CRIT_DATA);                   /* the sample ISR / tick read parameters */
    param_set_raw(&g_fw.p, m, raw);
    CRIT_END();
    params_rt_apply(m->id);                      /* effective <= 10 ms (here: at once) */
    nvm_cfg_dirty_update();                      /* ICD §11.2; never a flash write (FW-CFG-003) */
    params_rt_entry(m, body);                    /* value as stored */
    link_respond(type, seq, ST_OK, body, PROTO_PARAM_ENTRY_LEN);
}

void cmd_execute(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len)
{
    cmd_ctx_t c;
    cmd_verdict_t v;
    cmd_req_t r;
    uint8_t body[PROTO_MAX_LEN];
    uint32_t now_us;

    fw_cmd_ctx(&c);
    v = cmd_check(&c, type, payload, len);
    if (v.status != ST_OK) {
        link_nack(type, seq, v.status, v.detail);    /* no side effect (ICD §4.1) */
        return;
    }
    (void)payload_decode_request(type, payload, len, &r);
    now_us = hal_time_us();

    switch (type) {
    case CMD_PING:
    case CMD_STREAM_START:
    case CMD_STREAM_STOP:
        if (type != (uint8_t)CMD_PING) {
            stream_set(type == (uint8_t)CMD_STREAM_START);
        }
        ok_empty(type, seq);
        break;
    case CMD_GET_INFO: {
        info_t i;
        info_build(&i);
        payload_info(&i, body);
        link_respond(type, seq, ST_OK, body, PROTO_INFO_LEN);
        break;
    }
    case CMD_GET_STATUS: {
        status_t s;
        status_build(&s);
        payload_status(&s, body);
        link_respond(type, seq, ST_OK, body, PROTO_STATUS_LEN);
        break;
    }
    case CMD_REBOOT:
        ok_empty(type, seq);
        s_reboot_pending = true;                     /* drain, then reset <= 50 ms later */
        s_reboot_at_ms = hal_time_ms();
        break;
    case CMD_GET_ALL_PARAMS: {
        uint8_t n = params_rt_page(r.u.page.page, body);
        link_respond(type, seq, ST_OK, body, (uint16_t)(3u + (uint16_t)n * PROTO_PARAM_ENTRY_LEN));
        break;
    }
    case CMD_GET_PARAM:
        params_rt_entry(param_find(r.u.get_param.id), body);
        link_respond(type, seq, ST_OK, body, PROTO_PARAM_ENTRY_LEN);
        break;
    case CMD_SET_PARAM:
        do_set_param(type, seq, &r);
        break;
    case CMD_SAVE_PARAMS:
        nvm_save_start(seq);                         /* response from nvm_service() */
        break;
    case CMD_LOAD_PARAMS:
        if (nvm_load_cmd()) {
            params_rt_apply_all();
            ok_empty(type, seq);
        } else {
            link_nack(type, seq, ST_E_NVM, NVMD_NO_RECORD);   /* rule 5: RAM unchanged */
        }
        break;
    case CMD_DEFAULT_PARAMS:
        nvm_default_cmd();
        params_rt_apply_all();
        ok_empty(type, seq);
        break;
    case CMD_SET_VALID: {
        uint32_t t;
        CRIT_BEGIN(HAL_CRIT_DATA);
        t = valid_set(&g_fw.valid, r.u.set_valid.valid != 0u, now_us);
        CRIT_END();
        le_put32(body, t);
        link_respond(type, seq, ST_OK, body, 4u);
        break;
    }
    case CMD_STOP: {
        bool ctl = r.u.stop.mode == (uint8_t)STOPMODE_CONTROLLED;
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_stop(ctl ? (uint8_t)SC_PC_STOP_CONTROLLED : (uint8_t)SC_PC_STOP, ctl, (uint8_t)MD_STOPPED);
        CRIT_END();
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_stop(&g_fw.lat, &g_fw.valid, &g_fw.evq, r.u.stop.mode, now_us);   /* also when idle */
        CRIT_END();
        ok_empty(type, seq);                         /* the stop executes before the ACK */
        break;
    }
    case CMD_HALT:
        CRIT_BEGIN(HAL_CRIT_DATA);
        (void)latch_halt(&g_fw.lat, &g_fw.evq, now_us);    /* HALT_SET on the first HALT only */
        CRIT_END();
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_stop((uint8_t)SC_PC_HALT, false, (uint8_t)MD_STOPPED);
        CRIT_END();
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_valid_clear(&g_fw.valid, &g_fw.evq, (uint16_t)SC_PC_HALT, now_us);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_PAUSE:
        CRIT_BEGIN(HAL_CRIT_DATA);
        (void)latch_pause(&g_fw.lat, &g_fw.evq, (uint8_t)SRC_PC, now_us);   /* EVENT on 0 -> 1 only */
        CRIT_END();
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_stop((uint8_t)SC_PC_PAUSE, true, (uint8_t)MD_STOPPED);      /* controlled (SAF-FW-023) */
        CRIT_END();
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_valid_clear(&g_fw.valid, &g_fw.evq, (uint16_t)SC_PC_PAUSE, now_us);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_HALT_CLEAR:
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_halt_clear(&g_fw.lat, &g_fw.evq, now_us);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_RESUME:
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_resume(&g_fw.lat, &g_fw.evq, now_us);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_ESTOP_CLEAR:
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_estop_clear(&g_fw.lat, &g_fw.evq, now_us);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_FAULT_CLEAR: {
        uint16_t m;
        CRIT_BEGIN(HAL_CRIT_TICK);
        afe_fold_trip();                             /* a trip not yet folded is reported first */
        CRIT_END();
        CRIT_BEGIN(HAL_CRIT_DATA);
        m = latch_on_fault_clear(&g_fw.lat, &g_fw.evq, now_us);
        CRIT_END();
        if ((m & FAULT_LOAD_LIMIT) != 0u) {
            afe_loadlim_cleared();                   /* regrow reference (SAF-FW-011) */
        }
        le_put16(body, m);
        link_respond(type, seq, ST_OK, body, 2u);
        break;
    }
    case CMD_ENABLE: {
        uint16_t left;
        CRIT_BEGIN(HAL_CRIT_TICK);
        left = motion_enable(hal_time_ms());         /* settle motion.ena_settle_ms (FW-MOT-008) */
        CRIT_END();
        le_put16(body, left);
        link_respond(type, seq, ST_OK, body, 2u);
        break;
    }
    case CMD_DISABLE:
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_disable((uint8_t)DD_PC_DISABLE);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_HOME:
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_home();                               /* START only (D-29 b) */
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_MOVE_ABS:
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_move_abs(r.u.move_abs.target_um, r.u.move_abs.v_um_s, r.u.move_abs.a_um_s2);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_JOG:
        CRIT_BEGIN(HAL_CRIT_TICK);
        motion_jog(r.u.jog.v_um_s, r.u.jog.a_um_s2, r.u.jog.bound_um, hal_time_ms());
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_MOVE_UNTIL_LOAD:
        link_nack(type, seq, ST_E_INTERNAL, INTERNAL_NOT_IN_BUILD);   /* M4 (FW_design §5.10) */
        break;
    case CMD_DIAG_MEAS:                              /* measurement images only (D-40 c, App. C) */
        if (hal_meas_cmd(payload, len, body, PROTO_MEAS_BODY_LEN) == PROTO_MEAS_BODY_LEN) {
            link_respond(type, seq, ST_OK, body, PROTO_MEAS_BODY_LEN);
        } else {
            link_nack(type, seq, ST_E_INTERNAL, INTERNAL_NOT_IN_BUILD);
        }
        break;
    default:
        link_nack(type, seq, ST_E_INTERNAL, INTERNAL_INVARIANT);
        break;
    }
}

void cmd_reboot_service(void)
{
    if (s_reboot_pending &&
        (hal_uart_tx_idle() || (uint32_t)(hal_time_ms() - s_reboot_at_ms) >= REBOOT_DRAIN_MAX_MS)) {
        s_reboot_pending = false;
        hal_reset();
    }
}
