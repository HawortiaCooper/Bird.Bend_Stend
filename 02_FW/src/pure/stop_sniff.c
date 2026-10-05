/* Stop sniffer + sniffed-stop hold. Implements: SAF-FW-002, SAF-FW-003, FW-MOT-007, D-31, DEF-M3-01 */
#include "stop_sniff.h"

#include <string.h>

#include "crc16.h"
#include "le.h"
#include "proto.h"

void sniff_init(sniff_t *s, uint32_t stream_pos)
{
    memset(s, 0, sizeof *s);
    s->base = stream_pos;
}

/* candidate at w[i] of the window (total bytes): frame length if it is a valid sniffed frame */
static uint16_t candidate(const uint8_t *w, uint16_t i, uint16_t total)
{
    uint8_t type;
    uint16_t len, flen;
    if ((uint16_t)(total - i) < PROTO_HALT_FRAME_LEN) {
        return 0u;
    }
    if (w[i] != FRAME_SYNC0 || w[i + 1u] != FRAME_SYNC1) {
        return 0u;
    }
    type = w[i + 2u];
    if (!CMD_IS_SNIFFED(type)) {
        return 0u;
    }
    len = le_get16(&w[i + 4u]);
    if (len != (uint16_t)proto_req_len(type)) {
        return 0u;
    }
    flen = (uint16_t)(len + FRAME_OVERHEAD);
    if ((uint16_t)(total - i) < flen) {
        return 0u;
    }
    if (crc16_ccitt(&w[i + 2u], (uint32_t)(4u + len), CRC16_INIT) != le_get16(&w[i + FRAME_HEADER_LEN + len])) {
        return 0u;
    }
    if (type == (uint8_t)CMD_STOP && w[i + FRAME_HEADER_LEN] > (uint8_t)STOPMODE_CONTROLLED) {
        return 0u;                              /* a STOP the dispatcher would NACK is ignored */
    }
    return flen;
}

uint8_t sniff_scan(sniff_t *s, const uint8_t *in, uint16_t n, sniff_hit_t *hits, uint8_t max_hits)
{
    uint8_t w[SNIFF_CARRY + SNIFF_MAX_CHUNK];
    uint16_t total, i, keep;
    uint8_t nh = 0u;
    if (n > SNIFF_MAX_CHUNK) {
        n = SNIFF_MAX_CHUNK;
    }
    memcpy(w, s->carry, s->n);
    memcpy(&w[s->n], in, n);
    total = (uint16_t)(s->n + n);
    for (i = 0u; i < total; i++) {
        uint16_t flen = candidate(w, i, total);
        /* report a frame once: its last byte must be new (frames inside the carry were seen) */
        if (flen != 0u && (uint16_t)(i + flen - 1u) >= s->n && nh < max_hits) {
            hits[nh].type = w[i + 2u];
            hits[nh].seq = w[i + 3u];
            hits[nh].mode = (w[i + 2u] == (uint8_t)CMD_STOP) ? w[i + FRAME_HEADER_LEN] : 0u;
            hits[nh].pos = s->base + i;
            nh++;
        }
    }
    keep = (total < SNIFF_CARRY) ? total : (uint16_t)SNIFF_CARRY;
    memcpy(s->carry, &w[total - keep], keep);
    s->base += (uint32_t)(total - keep);
    s->n = (uint8_t)keep;
    return nh;
}

uint8_t sniff_cause(const sniff_hit_t *h)
{
    if (h->type == (uint8_t)CMD_STOP) {
        return (h->mode == (uint8_t)STOPMODE_CONTROLLED) ? (uint8_t)SC_PC_STOP_CONTROLLED
                                                         : (uint8_t)SC_PC_STOP;
    }
    if (h->type == (uint8_t)CMD_HALT) {
        return (uint8_t)SC_PC_HALT;
    }
    return (uint8_t)SC_PC_PAUSE;
}

void hold_init(sniff_hold_t *h)
{
    memset(h, 0, sizeof *h);
}

/* FIFO helpers: drop the first n entries; push (drops the oldest when full); find (type, seq) */
static void q_drop(hold_ent_t *q, uint8_t *qn, uint8_t n)
{
    uint8_t i;
    if (n >= *qn) {
        *qn = 0u;
        return;
    }
    for (i = 0u; (uint8_t)(i + n) < *qn; i++) {
        q[i] = q[i + n];
    }
    *qn = (uint8_t)(*qn - n);
}

static void q_push(hold_ent_t *q, uint8_t *qn, uint8_t type, uint8_t seq, uint32_t t_ms)
{
    if (*qn >= HOLD_FIFO) {
        q_drop(q, qn, 1u);
    }
    q[*qn].type = type;
    q[*qn].seq = seq;
    q[*qn].t_ms = t_ms;
    (*qn)++;
}

static int q_find(const hold_ent_t *q, uint8_t qn, uint8_t type, uint8_t seq)
{
    uint8_t i;
    for (i = 0u; i < qn; i++) {
        if (q[i].type == type && q[i].seq == seq) {
            return (int)i;
        }
    }
    return -1;
}

void hold_set(sniff_hold_t *h, const sniff_hit_t *hit, uint32_t now_ms)
{
    h->type = hit->type;                        /* the hold lasts until the LATEST sniffed frame */
    h->seq = hit->seq;
    h->cause = sniff_cause(hit);
    h->pending = true;
    h->t_ms = now_ms;
    q_push(h->sq, &h->sn, hit->type, hit->seq, now_ms);
}

bool hold_on_dispatch_at(sniff_hold_t *h, uint8_t type, uint8_t seq, uint32_t now_ms)
{
    int j;
    if (!CMD_IS_SNIFFED(type)) {
        return false;
    }
    j = q_find(h->sq, h->sn, type, seq);
    if (j >= 0) {                               /* sniffed first: reached in order */
        q_drop(h->sq, &h->sn, (uint8_t)(j + 1));
        if (h->sn == 0u && h->pending) {
            h->pending = false;
            return true;
        }
        return false;
    }
    q_push(h->dq, &h->dn, type, seq, now_ms);  /* dispatched before the sniffer saw it (DEF-M3-01) */
    return false;
}

bool hold_on_dispatch(sniff_hold_t *h, uint8_t type, uint8_t seq)
{
    return hold_on_dispatch_at(h, type, seq, h->t_ms);
}

bool hold_already_dispatched(sniff_hold_t *h, const sniff_hit_t *hit)
{
    int j = q_find(h->dq, h->dn, hit->type, hit->seq);
    if (j < 0) {
        return false;
    }
    q_drop(h->dq, &h->dn, (uint8_t)(j + 1));   /* older entries: frames the sniffer lost */
    return true;
}

bool hold_timeout(sniff_hold_t *h, uint32_t now_ms, uint32_t max_ms)
{
    while (h->dn != 0u && (uint32_t)(now_ms - h->dq[0].t_ms) >= max_ms) {
        q_drop(h->dq, &h->dn, 1u);
    }
    if (h->pending && (uint32_t)(now_ms - h->t_ms) >= max_ms) {
        h->pending = false;
        h->sn = 0u;
        return true;
    }
    return false;
}
