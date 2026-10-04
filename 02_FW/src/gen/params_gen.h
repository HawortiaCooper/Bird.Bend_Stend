/* GENERATED - do not edit.
 * Source : 00_System/specs/params.yaml (dict_version 5, schema 1)
 * Tool   : 00_System/tools/gen_params.py
 * Hash   : PARAM_DICT_HASH = 0xB7B0263F
 * Implements: FW-CFG-001, IF-010
 */
#ifndef PARAMS_GEN_H
#define PARAMS_GEN_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifndef PARAMS_GEN_WITH_KEYS
#define PARAMS_GEN_WITH_KEYS 1
#endif

#define PARAM_DICT_HASH       0xB7B0263FUL
#define PARAM_DICT_VERSION    5u
#define PARAM_SCHEMA_VERSION  1u
#define PARAM_COUNT           48u

/* Wire type codes (ICD §7.5). */
typedef enum {
    PARAM_T_U8    = 1,
    PARAM_T_I8    = 2,
    PARAM_T_U16   = 3,
    PARAM_T_I16   = 4,
    PARAM_T_U32   = 5,
    PARAM_T_I32   = 6,
    PARAM_T_F32   = 7,
    PARAM_T_BOOL  = 8,
    PARAM_T_ENUM  = 9,
} param_type_t;

/* param_meta_t.flags */
#define PARAM_F_MOVING_OK 0x01u /* SET_PARAM allowed while moving */
#define PARAM_F_NVM       0x02u /* persisted by SAVE_PARAMS */
#define PARAM_F_REBOOT    0x04u /* effective after SAVE + REBOOT */

/* ICD status codes returned by param_validate_set(). */
#define PARAM_ST_OK        0u
#define PARAM_ST_E_TYPE    4u
#define PARAM_ST_E_RANGE   5u

/* Parameter IDs (stable, ICD Appendix A). */
typedef enum {
    PID_AFE_GAIN_CHANNEL               = 0x0101, /* enum */
    PID_AFE_RATE_SPS                   = 0x0102, /* enum */
    PID_AFE_RATE_TOL_PCT               = 0x0103, /* u8 */
    PID_AFE_SETTLE_DISCARD             = 0x0104, /* u8 */
    PID_AFE_TIMEOUT_MS                 = 0x0105, /* u16 */
    PID_MOTION_STEPS_PER_MM            = 0x0201, /* f32 */
    PID_MOTION_PUL_INVERT              = 0x0202, /* bool */
    PID_MOTION_DIR_INVERT              = 0x0203, /* bool */
    PID_MOTION_ENA_INVERT              = 0x0204, /* bool */
    PID_MOTION_PULSE_HIGH_NS           = 0x0205, /* u32 */
    PID_MOTION_PULSE_LOW_MIN_NS        = 0x0206, /* u32 */
    PID_MOTION_MAX_STEP_RATE_HZ        = 0x0207, /* u32 */
    PID_MOTION_DIR_SETUP_US            = 0x0208, /* u16 */
    PID_MOTION_ENA_SETTLE_MS           = 0x0209, /* u16 */
    PID_MOTION_V_MAX_TRAVEL_UM_S       = 0x020A, /* u32 */
    PID_MOTION_V_MAX_LOAD_UM_S         = 0x020B, /* u32 */
    PID_MOTION_A_MAX_UM_S2             = 0x020C, /* u32 */
    PID_MOTION_A_STOP_UM_S2            = 0x020D, /* u32 */
    PID_MOTION_V_UNHOMED_UM_S          = 0x020E, /* u32 */
    PID_MOTION_JOG_TIMEOUT_MS          = 0x020F, /* u16 */
    PID_LIMITS_SOFT_MIN_UM             = 0x0301, /* i32 */
    PID_LIMITS_SOFT_MAX_UM             = 0x0302, /* i32 */
    PID_HOME_V_FAST_UM_S               = 0x0402, /* u32 */
    PID_HOME_V_SLOW_UM_S               = 0x0403, /* u32 */
    PID_HOME_A_UM_S2                   = 0x0404, /* u32 */
    PID_HOME_BACKOFF_UM                = 0x0405, /* u32 */
    PID_HOME_OFFSET_UM                 = 0x0406, /* u32 */
    PID_HOME_MAX_TRAVEL_UM             = 0x0407, /* u32 */
    PID_HOME_MAX_LOAD_RAW              = 0x0408, /* i32 */
    PID_HOME_DRIFT_TOL_UM              = 0x0409, /* u32 */
    PID_SAFETY_LOAD_RAW_MAX            = 0x0501, /* i32 */
    PID_SAFETY_LOAD_RAW_MIN            = 0x0502, /* i32 */
    PID_SAFETY_LOAD_TRIP_SAMPLES       = 0x0503, /* u8 */
    PID_SAFETY_LOAD_REGROW_RAW         = 0x0504, /* i32 */
    PID_SAFETY_ZERO_RAW                = 0x0505, /* i32 */
    PID_SAFETY_RELEASE_BAND_RAW        = 0x0506, /* i32 */
    PID_SAFETY_IDLE_DISABLE_S          = 0x0507, /* u16 */
    PID_SAFETY_LINK_TIMEOUT_MS         = 0x0508, /* u16 */
    PID_IO_RELEASE_MS                  = 0x0601, /* u8 */
    PID_IO_ESTOP_RELEASE_MS            = 0x0602, /* u16 */
    PID_IO_PAUSE_ACTIVE_LEVEL          = 0x0604, /* enum */
    PID_DRV_ALM_ACTIVE_LEVEL           = 0x0701, /* enum */
    PID_DRV_PEND_ACTIVE_LEVEL          = 0x0702, /* enum */
    PID_DRV_PEND_TIMEOUT_MS            = 0x0703, /* u16 */
    PID_DRV_PWR_SENSE_ENABLE           = 0x0704, /* bool */
    PID_DRV_K1_WELD_MS                 = 0x0705, /* u16 */
    PID_DRV_K1_CHECK_ENABLE            = 0x0706, /* bool */
    PID_STREAM_FALLBACK_HZ             = 0x0801, /* u8 */
} param_id_t;

