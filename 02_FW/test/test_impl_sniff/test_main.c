/* Stop sniffer + sniffed-stop hold: STOP/HALT/PAUSE hits (vectors' own frames), split across ticks
 * at every position, bad CRC, STOP mode 2 ignored, sync pattern inside another payload, a frame
 * reported exactly once, RESUME and the clears never sniffed, hold resolution / timeout, a stop
 * frame dispatched before it was sniffed (DEF-M3-01).
 * Verifies: SAF-FW-002, SAF-FW-003, FW-MOT-007, D-31 (RESUME not sniffed), DEF-P1-04 (hold), DEF-M3-01
 */
#include <string.h>
#include <unity.h>

#include "frame.h"
#include "proto.h"
#include "stop_sniff.h"

void setUp(void) {}
void tearDown(void) {}

static uint16_t mk(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t len, uint8_t *out)
{
    return frame_build(type, seq, pl, len, out);
}

static uint8_t scan_all(const uint8_t *d, uint16_t n, uint16_t split, sniff_hit_t *hits)
{
    sniff_t s;
    uint8_t nh;
    sniff_init(&s, 0u);
    nh = sniff_scan(&s, d, split, hits, 8u);
    nh = (uint8_t)(nh + sniff_scan(&s, &d[split], (uint16_t)(n - split), &hits[nh], (uint8_t)(8u - nh)));
    return nh;
}

static void test_each_sniffed_frame_every_split(void)
{
    static const uint8_t types[] = {CMD_STOP, CMD_HALT, CMD_PAUSE};
    uint8_t k;
    for (k = 0u; k < 3u; k++) {
        uint8_t pl[1] = {1u};
        uint8_t buf[40];
        uint16_t n, split;
        memset(buf, 0x55, sizeof buf);
        n = mk(types[k], 0x5Bu, pl, (uint16_t)proto_req_len(types[k]), &buf[5]);
        n = (uint16_t)(n + 10u);
        for (split = 0u; split <= n; split++) {
            sniff_hit_t h[8];
            TEST_ASSERT_EQUAL_UINT8(1u, scan_all(buf, n, split, h));
            TEST_ASSERT_EQUAL_HEX8(types[k], h[0].type);
            TEST_ASSERT_EQUAL_HEX8(0x5Bu, h[0].seq);
            TEST_ASSERT_EQUAL_UINT32(5u, h[0].pos);
        }
    }
}

static void test_reported_once_over_many_ticks(void)
{
    uint8_t buf[8];
    sniff_t s;
    sniff_hit_t h[8];
    uint8_t total = 0u, i;
    uint16_t n = mk(CMD_HALT, 1u, NULL, 0u, buf);
    sniff_init(&s, 100u);
    total = (uint8_t)(total + sniff_scan(&s, buf, n, h, 8u));
    TEST_ASSERT_EQUAL_UINT32(100u, h[0].pos);
    for (i = 0u; i < 5u; i++) {                       /* carry still holds the HALT frame */
        uint8_t z = 0u;
        total = (uint8_t)(total + sniff_scan(&s, &z, 1u, h, 8u));
    }
    TEST_ASSERT_EQUAL_UINT8(1u, total);
}

static void test_rejects(void)
{
    uint8_t buf[32];
    sniff_hit_t h[8];
    uint8_t pl[1] = {2u};
    uint16_t n;
    n = mk(CMD_STOP, 1u, pl, 1u, buf);                 /* STOP mode 2: the dispatcher NACKs it */
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
    n = mk(CMD_HALT, 1u, NULL, 0u, buf);
    buf[7] ^= 0x01u;                                   /* bad CRC */
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
    n = mk(CMD_RESUME, 1u, NULL, 0u, buf);             /* D-31: never sniffed */
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
    n = mk(CMD_HALT_CLEAR, 1u, NULL, 0u, buf);
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
    n = mk(CMD_FAULT_CLEAR, 1u, NULL, 0u, buf);
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
    n = mk(CMD_ESTOP_CLEAR, 1u, NULL, 0u, buf);
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
    n = mk(CMD_PING, 1u, NULL, 0u, buf);
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(buf, n, 0u, h));
}

/* a complete HALT frame embedded in a SET_PARAM-sized payload would be found (safe side); a
 * partial / shifted pattern is not */
