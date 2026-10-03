/* GENERATED - do not edit.
 * Source : 00_System/specs/params.yaml (dict_version 1, schema 1)
 * Tool   : 00_System/tools/gen_params.py
 * Hash   : PARAM_DICT_HASH = 0x13961802
 * Implements: FW-CFG-001, IF-010
 */
#include "params_gen.h"

#include <string.h>

#if !PARAMS_GEN_WITH_KEYS
#define PKEY(k)
#else
#define PKEY(k) , k
#endif

#define PM(id, type, flags, member, size, mn, mx, df, key) \
    { (uint16_t)(id), (uint8_t)(type), (uint8_t)(flags), \
      (uint16_t)offsetof(params_t, member), (uint8_t)(size), \
      (uint32_t)(mn), (uint32_t)(mx), (uint32_t)(df) PKEY(key) }

const param_meta_t PARAM_TABLE[PARAM_COUNT] = {
    /* afe.gain_channel: min A128, max A64, default A128  */
    PM(PID_AFE_GAIN_CHANNEL, PARAM_T_ENUM, PARAM_F_NVM, afe.gain_channel, 1, 0x00000000u, 0x00000002u, 0x00000000u, "afe.gain_channel"),
    /* afe.rate_sps: min SPS10, max SPS80, default SPS80  */
    PM(PID_AFE_RATE_SPS, PARAM_T_ENUM, PARAM_F_NVM, afe.rate_sps, 1, 0x00000000u, 0x00000001u, 0x00000001u, "afe.rate_sps"),
    /* afe.rate_tol_pct: min 5, max 50, default 20 % */
    PM(PID_AFE_RATE_TOL_PCT, PARAM_T_U8, PARAM_F_MOVING_OK|PARAM_F_NVM, afe.rate_tol_pct, 1, 0x00000005u, 0x00000032u, 0x00000014u, "afe.rate_tol_pct"),
    /* afe.settle_discard: min 0, max 20, default 4 samples */
    PM(PID_AFE_SETTLE_DISCARD, PARAM_T_U8, PARAM_F_MOVING_OK|PARAM_F_NVM, afe.settle_discard, 1, 0x00000000u, 0x00000014u, 0x00000004u, "afe.settle_discard"),
    /* afe.timeout_ms: min 20, max 1000, default 100 ms */
    PM(PID_AFE_TIMEOUT_MS, PARAM_T_U16, PARAM_F_MOVING_OK|PARAM_F_NVM, afe.timeout_ms, 2, 0x00000014u, 0x000003E8u, 0x00000064u, "afe.timeout_ms"),
    /* motion.steps_per_mm: min 100, max 100000, default 160 steps/mm */
    PM(PID_MOTION_STEPS_PER_MM, PARAM_T_F32, PARAM_F_NVM, motion.steps_per_mm, 4, 0x42C80000u, 0x47C35000u, 0x43200000u, "motion.steps_per_mm"),
    /* motion.pul_invert: min false, max true, default false  */
    PM(PID_MOTION_PUL_INVERT, PARAM_T_BOOL, PARAM_F_NVM|PARAM_F_REBOOT, motion.pul_invert, 1, 0x00000000u, 0x00000001u, 0x00000000u, "motion.pul_invert"),
    /* motion.dir_invert: min false, max true, default false  */
    PM(PID_MOTION_DIR_INVERT, PARAM_T_BOOL, PARAM_F_NVM, motion.dir_invert, 1, 0x00000000u, 0x00000001u, 0x00000000u, "motion.dir_invert"),
    /* motion.ena_invert: min false, max true, default false  */
    PM(PID_MOTION_ENA_INVERT, PARAM_T_BOOL, PARAM_F_NVM|PARAM_F_REBOOT, motion.ena_invert, 1, 0x00000000u, 0x00000001u, 0x00000000u, "motion.ena_invert"),
    /* motion.pulse_high_ns: min 2500, max 100000, default 10000 ns */
    PM(PID_MOTION_PULSE_HIGH_NS, PARAM_T_U32, PARAM_F_NVM, motion.pulse_high_ns, 4, 0x000009C4u, 0x000186A0u, 0x00002710u, "motion.pulse_high_ns"),
    /* motion.pulse_low_min_ns: min 2500, max 100000, default 10000 ns */
    PM(PID_MOTION_PULSE_LOW_MIN_NS, PARAM_T_U32, PARAM_F_NVM, motion.pulse_low_min_ns, 4, 0x000009C4u, 0x000186A0u, 0x00002710u, "motion.pulse_low_min_ns"),
    /* motion.max_step_rate_hz: min 100, max 100000, default 50000 Hz */
    PM(PID_MOTION_MAX_STEP_RATE_HZ, PARAM_T_U32, PARAM_F_NVM, motion.max_step_rate_hz, 4, 0x00000064u, 0x000186A0u, 0x0000C350u, "motion.max_step_rate_hz"),
    /* motion.dir_setup_us: min 5, max 1000, default 20 us */
    PM(PID_MOTION_DIR_SETUP_US, PARAM_T_U16, PARAM_F_NVM, motion.dir_setup_us, 2, 0x00000005u, 0x000003E8u, 0x00000014u, "motion.dir_setup_us"),
    /* motion.ena_settle_ms: min 0, max 2000, default 500 ms */
    PM(PID_MOTION_ENA_SETTLE_MS, PARAM_T_U16, PARAM_F_NVM, motion.ena_settle_ms, 2, 0x00000000u, 0x000007D0u, 0x000001F4u, "motion.ena_settle_ms"),
    /* motion.v_max_travel_um_s: min 1, max 250000, default 30000 um/s */
    PM(PID_MOTION_V_MAX_TRAVEL_UM_S, PARAM_T_U32, PARAM_F_NVM, motion.v_max_travel_um_s, 4, 0x00000001u, 0x0003D090u, 0x00007530u, "motion.v_max_travel_um_s"),
    /* motion.v_max_load_um_s: min 1, max 250000, default 20000 um/s */
    PM(PID_MOTION_V_MAX_LOAD_UM_S, PARAM_T_U32, PARAM_F_NVM, motion.v_max_load_um_s, 4, 0x00000001u, 0x0003D090u, 0x00004E20u, "motion.v_max_load_um_s"),
    /* motion.a_max_um_s2: min 1000, max 10000000, default 100000 um/s2 */
    PM(PID_MOTION_A_MAX_UM_S2, PARAM_T_U32, PARAM_F_NVM, motion.a_max_um_s2, 4, 0x000003E8u, 0x00989680u, 0x000186A0u, "motion.a_max_um_s2"),
    /* motion.a_stop_um_s2: min 10000, max 10000000, default 1000000 um/s2 */
    PM(PID_MOTION_A_STOP_UM_S2, PARAM_T_U32, PARAM_F_NVM, motion.a_stop_um_s2, 4, 0x00002710u, 0x00989680u, 0x000F4240u, "motion.a_stop_um_s2"),
    /* motion.v_unhomed_um_s: min 1, max 20000, default 2000 um/s */
    PM(PID_MOTION_V_UNHOMED_UM_S, PARAM_T_U32, PARAM_F_NVM, motion.v_unhomed_um_s, 4, 0x00000001u, 0x00004E20u, 0x000007D0u, "motion.v_unhomed_um_s"),
    /* motion.jog_timeout_ms: min 50, max 1000, default 250 ms */
    PM(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, PARAM_F_MOVING_OK|PARAM_F_NVM, motion.jog_timeout_ms, 2, 0x00000032u, 0x000003E8u, 0x000000FAu, "motion.jog_timeout_ms"),
    /* limits.soft_min_um: min -10000, max 400000, default 500 um */
    PM(PID_LIMITS_SOFT_MIN_UM, PARAM_T_I32, PARAM_F_NVM, limits.soft_min_um, 4, 0xFFFFD8F0u, 0x00061A80u, 0x000001F4u, "limits.soft_min_um"),
    /* limits.soft_max_um: min 0, max 400000, default 290000 um */
    PM(PID_LIMITS_SOFT_MAX_UM, PARAM_T_I32, PARAM_F_NVM, limits.soft_max_um, 4, 0x00000000u, 0x00061A80u, 0x00046CD0u, "limits.soft_max_um"),
    /* home.ref_switch: min START, max END, default START  */
    PM(PID_HOME_REF_SWITCH, PARAM_T_ENUM, PARAM_F_NVM, home.ref_switch, 1, 0x00000000u, 0x00000001u, 0x00000000u, "home.ref_switch"),
    /* home.v_fast_um_s: min 10, max 20000, default 5000 um/s */
    PM(PID_HOME_V_FAST_UM_S, PARAM_T_U32, PARAM_F_NVM, home.v_fast_um_s, 4, 0x0000000Au, 0x00004E20u, 0x00001388u, "home.v_fast_um_s"),
    /* home.v_slow_um_s: min 10, max 20000, default 500 um/s */
    PM(PID_HOME_V_SLOW_UM_S, PARAM_T_U32, PARAM_F_NVM, home.v_slow_um_s, 4, 0x0000000Au, 0x00004E20u, 0x000001F4u, "home.v_slow_um_s"),
    /* home.a_um_s2: min 1000, max 1000000, default 50000 um/s2 */
    PM(PID_HOME_A_UM_S2, PARAM_T_U32, PARAM_F_NVM, home.a_um_s2, 4, 0x000003E8u, 0x000F4240u, 0x0000C350u, "home.a_um_s2"),
    /* home.backoff_um: min 0, max 20000, default 2000 um */
    PM(PID_HOME_BACKOFF_UM, PARAM_T_U32, PARAM_F_NVM, home.backoff_um, 4, 0x00000000u, 0x00004E20u, 0x000007D0u, "home.backoff_um"),
    /* home.offset_um: min 0, max 20000, default 1000 um */
    PM(PID_HOME_OFFSET_UM, PARAM_T_U32, PARAM_F_NVM, home.offset_um, 4, 0x00000000u, 0x00004E20u, 0x000003E8u, "home.offset_um"),
    /* home.max_travel_um: min 1000, max 500000, default 360000 um */
    PM(PID_HOME_MAX_TRAVEL_UM, PARAM_T_U32, PARAM_F_NVM, home.max_travel_um, 4, 0x000003E8u, 0x0007A120u, 0x00057E40u, "home.max_travel_um"),
    /* home.max_load_raw: min 0, max 7151121, default 322123 counts */
    PM(PID_HOME_MAX_LOAD_RAW, PARAM_T_I32, PARAM_F_NVM, home.max_load_raw, 4, 0x00000000u, 0x006D1E11u, 0x0004EA4Bu, "home.max_load_raw"),
    /* home.drift_tol_um: min 10, max 10000, default 200 um */
    PM(PID_HOME_DRIFT_TOL_UM, PARAM_T_U32, PARAM_F_NVM, home.drift_tol_um, 4, 0x0000000Au, 0x00002710u, 0x000000C8u, "home.drift_tol_um"),
    /* safety.load_raw_max: min -7151121, max 7151121, default 7022271 counts */
    PM(PID_SAFETY_LOAD_RAW_MAX, PARAM_T_I32, PARAM_F_MOVING_OK, safety.load_raw_max, 4, 0xFF92E1EFu, 0x006D1E11u, 0x006B26BFu, "safety.load_raw_max"),
    /* safety.load_raw_min: min -7151121, max 7151121, default -7022271 counts */
    PM(PID_SAFETY_LOAD_RAW_MIN, PARAM_T_I32, PARAM_F_MOVING_OK, safety.load_raw_min, 4, 0xFF92E1EFu, 0x006D1E11u, 0xFF94D941u, "safety.load_raw_min"),
    /* safety.load_trip_samples: min 1, max 4, default 1 samples */
    PM(PID_SAFETY_LOAD_TRIP_SAMPLES, PARAM_T_U8, PARAM_F_MOVING_OK|PARAM_F_NVM, safety.load_trip_samples, 1, 0x00000001u, 0x00000004u, 0x00000001u, "safety.load_trip_samples"),
    /* safety.load_regrow_raw: min 0, max 1288490, default 128849 counts */
    PM(PID_SAFETY_LOAD_REGROW_RAW, PARAM_T_I32, PARAM_F_MOVING_OK|PARAM_F_NVM, safety.load_regrow_raw, 4, 0x00000000u, 0x0013A92Au, 0x0001F751u, "safety.load_regrow_raw"),
    /* safety.zero_raw: min -8388608, max 8388607, default 0 counts */
    PM(PID_SAFETY_ZERO_RAW, PARAM_T_I32, PARAM_F_MOVING_OK, safety.zero_raw, 4, 0xFF800000u, 0x007FFFFFu, 0x00000000u, "safety.zero_raw"),
    /* safety.release_band_raw: min 0, max 644245, default 128849 counts */
    PM(PID_SAFETY_RELEASE_BAND_RAW, PARAM_T_I32, PARAM_F_MOVING_OK|PARAM_F_NVM, safety.release_band_raw, 4, 0x00000000u, 0x0009D495u, 0x0001F751u, "safety.release_band_raw"),
    /* safety.idle_disable_s: min 0, max 65535, default 600 s */
    PM(PID_SAFETY_IDLE_DISABLE_S, PARAM_T_U16, PARAM_F_MOVING_OK|PARAM_F_NVM, safety.idle_disable_s, 2, 0x00000000u, 0x0000FFFFu, 0x00000258u, "safety.idle_disable_s"),
    /* safety.link_timeout_ms: min 200, max 5000, default 1000 ms */
    PM(PID_SAFETY_LINK_TIMEOUT_MS, PARAM_T_U16, PARAM_F_MOVING_OK|PARAM_F_NVM, safety.link_timeout_ms, 2, 0x000000C8u, 0x00001388u, 0x000003E8u, "safety.link_timeout_ms"),
    /* io.release_ms: min 5, max 200, default 20 ms */
    PM(PID_IO_RELEASE_MS, PARAM_T_U8, PARAM_F_NVM, io.release_ms, 1, 0x00000005u, 0x000000C8u, 0x00000014u, "io.release_ms"),
    /* io.estop_release_ms: min 50, max 2000, default 100 ms */
    PM(PID_IO_ESTOP_RELEASE_MS, PARAM_T_U16, PARAM_F_NVM, io.estop_release_ms, 2, 0x00000032u, 0x000007D0u, 0x00000064u, "io.estop_release_ms"),
    /* io.stop_active_level: min OPEN_ACTIVE, max CLOSED_ACTIVE, default OPEN_ACTIVE  */
    PM(PID_IO_STOP_ACTIVE_LEVEL, PARAM_T_ENUM, PARAM_F_NVM, io.stop_active_level, 1, 0x00000000u, 0x00000001u, 0x00000000u, "io.stop_active_level"),
    /* io.pause_active_level: min OPEN_ACTIVE, max CLOSED_ACTIVE, default CLOSED_ACTIVE  */
    PM(PID_IO_PAUSE_ACTIVE_LEVEL, PARAM_T_ENUM, PARAM_F_NVM, io.pause_active_level, 1, 0x00000000u, 0x00000001u, 0x00000001u, "io.pause_active_level"),
    /* drv.alm_active_level: min HIGH_ACTIVE, max LOW_ACTIVE, default HIGH_ACTIVE  */
    PM(PID_DRV_ALM_ACTIVE_LEVEL, PARAM_T_ENUM, PARAM_F_NVM, drv.alm_active_level, 1, 0x00000000u, 0x00000001u, 0x00000000u, "drv.alm_active_level"),
    /* drv.pend_active_level: min HIGH_ACTIVE, max LOW_ACTIVE, default HIGH_ACTIVE  */
    PM(PID_DRV_PEND_ACTIVE_LEVEL, PARAM_T_ENUM, PARAM_F_NVM, drv.pend_active_level, 1, 0x00000000u, 0x00000001u, 0x00000000u, "drv.pend_active_level"),
    /* drv.pend_timeout_ms: min 0, max 5000, default 200 ms */
    PM(PID_DRV_PEND_TIMEOUT_MS, PARAM_T_U16, PARAM_F_MOVING_OK|PARAM_F_NVM, drv.pend_timeout_ms, 2, 0x00000000u, 0x00001388u, 0x000000C8u, "drv.pend_timeout_ms"),
    /* drv.pwr_sense_enable: min false, max true, default true  */
    PM(PID_DRV_PWR_SENSE_ENABLE, PARAM_T_BOOL, PARAM_F_NVM|PARAM_F_REBOOT, drv.pwr_sense_enable, 1, 0x00000000u, 0x00000001u, 0x00000001u, "drv.pwr_sense_enable"),
    /* stream.fallback_hz: min 1, max 80, default 10 Hz */
    PM(PID_STREAM_FALLBACK_HZ, PARAM_T_U8, PARAM_F_MOVING_OK|PARAM_F_NVM, stream.fallback_hz, 1, 0x00000001u, 0x00000050u, 0x0000000Au, "stream.fallback_hz"),
};

