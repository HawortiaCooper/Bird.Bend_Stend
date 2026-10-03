/* ICD §2.3 parser against the 11 `streams` vectors (frames AND counters), byte-wise feeding,
 * the frame vectors, frame_build round trip, fp_frame_tail.
 * Verifies: IF-003, IF-004, FW-CMD-001 (framing part)
 */
#include <string.h>
#include <unity.h>

#include "frame.h"
#include "vec_frames.h"
#include "vec_streams.h"

void setUp(void) {}
void tearDown(void) {}

static fparser_t g_p;

/* feed whole chunks (or byte-wise if bytewise) and collect frames; returns frames found */
static uint32_t run_stream(const vec_stream_t *s, bool bytewise, uint8_t out[][PROTO_FRAME_MAX],
                           uint16_t *out_len, uint32_t *n_before)
{
    uint32_t n = 0u;
    uint8_t c, j;
    fp_frame_t f;
    fp_init(&g_p);
    for (c = 0u; c < s->n_chunks; c++) {
        uint16_t pos = 0u;
        while (pos < s->chunk_len[c]) {
            uint16_t take = bytewise ? 1u : (uint16_t)(s->chunk_len[c] - pos);
            uint16_t acc = fp_append(&g_p, &s->chunks[c][pos], take, 0u);
            TEST_ASSERT_EQUAL_UINT16(take, acc);
            pos = (uint16_t)(pos + acc);
            while (fp_poll(&g_p, &f)) {
                out_len[n] = frame_build(f.type, f.seq, f.payload, f.len, out[n]);
                n++;
            }
        }
    }
    *n_before = n;
    if (s->idle_timeout_at_end) {
        fp_force_timeout(&g_p);
        while (fp_poll(&g_p, &f)) {
            out_len[n] = frame_build(f.type, f.seq, f.payload, f.len, out[n]);
            n++;
        }
    }
    for (j = 0u; j < s->n_frames && j < n; j++) {
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(s->frame_len[j], out_len[j], s->name);
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(s->frames[j], out[j], s->frame_len[j], s->name);
    }
    return n;
}

static void check_stream(bool bytewise)
{
    static uint8_t out[8][PROTO_FRAME_MAX];
    uint16_t out_len[8];
    uint32_t i;
    for (i = 0u; i < VEC_STREAMS_N; i++) {
        const vec_stream_t *s = &VEC_STREAMS[i];
        uint32_t before = 0u;
        uint32_t n = run_stream(s, bytewise, out, out_len, &before);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(s->n_frames, n, s->name);
        if (s->n_before != 255u) {
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(s->n_before, before, s->name);
        }
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(s->frames_ok, g_p.frames_ok, s->name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(s->crc_errors, g_p.crc_errors, s->name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(s->len_errors, g_p.len_errors, s->name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(s->timeout_drops, g_p.timeout_drops, s->name);
    }
}

static void test_streams_chunked(void) { check_stream(false); }
static void test_streams_bytewise(void) { check_stream(true); }

/* every vector frame parses back to itself; the invalid one is dropped with crc_errors += 1 */
static void test_frame_vectors(void)
{
    uint32_t i;
    fp_frame_t f;
    uint8_t buf[PROTO_FRAME_MAX + 16u];
    for (i = 0u; i < VEC_FRAMES_N; i++) {
        const vec_frame_t *v = &VEC_FRAMES[i];
        fp_init(&g_p);
        (void)fp_append(&g_p, v->frame, v->frame_len, 0u);
        if (v->kind == VK_INVALID) {
            TEST_ASSERT_FALSE_MESSAGE(fp_poll(&g_p, &f), v->name);
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(v->expect_crc_errors, g_p.crc_errors, v->name);
            continue;
        }
        TEST_ASSERT_TRUE_MESSAGE(fp_poll(&g_p, &f), v->name);
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(v->type, f.type, v->name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(v->seq, f.seq, v->name);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(v->len, f.len, v->name);
        TEST_ASSERT_EQUAL_UINT16(v->frame_len, frame_build(f.type, f.seq, f.payload, f.len, buf));
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(v->frame, buf, v->frame_len, v->name);
        TEST_ASSERT_FALSE(fp_poll(&g_p, &f));
    }
}

/* the stream position of a delivered frame (used by the sniffed-stop hold) */
static void test_frame_tail(void)
{
    static const uint8_t ping[] = {0xA5, 0x5A, 0x01, 0x07, 0x00, 0x00, 0xE4, 0x77};
    uint8_t in[3u + 2u * sizeof ping];
    fp_frame_t f;
    in[0] = 0x00; in[1] = 0x11; in[2] = 0x22;              /* noise */
    memcpy(&in[3], ping, sizeof ping);
    memcpy(&in[3 + sizeof ping], ping, sizeof ping);
    fp_init(&g_p);
    (void)fp_append(&g_p, in, (uint16_t)sizeof in, 0u);
    TEST_ASSERT_TRUE(fp_poll(&g_p, &f));
    TEST_ASSERT_EQUAL_UINT16(2u * sizeof ping, fp_frame_tail(&g_p));   /* frame starts at byte 3 */
    TEST_ASSERT_TRUE(fp_poll(&g_p, &f));
    TEST_ASSERT_EQUAL_UINT16(sizeof ping, fp_frame_tail(&g_p));
}

/* inter-byte timeout through fp_check_timeout: 19 ms -> still waiting, 20 ms -> dropped */
static void test_timeout_20ms(void)
{
    static const uint8_t part[] = {0xA5, 0x5A, 0x01, 0x07};
    fp_frame_t f;
    fp_init(&g_p);
    (void)fp_append(&g_p, part, (uint16_t)sizeof part, 1000u);
    TEST_ASSERT_FALSE(fp_poll(&g_p, &f));
    fp_check_timeout(&g_p, 1019u);
    TEST_ASSERT_FALSE(fp_poll(&g_p, &f));
    TEST_ASSERT_EQUAL_UINT32(0u, g_p.timeout_drops);
    fp_check_timeout(&g_p, 1020u);
    TEST_ASSERT_FALSE(fp_poll(&g_p, &f));
    TEST_ASSERT_EQUAL_UINT32(1u, g_p.timeout_drops);
    TEST_ASSERT_EQUAL_UINT16(0u, fp_buffered(&g_p));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_streams_chunked);
    RUN_TEST(test_streams_bytewise);
    RUN_TEST(test_frame_vectors);
    RUN_TEST(test_frame_tail);
    RUN_TEST(test_timeout_20ms);
    return UNITY_END();
}
