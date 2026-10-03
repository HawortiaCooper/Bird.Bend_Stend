/* Measured AFE rate: median of 16 periods (robust to single outliers), 0.1 SPS, mismatch hysteresis
 * free set/clear against afe.rate_tol_pct, reset.
 * Verifies: FW-AFE-004
 */
#include <unity.h>

#include "afe_rate.h"

void setUp(void) {}
void tearDown(void) {}

static void feed(afe_rate_t *r, uint32_t *t, uint32_t period, uint32_t n)
{
    uint32_t i;
    for (i = 0u; i < n; i++) {
        *t += period;
        afe_rate_push(r, *t);
    }
}

static void test_needs_16_periods(void)
{
    afe_rate_t r;
    uint32_t t = 0u;
    afe_rate_reset(&r);
    afe_rate_push(&r, t);
    feed(&r, &t, 12500u, 15u);
    TEST_ASSERT_EQUAL_UINT16(0u, afe_rate_dsps(&r));
    feed(&r, &t, 12500u, 1u);
    TEST_ASSERT_EQUAL_UINT16(800u, afe_rate_dsps(&r));
}

static void test_median_ignores_outliers(void)
{
    afe_rate_t r;
    uint32_t t = 0xFFFF0000u;                 /* across the 2^32 wrap */
    afe_rate_reset(&r);
    afe_rate_push(&r, t);
    feed(&r, &t, 12484u, 14u);                /* 80.1 SPS crystal error */
    feed(&r, &t, 25000u, 1u);                 /* one missed conversion */
    feed(&r, &t, 100u, 1u);                   /* one glitch */
    TEST_ASSERT_EQUAL_UINT16(801u, afe_rate_dsps(&r));
}

static void test_mismatch_set_and_clear(void)
{
    afe_rate_t r;
    uint32_t t = 0u;
    afe_rate_reset(&r);
    afe_rate_push(&r, t);
    feed(&r, &t, 100000u, 16u);               /* 10 SPS while 80 SPS configured */
    TEST_ASSERT_TRUE(afe_rate_eval(&r, 80u, 20u));
    TEST_ASSERT_TRUE(r.mismatch);
    TEST_ASSERT_FALSE(afe_rate_eval(&r, 80u, 20u));    /* no repeated change */
    TEST_ASSERT_TRUE(afe_rate_eval(&r, 10u, 20u));     /* matches 10 SPS */
    TEST_ASSERT_FALSE(r.mismatch);
    feed(&r, &t, 83333u, 16u);                /* 12.0 SPS = +20 %: not beyond 20 % */
    TEST_ASSERT_FALSE(afe_rate_eval(&r, 10u, 20u));
    feed(&r, &t, 82000u, 16u);                /* 12.2 SPS > +20 % */
    TEST_ASSERT_TRUE(afe_rate_eval(&r, 10u, 20u));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_needs_16_periods);
    RUN_TEST(test_median_ignores_outliers);
    RUN_TEST(test_mismatch_set_and_clear);
    return UNITY_END();
}