const param_meta_t *param_find(uint16_t id)
{
    uint16_t lo = 0u, hi = (uint16_t)PARAM_COUNT;
    while (lo < hi) {
        uint16_t mid = (uint16_t)((lo + hi) / 2u);
        uint16_t mid_id = PARAM_TABLE[mid].id;
        if (mid_id == id) {
            return &PARAM_TABLE[mid];
        }
        if (mid_id < id) {
            lo = (uint16_t)(mid + 1u);
        } else {
            hi = mid;
        }
    }
    return NULL;
}

uint32_t param_get_raw(const params_t *p, const param_meta_t *m)
{
    const uint8_t *src = (const uint8_t *)p + m->offset;
    switch (m->type) {
    case PARAM_T_U8:
    case PARAM_T_ENUM: { uint8_t v; memcpy(&v, src, 1); return v; }
    case PARAM_T_BOOL: { bool v; memcpy(&v, src, sizeof v); return v ? 1u : 0u; }
    case PARAM_T_I8:   { int8_t v; memcpy(&v, src, 1); return (uint32_t)(int32_t)v; }
    case PARAM_T_U16:  { uint16_t v; memcpy(&v, src, 2); return v; }
    case PARAM_T_I16:  { int16_t v; memcpy(&v, src, 2); return (uint32_t)(int32_t)v; }
    case PARAM_T_U32:
    case PARAM_T_I32:
    case PARAM_T_F32:  { uint32_t v; memcpy(&v, src, 4); return v; }
    default: return 0u;
    }
}

