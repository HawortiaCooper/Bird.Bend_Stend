/* Payload codec (ICD §4, §5, §7): encoders of every FW->PC body (INFO 44 B, STATUS 86 B, DATA 18 B,
 * EVENT 16 B, PARAM_ENTRY 7 B, NACK 3 B) and the decoder of every PC->FW request (§3.2).
 * Pure C11, byte-exact against vectors/protocol_vectors.json (test_impl_payload).
 * Origin: pattern of Thrust_Stand_HAW/02_FW/src/pure/payload.* @37c8747 (rewritten: other
 * message set).
 * Implements: IF-005, IF-006, IF-009, FW-STR-003, FW-CFG-002, FW-CFG-004, FW-CMD-004
 */
#ifndef PURE_PAYLOAD_H
#define PURE_PAYLOAD_H

#include <stdbool.h>
#include <stdint.h>

#include "proto.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t  proto_major, proto_minor, payload_version;
    uint8_t  fw_version[3];
    uint32_t param_dict_hash;
    uint8_t  uid[12];
    uint8_t  build[16];          /* ASCII, NUL-padded */
    uint16_t param_count;
    uint32_t feature_mask;
} info_t;

typedef struct {
    uint32_t uptime_ms, t_us;
    uint8_t  flags, motion_state;
    uint16_t status, faults, io;
    uint8_t  home_phase, halt_src, reset_cause, sys_flags;
    int32_t  pos_um, target_um, pos_steps, afe_raw_last;
    uint16_t afe_rate_dsps, afe_reinit_count;
    uint32_t rx_frames_ok, rx_crc_errors, rx_frame_errors, rx_overruns, tx_drops;
    uint16_t event_overflows, loop_max_us, link_age_ms, stack_free_min, nvm_save_ms,
             idle_disable_left_s;
    uint32_t nvm_record_seq, nvm_save_uptime_ms, v_limit_um_s;
    uint8_t  pause_src;
} status_t;

typedef struct {
    uint32_t t_us;
    uint8_t  flags;
    int32_t  afe_raw;
    int32_t  setpoint_um;
    uint16_t frame_seq;
    uint16_t status;
} data_t;

typedef struct {
    uint32_t t_us;
    uint16_t code, arg;
    int32_t  value, value2;
} event_t;

typedef struct {
    uint16_t id;
    uint8_t  type;
    uint8_t  wire[4];
} param_entry_t;

/* ---- FW -> PC bodies ---- */
void     payload_info(const info_t *in, uint8_t out[PROTO_INFO_LEN]);
void     payload_status(const status_t *in, uint8_t out[PROTO_STATUS_LEN]);
void     payload_data(const data_t *in, uint8_t out[PROTO_DATA_LEN]);   /* payload_version = 1 */
void     payload_event(const event_t *in, uint8_t out[PROTO_EVENT_LEN]);
void     payload_param_entry(const param_entry_t *in, uint8_t out[PROTO_PARAM_ENTRY_LEN]);
/** NACK payload: u8 status, u16 detail (ICD §4.1); returns 3. */
uint16_t payload_nack(uint8_t status, uint16_t detail, uint8_t out[PROTO_NACK_LEN]);

/* ---- PC -> FW requests (fixed LEN already checked) ---- */
typedef struct {
    uint8_t type;
    union {
        struct { uint32_t magic; } reboot;
        struct { uint8_t page; } page;
        struct { uint16_t id; } get_param;
        param_entry_t set_param;
        struct { uint8_t valid; } set_valid;
        struct { uint8_t flags; } home;
        struct { int32_t target_um; uint32_t v_um_s; uint32_t a_um_s2; } move_abs;
        struct { int32_t v_um_s; uint32_t a_um_s2; int32_t bound_um; } jog;
        struct { int32_t bound_um; uint32_t v_um_s; uint32_t a_um_s2; int32_t raw_stop;
                 uint8_t cmp; } mul;
        struct { uint8_t mode; } stop;
        struct { uint8_t op; uint8_t sel; uint16_t a; uint32_t b; } meas;   /* DIAG_MEAS (ICD v0.6) */
    } u;
} cmd_req_t;

/** Decode a request of a defined TYPE whose LEN equals proto_req_len(type). False if TYPE is
 *  undefined or LEN differs (nothing written then except out->type). */
bool payload_decode_request(uint8_t type, const uint8_t *p, uint16_t len, cmd_req_t *out);

#ifdef __cplusplus
}
#endif

#endif /* PURE_PAYLOAD_H */
