/* Origin: Thrust_Stand_HAW/02_FW/src/pure/frame.c @37c8747 (copied; fp_frame_tail() added). */
/* ICD §2.3 receiver state machine + frame builder.
 * Implements: IF-003, IF-004, FW-CMD-001
 */
#include "frame.h"

#include <string.h>

#include "crc16.h"
#include "le.h"

void fp_init(fparser_t *p)
{
    memset(p, 0, sizeof *p);
}

static void drop_consumed(fparser_t *p)
{
    if (p->consume != 0u) {
        p->head = (uint16_t)(p->head + p->consume);
        p->consume = 0u;
    }
    if (p->head == p->tail) {
        p->head = 0u;
        p->tail = 0u;
    }
}

uint16_t fp_free(fparser_t *p)
{
    drop_consumed(p);
    return (uint16_t)(FP_BUF_SIZE - (p->tail - p->head));
}

uint16_t fp_buffered(const fparser_t *p)
{
    return (uint16_t)(p->tail - p->head - p->consume);
}

uint16_t fp_append(fparser_t *p, const uint8_t *d, uint16_t n, uint32_t now_ms)
{
    uint16_t room;
    drop_consumed(p);
    if (n == 0u) {
        return 0u;
    }
    if ((uint16_t)(FP_BUF_SIZE - p->tail) < n && p->head != 0u) {
        uint16_t used = (uint16_t)(p->tail - p->head);
        memmove(p->buf, &p->buf[p->head], used);
        p->head = 0u;
        p->tail = used;
    }
    room = (uint16_t)(FP_BUF_SIZE - p->tail);
    if (n > room) {
        n = room;
    }
    memcpy(&p->buf[p->tail], d, n);
    p->tail = (uint16_t)(p->tail + n);
    p->last_byte_ms = now_ms;
    p->timed_out = 0u;
    return n;
}

void fp_check_timeout(fparser_t *p, uint32_t now_ms)
{
    if (fp_buffered(p) != 0u && (uint32_t)(now_ms - p->last_byte_ms) >= FRAME_TIMEOUT_MS) {
        p->timed_out = 1u;
    }
}

void fp_force_timeout(fparser_t *p)
{
    if (fp_buffered(p) != 0u) {
        p->timed_out = 1u;
    }
}

/* Index of the first A5 5A at or after head, or -1. */
static int32_t find_sync(const fparser_t *p)
{
    uint16_t i;
    for (i = p->head; (uint16_t)(i + 1u) < p->tail; i++) {
        if (p->buf[i] == FRAME_SYNC0 && p->buf[i + 1u] == FRAME_SYNC1) {
            return (int32_t)i;
        }
    }
    return -1;
}

bool fp_poll(fparser_t *p, fp_frame_t *out)
{
    drop_consumed(p);
    for (;;) {
        int32_t i = find_sync(p);
        uint16_t avail;
        uint16_t len;
        uint16_t rx_crc;
        if (i < 0) {
            /* Step 1: discard everything but a trailing lone SYNC0 (which is dropped too once the
             * line has been idle: it can no longer become a frame). */
            uint16_t keep = 0u;
            if (!p->timed_out && p->tail > p->head && p->buf[p->tail - 1u] == FRAME_SYNC0) {
                keep = 1u;
            }
            p->head = (uint16_t)(p->tail - keep);
            if (keep == 0u) {
                p->head = 0u;
                p->tail = 0u;
                p->timed_out = 0u;
            }
            return false;
        }
        p->head = (uint16_t)i;
        avail = (uint16_t)(p->tail - p->head);
        if (avail < FRAME_HEADER_LEN) {
            if (p->timed_out) {
                p->timeout_drops++;
                p->head++;
                continue;
            }
            return false;
        }
        len = le_get16(&p->buf[p->head + 4u]);
        if (len > FRAME_MAX_LEN) {
            p->len_errors++;
            p->head++;                 /* drop the SYNC0 only (step 2) */
            continue;
        }
        if (avail < (uint16_t)(FRAME_OVERHEAD + len)) {
            if (p->timed_out) {
                p->timeout_drops++;    /* step 4: drop one byte, re-scan */
                p->head++;
                continue;
            }
            return false;
        }
        rx_crc = le_get16(&p->buf[p->head + FRAME_HEADER_LEN + len]);
        if (crc16_ccitt(&p->buf[p->head + 2u], (uint32_t)(4u + len), CRC16_INIT) != rx_crc) {
            p->crc_errors++;
            p->head++;                 /* step 3: drop the SYNC0 only */
            continue;
        }
        out->type = p->buf[p->head + 2u];
        out->seq = p->buf[p->head + 3u];
        out->len = len;
        out->payload = &p->buf[p->head + FRAME_HEADER_LEN];
        p->consume = (uint16_t)(FRAME_OVERHEAD + len);
        p->frames_ok++;
        return true;
    }
}

uint16_t fp_frame_tail(const fparser_t *p)
{
    return (uint16_t)(p->tail - p->head);
}

uint16_t frame_build(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len, uint8_t *out)
{
    uint16_t crc;
    out[0] = FRAME_SYNC0;
    out[1] = FRAME_SYNC1;
    out[2] = type;
    out[3] = seq;
    le_put16(&out[4], len);
    if (len != 0u && payload != &out[FRAME_HEADER_LEN]) {   /* in-place build allowed */
        memcpy(&out[FRAME_HEADER_LEN], payload, len);
    }
    crc = crc16_ccitt(&out[2], (uint32_t)(4u + len), CRC16_INIT);
    le_put16(&out[FRAME_HEADER_LEN + len], crc);
    return (uint16_t)(FRAME_OVERHEAD + len);
}
