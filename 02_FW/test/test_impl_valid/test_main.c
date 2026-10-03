/* VALID boundary (ICD §5.3): (int32)(t - t_apply) >= 0 carries the new value, incl. the 2^32 wrap;
 * automatic clear reports "was 1" once; settle keeps the comparison inside the 2^31 window.
 * Verifies: FW-CMD-002, SAF-FW-001 (VALID clear)
 */
#include <unity.h>

#include "valid.h"

void setUp(void) {}
void tearDown(void) {}

static void test_boundary(void)
{
    valid_t v;
    valid_init(&v, 0u);
    TEST_ASSERT_FALSE(valid_at(&v, 5000u));
    TEST_ASSERT_EQUAL_UINT32(7000123u, valid_set(&v, true, 7000123u));
    TEST_ASSERT_FALSE(valid_at(&v, 7000122u));          /* earlier frame: old value */
    TEST_ASSERT_TRUE(valid_at(&v, 7000123u));           /* not earlier: new value */
    TEST_ASSERT_TRUE(valid_at(&v, 7000124u));
}

static void test_wrap_2_32(void)
{
    valid_t v;
    valid_init(&v, 0xFFFFFF00u);
    (void)valid_set(&v, true, 4294967000u);            /* vector set_valid_clear_resp t_us */
    TEST_ASSERT_FALSE(valid_at(&v, 4294966999u));
    TEST_ASSERT_TRUE(valid_at(&v, 4294967295u));
    TEST_ASSERT_TRUE(valid_at(&v, 200u));               /* after the wrap */
}

static void test_clear_once(void)
{
    valid_t v;
    valid_init(&v, 0u);
    (void)valid_set(&v, true, 1000u);
    TEST_ASSERT_TRUE(valid_clear(&v, 2000u));           /* was 1 -> VALID_CLEARED */
    TEST_ASSERT_FALSE(valid_clear(&v, 2001u));          /* already 0 -> no second event */
    TEST_ASSERT_TRUE(valid_at(&v, 1999u));              /* frames before the stop keep VALID */
    TEST_ASSERT_FALSE(valid_at(&v, 2000u));
}

static void test_settle_long_run(void)
{
    valid_t v;
    uint32_t t;
    valid_init(&v, 0u);
    (void)valid_set(&v, true, 10u);
    for (t = 10u; t < 3000000000u; t += 1000000u) {   /* 50 min of 1 s settles */
        valid_settle(&v, t);
        TEST_ASSERT_TRUE(valid_at(&v, t));
    }
    TEST_ASSERT_TRUE(valid_at(&v, 3000000000u));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_boundary);
    RUN_TEST(test_wrap_2_32);
    RUN_TEST(test_clear_once);
    RUN_TEST(test_settle_long_run);
    return UNITY_END();
}
