/* Command handlers (FW_design §5.10, ICD §5): full pure check first (cmd_check, ICD §4.4), a NACK
 * has no effect; accepted commands are executed here. M1: system, parameter, NVM, stream, VALID
 * and the stop/latch/clear commands; motion commands that pass the check answer E_INTERNAL
 * NOT_IN_BUILD with no side effect (feature bits MOTION/HOMING/MOVE_UNTIL_LOAD = 0), JOG 0 = OK.
 * Implements: FW-CMD-001, FW-CMD-002, FW-CMD-003, FW-CMD-004, FW-CFG-002, FW-CFG-003, FW-CFG-004,
 *             FW-NVM-001, FW-STR-001, FW-MOT-007 (latch part), SAF-FW-001 (VALID clear),
 *             SAF-FW-006, SAF-FW-022, SAF-FW-023, IF-005, IF-008, D-30, D-31
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
    c->p = &g_fw.p;
    c->motion_state = g_fw.motion_state;
    c->enabling_left_ms = 0u;
    c->homed = g_fw.homed;
    c->pos_um = fw_pos_um();
    c->estop_latched = g_fw.lat.estop;
    c->estop_input_open = false;                 /* M1: inputs not sampled (M2) */
    c->estop_closed_ms = UINT32_MAX;
    c->halt_latched = g_fw.lat.halt;
    c->stop_btn_active = false;                  /* D-36: no holding STOP button input */
    c->stop_btn_released_ms = UINT32_MAX;
    c->faults = g_fw.lat.faults;
    c->fault_causes = 0u;                        /* M1: no fault source has a persistent cause */
    c->limit_start = false;
    c->limit_end = false;
    c->afe_stale = g_fw.afe.stale;
    c->afe_saturated = g_fw.afe.saturated;
    c->raw = (g_fw.afe.raw_last != PROTO_AFE_NO_DATA) ? g_fw.afe.raw_last : 0;
    /* M1: DRV_PWR not sensed -> power not confirmed unless sensing is disabled (fail-safe) */
    c->drv_power = !g_fw.p.drv.pwr_sense_enable;
    c->alm_active = false;
    c->nvm_record_valid = g_fw.nvm_record_valid;
    c->paused = g_fw.lat.paused;
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
    case CMD_STOP:
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_stop(&g_fw.lat, &g_fw.valid, &g_fw.evq, r.u.stop.mode, now_us);
        CRIT_END();
        ok_empty(type, seq);                         /* the stop executes before the ACK */
        break;
    case CMD_HALT:
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_halt(&g_fw.lat, &g_fw.valid, &g_fw.evq, SRC_PC, now_us);
        CRIT_END();
        ok_empty(type, seq);
        break;
    case CMD_PAUSE:
        CRIT_BEGIN(HAL_CRIT_DATA);
        latch_on_pause(&g_fw.lat, &g_fw.valid, &g_fw.evq, SRC_PC, now_us);
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
        CRIT_BEGIN(HAL_CRIT_DATA);
        m = latch_on_fault_clear(&g_fw.lat, &g_fw.evq, now_us);
        CRIT_END();
        le_put16(body, m);
        link_respond(type, seq, ST_OK, body, 2u);
        break;
    }
    case CMD_JOG:
        if (r.u.jog.v_um_s == 0) {
            ok_empty(type, seq);                     /* JOG 0 while not jogging: OK no-op */
            break;
        }
        link_nack(type, seq, ST_E_INTERNAL, INTERNAL_NOT_IN_BUILD);
        break;
    case CMD_ENABLE:
    case CMD_DISABLE:
    case CMD_HOME:
    case CMD_MOVE_ABS:
    case CMD_MOVE_UNTIL_LOAD:
        link_nack(type, seq, ST_E_INTERNAL, INTERNAL_NOT_IN_BUILD);   /* M2 / M4 (FW_design §5.10) */
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
