/* Validator E - M2 pure modules against validator oracles (independent suite; FW_test_plan v0.4 §2, level U).
 * Oracles: val_oracles/gen_val_vectors.py (sniff.txt: stop-sniffer oracle written from ICD §2 / FW_design §5.9.3;
 * rate.txt: median-of-16 rate oracle, FW-AFE-004), a simulated HX711 (datasheet: one bit per SCK rising edge, MSB
 * first, DOUT high from the 25th pulse) and the PWM-mode-2 timer model of R4 §1.6 for the halt decisions.
 *
 * Verifies: SAF-FW-002, SAF-FW-004, FW-AFE-001, FW-AFE-003, FW-AFE-004, FW-SW-001
 * TC: TC-SAF-FW-002-02, TC-SAF-FW-004-02, TC-FW-AFE-001-01, TC-FW-AFE-004-01, TC-FW-SW-001-01 (U part)
 */
#include "../val_common/val_io.h"

#include <stdbool.h>

#include "afe_rate.h"
#include "hx711_math.h"
#include "inputs.h"
#include "stepgen.h"
#include "stop_sniff.h"

/* ---- simulated HX711 for hx711_seq.h (validator model, not A's fake) ---- */
static uint32_t g_code;      /* 24-bit code to shift out */
static unsigned g_rises;     /* SCK rising edges in this read */
static bool g_sck;
static bool g_mask;
static unsigned g_mask_viol; /* SCK high while not masked */
#define HX_SCK_HI() do { g_sck = true; g_rises++; if (!g_mask) g_mask_viol++; } while (0)
#define HX_SCK_LO() do { g_sck = false; } while (0)
#define HX_DT() (g_rises == 0u ? false : (g_rises <= 24u ? (((g_code >> (24u - g_rises)) & 1u) != 0u) : true))
#define HX_MASK_ON() do { g_mask = true; } while (0)
#define HX_MASK_OFF() do { g_mask = false; } while (0)
#define HX_T_HIGH() do { } while (0)
#define HX_T_LOW() do { } while (0)
#include "hx711_seq.h"

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

/* ------------------------------------------------------------------ TC-SAF-FW-002-02 stop sniffer */
typedef struct { uint32_t pos; uint8_t type, seq, mode; } hit_t;

static void test_sniffer_vs_oracle(void)
{
    static uint8_t s[1024];
    val_file_t v;
    uint32_t done = 0u, total_hits = 0u;
    val_open(&v, "sniff.txt");
    while (val_tok(&v, T)) {
        char msg[160];
        uint16_t n, i, nch, off = 0u;
        uint32_t ne, k;
        hit_t exp[64], got[64];
        uint32_t ng = 0u;
        sniff_t sn;
        TEST_ASSERT_EQUAL_STRING("SN", T);
        val_tok(&v, T);
        n = val_hex(T, s, sizeof s);
        nch = (uint16_t)val_u32(&v);
        sniff_init(&sn, 1000u);                         /* arbitrary stream base */
        for (i = 0u; i < nch; i++) {
            uint16_t c = (uint16_t)val_u32(&v);
            sniff_hit_t h[16];
            uint8_t nh = sniff_scan(&sn, &s[off], c, h, 16u), j;
            for (j = 0u; j < nh && ng < 64u; j++) {
                got[ng].pos = h[j].pos - 1000u; got[ng].type = h[j].type; got[ng].seq = h[j].seq;
                got[ng].mode = h[j].mode; ng++;
            }
            off = (uint16_t)(off + c);
        }
        TEST_ASSERT_EQUAL_UINT16(n, off);
        ne = val_u32(&v);
        for (k = 0u; k < ne; k++) {
            exp[k].pos = val_u32(&v); exp[k].type = (uint8_t)val_u32(&v); exp[k].seq = (uint8_t)val_u32(&v);
            exp[k].mode = (uint8_t)val_u32(&v);
        }
        snprintf(msg, sizeof msg, "case %u: hits %u expected %u (each frame exactly once)", done, ng, ne);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(ne, ng, msg);
        for (k = 0u; k < ne; k++) {           /* hits are reported in stream order */
            snprintf(msg, sizeof msg, "case %u hit %u", done, k);
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(exp[k].pos, got[k].pos, msg);
            TEST_ASSERT_EQUAL_UINT8_MESSAGE(exp[k].type, got[k].type, msg);
            TEST_ASSERT_EQUAL_UINT8_MESSAGE(exp[k].seq, got[k].seq, msg);
            TEST_ASSERT_EQUAL_UINT8_MESSAGE(exp[k].mode, got[k].mode, msg);
        }
        total_hits += ne;
        done++;
    }
    val_close(&v, done);
    printf("VALSNIFF streams=%u hits=%u\n", done, total_hits);
}

