/* Validator E - pure link parts (independent suite): VALID boundary (modular across 2^32), TX class
 * wire order / drop policy, EVENT queue SEQ and overflow accounting. Expected values from the ICD
 * text (§2.2, §2.4, §5.3) written out here; no helper of Implementer A is used.
 *
 * Verifies: FW-CMD-002, FW-STR-004 (drop accounting), FW-STR-002 / IF-011 (wire order D > R > E,
 *           responses never dropped), FW-STR-006 (SEQ incl. drops, depth >= 8)
 * TC: TC-FW-CMD-002-01 (U part), TC-IF-011-01 (U part), TC-FW-STR-004-01 (U part)
 */
#include "../val_common/val_io.h"

#include "evq.h"
#include "fw_config.h"
#include "txsched.h"
#include "valid.h"

void setUp(void) {}
void tearDown(void) {}

/* ---------------------------------------------------------------- VALID (ICD §5.3) */
static void check_boundary(uint32_t t_apply)
{
    valid_t v;
    uint32_t t;
    valid_init(&v, t_apply - 5000u);
    TEST_ASSERT_FALSE(valid_at(&v, t_apply - 4000u));                  /* boot: 0 */
    TEST_ASSERT_EQUAL_UINT32(t_apply, valid_set(&v, true, t_apply));    /* response t = processing time */
    for (t = 1u; t <= 3000u; t += 7u) {
        TEST_ASSERT_FALSE(valid_at(&v, t_apply - t));                   /* earlier frames: old value */
        TEST_ASSERT_TRUE(valid_at(&v, t_apply + t - 1u));               /* not earlier: new value */
    }
    TEST_ASSERT_TRUE(valid_at(&v, t_apply));                            /* equal time: new value */
    /* SET_VALID 0 later: boundary moves, older frames keep 1 */
    TEST_ASSERT_EQUAL_UINT32(t_apply + 100000u, valid_set(&v, false, t_apply + 100000u));
    TEST_ASSERT_TRUE(valid_at(&v, t_apply + 99999u));
    TEST_ASSERT_FALSE(valid_at(&v, t_apply + 100000u));
}

static void test_valid_boundary_and_wrap(void)
{
    check_boundary(1000000u);
    check_boundary(0xFFFFFFFFu);          /* boundary at the last µs before the wrap */
    check_boundary(0x00000000u);          /* boundary at the wrap */
    check_boundary(0xFFFFF000u);
    check_boundary(0x80000000u);
}

static void test_valid_auto_clear_once(void)
{
    valid_t v;
    valid_init(&v, 0u);
    (void)valid_set(&v, true, 1000u);
    TEST_ASSERT_TRUE(valid_clear(&v, 2000u));           /* was 1 -> EVENT VALID_CLEARED */
    TEST_ASSERT_FALSE(valid_at(&v, 2000u));
    TEST_ASSERT_TRUE(valid_at(&v, 1999u));              /* frames before the stop keep 1 */
    TEST_ASSERT_FALSE(valid_clear(&v, 3000u));          /* already 0: no second event */
    TEST_ASSERT_TRUE(valid_at(&v, 1999u));              /* earlier boundary kept */
}

static void test_valid_settle_keeps_semantics(void)
{
    valid_t v;
    uint32_t t;
    valid_init(&v, 0xFFF00000u);
    (void)valid_set(&v, true, 0xFFF00000u);
    for (t = 0u; t < 3000u; t++) {                      /* 3000 s of 1 kHz ticks, step 1 s */
        valid_settle(&v, 0xFFF00000u + t * 1000000u);
    }
    TEST_ASSERT_TRUE(valid_at(&v, 0xFFF00000u + 2999u * 1000000u));
    TEST_ASSERT_TRUE(valid_at(&v, 0xFFF00000u + 2999u * 1000000u - 900000u));
}

