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
 * DEF-M3-01 (v0.7): the main-loop dispatcher may reach a STOP / HALT / PAUSE frame BEFORE the 1 kHz
 * sniffer scans it. Such a frame is remembered as "dispatched, not yet sniffed" (FIFO, TYPE/SEQ);
 * when the sniffer reports it later, hold_already_dispatched() consumes the entry and the core skips
 * the hit entirely (no hold, no second motion part - which would otherwise discard or stop a motion
 * started by a LATER frame). Both directions are FIFOs: both sides see the frames in the same stream
 * order; a match further down a FIFO drops the older entries (frames one side lost to an RX
 * overrun), and dispatched entries expire after max_ms in hold_timeout().
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

#define HOLD_FIFO 4u

typedef struct {
    uint8_t  type;
    uint8_t  seq;
    uint32_t t_ms;
} hold_ent_t;

typedef struct {
    bool       pending;      /* sniffed frames not yet dispatched (sn > 0) */
    uint8_t    type;         /* TYPE / SEQ of the latest sniffed frame */
    uint8_t    seq;
    uint8_t    cause;        /* SC_* of that frame */
    uint32_t   t_ms;         /* when it was set */
    hold_ent_t sq[HOLD_FIFO];/* sniffed, not yet dispatched (oldest first) */
    uint8_t    sn;
    hold_ent_t dq[HOLD_FIFO];/* dispatched before the sniffer saw them (DEF-M3-01), oldest first */
    uint8_t    dn;
} sniff_hold_t;

void    sniff_init(sniff_t *s, uint32_t stream_pos);
/** Scan n (<= SNIFF_MAX_CHUNK) new bytes; returns the number of hits written (<= max_hits). */
uint8_t sniff_scan(sniff_t *s, const uint8_t *in, uint16_t n, sniff_hit_t *hits, uint8_t max_hits);
/** Stop cause (SC_*) of a hit. */
uint8_t sniff_cause(const sniff_hit_t *h);

void hold_init(sniff_hold_t *h);
void hold_set(sniff_hold_t *h, const sniff_hit_t *hit, uint32_t now_ms);
/** The dispatcher took a command frame (type, seq) at now_ms: true if this resolved the hold. A
 *  sniffable frame the sniffer has not reported yet is remembered as dispatched (DEF-M3-01); the
 *  caller passes only frames the sniffer will report (sniffed TYPE, its LEN, STOP mode <= 1). */
bool hold_on_dispatch_at(sniff_hold_t *h, uint8_t type, uint8_t seq, uint32_t now_ms);
/** As hold_on_dispatch_at() at the time of the latest hold_set(). */
bool hold_on_dispatch(sniff_hold_t *h, uint8_t type, uint8_t seq);
/** A sniffer hit for a frame the dispatcher already executed: true -> entry consumed, the core
 *  ignores the hit (no hold, no motion part) (DEF-M3-01). */
bool hold_already_dispatched(sniff_hold_t *h, const sniff_hit_t *hit);
/** Safe-side resolution after max_ms without reaching the sniffed frame: true if it fired. Also
 *  expires dispatched-not-sniffed entries older than max_ms (frames the sniffer lost). */
bool hold_timeout(sniff_hold_t *h, uint32_t now_ms, uint32_t max_ms);
static inline bool hold_blocks_start(const sniff_hold_t *h) { return h->pending; }

#ifdef __cplusplus
}
#endif

#endif /* PURE_STOP_SNIFF_H */
