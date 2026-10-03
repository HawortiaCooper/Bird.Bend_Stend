/* Hard rules H1..H5 (ICD §11.4). Implements: FW-CFG-003, SAF-FW-010, SAF-FW-012 */
#include "param_rules.h"

uint32_t param_rules_sps(uint8_t rate_sps)
{
    return (rate_sps == (uint8_t)AFE_RATE_SPS_SPS80) ? 80u : 10u;
}

static bool h1(const params_t *p) { return p->limits.soft_min_um < p->limits.soft_max_um; }
static bool h2(const params_t *p) { return p->safety.load_raw_min < p->safety.load_raw_max; }
static bool h3(const params_t *p)
{
    uint64_t w = (uint64_t)p->motion.pulse_high_ns + (uint64_t)p->motion.pulse_low_min_ns;
    return (uint64_t)p->motion.max_step_rate_hz * w <= 1000000000ull;
}
static bool h4(const params_t *p) { return p->motion.v_max_load_um_s <= p->motion.v_max_travel_um_s; }
static bool h5(const params_t *p)
{
    return (uint32_t)p->afe.timeout_ms * param_rules_sps(p->afe.rate_sps) >= 2000u;
}

uint16_t param_rules_check_set(const params_t *cur, uint16_t id, uint32_t raw)
{
    const param_meta_t *m = param_find(id);
    params_t t;
    if (m == NULL) {
        return 0u;
    }
    t = *cur;
    param_set_raw(&t, m, raw);
    switch (id) {
    case PID_LIMITS_SOFT_MIN_UM:
        return h1(&t) ? 0u : (uint16_t)PID_LIMITS_SOFT_MAX_UM;
    case PID_LIMITS_SOFT_MAX_UM:
        return h1(&t) ? 0u : (uint16_t)PID_LIMITS_SOFT_MIN_UM;
    case PID_SAFETY_LOAD_RAW_MIN:
        return h2(&t) ? 0u : (uint16_t)PID_SAFETY_LOAD_RAW_MAX;
    case PID_SAFETY_LOAD_RAW_MAX:
        return h2(&t) ? 0u : (uint16_t)PID_SAFETY_LOAD_RAW_MIN;
    case PID_MOTION_MAX_STEP_RATE_HZ:
        return h3(&t) ? 0u : (uint16_t)PID_MOTION_PULSE_HIGH_NS;
    case PID_MOTION_PULSE_HIGH_NS:
    case PID_MOTION_PULSE_LOW_MIN_NS:
        return h3(&t) ? 0u : (uint16_t)PID_MOTION_MAX_STEP_RATE_HZ;
    case PID_MOTION_V_MAX_LOAD_UM_S:
        return h4(&t) ? 0u : (uint16_t)PID_MOTION_V_MAX_TRAVEL_UM_S;
    case PID_MOTION_V_MAX_TRAVEL_UM_S:
        return h4(&t) ? 0u : (uint16_t)PID_MOTION_V_MAX_LOAD_UM_S;
    case PID_AFE_TIMEOUT_MS:
        return h5(&t) ? 0u : (uint16_t)PID_AFE_RATE_SPS;
    case PID_AFE_RATE_SPS:
        return h5(&t) ? 0u : (uint16_t)PID_AFE_TIMEOUT_MS;
    default:
        return 0u;
    }
}

bool param_rules_check_all(const params_t *p)
{
    return h1(p) && h2(p) && h3(p) && h4(p) && h5(p);
}
