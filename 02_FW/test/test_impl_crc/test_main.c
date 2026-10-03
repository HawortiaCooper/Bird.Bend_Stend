/* CRC-16 vectors (protocol_vectors.json crc16) + CRC-32 check value.
 * Verifies: IF-004, FW-NVM-002 (CRC-32)
 */
#include <unity.h>

#include "crc16.h"
#include "crc32.h"
#include "vec_crc.h"

void setUp(void) {}
void tearDown(void) {}

static void test_crc16_vectors(void)
{
    uint32_t i;
    for (i = 0u; i < VEC_CRC_N; i++) {
        TEST_ASSERT_EQUAL_HEX16_MESSAGE(VEC_CRC[i].crc, crc16_ccitt(VEC_CRC[i].data, VEC_CRC[i].len, CRC16_INIT),
                                        VEC_CRC[i].name);
    }
}

static void test_crc16_split_equals_whole(void)
{
    const uint8_t s[] = "123456789";
    uint16_t c = crc16_ccitt(s, 4u, CRC16_INIT);
    c = crc16_ccitt(&s[4], 5u, c);
    TEST_ASSERT_EQUAL_HEX16(0x29B1u, c);
}

static void test_crc32_check_value(void)
{
    const uint8_t s[] = "123456789";
    TEST_ASSERT_EQUAL_HEX32(0xCBF43926u, crc32_iso(s, 9u));
    TEST_ASSERT_EQUAL_HEX32(0x00000000u, crc32_iso(s, 0u));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_crc16_vectors);
    RUN_TEST(test_crc16_split_equals_whole);
    RUN_TEST(test_crc32_check_value);
    return UNITY_END();
}