/* RESUME is never sniffed (D-31), the hold blocks starts until the sniffed frame is dispatched (DEF-P1-04) */
static void test_sniff_hold_rules(void)
{
    sniff_hold_t h;
    sniff_hit_t hit = {CMD_HALT, 7u, 0u, 0u};
    TEST_ASSERT_FALSE(CMD_IS_SNIFFED(CMD_RESUME));
    TEST_ASSERT_TRUE(CMD_IS_SNIFFED(CMD_STOP) && CMD_IS_SNIFFED(CMD_HALT) && CMD_IS_SNIFFED(CMD_PAUSE));
    hold_init(&h);
    TEST_ASSERT_FALSE(hold_blocks_start(&h));
    hold_set(&h, &hit, 100u);
    TEST_ASSERT_TRUE(hold_blocks_start(&h));
    TEST_ASSERT_FALSE(hold_on_dispatch(&h, CMD_HALT, 6u));           /* an older frame does not release it */
    TEST_ASSERT_FALSE(hold_on_dispatch(&h, CMD_MOVE_ABS, 7u));
    TEST_ASSERT_TRUE(hold_blocks_start(&h));
    TEST_ASSERT_TRUE(hold_on_dispatch(&h, CMD_HALT, 7u));
    TEST_ASSERT_FALSE(hold_blocks_start(&h));
    hold_set(&h, &hit, 100u);
    TEST_ASSERT_FALSE(hold_timeout(&h, 119u, 20u));
    TEST_ASSERT_TRUE(hold_timeout(&h, 120u, 20u));                    /* safe-side timeout releases */
    TEST_ASSERT_FALSE(hold_blocks_start(&h));
    TEST_ASSERT_EQUAL_UINT8(SC_PC_HALT, sniff_cause(&hit));
}