static void test_sync_inside_payload(void)
{
    uint8_t halt[8], pl[16], fr[32];
    sniff_hit_t h[8];
    uint16_t n;
    (void)mk(CMD_HALT, 9u, NULL, 0u, halt);
    memset(pl, 0, sizeof pl);
    memcpy(&pl[4], halt, 7u);                          /* truncated copy: no hit */
    n = mk(CMD_MOVE_UNTIL_LOAD, 3u, pl, 16u, fr);
    TEST_ASSERT_EQUAL_UINT8(0u, scan_all(fr, n, 0u, h));
    memcpy(&pl[4], halt, 8u);                          /* full copy: false positive (safe side) */
    n = mk(CMD_MOVE_UNTIL_LOAD, 3u, pl, 16u, fr);
    TEST_ASSERT_EQUAL_UINT8(1u, scan_all(fr, n, 0u, h));
}

static void test_two_frames_one_tick_and_cause(void)
{
    uint8_t buf[32];
    uint8_t m1[1] = {1u};
    sniff_hit_t h[8];
    uint16_t n = mk(CMD_STOP, 1u, m1, 1u, buf);
    n = (uint16_t)(n + mk(CMD_PAUSE, 2u, NULL, 0u, &buf[n]));
    TEST_ASSERT_EQUAL_UINT8(2u, scan_all(buf, n, n, h));
    TEST_ASSERT_EQUAL_UINT8(SC_PC_STOP_CONTROLLED, sniff_cause(&h[0]));
    TEST_ASSERT_EQUAL_UINT8(SC_PC_PAUSE, sniff_cause(&h[1]));
    h[0].mode = 0u;
    TEST_ASSERT_EQUAL_UINT8(SC_PC_STOP, sniff_cause(&h[0]));
    h[0].type = CMD_HALT;
    TEST_ASSERT_EQUAL_UINT8(SC_PC_HALT, sniff_cause(&h[0]));
}

/* MOVE_ABS + HALT burst: the HALT is sniffed in the tick before the MOVE_ABS is dispatched -> the
 * hold blocks the start until the dispatcher reaches the HALT (FW_design §5.9.3) */
static void test_hold_blocks_until_dispatched(void)
{
    sniff_hold_t hd;
    sniff_hit_t hit;
    hold_init(&hd);
    TEST_ASSERT_FALSE(hold_blocks_start(&hd));
    hit.type = CMD_HALT; hit.seq = 7u; hit.mode = 0u; hit.pos = 0u;
    hold_set(&hd, &hit, 1000u);
    TEST_ASSERT_TRUE(hold_blocks_start(&hd));              /* MOVE_ABS dispatched now: parked */
    TEST_ASSERT_FALSE(hold_on_dispatch(&hd, CMD_MOVE_ABS, 6u));
    TEST_ASSERT_FALSE(hold_on_dispatch(&hd, CMD_HALT, 6u)); /* an older HALT does not resolve */
    TEST_ASSERT_TRUE(hold_blocks_start(&hd));
    TEST_ASSERT_TRUE(hold_on_dispatch(&hd, CMD_HALT, 7u));
    TEST_ASSERT_FALSE(hold_blocks_start(&hd));
}

static void test_hold_latest_and_timeout(void)
{
    sniff_hold_t hd;
    sniff_hit_t a, b;
    hold_init(&hd);
    a.type = CMD_HALT; a.seq = 1u; a.mode = 0u; a.pos = 0u;
    b.type = CMD_STOP; b.seq = 2u; b.mode = 0u; b.pos = 8u;
    hold_set(&hd, &a, 100u);
    hold_set(&hd, &b, 101u);
    TEST_ASSERT_FALSE(hold_on_dispatch(&hd, CMD_HALT, 1u)); /* lasts until the latest frame */
    TEST_ASSERT_EQUAL_UINT8(SC_PC_STOP, hd.cause);
    TEST_ASSERT_FALSE(hold_timeout(&hd, 120u, 20u));
    TEST_ASSERT_TRUE(hold_timeout(&hd, 121u, 20u));         /* never dispatched: safe side */
    TEST_ASSERT_FALSE(hold_blocks_start(&hd));
}

