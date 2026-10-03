/* Pure command check against every check_vectors.json case (ref_cmdcheck oracle): STATUS and
 * detail, the exact NACK frame bytes, the request frame through the parser, no side effect
 * (context and parameter image unchanged after the call).
 * Verifies: FW-CMD-001, FW-CFG-003, SAF-FW-006, SAF-FW-012, SAF-FW-020, SAF-FW-021, SAF-FW-022,
 *           SAF-FW-023, SAF-FW-024, SAF-FW-026, FW-CMD-003, FW-MOT-009, D-30, D-31, D-40 c (DIAG_MEAS)
 */
#include <string.h>
#include <unity.h>

#include "check_ctx.h"
#include "frame.h"
#include "test_util.h"

void setUp(void) {}
void tearDown(void) {}

static uint32_t replay(const vec_check_t *tab, uint32_t n_vec)
{
    uint32_t i;
    uint32_t nacks = 0u;
    for (i = 0u; i < n_vec; i++) {
        const vec_check_t *v = &tab[i];
        params_t p, p_before;
        cmd_ctx_t c, c_before;
        cmd_verdict_t r;
        fparser_t fp;
        fp_frame_t f;
        cc_setup(v, &p, &c);
        p_before = p;
        c_before = c;

        /* the request frame of the vector parses to (type, payload) */
        fp_init(&fp);
        (void)fp_append(&fp, v->req_frame, v->req_frame_len, 0u);
        TEST_ASSERT_TRUE_MESSAGE(fp_poll(&fp, &f), v->name);
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(v->type, f.type, v->name);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(v->len, f.len, v->name);

        r = cmd_check(&c, f.type, f.payload, f.len);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(v->exp_status, r.status, v->name);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(v->exp_detail, r.detail, v->name);
        TEST_ASSERT_EQUAL_MEMORY_MESSAGE(&p_before, &p, sizeof p, v->name);     /* no side effect */
        TEST_ASSERT_EQUAL_MEMORY_MESSAGE(&c_before, &c, sizeof c, v->name);
        if (r.status != ST_OK) {
            uint8_t fr[PROTO_FRAME_MAX];
            uint16_t n = tu_nack_frame(v->type, v->seq, r.status, r.detail, fr);
            nacks++;
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(v->resp_len, n, v->name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(v->resp_frame, fr, n, v->name);
        }
    }
    return nacks;
}

static void test_all_check_vectors(void)        /* release / twin build: FEAT_HW_MEAS = 0 */
{
    TEST_ASSERT_TRUE(VEC_CHECK_N >= 500u);
    TEST_ASSERT_TRUE(replay(VEC_CHECK, VEC_CHECK_N) > 250u);
}

static void test_hw_meas_check_vectors(void)    /* measurement build (D-40 c, ICD v0.6 Appendix C) */
{
    TEST_ASSERT_TRUE(VEC_CHECK_MEAS_N >= 20u);
    TEST_ASSERT_TRUE(replay(VEC_CHECK_MEAS, VEC_CHECK_MEAS_N) > 5u);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_all_check_vectors);
    RUN_TEST(test_hw_meas_check_vectors);
    return UNITY_END();
}
