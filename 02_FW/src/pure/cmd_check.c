/* Pure command acceptance (ICD §4.4). Implements: FW-CMD-001, FW-CFG-003, SAF-FW-006,
 * SAF-FW-020, SAF-FW-021, SAF-FW-022, SAF-FW-023, SAF-FW-024, SAF-FW-026, FW-CMD-003, FW-MOT-009,
 * D-30, D-31
 */
#include "cmd_check.h"

#include "param_rules.h"
#include "payload.h"
#include "units.h"

static cmd_verdict_t verdict(uint8_t status, uint32_t detail)
{
    cmd_verdict_t v;
    v.status = status;
    v.detail = (uint16_t)detail;
    return v;
}

static int64_t abs64(int64_t v) { return v < 0 ? -v : v; }

bool cmd_is_moving(uint8_t ms)
{
    return ms == (uint8_t)MS_MOVE_ABS || ms == (uint8_t)MS_JOG || ms == (uint8_t)MS_MOVE_UNTIL_LOAD ||
           ms == (uint8_t)MS_HOMING || ms == (uint8_t)MS_STOPPING;
}

bool cmd_loaded(const cmd_ctx_t *c)
{
    if (c->afe_stale) {
        return true;                        /* unknown load -> conservative */
    }
    return abs64((int64_t)c->raw - (int64_t)c->p->safety.zero_raw) >=
           (int64_t)c->p->safety.release_band_raw;
}

uint32_t cmd_v_limit(const cmd_ctx_t *c, bool loaded, bool unhomed_jog)
{
    uint32_t v = loaded ? c->p->motion.v_max_load_um_s : c->p->motion.v_max_travel_um_s;
    uint32_t rate = units_rate_cap_um_s(c->p->motion.max_step_rate_hz, c->p->motion.steps_per_mm);
    if (rate < v) {
        v = rate;
    }
    if (unhomed_jog && c->p->motion.v_unhomed_um_s < v) {
        v = c->p->motion.v_unhomed_um_s;
    }
    return v;
}

uint16_t cmd_block_mask(const cmd_ctx_t *c, uint8_t type, int dir, bool jog_bound)
{
    uint16_t b = 0u;
    bool unpowered = c->p->drv.pwr_sense_enable && !c->drv_power;
    bool jog_refresh;
    if (c->estop_latched || c->estop_input_open) {
        b |= BLOCK_ESTOP;
    }
    if (type == (uint8_t)CMD_ENABLE) {
        if (unpowered) {
            b |= BLOCK_DRV_UNPOWERED;
        }
        return b;
    }
    if (c->halt_latched) {
        b |= BLOCK_HALT;
    }
    if (c->faults != 0u) {
        b |= BLOCK_FAULT;
    }
    if (c->motion_state == (uint8_t)MS_NOT_ENABLED) {
        b |= BLOCK_NOT_ENABLED;
    }
    if (!c->homed && (type == (uint8_t)CMD_MOVE_ABS || type == (uint8_t)CMD_MOVE_UNTIL_LOAD ||
                      (type == (uint8_t)CMD_JOG && jog_bound))) {
        b |= BLOCK_NOT_HOMED;
    }
    if (type != (uint8_t)CMD_HOME && ((dir < 0 && c->limit_start) || (dir > 0 && c->limit_end))) {
        b |= BLOCK_LIMIT;
    }
    if (c->afe_stale) {
        b |= BLOCK_AFE_STALE;
    }
    if (c->afe_saturated) {
        b |= BLOCK_AFE_SATURATED;
    }
    if (unpowered) {
        b |= BLOCK_DRV_UNPOWERED;
    }
    /* SAF-FW-026: ALM blocks only NEW starts; a refresh of a running jog is not blocked */
    jog_refresh = type == (uint8_t)CMD_JOG && c->motion_state == (uint8_t)MS_JOG;
    if (c->alm_active && !unpowered && !jog_refresh) {
        b |= BLOCK_DRIVER_ALARM;
    }
    /* D-30: PAUSED refuses MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG != 0 incl. refreshes */
    if (c->paused) {
        b |= BLOCK_PAUSED;
    }
    return b;
}

static int sign64(int64_t v) { return (v > 0) - (v < 0); }