void param_set_raw(params_t *p, const param_meta_t *m, uint32_t raw)
{
    uint8_t *dst = (uint8_t *)p + m->offset;
    switch (m->type) {
    case PARAM_T_U8:
    case PARAM_T_ENUM:
    case PARAM_T_I8:   { uint8_t v = (uint8_t)raw; memcpy(dst, &v, 1); break; }
    case PARAM_T_BOOL: { bool v = (raw != 0u); memcpy(dst, &v, sizeof v); break; }
    case PARAM_T_U16:
    case PARAM_T_I16:  { uint16_t v = (uint16_t)raw; memcpy(dst, &v, 2); break; }
    case PARAM_T_U32:
    case PARAM_T_I32:
    case PARAM_T_F32:  { memcpy(dst, &raw, 4); break; }
    default: break;
    }
}

void param_raw_to_wire(const param_meta_t *m, uint32_t raw, uint8_t wire[4])
{
    uint8_t i;
    for (i = 0u; i < 4u; i++) {
        wire[i] = (i < m->size) ? (uint8_t)(raw >> (8u * i)) : 0u;
    }
}

static float raw_to_f32(uint32_t raw)
{
    float f;
    memcpy(&f, &raw, 4);
    return f;
}

bool param_raw_in_range(const param_meta_t *m, uint32_t raw)
{
    switch (m->type) {
    case PARAM_T_F32: {
        float v = raw_to_f32(raw);
        if (v != v) {
            return false; /* NaN */
        }
        return (v >= raw_to_f32(m->min_raw)) && (v <= raw_to_f32(m->max_raw));
    }
    case PARAM_T_I8:
    case PARAM_T_I16:
    case PARAM_T_I32:
        return ((int32_t)raw >= (int32_t)m->min_raw) && ((int32_t)raw <= (int32_t)m->max_raw);
    default:
        return (raw >= m->min_raw) && (raw <= m->max_raw);
    }
}

