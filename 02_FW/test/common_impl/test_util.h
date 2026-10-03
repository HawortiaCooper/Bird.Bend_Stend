/* Implementer A host-test helpers (FW_design §8.5). Header-only.
 * Verifies: (helpers for) IF-003, IF-004, FW-CMD-001
 */
#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdint.h>
#include <string.h>

#include "frame.h"
#include "payload.h"

/* Build a frame around a payload into out (>= len + 8 B); returns the frame size. */
static inline uint16_t tu_frame(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t len, uint8_t *out)
{
    return frame_build(type, seq, pl, len, out);
}

/* NACK frame for a command TYPE. */
static inline uint16_t tu_nack_frame(uint8_t cmd_type, uint8_t seq, uint8_t status, uint16_t detail,
                                     uint8_t *out)
{
    uint8_t pl[PROTO_NACK_LEN];
    (void)payload_nack(status, detail, pl);
    return frame_build((uint8_t)(cmd_type | PROTO_RESP_BIT), seq, pl, PROTO_NACK_LEN, out);
}

#endif /* TEST_UTIL_H */