static cmd_verdict_t check_motion(const cmd_ctx_t *c, const cmd_req_t *r, bool moving)
{
    const params_t *p = c->p;
    int32_t lo = p->limits.soft_min_um;
    int32_t hi = p->limits.soft_max_um;
    uint32_t a_max = p->motion.a_max_um_s2;
    int dir = 0;
    bool jog_bound = false;
    uint16_t mask;

    /* (3) arguments */
    switch (r->type) {
    case CMD_MOVE_ABS:
        if (r->u.move_abs.target_um < lo || r->u.move_abs.target_um > hi) {
            return verdict(ST_E_RANGE, 0u);
        }
        if (r->u.move_abs.v_um_s < 1u || r->u.move_abs.v_um_s > cmd_v_limit(c, cmd_loaded(c), false)) {
            return verdict(ST_E_RANGE, 4u);
        }
        if (r->u.move_abs.a_um_s2 > a_max) {
            return verdict(ST_E_RANGE, 8u);
        }
        dir = sign64((int64_t)r->u.move_abs.target_um - (int64_t)c->pos_um);
        break;
    case CMD_MOVE_UNTIL_LOAD:
        if (r->u.mul.bound_um < lo || r->u.mul.bound_um > hi || r->u.mul.bound_um == c->pos_um) {
            return verdict(ST_E_RANGE, 0u);   /* outside soft limits or = position (F-B-28) */
        }
        if (r->u.mul.v_um_s < 1u || r->u.mul.v_um_s > cmd_v_limit(c, true, false)) {
            return verdict(ST_E_RANGE, 4u);
        }
        if (r->u.mul.a_um_s2 > a_max) {
            return verdict(ST_E_RANGE, 8u);
        }
        if (r->u.mul.raw_stop < PROTO_RAW_MIN || r->u.mul.raw_stop > PROTO_RAW_MAX) {
            return verdict(ST_E_RANGE, 12u);
        }
        if (r->u.mul.cmp > (uint8_t)CMP_LE) {
            return verdict(ST_E_RANGE, 16u);
        }
        dir = sign64((int64_t)r->u.mul.bound_um - (int64_t)c->pos_um);
        break;
    case CMD_JOG: {
        int64_t v = (int64_t)r->u.jog.v_um_s;
        if (abs64(v) > (int64_t)cmd_v_limit(c, cmd_loaded(c), !c->homed)) {
            return verdict(ST_E_RANGE, 0u);
        }
        if (r->u.jog.a_um_s2 > a_max) {
            return verdict(ST_E_RANGE, 4u);
        }
        if (v == 0) {
            return verdict(ST_OK, 0u);      /* JOG 0: stop a jog / no-op, never refused */
        }
        dir = v > 0 ? 1 : -1;
        if (r->u.jog.bound_um != PROTO_JOG_NO_BOUND) {
            int32_t bnd = r->u.jog.bound_um;
            jog_bound = true;
            if (bnd < lo || bnd > hi || ((int64_t)bnd - (int64_t)c->pos_um) * dir <= 0) {
                return verdict(ST_E_RANGE, 8u);
            }
        }
        break;
    }
    case CMD_HOME:
        if ((r->u.home.flags & (uint8_t)~HOMEF_DEFINED_MASK) != 0u) {
            return verdict(ST_E_RANGE, 0u);
        }
        break;
    default:
        return verdict(ST_E_INTERNAL, INTERNAL_INVARIANT);
    }
    /* (4) state: E_STATE -> E_BUSY -> E_CONFIRM */
    mask = cmd_block_mask(c, r->type, dir, jog_bound);
    if (mask != 0u) {
        return verdict(ST_E_STATE, mask);
    }
    if (c->motion_state == (uint8_t)MS_ENABLING) {
        return verdict(ST_E_BUSY, BUSY_ENABLING);
    }
    if (moving) {
        if (r->type == (uint8_t)CMD_JOG && c->motion_state == (uint8_t)MS_JOG) {
            return verdict(ST_OK, 0u);      /* speed change on the fly + dead-man refresh */
        }
        return verdict(ST_E_BUSY, BUSY_MOTION);
    }
    if (r->type == (uint8_t)CMD_HOME && (r->u.home.flags & HOMEF_LOAD_CONFIRMED) == 0u) {
        if (abs64((int64_t)c->raw - (int64_t)p->safety.zero_raw) > (int64_t)p->home.max_load_raw) {
            return verdict(ST_E_CONFIRM, 0u);
        }
    }
    return verdict(ST_OK, 0u);
}

