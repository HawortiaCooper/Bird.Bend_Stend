/* Core test harness on the fake seams (header-only): boot the core, send commands, run passes,
 * fetch responses / events / STATUS.
 * Verifies: (harness for) FW-CMD-001, FW-NVM-001...003, FW-STR-001...006
 */
#ifndef HARNESS_H
#define HARNESS_H

#include <string.h>
#include <unity.h>

#include "fake_hal.h"
#include "fw.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "le.h"

void core_tick_1ms(void);

static inline void h_boot(bool blank_flash)
{
    fake_hal_reset();
    if (blank_flash) {
        fake_flash_blank();
    }
    app_init();
    app_loop();
}

/* reboot keeping the flash (and clearing the dead flag of a power cut) */
static inline void h_reboot(void)
{
    fake_hal_reset();
    app_init();
    app_loop();
}

/* send a command and run main-loop passes (no tick) until its response is captured */
static inline const fake_frame_t *h_cmd(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t len)
{
    uint32_t from = fake_cap_n;
    int32_t i = -1;
    uint32_t k;
    fake_cmd(type, seq, pl, len);
    for (k = 0u; k < 50u && i < 0; k++) {
        app_loop();
        i = fake_cap_find((uint8_t)(type | PROTO_RESP_BIT), seq, from);
    }
    TEST_ASSERT_TRUE_MESSAGE(i >= 0, "no response");
    return &fake_cap[i];
}

static inline uint8_t h_status_of(const fake_frame_t *r) { return r->payload[0]; }
static inline uint16_t h_detail_of(const fake_frame_t *r) { return le_get16(&r->payload[1]); }

static inline void h_expect_ok(const fake_frame_t *r) { TEST_ASSERT_EQUAL_UINT8(ST_OK, r->payload[0]); }
static inline void h_expect_nack(const fake_frame_t *r, uint8_t st, uint16_t detail)
{
    TEST_ASSERT_EQUAL_UINT16(3u, r->len);
    TEST_ASSERT_EQUAL_UINT8(st, r->payload[0]);
    TEST_ASSERT_EQUAL_UINT16(detail, le_get16(&r->payload[1]));
}

/* GET_STATUS fields (offsets ICD §7.2) */
typedef struct { const uint8_t *b; } h_status_t;
static inline h_status_t h_status(void)
{
    static uint8_t seq = 0x70u;
    h_status_t s;
    const fake_frame_t *r = h_cmd(CMD_GET_STATUS, ++seq, NULL, 0u);
    h_expect_ok(r);
    TEST_ASSERT_EQUAL_UINT16(1u + PROTO_STATUS_LEN, r->len);
    s.b = &r->payload[1];
    return s;
}

static inline void h_set_param(uint16_t id, uint8_t type, uint32_t raw)
{
    uint8_t pl[7];
    le_put16(pl, id);
    pl[2] = type;
    le_put32(&pl[3], raw);
    if (type == PARAM_T_U8 || type == PARAM_T_ENUM || type == PARAM_T_BOOL || type == PARAM_T_I8) {
        pl[4] = pl[5] = pl[6] = 0u;
    } else if (type == PARAM_T_U16 || type == PARAM_T_I16) {
        pl[5] = pl[6] = 0u;
    }
    h_expect_ok(h_cmd(CMD_SET_PARAM, 0x55u, pl, 7u));
}

static inline uint32_t h_get_param_raw(uint16_t id)
{
    uint8_t pl[2];
    const fake_frame_t *r;
    le_put16(pl, id);
    r = h_cmd(CMD_GET_PARAM, 0x56u, pl, 2u);
    h_expect_ok(r);
    return le_get32(&r->payload[4]);
}

#endif /* HARNESS_H */