/* ------------------------------------------------------------------ TC-SAF-FW-004-02 halt decisions */
static void test_halt_decisions_every_cnt(void)
{
    static const uint32_t PW[3] = {225u, 900u, 9000u};
    const uint32_t guard = 45u;                                      /* 0.5 us at 90 MHz (FW_design §5.3) */
    uint32_t i, cnt, n = 0u;
    for (i = 0u; i < 3u; i++) {
        uint32_t arr = PW[i] * 3u - 1u;                              /* period = 3 PW */
        uint32_t ccr1 = arr + 1u - PW[i];                            /* PWM mode 2: pulse at the end */
        for (cnt = 0u; cnt <= arr; cnt++) {
            bool in_flight = cnt >= ccr1;
            bool complete = stepgen_halt_complete(cnt, ccr1, guard);
            bool cut = stepgen_abort_cuts(cnt, ccr1);
            /* CLEAN: if the output is forced inactive now (complete = false) no pulse may have started or
             * start within the guard -> no runt, nothing uncounted; if a pulse is in flight it must complete */
            if (in_flight) TEST_ASSERT_TRUE_MESSAGE(complete, "pulse in flight must complete (counted)");
            if (!complete) TEST_ASSERT_TRUE_MESSAGE(cnt + guard < ccr1, "forced inactive inside the guard window");
            TEST_ASSERT_EQUAL_MESSAGE(in_flight, cut, "TRUNCATE flags exactly a pulse in flight");
            n++;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(3u * 0u + 225u * 3u + 900u * 3u + 9000u * 3u, n);
}

static void test_stretch_extend_only(void)
{
    const uint32_t pw = 900u, guard = 45u, arr = 9999u, ccr1 = arr + 1u - pw;
    uint32_t cnt, nc;
    for (cnt = 0u; cnt <= arr; cnt += 7u) {
        for (nc = 5000u; nc < 30000u; nc += 997u) {
            bool ok = stepgen_stretch_ok(cnt, ccr1, arr, pw, guard, nc);
            if (ok) {
                TEST_ASSERT_TRUE_MESSAGE(nc >= arr + 1u, "a stretch never shortens the running interval");
                TEST_ASSERT_TRUE_MESSAGE(cnt + guard < ccr1, "no stretch with a pulse in flight / imminent");
                TEST_ASSERT_TRUE_MESSAGE(cnt + guard < nc - pw, "new compare must lie ahead of CNT");
            } else if (nc >= arr + 1u && cnt + guard < ccr1) {
                TEST_ASSERT_FALSE_MESSAGE(cnt + guard < nc - pw, "a safe stretch was refused");
            }
        }
    }
}

/* ------------------------------------------------------------------ TC-FW-AFE-001-01 HX711 read */
static void read_check(uint32_t code, uint8_t gain_channel, uint8_t pulses_exp)
{
    bool dout_high = false;
    uint32_t got;
    char msg[96];
    g_code = code & 0xFFFFFFu; g_rises = 0u; g_mask = false; g_mask_viol = 0u;
    got = hx711_shift_in(hx711_pulses(gain_channel), &dout_high);
    snprintf(msg, sizeof msg, "code 0x%06X gain %u", (unsigned)g_code, gain_channel);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(g_code, got, msg);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(pulses_exp, g_rises, msg);
    TEST_ASSERT_TRUE_MESSAGE(dout_high, msg);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, g_mask_viol, "SCK high outside the masked window");
    TEST_ASSERT_FALSE(g_sck);
    TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)((code & 0x800000u) ? (code | 0xFF000000u) : code),
                                    hx711_sign_extend(g_code), msg);
}

static void test_hx711_read_and_math(void)
{
    static const uint32_t C[] = {0u, 1u, 0xFFFFFFu, 0x7FFFFFu, 0x800000u, 0x123456u, 0xA5A5A5u, 0x7FFFFEu, 0x800001u};
    uint32_t i, r = 12345u;
    for (i = 0u; i < sizeof C / sizeof C[0]; i++) {
        read_check(C[i], AFE_GAIN_CHANNEL_A128, 25u);
        read_check(C[i], AFE_GAIN_CHANNEL_B32, 26u);
        read_check(C[i], AFE_GAIN_CHANNEL_A64, 27u);
    }
    for (i = 0u; i < 2000u; i++) {
        r = r * 1103515245u + 12345u;
        read_check(r >> 8, (uint8_t)(i % 3u == 0u ? AFE_GAIN_CHANNEL_A128 : (i % 3u == 1u ? AFE_GAIN_CHANNEL_B32
                                                                               : AFE_GAIN_CHANNEL_A64)),
                   (uint8_t)(i % 3u == 0u ? 25u : (i % 3u == 1u ? 26u : 27u)));
    }
    TEST_ASSERT_EQUAL_INT32(-8388608, hx711_sign_extend(0x800000u));
    TEST_ASSERT_EQUAL_INT32(8388607, hx711_sign_extend(0x7FFFFFu));
    TEST_ASSERT_TRUE(hx711_is_saturated(0x7FFFFFu) && hx711_is_saturated(0x800000u));
    TEST_ASSERT_FALSE(hx711_is_saturated(0x7FFFFEu) || hx711_is_saturated(0x800001u));
    TEST_ASSERT_TRUE(hx711_raw_at_rail(8388607) && hx711_raw_at_rail(-8388608));
    TEST_ASSERT_FALSE(hx711_raw_at_rail(8388606) || hx711_raw_at_rail(-8388607));
}

