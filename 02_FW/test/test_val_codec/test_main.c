/* Validator E - codec conformance (independent suite; FW_test_plan v0.1 §2, level U).
 * Oracle: protocol_vectors.json (crc16, 164 frames, 11 streams) read in place via
 * test/val_oracles/gen_val_vectors.py, plus a 10^5-stream differential corpus whose expectation is
 * ref_codec.FrameParser (validator-generated). No helper of Implementer A is used.
 *
 * Verifies: IF-003, IF-004, IF-006, IF-002, IF-009, FW-CMD-001, FW-STR-003, FW-CFG-002, FW-CFG-004,
 *           FW-CMD-004
 * TC: TC-IF-003-01, TC-IF-004-01 (U), TC-IF-006-01 (U), TC-IF-002-01 (U), TC-IF-009-01 (U),
 *     TC-FW-CMD-001-01, TC-FW-STR-003-01 (U), TC-FW-CFG-002-01 (U), TC-FW-CFG-004-01 (U),
 *     TC-FW-CMD-004-01 (U: STATUS encode)
 */
#include "../val_common/val_io.h"

#include "crc16.h"
#include "frame.h"
#include "le.h"
#include "payload.h"
#include "proto.h"

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

/* parse a whole byte string with a fresh parser (drain after every piece) */
typedef struct {
    uint16_t n;
    uint8_t  type[64], seq[64];
    uint16_t len[64];
    uint8_t  pl[64][PROTO_MAX_LEN];
} got_t;

static void drain(fparser_t *p, got_t *g)
{
    fp_frame_t f;
    while (fp_poll(p, &f)) {
        TEST_ASSERT_TRUE_MESSAGE(g->n < 64u, "too many frames");
        g->type[g->n] = f.type;
        g->seq[g->n] = f.seq;
        g->len[g->n] = f.len;
        memcpy(g->pl[g->n], f.payload, f.len);
        g->n++;
    }
}

static void feed(fparser_t *p, got_t *g, const uint8_t *d, uint16_t n)
{
    uint16_t off = 0u;
    while (off < n) {
        uint16_t room = fp_free(p);
        uint16_t k = (uint16_t)((n - off) < room ? (n - off) : room);
        TEST_ASSERT_TRUE_MESSAGE(k > 0u, "parser buffer full while drained");
        TEST_ASSERT_EQUAL_UINT16(k, fp_append(p, &d[off], k, 0u));
        off = (uint16_t)(off + k);
        drain(p, g);
    }
}

static void assert_frame_eq(const got_t *g, uint16_t i, const uint8_t *fr, uint16_t n, const char *msg)
{
    uint8_t b[PROTO_FRAME_MAX];
    uint16_t m = frame_build(g->type[i], g->seq[i], g->pl[i], g->len[i], b);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(n, m, msg);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(fr, b, n, msg);
}

/* ------------------------------------------------------------------ CRC */
static void test_crc16_vectors(void)
{
    val_file_t v;
    uint32_t done = 0u;
    uint8_t d[256];
    val_open(&v, "crc.txt");
    while (val_tok(&v, T)) {
        char name[128];
        uint16_t n;
        uint32_t crc;
        TEST_ASSERT_EQUAL_STRING("CR", T);
        val_tok(&v, name);
        val_tok(&v, T);
        n = val_hex(T, d, sizeof d);
        crc = val_u32(&v);
        TEST_ASSERT_EQUAL_HEX16_MESSAGE(crc, crc16_ccitt(d, n, CRC16_INIT), name);
        done++;
    }
    val_close(&v, done);
    /* ICD §2.1 literal check values (independent of the vector file) */
    TEST_ASSERT_EQUAL_HEX16(0x29B1u, crc16_ccitt((const uint8_t *)"123456789", 9u, 0xFFFFu));
    TEST_ASSERT_EQUAL_HEX16(0xFFFFu, crc16_ccitt(NULL, 0u, 0xFFFFu));
}

