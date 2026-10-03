/* Payload codec (ICD §4, §5, §7). Implements: IF-005, IF-006, IF-009, FW-STR-003, FW-CFG-002,
 * FW-CFG-004, FW-CMD-004
 */
#include "payload.h"

#include <string.h>

#include "le.h"

void payload_info(const info_t *in, uint8_t out[PROTO_INFO_LEN])
{
    out[0] = in->proto_major;
    out[1] = in->proto_minor;
    out[2] = in->payload_version;
    out[3] = in->fw_version[0];
    out[4] = in->fw_version[1];
    out[5] = in->fw_version[2];
    le_put32(&out[6], in->param_dict_hash);
    memcpy(&out[10], in->uid, 12u);
    memcpy(&out[22], in->build, 16u);
    le_put16(&out[38], in->param_count);
    le_put32(&out[40], in->feature_mask);
}
_Static_assert(PROTO_INFO_LEN == 44u, "ICD §7.1");

void payload_status(const status_t *in, uint8_t out[PROTO_STATUS_LEN])
{
    le_put32(&out[0], in->uptime_ms);
    le_put32(&out[4], in->t_us);
    out[8] = in->flags;
    out[9] = in->motion_state;
    le_put16(&out[10], in->status);
    le_put16(&out[12], in->faults);
    le_put16(&out[14], in->io);
    out[16] = in->home_phase;
    out[17] = in->halt_src;
    out[18] = in->reset_cause;
    out[19] = in->sys_flags;
    le_put32(&out[20], (uint32_t)in->pos_um);
    le_put32(&out[24], (uint32_t)in->target_um);
    le_put32(&out[28], (uint32_t)in->pos_steps);
    le_put32(&out[32], (uint32_t)in->afe_raw_last);
    le_put16(&out[36], in->afe_rate_dsps);
    le_put16(&out[38], in->afe_reinit_count);
    le_put32(&out[40], in->rx_frames_ok);
    le_put32(&out[44], in->rx_crc_errors);
    le_put32(&out[48], in->rx_frame_errors);
    le_put32(&out[52], in->rx_overruns);
    le_put32(&out[56], in->tx_drops);
    le_put16(&out[60], in->event_overflows);
    le_put16(&out[62], in->loop_max_us);
    le_put16(&out[64], in->link_age_ms);
    le_put16(&out[66], in->stack_free_min);
    le_put16(&out[68], in->nvm_save_ms);
    le_put16(&out[70], in->idle_disable_left_s);
    le_put32(&out[72], in->nvm_record_seq);
    le_put32(&out[76], in->nvm_save_uptime_ms);
    le_put32(&out[80], in->v_limit_um_s);
    out[84] = in->pause_src;
    out[85] = 0u;
}
_Static_assert(PROTO_STATUS_LEN == 86u, "ICD §7.2");

void payload_data(const data_t *in, uint8_t out[PROTO_DATA_LEN])
{
    le_put32(&out[0], in->t_us);
    out[4] = (uint8_t)PROTO_PAYLOAD_VERSION;
    out[5] = in->flags;
    le_put32(&out[6], (uint32_t)in->afe_raw);
    le_put32(&out[10], (uint32_t)in->setpoint_um);
    le_put16(&out[14], in->frame_seq);
    le_put16(&out[16], in->status);
}
_Static_assert(PROTO_DATA_LEN == 18u, "ICD §7.3");

void payload_event(const event_t *in, uint8_t out[PROTO_EVENT_LEN])
{
    le_put32(&out[0], in->t_us);
    le_put16(&out[4], in->code);
    le_put16(&out[6], in->arg);
    le_put32(&out[8], (uint32_t)in->value);
    le_put32(&out[12], (uint32_t)in->value2);
}
_Static_assert(PROTO_EVENT_LEN == 16u, "ICD §7.4");

void payload_param_entry(const param_entry_t *in, uint8_t out[PROTO_PARAM_ENTRY_LEN])
{
    le_put16(&out[0], in->id);
    out[2] = in->type;
    memcpy(&out[3], in->wire, 4u);
}

uint16_t payload_nack(uint8_t status, uint16_t detail, uint8_t out[PROTO_NACK_LEN])
{
    out[0] = status;
    le_put16(&out[1], detail);
    return (uint16_t)PROTO_NACK_LEN;
}

bool payload_decode_request(uint8_t type, const uint8_t *p, uint16_t len, cmd_req_t *out)
{
    int16_t need = proto_req_len(type);
    memset(out, 0, sizeof *out);
    out->type = type;
    if (need < 0 || len != (uint16_t)need) {
        return false;
    }
    switch (type) {
    case CMD_REBOOT:
        out->u.reboot.magic = le_get32(p);
        break;
    case CMD_GET_ALL_PARAMS:
        out->u.page.page = p[0];
        break;
    case CMD_GET_PARAM:
        out->u.get_param.id = le_get16(p);
        break;
    case CMD_SET_PARAM:
        out->u.set_param.id = le_get16(p);
        out->u.set_param.type = p[2];
        memcpy(out->u.set_param.wire, &p[3], 4u);
        break;
    case CMD_SET_VALID:
        out->u.set_valid.valid = p[0];
        break;
    case CMD_HOME:
        out->u.home.flags = p[0];
        break;
    case CMD_MOVE_ABS:
        out->u.move_abs.target_um = (int32_t)le_get32(&p[0]);
        out->u.move_abs.v_um_s = le_get32(&p[4]);
        out->u.move_abs.a_um_s2 = le_get32(&p[8]);
        break;
    case CMD_JOG:
        out->u.jog.v_um_s = (int32_t)le_get32(&p[0]);
        out->u.jog.a_um_s2 = le_get32(&p[4]);
        out->u.jog.bound_um = (int32_t)le_get32(&p[8]);
        break;
    case CMD_MOVE_UNTIL_LOAD:
        out->u.mul.bound_um = (int32_t)le_get32(&p[0]);
        out->u.mul.v_um_s = le_get32(&p[4]);
        out->u.mul.a_um_s2 = le_get32(&p[8]);
        out->u.mul.raw_stop = (int32_t)le_get32(&p[12]);
        out->u.mul.cmp = p[16];
        break;
    case CMD_STOP:
        out->u.stop.mode = p[0];
        break;
    case CMD_DIAG_MEAS:
        out->u.meas.op = p[0];
        out->u.meas.sel = p[1];
        out->u.meas.a = le_get16(&p[2]);
        out->u.meas.b = le_get32(&p[4]);
        break;
    default:
        break;               /* LEN 0 commands: no fields */
    }
    return true;
}
