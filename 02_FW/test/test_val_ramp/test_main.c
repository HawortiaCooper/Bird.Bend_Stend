/* Validator E - ramp generator and controlled-stop path vs the shared motion vectors (independent suite;
 * FW_test_plan v0.3 §2, level U). PRE-WRITTEN for M2: compiled against Implementer A's pure API
 * (src/pure/ramp.h, stepgen.h); while those headers are absent every case is IGNORED with "M2 pending"
 * (an IGNORE is never M2 evidence, plan §5.2).
 *
 * Oracle: vectors/motion_vectors.json (Integrator, read in place by val_oracles/gen_val_vectors.py into
 * motion.txt; that script refuses to write the file unless the validator's independent oracle
 * val_oracles/ramp_ref.py reproduces every vector period exactly). Tolerances: each period ±1 tick, the
 * sum ±floor(N/1000) ticks (SRS FW-MOT-003 literal; OBS-E-M2-02), period count exact.
 *
 * Event mapping (motion_vectors.json `events`, after_step = steps already emitted; A's API of 2026-10-04):
 *   controlled_stop      -> ramp_stop(alpha_stop, extra 0, floor 0 = c_last)
 *   jog v (first, step 0) -> ramp_start(n = unbounded); jog v != 0 later -> ramp_set_speed; jog 0 -> ramp_stop
 *
 * Verifies: FW-MOT-003, SAF-FW-003 (stop sizing, non-decreasing stop periods, path choice), FW-MOT-005
 * TC: TC-FW-MOT-003-01, TC-FW-MOT-003-02, TC-SAF-FW-003-02 (U part)
 */
#include "../val_common/val_io.h"

#include <math.h>

#if defined(__has_include)
#if __has_include("ramp.h") && __has_include("stepgen.h")
#define VAL_HAVE_RAMP 1
#include "ramp.h"
#include "stepgen.h"
#endif
#endif
#ifndef VAL_HAVE_RAMP
#define VAL_HAVE_RAMP 0
#endif

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

#if VAL_HAVE_RAMP
#define VAL_UNBOUNDED 0x3FFFFFFFu

static float f32_bits(uint32_t b)
{
    float f;
    memcpy(&f, &b, sizeof f);
    return f;
}

typedef struct {
    char kind;
    uint32_t after, v;
} val_ev_t;

static uint32_t val_fail;

static void case_fail(const char *m)
{
    if (val_fail < 20u) printf("VALRAMP FAIL %s\n", m);
    val_fail++;
}

