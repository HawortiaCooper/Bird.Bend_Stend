/* RCC->CSR -> RST_* with the precedence of FW_design §5.1 (PINRSTF accompanies every internal reset,
 * BORRSTF every POR).
 * Verifies: SAF-FW-018 (reset cause), SAF-FW-019 (IWDG reset reported)
 */
#include <unity.h>

#include "proto_gen.h"
#include "resetcause.h"

void setUp(void) {}
void tearDown(void) {}

static void test_precedence(void)
{
    TEST_ASSERT_EQUAL_UINT8(RST_UNKNOWN, resetcause(0u));
    TEST_ASSERT_EQUAL_UINT8(RST_PIN, resetcause(RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_POWER_ON, resetcause(RCSR_PORRSTF | RCSR_BORRSTF | RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_BROWN_OUT, resetcause(RCSR_BORRSTF | RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_SOFTWARE, resetcause(RCSR_SFTRSTF | RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_IWDG, resetcause(RCSR_IWDGRSTF | RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_WWDG, resetcause(RCSR_WWDGRSTF | RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_LOW_POWER, resetcause(RCSR_LPWRRSTF | RCSR_IWDGRSTF | RCSR_PINRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_IWDG, resetcause(RCSR_IWDGRSTF | RCSR_SFTRSTF | RCSR_PORRSTF));
    TEST_ASSERT_EQUAL_UINT8(RST_UNKNOWN, resetcause(0x01FFFFFFu));   /* only non-flag bits */
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_precedence);
    return UNITY_END();
}
