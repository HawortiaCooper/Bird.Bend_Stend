/* DATA stream (FW_design §5.14, ICD §5.3/§7.3): one frame per conversion, built in the sample ISR
 * (level 3) into TX class D; frame_seq advances per due frame also when the frame is dropped, the
 * next sent frame carries OVERRUN; fallback frames at stream.fallback_hz while the AFE is stale
 * (tick, level 4). The stream state never gates a safety function.
 * Implements: FW-STR-001, FW-STR-002, FW-STR-003, FW-STR-004, FW-STR-005, IF-006, IF-007,
 *             FW-CMD-002 (VALID per frame time), SYS-002 (raw counts + commanded position only)
 */
#include "fw.h"

#include "frame.h"
#include "hal_sys.h"
#include "hal_uart.h"
#include "units.h"

/* caller holds CRIT_DATA or runs at level 3 */
static void emit(uint32_t t_us, int32_t raw, int32_t setpoint_um, uint16_t extra_status, bool settling,
                 bool saturated)
{
    flags_in_t fi;
    data_t d;
    uint8_t pl[PROTO_DATA_LEN];
    uint8_t fr[PROTO_DATA_FRAME_LEN];
    fw_flags(&fi, t_us);
    fi.overrun = g_fw.st.overrun;
    fi.afe_settling = settling;
    fi.afe_saturated = saturated;
    d.t_us = t_us;
    d.flags = flags_data(&fi);
    d.afe_raw = raw;
    d.setpoint_um = setpoint_um;
    d.frame_seq = g_fw.st.frame_seq;
    d.status = (uint16_t)(flags_status(&fi) | extra_status);
    g_fw.st.frame_seq = (uint16_t)(g_fw.st.frame_seq + 1u);      /* per due frame (ICD §2.2) */
    payload_data(&d, pl);
    (void)frame_build(ASYNC_DATA, (uint8_t)d.frame_seq, pl, PROTO_DATA_LEN, fr);
    if (hal_uart_write(fr, PROTO_DATA_FRAME_LEN, HAL_TX_DATA)) {
        g_fw.st.overrun = false;                 /* carried by this frame */
    } else {
        g_fw.st.tx_drops++;                      /* FW-STR-004 */
        g_fw.st.overrun = true;
    }
}

void stream_on_sample(uint32_t t_us, int32_t raw, int32_t pos_steps, bool settling, bool saturated)
{
    /* setpoint = commanded position latched at data-ready (A-03) */
    emit(t_us, raw, units_steps_to_um(pos_steps, g_fw.p.motion.steps_per_mm), 0u, settling, saturated);
}

void stream_set(bool on)
{
    CRIT_BEGIN(HAL_CRIT_DATA);
    /* a pending OVERRUN (class-D drop) survives STOP/START: the frame_seq gap must be explained by
     * the next sent frame (ICD §2.2/§2.4, DEF-M1-03); only FW losses set it (DEF-M1-02/-04) */
    g_fw.st.on = on;                             /* idempotent; frame_seq is not reset */
    CRIT_END();
}

void stream_tick(uint32_t now_us)
{
    bool want = g_fw.st.on && g_fw.afe.stale && !g_fw.st.hold;
    uint32_t skipped = 0u;
    if (!want) {
        if (g_fw.st.fb_running) {
            ss_stop(&g_fw.st.fb);
            g_fw.st.fb_running = false;
        }
        return;
    }
    if (!g_fw.st.fb_running) {
        ss_start(&g_fw.st.fb, g_fw.p.stream.fallback_hz, now_us);   /* first frame at once */
        g_fw.st.fb_running = true;
    }
    if (ss_poll(&g_fw.st.fb, now_us, &skipped)) {
        CRIT_BEGIN(HAL_CRIT_DATA);               /* frame_seq / class D shared with level 3 */
        emit(now_us, PROTO_AFE_NO_DATA, fw_pos_um(), (uint16_t)DS_NO_AFE_DATA, false, false);
        CRIT_END();
    }
}
