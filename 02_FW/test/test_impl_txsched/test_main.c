/* TX class queues: whole-frame push, free = largest frame, ring wrap, wire order D > R > E.
 * Verifies: FW-STR-002 (DATA first), FW-STR-004 (D full -> push refused), IF-011,
 *           FW-CMD-001 (R never loses a frame it accepted)
 */
#include <string.h>
#include <unity.h>

#include "fw_config.h"
#include "txsched.h"

void setUp(void) {}
void tearDown(void) {}

static uint8_t bd[TX_D_BYTES], br[TX_R_BYTES], be[TX_E_BYTES];
static txq_t qd, qr, qe;

static void init(void)
{
    txq_init(&qd, bd, (uint16_t)sizeof bd);
    txq_init(&qr, br, (uint16_t)sizeof br);
    txq_init(&qe, be, (uint16_t)sizeof be);
}

static void fill(uint8_t *f, uint16_t n, uint8_t tag)
{
    uint16_t i;
    for (i = 0u; i < n; i++) {
        f[i] = (uint8_t)(tag + i);
    }
}

static void test_d_mailbox_two_frames(void)
{
    uint8_t f[PROTO_DATA_FRAME_LEN];
    init();
    fill(f, sizeof f, 1u);
    TEST_ASSERT_EQUAL_UINT16(PROTO_DATA_FRAME_LEN * 2u + 1u, txq_free(&qd));
    TEST_ASSERT_TRUE(txq_push(&qd, f, sizeof f));
    TEST_ASSERT_TRUE(txq_push(&qd, f, sizeof f));
    TEST_ASSERT_FALSE(txq_push(&qd, f, sizeof f));            /* third DATA frame dropped */
    TEST_ASSERT_EQUAL_UINT16(0u, txq_free(&qd));
}

static void test_wire_order_d_r_e(void)
{
    uint8_t d[PROTO_DATA_FRAME_LEN], r[9], e[PROTO_EVENT_FRAME_LEN], out[256];
    uint8_t cls;
    init();
    fill(d, sizeof d, 0x10u);
    fill(r, sizeof r, 0x20u);
    fill(e, sizeof e, 0x30u);
    TEST_ASSERT_TRUE(txq_push(&qe, e, sizeof e));
    TEST_ASSERT_TRUE(txq_push(&qr, r, sizeof r));
    TEST_ASSERT_TRUE(txq_push(&qd, d, sizeof d));
    TEST_ASSERT_EQUAL_UINT16(sizeof d, txsched_next(&qd, &qr, &qe, out, &cls));
    TEST_ASSERT_EQUAL_UINT8(0u, cls);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(d, out, sizeof d);
    TEST_ASSERT_TRUE(txq_push(&qd, d, sizeof d));            /* a new DATA frame overtakes R/E */
    TEST_ASSERT_EQUAL_UINT16(sizeof d, txsched_next(&qd, &qr, &qe, out, &cls));
    TEST_ASSERT_EQUAL_UINT16(sizeof r, txsched_next(&qd, &qr, &qe, out, &cls));
    TEST_ASSERT_EQUAL_UINT8(1u, cls);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(r, out, sizeof r);
    TEST_ASSERT_EQUAL_UINT16(sizeof e, txsched_next(&qd, &qr, &qe, out, &cls));
    TEST_ASSERT_EQUAL_UINT8(2u, cls);
    TEST_ASSERT_EQUAL_UINT16(0u, txsched_next(&qd, &qr, &qe, out, &cls));
}

static void test_r_ring_wrap_and_reserve(void)
{
    uint8_t f[PROTO_FRAME_MAX], out[256];
    uint32_t pushed = 0u, popped = 0u, k;
    init();
    /* fill/drain 50 times with frames of varying size: the content survives every wrap */
    for (k = 0u; k < 50u; k++) {
        uint16_t n = (uint16_t)(9u + (k * 37u) % 160u);
        fill(f, n, (uint8_t)k);
        while (txq_free(&qr) >= TX_R_RESERVE) {
            TEST_ASSERT_TRUE(txq_push(&qr, f, n));
            pushed++;
        }
        TEST_ASSERT_TRUE(txq_free(&qr) < TX_R_RESERVE);
        while (!txq_empty(&qr)) {
            TEST_ASSERT_EQUAL_UINT16(n, txq_pop(&qr, out));
            TEST_ASSERT_EQUAL_HEX8_ARRAY(f, out, n);
            popped++;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(pushed, popped);
    TEST_ASSERT_EQUAL_UINT16(sizeof br - 1u > 255u ? 255u : sizeof br - 1u, txq_free(&qr));
}

static void test_reject_bad_sizes(void)
{
    uint8_t f[300];
    init();
    memset(f, 0, sizeof f);
    TEST_ASSERT_FALSE(txq_push(&qr, f, 0u));
    TEST_ASSERT_FALSE(txq_push(&qr, f, 256u));
    TEST_ASSERT_TRUE(txq_empty(&qr));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_d_mailbox_two_frames);
    RUN_TEST(test_wire_order_d_r_e);
    RUN_TEST(test_r_ring_wrap_and_reserve);
    RUN_TEST(test_reject_bad_sizes);
    return UNITY_END();
}