/* ------------------------------------------------------------------ frames */
static void info_from(const uint32_t *x, info_t *i)
{
    uint32_t k;
    memset(i, 0, sizeof *i);
    i->proto_major = (uint8_t)x[0];
    i->proto_minor = (uint8_t)x[1];
    i->payload_version = (uint8_t)x[2];
    i->fw_version[0] = (uint8_t)x[3];
    i->fw_version[1] = (uint8_t)x[4];
    i->fw_version[2] = (uint8_t)x[5];
    i->param_dict_hash = x[6];
    for (k = 0u; k < 12u; k++) i->uid[k] = (uint8_t)x[7u + k];
    for (k = 0u; k < 16u; k++) i->build[k] = (uint8_t)x[19u + k];
    i->param_count = (uint16_t)x[35];
    i->feature_mask = x[36];
}

/* order = ref_codec.STATUS_FIELDS (ICD §7.2) */
static void status_from(const uint32_t *x, status_t *s)
{
    memset(s, 0, sizeof *s);
    s->uptime_ms = x[0]; s->t_us = x[1]; s->flags = (uint8_t)x[2]; s->motion_state = (uint8_t)x[3];
    s->status = (uint16_t)x[4]; s->faults = (uint16_t)x[5]; s->io = (uint16_t)x[6];
    s->home_phase = (uint8_t)x[7]; s->halt_src = (uint8_t)x[8]; s->reset_cause = (uint8_t)x[9];
    s->sys_flags = (uint8_t)x[10]; s->pos_um = (int32_t)x[11]; s->target_um = (int32_t)x[12];
    s->pos_steps = (int32_t)x[13]; s->afe_raw_last = (int32_t)x[14]; s->afe_rate_dsps = (uint16_t)x[15];
    s->afe_reinit_count = (uint16_t)x[16]; s->rx_frames_ok = x[17]; s->rx_crc_errors = x[18];
    s->rx_frame_errors = x[19]; s->rx_overruns = x[20]; s->tx_drops = x[21];
    s->event_overflows = (uint16_t)x[22]; s->loop_max_us = (uint16_t)x[23]; s->link_age_ms = (uint16_t)x[24];
    s->stack_free_min = (uint16_t)x[25]; s->nvm_save_ms = (uint16_t)x[26];
    s->idle_disable_left_s = (uint16_t)x[27]; s->nvm_record_seq = x[28]; s->nvm_save_uptime_ms = x[29];
    s->v_limit_um_s = x[30]; s->pause_src = (uint8_t)x[31];
}

static void entry_check(const uint32_t *x, const uint8_t *slice, const char *name, const char *wtok)
{
    param_entry_t e;
    uint8_t out[PROTO_PARAM_ENTRY_LEN];
    e.id = (uint16_t)x[0];
    e.type = (uint8_t)x[1];
    le_put32(e.wire, x[2]);
    payload_param_entry(&e, out);
    if (wtok != NULL && strcmp(wtok, "X") == 0) {
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(slice, out, 3u, name);
    } else {
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(slice, out, PROTO_PARAM_ENTRY_LEN, name);
    }
}

static void req_fields_check(uint8_t type, const cmd_req_t *r, const uint32_t *x, uint32_t nv,
                             const char *wtok, const char *name)
{
    switch (type) {
    case CMD_REBOOT: TEST_ASSERT_EQUAL_HEX32_MESSAGE(x[0], r->u.reboot.magic, name); break;
    case CMD_GET_ALL_PARAMS: TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[0], r->u.page.page, name); break;
    case CMD_GET_PARAM: TEST_ASSERT_EQUAL_HEX16_MESSAGE(x[0], r->u.get_param.id, name); break;
    case CMD_SET_PARAM:
        TEST_ASSERT_EQUAL_HEX16_MESSAGE(x[0], r->u.set_param.id, name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[1], r->u.set_param.type, name);
        if (strcmp(wtok, "X") != 0) {
            TEST_ASSERT_EQUAL_HEX32_MESSAGE(x[2], le_get32(r->u.set_param.wire), name);
        }
        break;
    case CMD_SET_VALID: TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[0], r->u.set_valid.valid, name); break;
    case CMD_HOME: TEST_ASSERT_EQUAL_HEX8_MESSAGE(x[0], r->u.home.flags, name); break;
    case CMD_STOP: TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[0], r->u.stop.mode, name); break;
    case CMD_MOVE_ABS:
        TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)x[0], r->u.move_abs.target_um, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(x[1], r->u.move_abs.v_um_s, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(x[2], r->u.move_abs.a_um_s2, name);
        break;
    case CMD_JOG:
        TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)x[0], r->u.jog.v_um_s, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(x[1], r->u.jog.a_um_s2, name);
        TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)x[2], r->u.jog.bound_um, name);
        break;
    case CMD_MOVE_UNTIL_LOAD:
        TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)x[0], r->u.mul.bound_um, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(x[1], r->u.mul.v_um_s, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(x[2], r->u.mul.a_um_s2, name);
        TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)x[3], r->u.mul.raw_stop, name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[4], r->u.mul.cmp, name);
        break;
    default:
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(0u, nv, name);   /* LEN-0 commands carry no fields */
        break;
    }
}

