/* Latches without motion (M1 subset): the PAUSED execution effect of every check vector
 * (`paused_after`, executed with the real latch functions), idempotent STOP/HALT/PAUSE repeats
 * (one event), RESUME clears only PAUSED, HALT_CLEAR clears HALT + PAUSED, FAULT_CLEAR
 * all-or-nothing body, ESTOP_CLEAR, VALID_CLEARED with the stop cause, EVENT order.
 * Verifies: SAF-FW-001, SAF-FW-006, SAF-FW-022, SAF-FW-023, FW-CMD-003, FW-MOT-007, D-30, D-31
 */
#include <unity.h>

#include "check_ctx.h"
#include "latches.h"
#include "proto_gen.h"

void setUp(void) {}
void tearDown(void) {}

static latch_t L;
static valid_t V;
static evq_t Q;

static void reset(void)
{
    latch_init(&L);
    valid_init(&V, 0u);
    evq_init(&Q);
}

static uint16_t pop_code(uint16_t *arg)
{
    event_t e;
    uint8_t s;
    if (!evq_pop(&Q, &e, &s)) {
        return 0u;
    }
    if (arg != NULL) {
        *arg = e.arg;
    }
    return e.code;
}

/* execute the latch part of an accepted command on a latch with the vector's PAUSED / HALT state */
static void exec(uint8_t type, const uint8_t *payload)
{
    switch (type) {
    case CMD_PAUSE:      latch_on_pause(&L, &V, &Q, SRC_PC, 10u); break;
    case CMD_RESUME:     latch_on_resume(&L, &Q, 10u); break;
    case CMD_HALT_CLEAR: latch_on_halt_clear(&L, &Q, 10u); break;
    case CMD_HALT:       latch_on_halt(&L, &V, &Q, SRC_PC, 10u); break;
    case CMD_STOP:       latch_on_stop(&L, &V, &Q, payload[0], 10u); break;
    case CMD_FAULT_CLEAR:(void)latch_on_fault_clear(&L, &Q, 10u); break;
    case CMD_ESTOP_CLEAR:latch_on_estop_clear(&L, &Q, 10u); break;
    default: break;                                  /* motion / others never touch PAUSED */
    }
}

static void test_paused_after_vectors(void)
{
    uint32_t i, n = 0u;
    for (i = 0u; i < VEC_CHECK_N; i++) {
        const vec_check_t *v = &VEC_CHECK[i];
        params_t p;
        cmd_ctx_t c;
        cmd_verdict_t r;
        if (v->paused_after < 0) {
            continue;
        }
        n++;
        cc_setup(v, &p, &c);
        r = cmd_check(&c, v->type, v->payload, v->len);
        TEST_ASSERT_EQUAL_MESSAGE(v->paused_after != 0, latch_paused_after(v->paused, v->type, r.status == ST_OK),
                                  v->name);
        reset();
        L.paused = v->paused;
        L.halt = v->halt_latched;
        L.faults = v->faults;
        if (r.status == ST_OK) {
            exec(v->type, v->payload);
        }
        TEST_ASSERT_EQUAL_MESSAGE(v->paused_after != 0, L.paused, v->name);
    }
    TEST_ASSERT_TRUE(n >= 30u);
}

