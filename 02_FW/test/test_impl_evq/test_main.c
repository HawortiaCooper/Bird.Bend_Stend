/* EVENT ring: FIFO order, SEQ = event counter incl. dropped events, overflow count, u8 wrap.
 * Verifies: FW-STR-006, IF-011
 */
#include <unity.h>

#include "evq.h"

void setUp(void) {}
void tearDown(void) {}

static void test_fifo_and_seq(void)
{
    evq_t q;
    event_t e;
    uint8_t s;
    evq_init(&q);
    evq_push(&q, 100u, 1u, 2u, 3, 4);
    evq_push(&q, 200u, 5u, 6u, -7, -8);
    TEST_ASSERT_EQUAL_UINT8(2u, evq_count(&q));
    TEST_ASSERT_TRUE(evq_pop(&q, &e, &s));
    TEST_ASSERT_EQUAL_UINT8(0u, s);
    TEST_ASSERT_EQUAL_UINT32(100u, e.t_us);
    TEST_ASSERT_EQUAL_UINT16(1u, e.code);
    TEST_ASSERT_EQUAL_INT32(4, e.value2);
    TEST_ASSERT_TRUE(evq_pop(&q, &e, &s));
    TEST_ASSERT_EQUAL_UINT8(1u, s);
    TEST_ASSERT_EQUAL_INT32(-7, e.value);
    TEST_ASSERT_FALSE(evq_pop(&q, &e, &s));
}

static void test_overflow_counts_and_seq_gap(void)
{
    evq_t q;
    event_t e;
    uint8_t s, i;
    evq_init(&q);
    for (i = 0u; i < EVQ_SIZE + 3u; i++) {
        evq_push(&q, i, (uint16_t)i, 0u, 0, 0);
    }
    TEST_ASSERT_EQUAL_UINT16(3u, q.overflows);
    TEST_ASSERT_EQUAL_UINT8(EVQ_SIZE, evq_count(&q));
    for (i = 0u; i < EVQ_SIZE; i++) {
        TEST_ASSERT_TRUE(evq_pop(&q, &e, &s));
        TEST_ASSERT_EQUAL_UINT8(i, s);
    }
    evq_push(&q, 0u, 99u, 0u, 0, 0);            /* next SEQ shows the gap of 3 dropped events */
    TEST_ASSERT_TRUE(evq_pop(&q, &e, &s));
    TEST_ASSERT_EQUAL_UINT8(EVQ_SIZE + 3u, s);
}

static void test_seq_wraps_u8(void)
{
    evq_t q;
    event_t e;
    uint8_t s;
    uint16_t i;
    evq_init(&q);
    for (i = 0u; i < 300u; i++) {
        evq_push(&q, 0u, 1u, 0u, 0, 0);
        TEST_ASSERT_TRUE(evq_pop(&q, &e, &s));
        TEST_ASSERT_EQUAL_UINT8((uint8_t)i, s);
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_fifo_and_seq);
    RUN_TEST(test_overflow_counts_and_seq_gap);
    RUN_TEST(test_seq_wraps_u8);
    return UNITY_END();
}