static uint32_t n_req, n_resp, n_data, n_event, n_inv;

static void test_frame_vectors(void)
{
    val_file_t v;
    uint32_t done = 0u;
    static uint8_t pl[PROTO_FRAME_MAX], fr[PROTO_FRAME_MAX * 2], b[PROTO_FRAME_MAX * 2];
    val_open(&v, "frames.txt");
    n_req = n_resp = n_data = n_event = n_inv = 0u;
    while (val_tok(&v, T)) {
        char kind[4], name[128], wtok[32] = "";
        uint32_t type = 0u, seq = 0u, re_ok = 1u, x[400];
        uint16_t npl = 0u, nfr;
        got_t g;
        fparser_t p;
        strcpy(kind, T);
        val_tok(&v, name);
        if (strcmp(kind, "IV") == 0) {                  /* corrupted frame: dropped and counted */
            val_tok(&v, T);
            nfr = val_hex(T, fr, sizeof fr);
            fp_init(&p);
            memset(&g, 0, sizeof g);
            feed(&p, &g, fr, nfr);
            fp_force_timeout(&p);
            drain(&p, &g);
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(0u, g.n, name);
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(1u, p.crc_errors, name);
            n_inv++;
            done++;
            continue;
        }
        type = val_u32(&v);
        seq = val_u32(&v);
        if (strcmp(kind, "RS") == 0) {
            re_ok = val_u32(&v);
        }
        val_tok(&v, T);
        npl = val_hex(T, pl, sizeof pl);
        val_tok(&v, T);
        nfr = val_hex(T, fr, sizeof fr);
        /* (1) the FW frame builder reproduces the vector frame byte for byte */
        if (re_ok) {
            uint16_t m = frame_build((uint8_t)type, (uint8_t)seq, pl, npl, b);
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(nfr, m, name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(fr, b, nfr, name);
        }
        /* (2) the FW parser delivers exactly this frame */
        fp_init(&p);
        memset(&g, 0, sizeof g);
        feed(&p, &g, fr, nfr);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE(1u, g.n, name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(type, g.type[0], name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(seq, g.seq[0], name);
        if (re_ok) {
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(npl, g.len[0], name);
            if (npl) TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(pl, g.pl[0], npl, name);
        }
        if (strcmp(kind, "RQ") == 0) {                  /* (3) request decode = decoded fields */
            uint32_t defined = val_u32(&v), nv = val_u32(&v), k;
            cmd_req_t r;
            bool ok;
            for (k = 0u; k < nv; k++) {
                val_tok(&v, T);
                if (k == 2u) strcpy(wtok, T);
                x[k] = (strcmp(T, "X") == 0) ? 0u : (uint32_t)strtoll(T, NULL, 10);
            }
            ok = payload_decode_request((uint8_t)type, pl, npl, &r);
            TEST_ASSERT_EQUAL_MESSAGE(defined != 0u, ok, name);
            if (ok) {
                TEST_ASSERT_EQUAL_UINT8_MESSAGE(type, r.type, name);
                req_fields_check((uint8_t)type, &r, x, nv, wtok, name);
                TEST_ASSERT_EQUAL_INT16_MESSAGE((int16_t)npl, proto_req_len((uint8_t)type), name);
            }
            n_req++;
        } else if (strcmp(kind, "RS") == 0) {           /* (4) response body encoders */
            uint32_t code = val_u32(&v), nv = val_u32(&v), k;
            uint8_t out[PROTO_MAX_LEN];
            for (k = 0u; k < nv; k++) {
                val_tok(&v, T);
                if (k == 2u) strcpy(wtok, T);
                x[k] = (strcmp(T, "X") == 0) ? 0u : (uint32_t)strtoll(T, NULL, 10);
            }
            switch (code) {
            case 0u:
                TEST_ASSERT_EQUAL_UINT16_MESSAGE(1u, npl, name);
                TEST_ASSERT_EQUAL_HEX8_MESSAGE(ST_OK, pl[0], name);
                break;
            case 1u:
                TEST_ASSERT_EQUAL_UINT16(PROTO_NACK_LEN, payload_nack((uint8_t)x[0], (uint16_t)x[1], out));
                TEST_ASSERT_EQUAL_UINT16_MESSAGE(PROTO_NACK_LEN, npl, name);
                TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(pl, out, PROTO_NACK_LEN, name);
                break;
            case 2u: {
                info_t in;
                TEST_ASSERT_EQUAL_UINT32_MESSAGE(37u, nv, name);
                info_from(x, &in);
                payload_info(&in, out);
                TEST_ASSERT_EQUAL_UINT16_MESSAGE(1u + PROTO_INFO_LEN, npl, name);
                TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(&pl[1], out, PROTO_INFO_LEN, name);
                break;
            }
            case 3u: {
                status_t s;
                TEST_ASSERT_EQUAL_UINT32_MESSAGE(32u, nv, name);
                status_from(x, &s);
                payload_status(&s, out);
                TEST_ASSERT_EQUAL_UINT16_MESSAGE(1u + PROTO_STATUS_LEN, npl, name);
                TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(&pl[1], out, PROTO_STATUS_LEN, name);
                break;
            }
            case 4u: {
                uint32_t e;
                TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[0], pl[1], name);
                TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[1], pl[2], name);
                TEST_ASSERT_EQUAL_UINT8_MESSAGE(x[2], pl[3], name);
                TEST_ASSERT_EQUAL_UINT16_MESSAGE(4u + x[2] * PROTO_PARAM_ENTRY_LEN, npl, name);
                for (e = 0u; e < x[2]; e++) {
                    entry_check(&x[3u + 3u * e], &pl[4u + e * PROTO_PARAM_ENTRY_LEN], name, NULL);
                }
                break;
            }
            case 5u:
                TEST_ASSERT_EQUAL_UINT16_MESSAGE(1u + PROTO_PARAM_ENTRY_LEN, npl, name);
                entry_check(x, &pl[1], name, wtok);
                break;
            case 6u:
                le_put32(out, x[0]);
                TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(&pl[1], out, 4u, name);
                break;
            case 7u:
                le_put16(out, (uint16_t)x[0]);
                TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(&pl[1], out, 2u, name);
                break;
            default:
                break;                                  /* 8: raw frame, (1)+(2) only */
            }
            n_resp++;
        } else if (strcmp(kind, "AD") == 0) {           /* (5) DATA encoder */
            data_t d;
            uint8_t out[PROTO_DATA_LEN];
            uint32_t k;
            for (k = 0u; k < 7u; k++) x[k] = val_u32(&v);
            d.t_us = x[0];
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(PROTO_PAYLOAD_VERSION, x[1], name);
            d.flags = (uint8_t)x[2];
            d.afe_raw = (int32_t)x[3];
            d.setpoint_um = (int32_t)x[4];
            d.frame_seq = (uint16_t)x[5];
            d.status = (uint16_t)x[6];
            payload_data(&d, out);
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(PROTO_DATA_LEN, npl, name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(pl, out, PROTO_DATA_LEN, name);
            TEST_ASSERT_EQUAL_UINT8_MESSAGE((uint8_t)d.frame_seq, (uint8_t)seq, name);   /* ICD §2.2 */
            n_data++;
        } else if (strcmp(kind, "AE") == 0) {           /* (6) EVENT encoder */
            event_t e;
            uint8_t out[PROTO_EVENT_LEN];
            uint32_t k;
            for (k = 0u; k < 5u; k++) x[k] = val_u32(&v);
            e.t_us = x[0];
            e.code = (uint16_t)x[1];
            e.arg = (uint16_t)x[2];
            e.value = (int32_t)x[3];
            e.value2 = (int32_t)x[4];
            payload_event(&e, out);
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(PROTO_EVENT_LEN, npl, name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(pl, out, PROTO_EVENT_LEN, name);
            n_event++;
        } else {
            TEST_FAIL_MESSAGE(kind);
        }
        done++;
    }
    val_close(&v, done);
    printf("VALFRAMES requests=%u responses=%u data=%u events=%u invalid=%u\n", n_req, n_resp, n_data, n_event,
           n_inv);
}

/* ------------------------------------------------------------------ streams + corpus */
static void run_stream_file(const char *file, const char *tag)
{
    val_file_t v;
    uint32_t done = 0u;
    static uint8_t buf[4096];
    val_open(&v, file);
    while (val_tok(&v, T)) {
        char name[128] = "corpus";
        uint32_t nev, k, nexp, c[4];
        int32_t nbefore = -1;
        got_t g;
        fparser_t p;
        static uint8_t before[64][PROTO_FRAME_MAX];
        uint16_t blen[64];
        uint32_t idle_end = 0u;
        TEST_ASSERT_EQUAL_STRING(tag, T);
        if (strcmp(tag, "ST") == 0) {
            val_tok(&v, name);
        }
        fp_init(&p);
        memset(&g, 0, sizeof g);
        nev = val_u32(&v);
        for (k = 0u; k < nev; k++) {
            val_tok(&v, T);
            if (strcmp(T, "T") == 0) {
                fp_force_timeout(&p);
                drain(&p, &g);
            } else {
                uint16_t n = val_hex(T, buf, sizeof buf);
                feed(&p, &g, buf, n);
            }
        }
        if (strcmp(tag, "ST") == 0) {
            idle_end = val_u32(&v);
            nbefore = (int32_t)val_u32(&v);
            if (nbefore >= 0) {
                for (k = 0u; k < (uint32_t)nbefore; k++) {
                    val_tok(&v, T);
                    blen[k] = val_hex(T, before[k], PROTO_FRAME_MAX);
                }
                TEST_ASSERT_EQUAL_UINT16_MESSAGE((uint16_t)nbefore, g.n, name);
                for (k = 0u; k < (uint32_t)nbefore; k++) {
                    assert_frame_eq(&g, (uint16_t)k, before[k], blen[k], name);
                }
            }
            if (idle_end) {
                fp_force_timeout(&p);
                drain(&p, &g);
            }
        }
        nexp = val_u32(&v);
        TEST_ASSERT_EQUAL_UINT16_MESSAGE((uint16_t)nexp, g.n, name);
        for (k = 0u; k < nexp; k++) {
            uint16_t n;
            val_tok(&v, T);
            n = val_hex(T, buf, sizeof buf);
            assert_frame_eq(&g, (uint16_t)k, buf, n, name);
        }
        for (k = 0u; k < 4u; k++) c[k] = val_u32(&v);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(c[0], p.frames_ok, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(c[1], p.crc_errors, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(c[2], p.len_errors, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(c[3], p.timeout_drops, name);
        done++;
    }
    val_close(&v, done);
}

static void test_stream_vectors(void) { run_stream_file("streams.txt", "ST"); }

static void test_parser_differential_corpus(void) { run_stream_file("corpus.txt", "PC"); }

/* ------------------------------------------------------------------ little-endian (IF-002) */
static void test_le_roundtrip(void)
{
    uint8_t b[4];
    le_put16(b, 0x1234u);
    TEST_ASSERT_EQUAL_HEX8(0x34u, b[0]);
    TEST_ASSERT_EQUAL_HEX8(0x12u, b[1]);
    TEST_ASSERT_EQUAL_HEX16(0x1234u, le_get16(b));
    le_put32(b, 0xA1B2C3D4u);
    TEST_ASSERT_EQUAL_HEX8(0xD4u, b[0]);
    TEST_ASSERT_EQUAL_HEX8(0xA1u, b[3]);
    TEST_ASSERT_EQUAL_HEX32(0xA1B2C3D4u, le_get32(b));
    TEST_ASSERT_EQUAL_HEX32(0x3F800000u, f32_bits(1.0f));
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_crc16_vectors),
        VAL_CASE(test_frame_vectors),
        VAL_CASE(test_stream_vectors),
        VAL_CASE(test_parser_differential_corpus),
        VAL_CASE(test_le_roundtrip),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