/* ---------------------------------------------------------------- TX classes (ICD §2.4) */
static void test_wire_order_and_drop_policy(void)
{
    static uint8_t bd[TX_D_BYTES], br[TX_R_BYTES], be[TX_E_BYTES];
    txq_t d, r, e;
    uint8_t fr[PROTO_FRAME_MAX], out[256], cls = 9u;
    uint16_t n, i;
    uint32_t r_pushed = 0u;
    txq_init(&d, bd, (uint16_t)sizeof bd);
    txq_init(&r, br, (uint16_t)sizeof br);
    txq_init(&e, be, (uint16_t)sizeof be);
    memset(fr, 0x11, sizeof fr);
    TEST_ASSERT_TRUE(txq_push(&e, fr, PROTO_EVENT_FRAME_LEN));
    TEST_ASSERT_TRUE(txq_push(&r, fr, 9u));
    TEST_ASSERT_TRUE(txq_push(&d, fr, PROTO_DATA_FRAME_LEN));
    n = txsched_next(&d, &r, &e, out, &cls);
    TEST_ASSERT_EQUAL_UINT16(PROTO_DATA_FRAME_LEN, n);
    TEST_ASSERT_EQUAL_UINT8(0u, cls);                    /* DATA first */
    n = txsched_next(&d, &r, &e, out, &cls);
    TEST_ASSERT_EQUAL_UINT8(1u, cls);                    /* then responses */
    n = txsched_next(&d, &r, &e, out, &cls);
    TEST_ASSERT_EQUAL_UINT8(2u, cls);                    /* then EVENT */
    TEST_ASSERT_EQUAL_UINT16(0u, txsched_next(&d, &r, &e, out, &cls));
    /* class D holds exactly TX_D_SLOTS DATA frames; the next push fails (producer counts a drop) */
    for (i = 0u; i < TX_D_SLOTS; i++) {
        TEST_ASSERT_TRUE(txq_push(&d, fr, PROTO_DATA_FRAME_LEN));
    }
    TEST_ASSERT_FALSE(txq_push(&d, fr, PROTO_DATA_FRAME_LEN));
    /* class R: a 168 B response always fits while txq_free >= 168 (dispatcher back-pressure, DEF-P1-07) */
    while (txq_free(&r) >= PROTO_FRAME_MAX) {
        TEST_ASSERT_TRUE(txq_push(&r, fr, PROTO_FRAME_MAX));
        r_pushed++;
    }
    TEST_ASSERT_TRUE(r_pushed >= 5u);                     /* >= 4 outstanding maximum responses (ICD §9.1) */
    /* frames come out whole and in FIFO order within a class */
    for (i = 0u; i < 3u; i++) {
        uint8_t a[PROTO_FRAME_MAX], z[256];
        txq_t q;
        static uint8_t qb[64];
        txq_init(&q, qb, (uint16_t)sizeof qb);
        memset(a, (int)(0x40 + i), sizeof a);
        TEST_ASSERT_TRUE(txq_push(&q, a, 20u));
        a[0] = 0x99u;
        TEST_ASSERT_TRUE(txq_push(&q, a, 21u));
        TEST_ASSERT_EQUAL_UINT16(20u, txq_pop(&q, z));
        TEST_ASSERT_EQUAL_HEX8((uint8_t)(0x40 + i), z[0]);
        TEST_ASSERT_EQUAL_UINT16(21u, txq_pop(&q, z));
        TEST_ASSERT_EQUAL_HEX8(0x99u, z[0]);
    }
}

/* ---------------------------------------------------------------- EVENT queue (ICD §2.2) */
static void test_event_seq_counts_drops(void)
{
    evq_t q;
    event_t ev;
    uint8_t seq, last = 0u;
    uint32_t i, popped = 0u;
    evq_init(&q);
    for (i = 0u; i < 20u; i++) {
        evq_push(&q, i, 1u, (uint16_t)i, 0, 0);
    }
    TEST_ASSERT_TRUE(evq_count(&q) >= 8u);               /* FW-STR-006: depth >= 8 */
    TEST_ASSERT_EQUAL_UINT16(20u - evq_count(&q), q.overflows);
    while (evq_pop(&q, &ev, &seq)) {
        TEST_ASSERT_EQUAL_UINT8((uint8_t)popped, seq);   /* SEQ = event counter of the kept events */
        last = seq;
        popped++;
    }
    evq_push(&q, 99u, 2u, 0u, 0, 0);
    TEST_ASSERT_TRUE(evq_pop(&q, &ev, &seq));
    TEST_ASSERT_EQUAL_UINT8(20u, seq);                   /* gap = dropped events (SEQ incl. drops) */
    TEST_ASSERT_TRUE(seq != (uint8_t)(last + 1u));
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_valid_boundary_and_wrap),
        VAL_CASE(test_valid_auto_clear_once),
        VAL_CASE(test_valid_settle_keeps_semantics),
        VAL_CASE(test_wire_order_and_drop_policy),
        VAL_CASE(test_event_seq_counts_drops),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
