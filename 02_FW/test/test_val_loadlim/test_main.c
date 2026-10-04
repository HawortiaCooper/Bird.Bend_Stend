/* Validator E - FW load limit (pure loadlim) vs the shared loadlim_vectors.json (independent suite; FW_test_plan
 * v0.4 §2, level U). The vector file is translated by val_oracles/gen_val_vectors.py only if the validator's own
 * oracle (val_oracles/latch_ref.LoadLimit, written from ICD v0.6 §5.5 / D-40 d) reproduces every step.
 *
 * Core gate modelled: FAULT_CLEAR passes the reference to loadlim only when it clears a latched LOAD_LIMIT.
 * Verifies: SAF-FW-008, SAF-FW-009, SAF-FW-010 (next-sample effect of a threshold change), SAF-FW-011
 * TC: TC-SAF-FW-008-02, TC-SAF-FW-009-01 (U part), TC-SAF-FW-011-01 (U part)
 */
#include "../val_common/val_io.h"

#include "loadlim.h"

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

static bool is_rail(int32_t raw) { return raw == 0x7FFFFF || raw == -0x800000; }

static void test_loadlim_vectors(void)
{
    val_file_t v;
    uint32_t done = 0u, steps = 0u;
    val_open(&v, "loadlim.txt");
    while (val_tok(&v, T)) {
        char name[96], msg[200];
        loadlim_t l;
        int32_t mn, mx, rg, last = 0;
        bool latched = false;                   /* LOAD_LIMIT latch of the core (cmd.c calls on_clear only
                                                   when FAULT_CLEAR actually clears it, ICD v0.7 OI-B-M2-03) */
        uint32_t ts, n, i;
        TEST_ASSERT_EQUAL_STRING("L", T);
        val_tok(&v, name);
        mn = (int32_t)val_u32(&v); mx = (int32_t)val_u32(&v); ts = val_u32(&v); rg = (int32_t)val_u32(&v);
        n = val_u32(&v);
        loadlim_init(&l, mn, mx, (uint8_t)ts, rg);
        for (i = 0u; i < n; i++) {
            val_tok(&v, T);
            if (T[0] == 'S') {
                int32_t raw = (int32_t)val_u32(&v);
                uint32_t trip = val_u32(&v), win = val_u32(&v);
                bool got = loadlim_check(&l, raw, is_rail(raw));
                last = raw;
                latched = latched || got;
                snprintf(msg, sizeof msg, "%s step %u sample %d: trip", name, i, (int)raw);
                TEST_ASSERT_EQUAL_MESSAGE((int)trip, (int)got, msg);
                snprintf(msg, sizeof msg, "%s step %u sample %d: regrow window", name, i, (int)raw);
                TEST_ASSERT_EQUAL_MESSAGE((int)win, (int)l.regrow_on, msg);
            } else if (T[0] == 'C') {
                uint32_t win = val_u32(&v);
                if (latched) {
                    loadlim_on_clear(&l, last, is_rail(last));
                    latched = false;
                }
                snprintf(msg, sizeof msg, "%s step %u fault_clear: regrow window", name, i);
                TEST_ASSERT_EQUAL_MESSAGE((int)win, (int)l.regrow_on, msg);
            } else if (T[0] == 'G') {
                int32_t a = (int32_t)val_u32(&v), b = (int32_t)val_u32(&v);
                uint32_t t2 = val_u32(&v);
                int32_t r2 = (int32_t)val_u32(&v);
                uint32_t win = val_u32(&v);
                loadlim_config(&l, a, b, (uint8_t)t2, r2);
                snprintf(msg, sizeof msg, "%s step %u config: regrow window kept", name, i);
                TEST_ASSERT_EQUAL_MESSAGE((int)win, (int)l.regrow_on, msg);
            } else {
                TEST_FAIL_MESSAGE(T);
            }
            steps++;
        }
        done++;
    }
    val_close(&v, done);
    printf("VALLOADLIM cases=%u steps=%u\n", done, steps);
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_loadlim_vectors),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
