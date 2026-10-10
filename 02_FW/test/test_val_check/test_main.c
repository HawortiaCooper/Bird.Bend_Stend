/* Validator E - command acceptance (independent suite; FW_test_plan v0.1 §2, level U).
 * Oracle: check_vectors.json (ref_cmdcheck) read in place via test/val_oracles/gen_val_vectors.py,
 * state translated by the validator (state_schema 4, ICD v0.7.5 (h): 23 state words, s[22] = ena_on;
 * parameter overrides via params.yaml raw form). `CV` lines = `vectors` replayed as a release / twin build
 * (hw_meas = 0), `CM` lines = `hw_meas_vectors` replayed as a measurement build (hw_meas = 1; DIAG_MEAS
 * STATIC_LEVEL sel PUL needs ENA at the disabled level, FWR-09 / D-40 c).
 * Per vector: STATUS + detail, the exact NACK frame bytes, `paused_after` (D-30/D-31), and no side
 * effect of the pure check on its context or the parameter image.
 *
 * Verifies: FW-CMD-001, FW-CFG-003, SAF-FW-020, SAF-FW-023, FW-PAR-004 (rule_h1)
 * TC: TC-FW-CMD-001-01 (check order), TC-FW-CFG-003-01, TC-FW-PAR-004-01 (H1 vectors);
 *     M2 TCs TC-SAF-FW-020-01 / TC-SAF-FW-023-03 (U part) are executed as a by-product
 */
#include "../val_common/val_io.h"

#include "cmd_check.h"
#include "frame.h"
#include "latches.h"
#include "payload.h"
#include "proto.h"

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

static void test_check_vectors(void)
{
    val_file_t v;
    uint32_t done = 0u, n_ok = 0u, n_nack = 0u, n_paused = 0u;
    val_open(&v, "check.txt");
    while (val_tok(&v, T)) {
        char name[160];
        uint8_t pl[PROTO_MAX_LEN], rf[PROTO_FRAME_MAX], b[PROTO_FRAME_MAX];
        uint16_t npl, nrf;
        uint32_t type, seq, st, detail, s[23], np, k;
        bool meas_line;
        int32_t paused_after;
        params_t prm, prm_before;
        cmd_ctx_t c, c_before;
        cmd_verdict_t vd;
        meas_line = strcmp(T, "CM") == 0;
        TEST_ASSERT_TRUE_MESSAGE(meas_line || strcmp(T, "CV") == 0, "line kind CV / CM");
        val_tok(&v, name);
        type = val_u32(&v);
        seq = val_u32(&v);
        val_tok(&v, T);
        npl = val_hex(T, pl, sizeof pl);
        st = val_u32(&v);
        detail = val_u32(&v);
        val_tok(&v, T);
        nrf = val_hex(T, rf, sizeof rf);
        paused_after = (int32_t)val_u32(&v);
        for (k = 0u; k < 23u; k++) s[k] = val_u32(&v);   /* state_schema 4 */
        params_set_defaults(&prm);
        np = val_u32(&v);
        for (k = 0u; k < np; k++) {
            uint32_t id = val_u32(&v), raw = val_u32(&v);
            const param_meta_t *m = param_find((uint16_t)id);
            TEST_ASSERT_NOT_NULL_MESSAGE(m, name);
            param_set_raw(&prm, m, raw);
            TEST_ASSERT_EQUAL_HEX32_MESSAGE(raw, param_get_raw(&prm, m), name);
        }
        memset(&c, 0, sizeof c);
        c.p = &prm;
        c.motion_state = (uint8_t)s[0];
        c.enabling_left_ms = (uint16_t)s[1];
        c.homed = s[2] != 0u;
        c.pos_um = (int32_t)s[3];
        c.estop_latched = s[4] != 0u;
        c.estop_input_open = s[5] != 0u;
        c.estop_closed_ms = s[6];
        c.halt_latched = s[7] != 0u;
        /* s[8], s[9] = retired STOP-button keys (ICD v0.5, D-36: kept in state_schema 2, never set) */
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, s[8], "retired stop_btn_active set in a vector");
        c.faults = (uint16_t)s[10];
        c.fault_causes = (uint16_t)s[11];
        c.limit_start = s[12] != 0u;
        c.limit_end = s[13] != 0u;
        c.afe_stale = s[14] != 0u;
        c.afe_saturated = s[15] != 0u;
        c.raw = (int32_t)s[16];
        c.drv_power = s[17] != 0u;
        c.alm_active = s[18] != 0u;
        c.nvm_record_valid = s[19] != 0u;
        c.paused = s[20] != 0u;
        c.unhomed_origin_um = (int32_t)s[21];         /* D-43 b (ICD v0.7) */
        c.ena_on = s[22] != 0u;                       /* ICD v0.7.5 (h), state_schema 4 (FWR-09) */
        c.hw_meas = meas_line;                        /* hw_meas_vectors: measurement build */
        c_before = c;
        prm_before = prm;

        vd = cmd_check(&c, (uint8_t)type, pl, npl);
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(st, vd.status, name);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(detail, vd.detail, name);
        /* no side effect of the check (ICD §4.1) */
        TEST_ASSERT_EQUAL_MEMORY_MESSAGE(&c_before, &c, sizeof c, name);
        TEST_ASSERT_EQUAL_MEMORY_MESSAGE(&prm_before, &prm, sizeof prm, name);
        if (st != ST_OK) {
            uint8_t nk[PROTO_NACK_LEN];
            uint16_t m;
            (void)payload_nack(vd.status, vd.detail, nk);
            m = frame_build((uint8_t)(type | PROTO_RESP_BIT), (uint8_t)seq, nk, PROTO_NACK_LEN, b);
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(nrf, m, name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(rf, b, nrf, name);
            n_nack++;
        } else {
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(0u, nrf, name);
            n_ok++;
        }
        if (paused_after >= 0) {
            TEST_ASSERT_EQUAL_MESSAGE(paused_after != 0, latch_paused_after(c.paused, (uint8_t)type, st == ST_OK),
                                      name);
            n_paused++;
        }
        done++;
    }
    val_close(&v, done);
    printf("VALCHECK ok=%u nack=%u paused_after=%u\n", n_ok, n_nack, n_paused);
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_check_vectors),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
