/* Thin wrapper of the generated protocol names (FW_design §0.1, §2.4): every code and bit comes
 * from gen/proto_gen.h (protocol.yaml); this header only adds derived sizes and the request-length
 * lookup. No hand-written protocol code values.
 * Implements: IF-001, IF-012, FW-CMD-001
 */
#ifndef PURE_PROTO_H
#define PURE_PROTO_H

#include <stdbool.h>
#include <stdint.h>

#include "proto_gen.h"

#ifdef __cplusplus
extern "C" {
#endif

/* framing aliases (frame.c keeps the Thrust_Stand names) */
#define FRAME_SYNC0        PROTO_SYNC0
#define FRAME_SYNC1        PROTO_SYNC1
#define FRAME_HEADER_LEN   PROTO_HEADER_LEN
#define FRAME_OVERHEAD     PROTO_FRAME_OVERHEAD
#define FRAME_MAX_LEN      PROTO_MAX_LEN
#define FRAME_TIMEOUT_MS   PROTO_INTERBYTE_TIMEOUT_MS

/* derived frame sizes */
#define PROTO_FRAME_MAX        (PROTO_MAX_LEN + PROTO_FRAME_OVERHEAD)            /* 168 */
#define PROTO_DATA_FRAME_LEN   (PROTO_DATA_LEN + PROTO_FRAME_OVERHEAD)           /* 26 */
#define PROTO_EVENT_FRAME_LEN  (PROTO_EVENT_LEN + PROTO_FRAME_OVERHEAD)          /* 24 */
#define PROTO_STOP_FRAME_LEN   (CMD_REQ_LEN_STOP + PROTO_FRAME_OVERHEAD)         /* 9 */
#define PROTO_HALT_FRAME_LEN   (CMD_REQ_LEN_HALT + PROTO_FRAME_OVERHEAD)         /* 8 */

/* compile-time checks usable from C11 and the C++ main.cpp */
#ifdef __cplusplus
#define PROTO_STATIC_ASSERT(c, m) static_assert(c, m)
#else
#define PROTO_STATIC_ASSERT(c, m) _Static_assert(c, m)
#endif

/* ICD §3.1 TYPE ranges */
#define PROTO_TYPE_IS_CMD(t)   ((uint8_t)(t) >= 0x01u && (uint8_t)(t) <= 0x3Fu)

PROTO_STATIC_ASSERT(PROTO_FRAME_MAX == 168u, "ICD §2: largest frame 168 B");
PROTO_STATIC_ASSERT(PROTO_DATA_FRAME_LEN == 26u, "ICD §7.3: DATA frame 26 B");
PROTO_STATIC_ASSERT(PROTO_EVENT_FRAME_LEN == 24u, "ICD §7.4: EVENT frame 24 B");
PROTO_STATIC_ASSERT(PROTO_RX_BUF_MIN <= 512u, "parser buffer RX_PARSE_BYTES covers ICD §2.3");

/** Fixed request LEN of a defined command TYPE (CMD_REQ_LEN_*), or -1 if TYPE is undefined. */
static inline int16_t proto_req_len(uint8_t type)
{
    switch (type) {
    case CMD_PING:             return (int16_t)CMD_REQ_LEN_PING;
    case CMD_GET_INFO:         return (int16_t)CMD_REQ_LEN_GET_INFO;
    case CMD_GET_STATUS:       return (int16_t)CMD_REQ_LEN_GET_STATUS;
    case CMD_REBOOT:           return (int16_t)CMD_REQ_LEN_REBOOT;
    case CMD_GET_ALL_PARAMS:   return (int16_t)CMD_REQ_LEN_GET_ALL_PARAMS;
    case CMD_GET_PARAM:        return (int16_t)CMD_REQ_LEN_GET_PARAM;
    case CMD_SET_PARAM:        return (int16_t)CMD_REQ_LEN_SET_PARAM;
    case CMD_SAVE_PARAMS:      return (int16_t)CMD_REQ_LEN_SAVE_PARAMS;
    case CMD_LOAD_PARAMS:      return (int16_t)CMD_REQ_LEN_LOAD_PARAMS;
    case CMD_DEFAULT_PARAMS:   return (int16_t)CMD_REQ_LEN_DEFAULT_PARAMS;
    case CMD_STREAM_START:     return (int16_t)CMD_REQ_LEN_STREAM_START;
    case CMD_STREAM_STOP:      return (int16_t)CMD_REQ_LEN_STREAM_STOP;
    case CMD_SET_VALID:        return (int16_t)CMD_REQ_LEN_SET_VALID;
    case CMD_ENABLE:           return (int16_t)CMD_REQ_LEN_ENABLE;
    case CMD_DISABLE:          return (int16_t)CMD_REQ_LEN_DISABLE;
    case CMD_HOME:             return (int16_t)CMD_REQ_LEN_HOME;
    case CMD_MOVE_ABS:         return (int16_t)CMD_REQ_LEN_MOVE_ABS;
    case CMD_JOG:              return (int16_t)CMD_REQ_LEN_JOG;
    case CMD_MOVE_UNTIL_LOAD:  return (int16_t)CMD_REQ_LEN_MOVE_UNTIL_LOAD;
    case CMD_STOP:             return (int16_t)CMD_REQ_LEN_STOP;
    case CMD_HALT:             return (int16_t)CMD_REQ_LEN_HALT;
    case CMD_HALT_CLEAR:       return (int16_t)CMD_REQ_LEN_HALT_CLEAR;
    case CMD_ESTOP_CLEAR:      return (int16_t)CMD_REQ_LEN_ESTOP_CLEAR;
    case CMD_FAULT_CLEAR:      return (int16_t)CMD_REQ_LEN_FAULT_CLEAR;
    case CMD_PAUSE:            return (int16_t)CMD_REQ_LEN_PAUSE;
    case CMD_RESUME:           return (int16_t)CMD_REQ_LEN_RESUME;
    case CMD_DIAG_MEAS:        return (int16_t)CMD_REQ_LEN_DIAG_MEAS;
    default:                   return -1;
    }
}

#ifdef __cplusplus
}
#endif

#endif /* PURE_PROTO_H */