/* DEF-M3-01: the dispatcher reached the stop frame before the sniffer scanned it -> the later hit is
 * recognised as already dispatched (no hold); FIFO order, lost entries, expiry */
static void test_dispatched_before_sniffed(void)
{
    sniff_hold_t hd;
    sniff_hit_t a, b, c;
    hold_init(&hd);
    a.type = CMD_HALT; a.seq = 1u; a.mode = 0u; a.pos = 0u;
    b.type = CMD_STOP; b.seq = 2u; b.mode = 0u; b.pos = 8u;
    c.type = CMD_PAUSE; c.seq = 3u; c.mode = 0u; c.pos = 17u;
    TEST_ASSERT_FALSE(hold_on_dispatch_at(&hd, CMD_HALT, 1u, 100u));   /* dispatched first */
    TEST_ASSERT_FALSE(hold_blocks_start(&hd));
    TEST_ASSERT_TRUE(hold_already_dispatched(&hd, &a));                 /* the late hit: ignored */
    TEST_ASSERT_FALSE(hold_already_dispatched(&hd, &a));                /* consumed once */
    TEST_ASSERT_FALSE(hold_blocks_start(&hd));
    /* a new frame the sniffer sees first still holds until it is dispatched */
    TEST_ASSERT_FALSE(hold_already_dispatched(&hd, &b));
    hold_set(&hd, &b, 101u);
    TEST_ASSERT_TRUE(hold_blocks_start(&hd));
    TEST_ASSERT_TRUE(hold_on_dispatch_at(&hd, CMD_STOP, 2u, 101u));
    TEST_ASSERT_FALSE(hold_blocks_start(&hd));
    /* mixed tick: A dispatched, then the sniffer reports A and C together: A ignored, C holds */
    TEST_ASSERT_FALSE(hold_on_dispatch_at(&hd, CMD_HALT, 1u, 102u));
    TEST_ASSERT_TRUE(hold_already_dispatched(&hd, &a));
    TEST_ASSERT_FALSE(hold_already_dispatched(&hd, &c));
    hold_set(&hd, &c, 102u);
    TEST_ASSERT_TRUE(hold_blocks_start(&hd));
    TEST_ASSERT_TRUE(hold_on_dispatch_at(&hd, CMD_PAUSE, 3u, 103u));
    /* a frame the sniffer lost: the next match further down the FIFO drops it */
    TEST_ASSERT_FALSE(hold_on_dispatch_at(&hd, CMD_HALT, 1u, 104u));
    TEST_ASSERT_FALSE(hold_on_dispatch_at(&hd, CMD_STOP, 2u, 104u));
    TEST_ASSERT_TRUE(hold_already_dispatched(&hd, &b));
    TEST_ASSERT_FALSE(hold_already_dispatched(&hd, &a));                /* dropped (lost) */
    /* expiry: a dispatched entry never sniffed is forgotten after max_ms */
    TEST_ASSERT_FALSE(hold_on_dispatch_at(&hd, CMD_HALT, 1u, 200u));
    TEST_ASSERT_FALSE(hold_timeout(&hd, 219u, 20u));
    TEST_ASSERT_EQUAL_UINT8(1u, hd.dn);
    TEST_ASSERT_FALSE(hold_timeout(&hd, 220u, 20u));                    /* no hold was pending */
    TEST_ASSERT_EQUAL_UINT8(0u, hd.dn);
    TEST_ASSERT_FALSE(hold_already_dispatched(&hd, &a));
    /* non-sniffed types are never remembered */
    TEST_ASSERT_FALSE(hold_on_dispatch_at(&hd, CMD_MOVE_ABS, 9u, 300u));
    TEST_ASSERT_EQUAL_UINT8(0u, hd.dn);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_each_sniffed_frame_every_split);
    RUN_TEST(test_reported_once_over_many_ticks);
    RUN_TEST(test_rejects);
    RUN_TEST(test_sync_inside_payload);
    RUN_TEST(test_two_frames_one_tick_and_cause);
    RUN_TEST(test_hold_blocks_until_dispatched);
    RUN_TEST(test_hold_latest_and_timeout);
    RUN_TEST(test_dispatched_before_sniffed);
    return UNITY_END();
}
