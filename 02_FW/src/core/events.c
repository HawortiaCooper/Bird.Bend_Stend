/* EVENT producers and the EVENT -> TX class E transfer (FW_design §5.14). Producers: main loop and
 * tick (serialised by CRIT_TICK); frames are built at send time, header SEQ = event counter.
 * Implements: FW-STR-006, IF-011 (EVENT class, sent regardless of the stream state)
 */
#include "frame.h"
#include "fw.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"

void fw_event(uint16_t code, uint16_t arg, int32_t value, int32_t value2)
{
    uint32_t t = hal_time_us();
    CRIT_BEGIN(HAL_CRIT_TICK);
    evq_push(&g_fw.evq, t, code, arg, value, value2);
    CRIT_END();
}

void events_flush(void)
{
    uint8_t k;
    for (k = 0u; k < EVENTS_PER_PASS; k++) {
        event_t ev;
        uint8_t seq = 0u;
        bool got = false;
        uint8_t pl[PROTO_EVENT_LEN];
        uint8_t fr[PROTO_EVENT_FRAME_LEN];
        if (hal_uart_tx_free(HAL_TX_EVENT) < PROTO_EVENT_FRAME_LEN) {
            return;                                  /* stays in the EVENT ring */
        }
        CRIT_BEGIN(HAL_CRIT_TICK);
        got = evq_pop(&g_fw.evq, &ev, &seq);
        CRIT_END();
        if (!got) {
            return;
        }
        payload_event(&ev, pl);
        (void)frame_build(ASYNC_EVENT, seq, pl, PROTO_EVENT_LEN, fr);
        (void)hal_uart_write(fr, PROTO_EVENT_FRAME_LEN, HAL_TX_EVENT);
    }
}