/* FW-AFE-003: exactly afe.settle_discard samples flagged after arming */
static void test_settle_discard(void)
{
    static const uint8_t N[] = {0u, 1u, 4u, 20u};
    uint32_t i, k;
    for (i = 0u; i < sizeof N; i++) {
        hx_settle_t s;
        uint32_t flagged = 0u;
        hx_settle_arm(&s, N[i]);
        for (k = 0u; k < 40u; k++) {
            if (hx_settle_take(&s)) flagged++;
        }
        TEST_ASSERT_EQUAL_UINT32(N[i], flagged);
    }
}

/* ------------------------------------------------------------------ TC-FW-AFE-004-01 rate */
static void test_rate_vs_oracle(void)
{
    val_file_t v;
    uint32_t done = 0u;
    val_open(&v, "rate.txt");
    while (val_tok(&v, T)) {
        afe_rate_t r;
        uint32_t nom, tol, n, i, dsps, mm;
        uint16_t got;
        char msg[96];
        TEST_ASSERT_EQUAL_STRING("AR", T);
        nom = val_u32(&v); tol = val_u32(&v); n = val_u32(&v);
        afe_rate_reset(&r);
        for (i = 0u; i < n; i++) afe_rate_push(&r, val_u32(&v));
        dsps = val_u32(&v); mm = val_u32(&v);
        got = afe_rate_dsps(&r);
        (void)afe_rate_eval(&r, nom, (uint8_t)tol);
        {   /* +-1 count + the 1 us quantisation of the integer median (relative 1/median_us) */
            uint32_t tl = 1u + (uint32_t)(2.0 * (double)dsps * (double)dsps / 1e7);
            snprintf(msg, sizeof msg, "case %u: dsps %u expected %u (+-%u)", done, got, dsps, tl);
            TEST_ASSERT_TRUE_MESSAGE((uint32_t)got + tl >= dsps && (uint32_t)got <= dsps + tl, msg);
        }
        if ((uint32_t)got == dsps) {
            snprintf(msg, sizeof msg, "case %u: mismatch flag (dsps %u, nominal %u)", done, got, nom);
            TEST_ASSERT_EQUAL_MESSAGE((int)mm, (int)r.mismatch, msg);
        }
        done++;
    }
    val_close(&v, done);
}

/* ------------------------------------------------------------------ TC-FW-SW-001-01 (U) release debounce */
static void test_release_debounce(void)
{
    static const uint16_t REL[] = {5u, 20u, 200u};
    uint32_t i, k;
    for (i = 0u; i < 3u; i++) {
        relf_t f;
        uint16_t rel = REL[i];
        bool fired = false;
        relf_init(&f, true);                               /* active */
        for (k = 0u; k < 10u; k++) TEST_ASSERT_FALSE(relf_sample(&f, true, false, rel));
        /* bounce: inactive for rel - 1 samples, then an edge restarts the count */
        for (k = 0u; k + 1u < rel; k++) TEST_ASSERT_FALSE(relf_sample(&f, false, false, rel));
        TEST_ASSERT_FALSE(relf_sample(&f, false, true, rel));
        TEST_ASSERT_FALSE(relf_released(&f, rel));
        for (k = 1u; k <= (uint32_t)rel + 5u; k++) {
            bool e = relf_sample(&f, false, false, rel);
            if (e) {
                TEST_ASSERT_EQUAL_UINT32_MESSAGE(rel, k, "release reported after exactly io.release_ms stable");
                TEST_ASSERT_FALSE_MESSAGE(fired, "release reported once");
                fired = true;
            }
        }
        TEST_ASSERT_TRUE(fired && relf_released(&f, rel));
    }
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_sniffer_vs_oracle),
        VAL_CASE(test_sniff_hold_rules),
        VAL_CASE(test_halt_decisions_every_cnt),
        VAL_CASE(test_stretch_extend_only),
        VAL_CASE(test_hx711_read_and_math),
        VAL_CASE(test_settle_discard),
        VAL_CASE(test_rate_vs_oracle),
        VAL_CASE(test_release_debounce),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