/* Enumerations. */
typedef enum {
    AFE_GAIN_CHANNEL_A128 = 0, /* Channel A, gain 128 (25 pulses) */
    AFE_GAIN_CHANNEL_B32 = 1, /* Channel B, gain 32 (26 pulses) */
    AFE_GAIN_CHANNEL_A64 = 2, /* Channel A, gain 64 (27 pulses) */
} afe_gain_channel_t;

typedef enum {
    AFE_RATE_SPS_SPS10 = 0, /* 10 SPS (RATE pin low) */
    AFE_RATE_SPS_SPS80 = 1, /* 80 SPS (RATE pin high) */
} afe_rate_sps_t;

typedef enum {
    IO_PAUSE_ACTIVE_LEVEL_OPEN_ACTIVE = 0, /* NC contact: open / high = pressed */
    IO_PAUSE_ACTIVE_LEVEL_CLOSED_ACTIVE = 1, /* NO contact: closed / low = pressed */
} io_pause_active_level_t;

typedef enum {
    DRV_ALM_ACTIVE_LEVEL_HIGH_ACTIVE = 0, /* pin high = alarm (Leadshine default, fail-safe) */
    DRV_ALM_ACTIVE_LEVEL_LOW_ACTIVE = 1, /* pin low = alarm */
} drv_alm_active_level_t;

typedef enum {
    DRV_PEND_ACTIVE_LEVEL_HIGH_ACTIVE = 0, /* pin high = in position (Leadshine default) */
    DRV_PEND_ACTIVE_LEVEL_LOW_ACTIVE = 1, /* pin low = in position */
} drv_pend_active_level_t;

/* Parameter storage (RAM image). Member order = params.yaml order. */
typedef struct {
    uint8_t   gain_channel;
    uint8_t   rate_sps;
    uint8_t   rate_tol_pct; /* % */
    uint8_t   settle_discard; /* samples */
    uint16_t  timeout_ms; /* ms */
} params_afe_t;

typedef struct {
    float     steps_per_mm; /* steps/mm */
    bool      pul_invert;
    bool      dir_invert;
    bool      ena_invert;
    uint32_t  pulse_high_ns; /* ns */
    uint32_t  pulse_low_min_ns; /* ns */
    uint32_t  max_step_rate_hz; /* Hz */
    uint16_t  dir_setup_us; /* us */
    uint16_t  ena_settle_ms; /* ms */
    uint32_t  v_max_travel_um_s; /* um/s */
    uint32_t  v_max_load_um_s; /* um/s */
    uint32_t  a_max_um_s2; /* um/s2 */
    uint32_t  a_stop_um_s2; /* um/s2 */
    uint32_t  v_unhomed_um_s; /* um/s */
    uint16_t  jog_timeout_ms; /* ms */
} params_motion_t;