uint8_t param_validate_set(const param_meta_t *m, uint8_t wire_type,
                           const uint8_t wire[4], uint32_t *raw_out)
{
    uint32_t raw = 0u;
    uint8_t i;
    if (wire_type != m->type) {
        return PARAM_ST_E_TYPE;
    }
    for (i = 0u; i < 4u; i++) {
        if (i < m->size) {
            raw |= (uint32_t)wire[i] << (8u * i);
        } else if (wire[i] != 0u) {
            return PARAM_ST_E_RANGE; /* padding must be zero */
        }
    }
    if (m->type == PARAM_T_I8) {
        raw = (uint32_t)(int32_t)(int8_t)(uint8_t)raw;
    } else if (m->type == PARAM_T_I16) {
        raw = (uint32_t)(int32_t)(int16_t)(uint16_t)raw;
    }
    if (m->type == PARAM_T_F32 && ((raw & 0x7F800000u) == 0x7F800000u)) {
        return PARAM_ST_E_RANGE; /* NaN or Inf */
    }
    if (!param_raw_in_range(m, raw)) {
        return PARAM_ST_E_RANGE;
    }
    *raw_out = raw;
    return PARAM_ST_OK;
}

void params_set_defaults(params_t *p)
{
    uint16_t i;
    memset(p, 0, sizeof *p);
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        param_set_raw(p, &PARAM_TABLE[i], PARAM_TABLE[i].def_raw);
    }
}

uint16_t params_sanitize(params_t *p)
{
    uint16_t i, fixed = 0u;
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        const param_meta_t *m = &PARAM_TABLE[i];
        if (!param_raw_in_range(m, param_get_raw(p, m))) {
            param_set_raw(p, m, m->def_raw);
            fixed++;
        }
    }
    return fixed;
}
