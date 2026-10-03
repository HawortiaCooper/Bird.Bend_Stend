/* Payload codec against protocol_vectors.json `frames`: decode every PC->FW request to the C
 * struct of `decoded`; encode every FW->PC frame (responses, NACKs, DATA, EVENT) from `decoded`
 * with the FW encoders and compare byte-identical (canonical payload where reencode = false).
 * Verifies: IF-005, IF-006, FW-STR-003, FW-CFG-002, FW-CFG-004, FW-CMD-004
 */
#include <string.h>
#include <unity.h>

#include "frame.h"
#include "payload.h"
#include "vec_frames.h"

void setUp(void) {}
void tearDown(void) {}

static void cmp_req(const cmd_req_t *e, const cmd_req_t *a, const char *name)
{
    TEST_ASSERT_EQUAL_HEX8_MESSAGE(e->type, a->type, name);
    switch (e->type) {
    case CMD_REBOOT:
        TEST_ASSERT_EQUAL_HEX32_MESSAGE(e->u.reboot.magic, a->u.reboot.magic, name);
        break;
    case CMD_GET_ALL_PARAMS:
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(e->u.page.page, a->u.page.page, name);
        break;
    case CMD_GET_PARAM:
        TEST_ASSERT_EQUAL_HEX16_MESSAGE(e->u.get_param.id, a->u.get_param.id, name);
        break;
    case CMD_SET_PARAM:
        TEST_ASSERT_EQUAL_HEX16_MESSAGE(e->u.set_param.id, a->u.set_param.id, name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(e->u.set_param.type, a->u.set_param.type, name);
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(e->u.set_param.wire, a->u.set_param.wire, 4, name);
        break;
    case CMD_SET_VALID:
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(e->u.set_valid.valid, a->u.set_valid.valid, name);
        break;
    case CMD_HOME:
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(e->u.home.flags, a->u.home.flags, name);
        break;
    case CMD_MOVE_ABS:
        TEST_ASSERT_EQUAL_INT32_MESSAGE(e->u.move_abs.target_um, a->u.move_abs.target_um, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(e->u.move_abs.v_um_s, a->u.move_abs.v_um_s, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(e->u.move_abs.a_um_s2, a->u.move_abs.a_um_s2, name);
        break;
    case CMD_JOG:
        TEST_ASSERT_EQUAL_INT32_MESSAGE(e->u.jog.v_um_s, a->u.jog.v_um_s, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(e->u.jog.a_um_s2, a->u.jog.a_um_s2, name);
        TEST_ASSERT_EQUAL_INT32_MESSAGE(e->u.jog.bound_um, a->u.jog.bound_um, name);
        break;
    case CMD_MOVE_UNTIL_LOAD:
        TEST_ASSERT_EQUAL_INT32_MESSAGE(e->u.mul.bound_um, a->u.mul.bound_um, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(e->u.mul.v_um_s, a->u.mul.v_um_s, name);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(e->u.mul.a_um_s2, a->u.mul.a_um_s2, name);
        TEST_ASSERT_EQUAL_INT32_MESSAGE(e->u.mul.raw_stop, a->u.mul.raw_stop, name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(e->u.mul.cmp, a->u.mul.cmp, name);
        break;
    case CMD_STOP:
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(e->u.stop.mode, a->u.stop.mode, name);
        break;
    default:
        break;
    }
}

static void test_decode_requests(void)
{
    uint32_t i;
    TEST_ASSERT_TRUE(VEC_REQS_N >= 40u);
    for (i = 0u; i < VEC_REQS_N; i++) {
        const vec_frame_t *f = &VEC_FRAMES[VEC_REQS[i].idx];
        cmd_req_t r;
        TEST_ASSERT_TRUE_MESSAGE(payload_decode_request(f->type, f->payload, f->len, &r), f->name);
        cmp_req(&VEC_REQS[i].r, &r, f->name);
    }
}

static void test_decode_rejects_wrong_len(void)
{
    uint8_t pl[16] = {0};
    cmd_req_t r;
    TEST_ASSERT_FALSE(payload_decode_request(CMD_MOVE_ABS, pl, 11u, &r));
    TEST_ASSERT_FALSE(payload_decode_request(0x3Fu, pl, 0u, &r));
    TEST_ASSERT_TRUE(payload_decode_request(CMD_PING, pl, 0u, &r));
}

/* build the response payload of a vector from its decoded description with the FW encoders */
static uint16_t build_resp(const vec_resp_t *v, uint8_t *pl)
{
    uint16_t n;
    uint8_t i;
    switch (v->kind) {
    case RB_NACK:
        return payload_nack(v->status, (uint16_t)v->num, pl);
    case RB_EMPTY:
        pl[0] = ST_OK;
        return 1u;
    case RB_INFO:
        pl[0] = ST_OK;
        payload_info(v->info, &pl[1]);
        return (uint16_t)(1u + PROTO_INFO_LEN);
    case RB_STATUS:
        pl[0] = ST_OK;
        payload_status(v->st, &pl[1]);
        return (uint16_t)(1u + PROTO_STATUS_LEN);
    case RB_PAGE:
        pl[0] = ST_OK;
        pl[1] = v->page;
        pl[2] = v->page_count;
        pl[3] = v->n;
        n = 4u;
        for (i = 0u; i < v->n; i++) {
            payload_param_entry(&v->entries[i], &pl[n]);
            n = (uint16_t)(n + PROTO_PARAM_ENTRY_LEN);
        }
        return n;
    case RB_ENTRY:
        pl[0] = ST_OK;
        payload_param_entry(v->entries, &pl[1]);
        return (uint16_t)(1u + PROTO_PARAM_ENTRY_LEN);
    case RB_U32:
        pl[0] = ST_OK;
        pl[1] = (uint8_t)v->num; pl[2] = (uint8_t)(v->num >> 8);
        pl[3] = (uint8_t)(v->num >> 16); pl[4] = (uint8_t)(v->num >> 24);
        return 5u;
    case RB_U16:
        pl[0] = ST_OK;
        pl[1] = (uint8_t)v->num; pl[2] = (uint8_t)(v->num >> 8);
        return 3u;
    default:
        TEST_FAIL_MESSAGE("unknown body kind");
        return 0u;
    }
}

static void test_encode_responses(void)
{
    uint32_t i;
    uint8_t pl[PROTO_MAX_LEN];
    uint8_t fr[PROTO_FRAME_MAX];
    TEST_ASSERT_TRUE(VEC_RESPS_N >= 60u);
    for (i = 0u; i < VEC_RESPS_N; i++) {
        const vec_resp_t *v = &VEC_RESPS[i];
        const vec_frame_t *f = &VEC_FRAMES[v->idx];
        uint16_t n = build_resp(v, pl);
        if (f->reencode) {
            uint16_t fl = frame_build(f->type, f->seq, pl, n, fr);
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(f->frame_len, fl, f->name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(f->frame, fr, fl, f->name);
        } else {
            TEST_ASSERT_EQUAL_UINT16_MESSAGE(f->canon_len, n, f->name);
            TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(f->canon, pl, n, f->name);
        }
    }
}

static void test_encode_data(void)
{
    uint32_t i;
    uint8_t pl[PROTO_DATA_LEN];
    uint8_t fr[PROTO_DATA_FRAME_LEN];
    TEST_ASSERT_EQUAL_UINT32(9u, VEC_DATAS_N);
    for (i = 0u; i < VEC_DATAS_N; i++) {
        const vec_frame_t *f = &VEC_FRAMES[VEC_DATAS[i].idx];
        payload_data(&VEC_DATAS[i].d, pl);
        /* header SEQ = low byte of frame_seq (ICD §2.2) */
        TEST_ASSERT_EQUAL_UINT8_MESSAGE((uint8_t)VEC_DATAS[i].d.frame_seq, f->seq, f->name);
        TEST_ASSERT_EQUAL_UINT16(PROTO_DATA_FRAME_LEN,
                                 frame_build(ASYNC_DATA, (uint8_t)VEC_DATAS[i].d.frame_seq, pl,
                                             PROTO_DATA_LEN, fr));
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(f->frame, fr, PROTO_DATA_FRAME_LEN, f->name);
    }
}

static void test_encode_events(void)
{
    uint32_t i;
    uint8_t pl[PROTO_EVENT_LEN];
    uint8_t fr[PROTO_EVENT_FRAME_LEN];
    TEST_ASSERT_TRUE(VEC_EVENTS_N >= 34u);
    for (i = 0u; i < VEC_EVENTS_N; i++) {
        const vec_frame_t *f = &VEC_FRAMES[VEC_EVENTS[i].idx];
        payload_event(&VEC_EVENTS[i].e, pl);
        (void)frame_build(ASYNC_EVENT, f->seq, pl, PROTO_EVENT_LEN, fr);
        TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(f->frame, fr, PROTO_EVENT_FRAME_LEN, f->name);
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_decode_requests);
    RUN_TEST(test_decode_rejects_wrong_len);
    RUN_TEST(test_encode_responses);
    RUN_TEST(test_encode_data);
    RUN_TEST(test_encode_events);
    return UNITY_END();
}