cmd_verdict_t cmd_check(const cmd_ctx_t *c, uint8_t type, const uint8_t *payload, uint16_t len)
{
    int16_t need = proto_req_len(type);
    cmd_req_t r;
    bool moving = cmd_is_moving(c->motion_state);

    /* (1) TYPE, (2) LEN */
    if (need < 0) {
        return verdict(ST_E_UNKNOWN_CMD, type);
    }
    if (len != (uint16_t)need) {
        return verdict(ST_E_LENGTH, (uint16_t)need);
    }
    (void)payload_decode_request(type, payload, len, &r);

    /* (3) arguments, (4) state, (5) execution - per command */
    switch (type) {
    case CMD_REBOOT:
        if (r.u.reboot.magic != PROTO_REBOOT_MAGIC) {
            return verdict(ST_E_RANGE, 0u);
        }
        return moving ? verdict(ST_E_BUSY, BUSY_MOTION) : verdict(ST_OK, 0u);
    case CMD_GET_ALL_PARAMS: {
        uint32_t pages = (PARAM_COUNT + PROTO_PARAMS_PER_PAGE - 1u) / PROTO_PARAMS_PER_PAGE;
        return (r.u.page.page >= pages) ? verdict(ST_E_RANGE, 0u) : verdict(ST_OK, 0u);
    }
    case CMD_GET_PARAM:
        return (param_find(r.u.get_param.id) != NULL) ? verdict(ST_OK, 0u)
                                                       : verdict(ST_E_PARAM_ID, r.u.get_param.id);
    case CMD_SET_PARAM: {
        const param_meta_t *m = param_find(r.u.set_param.id);
        uint32_t raw = 0u;
        uint8_t st;
        uint16_t other;
        if (m == NULL) {
            return verdict(ST_E_PARAM_ID, r.u.set_param.id);
        }
        st = param_validate_set(m, r.u.set_param.type, r.u.set_param.wire, &raw);
        if (st != PARAM_ST_OK) {
            return verdict(st, m->id);
        }
        if (moving && (m->flags & PARAM_F_MOVING_OK) == 0u) {
            return verdict(ST_E_BUSY, BUSY_MOTION);
        }
        other = param_rules_check_set(c->p, m->id, raw);
        if (other != 0u) {
            return verdict(ST_E_CONFIG, other);
        }
        return verdict(ST_OK, 0u);
    }
    case CMD_SAVE_PARAMS:
    case CMD_LOAD_PARAMS:
    case CMD_DEFAULT_PARAMS:
        if (moving) {
            return verdict(ST_E_BUSY, BUSY_MOTION);
        }
        if (type == (uint8_t)CMD_LOAD_PARAMS && !c->nvm_record_valid) {
            return verdict(ST_E_NVM, NVMD_NO_RECORD);
        }
        return verdict(ST_OK, 0u);
    case CMD_SET_VALID:
        return (r.u.set_valid.valid > 1u) ? verdict(ST_E_RANGE, 0u) : verdict(ST_OK, 0u);
    case CMD_STOP:
        return (r.u.stop.mode > (uint8_t)STOPMODE_CONTROLLED) ? verdict(ST_E_RANGE, 0u)
                                                               : verdict(ST_OK, 0u);
    case CMD_ENABLE: {
        uint16_t mask = cmd_block_mask(c, type, 0, false);
        return (mask != 0u) ? verdict(ST_E_STATE, mask) : verdict(ST_OK, 0u);
    }
    case CMD_DISABLE:
        return moving ? verdict(ST_E_BUSY, BUSY_MOTION) : verdict(ST_OK, 0u);
    case CMD_ESTOP_CLEAR:
        if (c->estop_latched || c->estop_input_open) {
            uint32_t need_ms = c->p->io.estop_release_ms;
            if (c->estop_input_open) {
                return verdict(ST_E_CAUSE_ACTIVE, PROTO_DETAIL_CAUSE_INPUT);
            }
            if (c->estop_closed_ms < need_ms) {
                return verdict(ST_E_CAUSE_ACTIVE, need_ms - c->estop_closed_ms);
            }
        }
        return verdict(ST_OK, 0u);
    case CMD_HALT_CLEAR:
        if (c->halt_latched) {
            uint32_t need_ms = c->p->io.release_ms;
            if (c->stop_btn_active) {
                return verdict(ST_E_CAUSE_ACTIVE, PROTO_DETAIL_CAUSE_INPUT);
            }
            if (c->stop_btn_released_ms < need_ms) {
                return verdict(ST_E_CAUSE_ACTIVE, need_ms - c->stop_btn_released_ms);
            }
        }
        return verdict(ST_OK, 0u);
    case CMD_RESUME: {
        /* D-31: only ESTOP, HALT and FAULT are evaluated; never E_BUSY */
        uint16_t b = 0u;
        if (c->estop_latched || c->estop_input_open) {
            b |= BLOCK_ESTOP;
        }
        if (c->halt_latched) {
            b |= BLOCK_HALT;
        }
        if (c->faults != 0u) {
            b |= BLOCK_FAULT;
        }
        return (b != 0u) ? verdict(ST_E_STATE, b) : verdict(ST_OK, 0u);
    }
    case CMD_FAULT_CLEAR: {
        uint16_t remaining = (uint16_t)(c->faults & c->fault_causes & (uint16_t)~FAULT_LOAD_LIMIT);
        return (remaining != 0u) ? verdict(ST_E_CAUSE_ACTIVE, remaining) : verdict(ST_OK, 0u);
    }
    case CMD_MOVE_ABS:
    case CMD_MOVE_UNTIL_LOAD:
    case CMD_JOG:
    case CMD_HOME:
        return check_motion(c, &r, moving);
    default:
        return verdict(ST_OK, 0u);          /* PING, GET_INFO, GET_STATUS, STREAM_*, HALT, PAUSE */
    }
}