static void run_case(val_file_t *v, uint32_t *n_cases)
{
    bool bad = false;
    char name[96], msg[200];
    uint32_t f, spm_bits, vu, au, du, asu, n_steps, n_ev, i, n_per, sum_exp, tol_p, tol_s;
    val_ev_t ev[8];
    ramp_t r;
    uint32_t gen = 0u, worst = 0u, e_i = 0u;
    int64_t sum = 0;
    float spm, chw;
    double fd, al_a, al_d, al_s;

    TEST_ASSERT_TRUE(val_tok(v, name));
    f = val_u32(v); val_tok(v, T); spm_bits = (uint32_t)strtoul(T, NULL, 16);
    vu = val_u32(v); au = val_u32(v); du = val_u32(v); asu = val_u32(v); n_steps = val_u32(v); n_ev = val_u32(v);
    TEST_ASSERT_TRUE_MESSAGE(n_ev <= 8u, name);
    for (i = 0u; i < n_ev; i++) {
        val_tok(v, T); ev[i].kind = T[0]; ev[i].after = val_u32(v); ev[i].v = val_u32(v);
    }
    n_per = val_u32(v); sum_exp = val_u32(v); tol_p = val_u32(v); tol_s = val_u32(v);

    fd = (double)f;
    spm = f32_bits(spm_bits);
    al_a = ramp_steps_of(au, spm);
    al_d = ramp_steps_of(du, spm);
    al_s = asu ? ramp_steps_of(asu, spm) : al_d;
    chw = (float)(fd / 100000.0);                           /* 100 kHz dictionary cap: never binding here */
    if (n_ev > 0u && ev[0].kind == 'J') {
        ramp_start(&r, fd, al_a, al_d, ramp_steps_of(ev[0].v, spm), chw, VAL_UNBOUNDED);
        e_i = 1u;
    } else {
        ramp_start(&r, fd, al_a, al_d, ramp_steps_of(vu, spm), chw, n_steps);
    }
    for (i = 0u; i < n_per; i++) {
        uint32_t want = val_u32(v), got, d;
        while (e_i < n_ev && ev[e_i].after == gen) {
            if (ev[e_i].kind == 'S' || ev[e_i].v == 0u) {
                (void)ramp_stop(&r, al_s, 0u, 0.0f);          /* floor = c_last (inside ramp_stop) */
            } else {
                ramp_set_speed(&r, ramp_steps_of(ev[e_i].v, spm), al_a, al_d);
            }
            e_i++;
        }
        if (r.rem < 1u) {
            if (!bad) {
                snprintf(msg, sizeof msg, "%s: generator ended at %u of %u periods", name, gen, n_per);
                case_fail(msg);
            }
            bad = true;
            continue;                                   /* keep reading the case's tokens */
        }
        got = ramp_next(&r);
        gen++;
        d = got > want ? got - want : want - got;
        if (d > worst) worst = d;
        if (d > tol_p && !bad) {
            snprintf(msg, sizeof msg, "%s period %u: got %u want %u (> +-%u tick)", name, i, got, want, tol_p);
            case_fail(msg);
            bad = true;
        }
        sum += got;
    }
    if ((n_ev == 0u || ev[n_ev - 1u].kind == 'S' || ev[n_ev - 1u].v == 0u) && r.rem != 0u) {
        snprintf(msg, sizeof msg, "%s: %u extra periods after the vector's last one", name, r.rem);
        case_fail(msg);
    }
    if (llabs(sum - (int64_t)sum_exp) > (int64_t)tol_s) {
        snprintf(msg, sizeof msg, "%s: sum %lld want %u (> +-%u ticks)", name, (long long)sum, sum_exp, tol_s);
        case_fail(msg);
    }
    printf("VALRAMP %s n=%u worst=%u sum_err=%lld\n", name, n_per, worst, (long long)(sum - (int64_t)sum_exp));
    (*n_cases)++;
}

static void run_path(val_file_t *v)
{
    uint32_t p = val_u32(v), spm_bits, a_stop, want;
    float spm;
    double al_s;
    char msg[120];
    val_tok(v, T); spm_bits = (uint32_t)strtoul(T, NULL, 16);
    a_stop = val_u32(v); want = val_u32(v);
    spm = f32_bits(spm_bits);
    al_s = ramp_steps_of(a_stop, spm);
    if (stepgen_ctrl_path(p, ramp_stop_dist(90000000.0, (float)p, al_s), 90000000u) != (uint8_t)want) {
        snprintf(msg, sizeof msg, "ctrl path P=%u spm=%g a_stop=%u want %u", p, (double)spm, a_stop, want);
        case_fail(msg);
    }
}
#endif /* VAL_HAVE_RAMP */

static void test_motion_vectors(void)
{
#if VAL_HAVE_RAMP
    val_file_t v;
    uint32_t done = 0u, cases = 0u, paths = 0u;
    val_open(&v, "motion.txt");
    while (val_tok(&v, T)) {
        if (strcmp(T, "C") == 0) {
            run_case(&v, &cases);
        } else if (strcmp(T, "S") == 0) {
            run_path(&v);
            paths++;
        } else {
            TEST_FAIL_MESSAGE(T);
        }
        done++;
    }
    val_close(&v, done);
    printf("VALRAMP cases=%u ctrl_stop_paths=%u failures=%u\n", cases, paths, val_fail);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, val_fail, "motion vector failures (see VALRAMP FAIL lines)");
#else
    TEST_IGNORE_MESSAGE("M2 pending: src/pure/ramp.h / stepgen.h not present (Implementer A)");
#endif
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_motion_vectors),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