static void test_confirm_repeats_one_event(void)
{
    int k;
    uint16_t arg = 0u;
    reset();
    (void)valid_set(&V, true, 1u);
    for (k = 0; k < 20; k++) {
        latch_on_halt(&L, &V, &Q, SRC_PC, (uint32_t)(10 + k));
    }
    TEST_ASSERT_EQUAL_UINT16(EV_HALT_SET, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(SRC_PC, arg);
    TEST_ASSERT_EQUAL_UINT16(EV_VALID_CLEARED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(SC_PC_HALT, arg);
    TEST_ASSERT_EQUAL_UINT16(0u, pop_code(NULL));
    reset();
    for (k = 0; k < 20; k++) {
        latch_on_pause(&L, &V, &Q, SRC_PC, 10u);
        latch_on_stop(&L, &V, &Q, STOPMODE_IMMEDIATE, 10u);
    }
    TEST_ASSERT_EQUAL_UINT16(EV_PAUSED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(SRC_PC, arg);
    TEST_ASSERT_EQUAL_UINT16(0u, pop_code(NULL));            /* VALID was 0: no VALID_CLEARED */
    TEST_ASSERT_EQUAL_UINT8(SRC_PC, L.pause_src);
}

static void test_stop_clears_valid_with_cause(void)
{
    uint16_t arg = 0u;
    reset();
    (void)valid_set(&V, true, 1u);
    latch_on_stop(&L, &V, &Q, STOPMODE_CONTROLLED, 5u);
    TEST_ASSERT_EQUAL_UINT16(EV_VALID_CLEARED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(SC_PC_STOP_CONTROLLED, arg);
    TEST_ASSERT_FALSE(valid_at(&V, 5u));
    TEST_ASSERT_FALSE(L.halt);                                /* STOP is not latched */
    (void)valid_set(&V, true, 6u);
    latch_on_pause(&L, &V, &Q, SRC_PC, 7u);
    TEST_ASSERT_EQUAL_UINT16(EV_PAUSED, pop_code(NULL));
    TEST_ASSERT_EQUAL_UINT16(EV_VALID_CLEARED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(SC_PC_PAUSE, arg);
}

static void test_resume_and_halt_clear(void)
{
    uint16_t arg = 0u;
    reset();
    latch_on_resume(&L, &Q, 1u);                              /* not PAUSED: no-op, no event */
    TEST_ASSERT_EQUAL_UINT16(0u, pop_code(NULL));
    latch_on_halt(&L, &V, &Q, SRC_PC, 1u);
    latch_on_pause(&L, &V, &Q, SRC_PC, 1u);
    (void)pop_code(NULL);
    (void)pop_code(NULL);
    latch_on_halt_clear(&L, &Q, 2u);                          /* clears HALT and PAUSED */
    TEST_ASSERT_EQUAL_UINT16(EV_HALT_CLEARED, pop_code(NULL));
    TEST_ASSERT_EQUAL_UINT16(EV_PAUSE_CLEARED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(PCLR_HALT_CLEAR, arg);
    TEST_ASSERT_FALSE(L.halt);
    TEST_ASSERT_FALSE(L.paused);
    TEST_ASSERT_EQUAL_UINT8(SRC_NONE, L.halt_src);
    TEST_ASSERT_EQUAL_UINT8(SRC_NONE, L.pause_src);
    latch_on_pause(&L, &V, &Q, SRC_BUTTON, 3u);
    TEST_ASSERT_EQUAL_UINT16(EV_PAUSED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(SRC_BUTTON, arg);
    latch_on_pause(&L, &V, &Q, SRC_BUTTON, 4u);               /* press while PAUSED */
    TEST_ASSERT_EQUAL_UINT16(EV_RESUME_REQUEST, pop_code(NULL));
    TEST_ASSERT_TRUE(L.paused);                               /* the FW never resumes */
    latch_on_resume(&L, &Q, 5u);
    TEST_ASSERT_EQUAL_UINT16(EV_PAUSE_CLEARED, pop_code(&arg));
    TEST_ASSERT_EQUAL_UINT16(PCLR_RESUME, arg);
    TEST_ASSERT_FALSE(L.paused);
}

static void test_fault_and_estop_clear(void)
{
    uint16_t arg = 0u;
    reset();
    TEST_ASSERT_EQUAL_UINT16(0u, latch_on_fault_clear(&L, &Q, 1u));   /* nothing latched */
    TEST_ASSERT_EQUAL_UINT16(0u, pop_code(NULL));
    L.faults = FAULT_LOAD_LIMIT | FAULT_STEP_FAULT;
    TEST_ASSERT_EQUAL_HEX16(FAULT_LOAD_LIMIT | FAULT_STEP_FAULT, latch_on_fault_clear(&L, &Q, 2u));
    TEST_ASSERT_EQUAL_UINT16(EV_FAULT_CLEARED, pop_code(&arg));
    TEST_ASSERT_EQUAL_HEX16(FAULT_LOAD_LIMIT | FAULT_STEP_FAULT, arg);
    TEST_ASSERT_EQUAL_HEX16(0u, L.faults);
    latch_on_estop_clear(&L, &Q, 3u);
    TEST_ASSERT_EQUAL_UINT16(0u, pop_code(NULL));
    L.estop = true;
    latch_on_estop_clear(&L, &Q, 3u);
    TEST_ASSERT_EQUAL_UINT16(EV_ESTOP_CLEARED, pop_code(NULL));
    TEST_ASSERT_FALSE(L.estop);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_paused_after_vectors);
    RUN_TEST(test_confirm_repeats_one_event);
    RUN_TEST(test_stop_clears_valid_with_cause);
    RUN_TEST(test_resume_and_halt_clear);
    RUN_TEST(test_fault_and_estop_clear);
    return UNITY_END();
}
