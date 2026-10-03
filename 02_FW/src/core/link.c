/* Link core (FW_design §5.9): RX bytes -> ICD §2.3 parser -> in-order dispatch with exactly one
 * response per valid command frame, dispatch budget 500 µs per pass, response back-pressure
 * (DEF-P1-07: a command is taken only when class R can hold the largest response), stop sniffer in
 * the 1 kHz tick with the sniffed-stop hold, link counters.
 * Implements: IF-003, IF-005, FW-CMD-001, NFR-008, SAF-FW-002/003 (receive-side stop path),
 *             SAF-FW-015 (last valid command frame time), FW-CMD-004 (counters)
 */
#include "fw.h"

#include "frame.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "stop_sniff.h"

static fparser_t    s_fp;
static sniff_t      s_sniff;
static uint32_t     s_sniff_cursor;
static sniff_hold_t s_hold;

void link_init(void)
{
    fp_init(&s_fp);
    sniff_init(&s_sniff, 0u);
    s_sniff_cursor = 0u;
    hold_init(&s_hold);
}

void link_counters(uint32_t *ok, uint32_t *crc, uint32_t *frame_err)
{
    *ok = s_fp.frames_ok;
    *crc = s_fp.crc_errors;
    *frame_err = s_fp.len_errors + s_fp.timeout_drops + g_fw.rx_invalid_type;
}

bool link_motion_start_allowed(void)
{
    return !hold_blocks_start(&s_hold);
}

void link_respond(uint8_t type, uint8_t seq, uint8_t status, const uint8_t *body, uint16_t n)
{
    uint8_t fr[PROTO_FRAME_MAX];
    uint16_t i;
    fr[FRAME_HEADER_LEN] = status;
    for (i = 0u; i < n && i < (uint16_t)(PROTO_MAX_LEN - 1u); i++) {
        fr[FRAME_HEADER_LEN + 1u + i] = body[i];
    }
    (void)hal_uart_write(fr, frame_build((uint8_t)(type | PROTO_RESP_BIT), seq, &fr[FRAME_HEADER_LEN],
                                         (uint16_t)(1u + i), fr),
                         HAL_TX_RESP);               /* room guaranteed by the back-pressure */
}

void link_nack(uint8_t type, uint8_t seq, uint8_t status, uint16_t detail)
{
    uint8_t pl[PROTO_NACK_LEN];
    uint8_t fr[PROTO_NACK_LEN + PROTO_FRAME_OVERHEAD];
    (void)payload_nack(status, detail, pl);
    (void)hal_uart_write(fr, frame_build((uint8_t)(type | PROTO_RESP_BIT), seq, pl, PROTO_NACK_LEN, fr),
                         HAL_TX_RESP);
}

static void dispatch(const fp_frame_t *f)
{
    if (!PROTO_TYPE_IS_CMD(f->type)) {
        g_fw.rx_invalid_type++;                      /* ICD §2.3 step 5: count, ignore */
        return;
    }
    g_fw.last_cmd_rx_ms = hal_time_ms();             /* NACKed frames included (ICD §3.1) */
    if (CMD_IS_SNIFFED(f->type)) {
        CRIT_BEGIN(HAL_CRIT_TICK);
        (void)hold_on_dispatch(&s_hold, f->type, f->seq);
        CRIT_END();
    }
    cmd_execute(f->type, f->seq, f->payload, f->len);
}

void link_poll(void)
{
    uint32_t t0 = hal_time_us();
    bool timeout_checked = false;
    if (nvm_busy()) {
        return;                                      /* SAVE: commands wait in the RX ring */
    }
    for (;;) {
        fp_frame_t f;
        uint8_t buf[64];
        size_t want, n;
        if (hal_uart_tx_free(HAL_TX_RESP) < TX_R_RESERVE) {
            break;                                   /* back-pressure (DEF-P1-07) */
        }
        if ((uint32_t)(hal_time_us() - t0) >= LINK_DISPATCH_BUDGET_US) {
            break;                                   /* rest in the next pass (NFR-006) */
        }
        if (fp_poll(&s_fp, &f)) {
            dispatch(&f);
            if (nvm_busy()) {
                break;                               /* SAVE accepted: stop dispatching */
            }
            continue;
        }
        want = fp_free(&s_fp);
        if (want > sizeof buf) {
            want = sizeof buf;
        }
        n = hal_uart_read(buf, want);
        if (n == 0u) {
            if (!timeout_checked) {                  /* ICD §2.3 step 4 (20 ms inter-byte) */
                timeout_checked = true;
                fp_check_timeout(&s_fp, hal_time_ms());
                continue;
            }
            break;
        }
        (void)fp_append(&s_fp, buf, (uint16_t)n, hal_time_ms());
    }
}

void link_tick(uint32_t now_ms)
{
    uint8_t buf[SNIFF_BYTES_PER_TICK];
    sniff_hit_t hits[8];
    uint8_t chunk, k;
    for (chunk = 0u; chunk < 2u; chunk++) {
        size_t n = hal_uart_peek(buf, sizeof buf, &s_sniff_cursor);
        uint8_t nh;
        if (n == 0u) {
            break;
        }
        nh = sniff_scan(&s_sniff, buf, (uint16_t)n, hits, (uint8_t)(sizeof hits / sizeof hits[0]));
        for (k = 0u; k < nh; k++) {
            /* motion part only (FW_design §5.9.3): M1 has no motion to stop; the hold parks motion
             * starts of earlier frames from M2 on. Latches, VALID, EVENTs and the response come
             * from the in-order dispatch of the same frame. */
            g_fw.last_cmd_rx_ms = now_ms;
            hold_set(&s_hold, &hits[k], now_ms);
        }
        if (n < sizeof buf) {
            break;
        }
    }
    (void)hold_timeout(&s_hold, now_ms, SNIFF_HOLD_MAX_MS);
}
