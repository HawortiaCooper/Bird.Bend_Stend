/* Stop sniffer (FW_design §5.9.3, ICD §2): finds CRC-valid STOP (9 B, mode <= 1), HALT (8 B) and
 * PAUSE (8 B) frames (protocol.yaml `sniffed`, CMD_IS_SNIFFED) in RX bytes not yet seen by it, an
 * 8-byte carry joins frames split across ticks; each frame is reported once (its last byte lies in
 * the new bytes). RESUME, the clears and every other command are never sniffed. The core executes
 * only the MOTION part of a hit at once; latches, VALID, EVENTs and the response come from the
 * normal in-order dispatch of the same frame.
 * Sniffed-stop hold (DEF-P1-04): a hit records {TYPE, SEQ, cause} of the latest sniffed frame;
 * while pending no motion start may begin (a start from an EARLIER frame is parked); it resolves
 * when the dispatcher reaches that frame (same TYPE and SEQ, in-order dispatch), or after
 * SNIFF_HOLD_MAX_MS on the safe side (parked start discarded; covers frames lost to an RX overrun).
 * Matching by TYPE/SEQ instead of a stream position keeps the hold correct across RX overruns,
 * where the parser's and the sniffer's byte counts would diverge.
 * Pure C11.
 * Implements: SAF-FW-002, SAF-FW-003 (receive-side path), FW-MOT-007, D-31 (RESUME not sniffed)
 */
#ifndef PURE_STOP_SNIFF_H
#define PURE_STOP_SNIFF_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SNIFF_CARRY       8u     /* longest sniffed frame - 1 */
#define SNIFF_MAX_CHUNK   256u

typedef struct {
    uint8_t  carry[SNIFF_CARRY];
    uint8_t  n;              /* bytes in carry */
    uint32_t base;           /* stream position of carry[0] */
} sniff_t;

typedef struct {
    uint8_t  type;           /* CMD_STOP / CMD_HALT / CMD_PAUSE */
    uint8_t  seq;
    uint8_t  mode;           /* STOP mode (0 for HALT / PAUSE) */
    uint32_t pos;            /* stream position of the frame's SYNC0 */
} sniff_hit_t;

typedef struct {
    bool     pending;
    uint8_t  type;           /* TYPE / SEQ of the latest sniffed frame */
    uint8_t  seq;
    uint8_t  cause;          /* SC_* of that frame */
    uint32_t t_ms;           /* when it was set */
} sniff_hold_t;

void    sniff_init(sniff_t *s, uint32_t stream_pos);
/** Scan n (<= SNIFF_MAX_CHUNK) new bytes; returns the number of hits written (<= max_hits). */
uint8_t sniff_scan(sniff_t *s, const uint8_t *in, uint16_t n, sniff_hit_t *hits, uint8_t max_hits);
/** Stop cause (SC_*) of a hit. */
uint8_t sniff_cause(const sniff_hit_t *h);

void hold_init(sniff_hold_t *h);
void hold_set(sniff_hold_t *h, const sniff_hit_t *hit, uint32_t now_ms);
/** The dispatcher took a command frame (type, seq): true if this resolved the hold. */
bool hold_on_dispatch(sniff_hold_t *h, uint8_t type, uint8_t seq);
/** Safe-side resolution after max_ms without reaching the sniffed frame: true if it fired. */
bool hold_timeout(sniff_hold_t *h, uint32_t now_ms, uint32_t max_ms);
static inline bool hold_blocks_start(const sniff_hold_t *h) { return h->pending; }

#ifdef __cplusplus
}
#endif

#endif /* PURE_STOP_SNIFF_H */
