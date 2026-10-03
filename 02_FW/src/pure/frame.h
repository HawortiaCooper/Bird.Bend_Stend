/* Origin: Thrust_Stand_HAW/02_FW/src/pure/frame.h @37c8747 (copied; buffer size, tags and
 * fp_frame_tail() adapted for the bend stand, constants from proto_gen.h via proto.h). */
/* ICD §2 framing: receiver state machine (§2.3) and frame builder. Pure C11.
 *
 * Pull API: bytes are appended with fp_append(); complete CRC-valid frames are taken out one at a
 * time with fp_poll(). The frame returned by fp_poll() points into the parser buffer and stays
 * valid until the next fp_* call on the same parser. fp_append() must only be called after
 * fp_poll() returned false (parser drained) — then the output is identical to the reference
 * parser (ref_codec.FrameParser) for any chunking of the input.
 *
 * Implements: IF-003, IF-004, FW-CMD-001
 */
#ifndef PURE_FRAME_H
#define PURE_FRAME_H

#include <stdbool.h>
#include <stdint.h>

#include "proto.h"

#ifdef __cplusplus
extern "C" {
#endif

/* RX_PARSE_BYTES (FW_design §2.5) >= PROTO_RX_BUF_MIN = 336 B (ICD §2.3: two maximum frames) */
#define FP_BUF_SIZE 512u
PROTO_STATIC_ASSERT(FP_BUF_SIZE >= PROTO_RX_BUF_MIN, "ICD §2.3 parser buffer");

typedef struct {
    uint8_t  type;
    uint8_t  seq;
    uint16_t len;
    const uint8_t *payload;
} fp_frame_t;

typedef struct {
    uint8_t  buf[FP_BUF_SIZE];
    uint16_t head;          /* first unconsumed byte */
    uint16_t tail;          /* one past the last byte */
    uint16_t consume;       /* bytes of the last delivered frame, dropped lazily */
    uint8_t  timed_out;     /* §2.3 step 4 processing in progress */
    uint32_t last_byte_ms;
    uint32_t frames_ok;
    uint32_t crc_errors;
    uint32_t len_errors;
    uint32_t timeout_drops;
} fparser_t;

void     fp_init(fparser_t *p);
/** Free space for fp_append() (after dropping a delivered frame). */
uint16_t fp_free(fparser_t *p);
/** Append up to n bytes; returns the number accepted. Clears a pending timeout state. */
uint16_t fp_append(fparser_t *p, const uint8_t *d, uint16_t n, uint32_t now_ms);
/** Bytes currently buffered (incomplete candidate / unscanned bytes). */
uint16_t fp_buffered(const fparser_t *p);
/** §2.3 step 4: if bytes are buffered and none arrived for >= 20 ms, start timeout processing. */
void     fp_check_timeout(fparser_t *p, uint32_t now_ms);
/** Start timeout processing unconditionally (host tests: "line idle >= 20 ms"). */
void     fp_force_timeout(fparser_t *p);
/** Next complete CRC-valid frame, or false if none (then the parser is drained). */
bool     fp_poll(fparser_t *p, fp_frame_t *out);
/** After fp_poll() returned true: bytes from the start of the delivered frame to the end of the
 *  buffered input (the frame's stream position = bytes appended so far - this value). */
uint16_t fp_frame_tail(const fparser_t *p);

/** Build a frame (A5 5A TYPE SEQ LEN payload CRC) into out (>= len + 8 bytes); returns size. */
uint16_t frame_build(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len, uint8_t *out);

#ifdef __cplusplus
}
#endif

#endif /* PURE_FRAME_H */
