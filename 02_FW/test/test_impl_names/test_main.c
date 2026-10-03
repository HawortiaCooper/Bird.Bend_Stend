/* Every generated protocol identifier (proto_gen.h) equals the independent ref_codec tables
 * (a missing/renamed macro is a compile error of vec_names.h, a different value a failure);
 * the request-length lookup equals CMD_REQ_LEN_*.
 * Verifies: IF-001, IF-012 (GF-08 name single source)
 */
#include <unity.h>

#include "proto.h"
#include "vec_names.h"

void setUp(void) {}
void tearDown(void) {}

static void test_names_equal_ref_codec(void)
{
    uint32_t i;
    TEST_ASSERT_TRUE(VEC_NAMES_N > 150u);
    for (i = 0u; i < VEC_NAMES_N; i++) {
        TEST_ASSERT_EQUAL_HEX32_MESSAGE(VEC_NAMES[i].expect, VEC_NAMES[i].actual, VEC_NAMES[i].name);
    }
}

static void test_req_len_lookup(void)
{
    uint32_t t;
    uint32_t defined = 0u;
    for (t = 0u; t < 256u; t++) {
        int16_t n = proto_req_len((uint8_t)t);
        if (n >= 0) {
            defined++;
            TEST_ASSERT_TRUE(PROTO_TYPE_IS_CMD(t));
        }
    }
    TEST_ASSERT_EQUAL_UINT32(27u, defined);                 /* ICD v0.6: + DIAG_MEAS 0x3D */
    TEST_ASSERT_EQUAL_INT16(17, proto_req_len(CMD_MOVE_UNTIL_LOAD));
    TEST_ASSERT_EQUAL_INT16(-1, proto_req_len(0x3Fu));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_names_equal_ref_codec);
    RUN_TEST(test_req_len_lookup);
    return UNITY_END();
}