typedef struct {
    int32_t   soft_min_um; /* um */
    int32_t   soft_max_um; /* um */
} params_limits_t;

typedef struct {
    uint32_t  v_fast_um_s; /* um/s */
    uint32_t  v_slow_um_s; /* um/s */
    uint32_t  a_um_s2; /* um/s2 */
    uint32_t  backoff_um; /* um */
    uint32_t  offset_um; /* um */
    uint32_t  max_travel_um; /* um */
    int32_t   max_load_raw; /* counts */
    uint32_t  drift_tol_um; /* um */
} params_home_t;

typedef struct {
    int32_t   load_raw_max; /* counts */
    int32_t   load_raw_min; /* counts */
    uint8_t   load_trip_samples; /* samples */
    int32_t   load_regrow_raw; /* counts */
    int32_t   zero_raw; /* counts */
    int32_t   release_band_raw; /* counts */
    uint16_t  idle_disable_s; /* s */
    uint16_t  link_timeout_ms; /* ms */
} params_safety_t;

typedef struct {
    uint8_t   release_ms; /* ms */
    uint16_t  estop_release_ms; /* ms */
    uint8_t   pause_active_level;
} params_io_t;

typedef struct {
    uint8_t   alm_active_level;
    uint8_t   pend_active_level;
    uint16_t  pend_timeout_ms; /* ms */
    bool      pwr_sense_enable;
    uint16_t  k1_weld_ms; /* ms */
    bool      k1_check_enable;
} params_drv_t;

typedef struct {
    uint8_t   fallback_hz; /* Hz */
} params_stream_t;

typedef struct {
    params_afe_t afe;
    params_motion_t motion;
    params_limits_t limits;
    params_home_t home;
    params_safety_t safety;
    params_io_t io;
    params_drv_t drv;
    params_stream_t stream;
} params_t;

/* Metadata table entry. Raw values: uint32 bit pattern; signed types are sign-
 * extended, f32 = IEEE-754 binary32 bits, bool/enum = 0..255. */
typedef struct {
    uint16_t id;
    uint8_t  type;    /* param_type_t */
    uint8_t  flags;   /* PARAM_F_* */
    uint16_t offset;  /* byte offset in params_t */
    uint8_t  size;    /* bytes in params_t and on the wire (1, 2, 4) */
    uint32_t min_raw;
    uint32_t max_raw;
    uint32_t def_raw;
#if PARAMS_GEN_WITH_KEYS
    const char *key;  /* dotted key, e.g. "afe.gain_channel" */
#endif
} param_meta_t;

/** Metadata of all parameters, sorted by ascending id. */
extern const param_meta_t PARAM_TABLE[PARAM_COUNT];

/** Binary search by id; NULL if unknown. */
const param_meta_t *param_find(uint16_t id);

/** Index of `m` in PARAM_TABLE (for paging GET_ALL_PARAMS). */
static inline uint16_t param_index(const param_meta_t *m) { return (uint16_t)(m - PARAM_TABLE); }

/** Write every default into `p` (whole struct is zeroed first). */
void params_set_defaults(params_t *p);

/** Current value as raw uint32 (see param_meta_t). */
uint32_t param_get_raw(const params_t *p, const param_meta_t *m);

/** Store a raw value without any check (use after param_validate_set). */
void param_set_raw(params_t *p, const param_meta_t *m, uint32_t raw);

/** Raw value -> 4-byte little-endian wire value (low `size` bytes, rest 0). */
void param_raw_to_wire(const param_meta_t *m, uint32_t raw, uint8_t wire[4]);

/** Check a SET_PARAM request (type byte + 4 value bytes) against `m`.
 *  Returns PARAM_ST_OK (raw value in *raw_out), PARAM_ST_E_TYPE or PARAM_ST_E_RANGE
 *  (padding bytes != 0, bool > 1, enum/int out of range, f32 NaN/Inf/out of range).
 *  Does NOT check the moving state (PARAM_F_MOVING_OK) nor the hard rules. */
uint8_t param_validate_set(const param_meta_t *m, uint8_t wire_type,
                           const uint8_t wire[4], uint32_t *raw_out);

/** True if the raw value lies in [min, max] of `m` (used after NVM load). */
bool param_raw_in_range(const param_meta_t *m, uint32_t raw);

/** Replace every out-of-range value in `p` by its default; returns count fixed. */
uint16_t params_sanitize(params_t *p);

#ifdef __cplusplus
}
#endif

#endif /* PARAMS_GEN_H */
