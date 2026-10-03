/* Parameter runtime (FW_design §5.15): GET_ALL_PARAMS pages and PARAM_ENTRY from the generated
 * PARAM_TABLE (no hand-written parameter list), apply hooks after SET/LOAD/DEFAULT, REBOOT_PENDING.
 * Implements: FW-CFG-001, FW-CFG-002, FW-CFG-003, FW-PAR-001...006 (consumer), SAF-FW-010 (session
 * thresholds read at use; loadlim copy from M2)
 */
#include "fw.h"

#include "hal_sys.h"
#include "hal_time.h"

void params_rt_entry(const param_meta_t *m, uint8_t out[PROTO_PARAM_ENTRY_LEN])
{
    param_entry_t e;
    e.id = m->id;
    e.type = m->type;
    param_raw_to_wire(m, param_get_raw(&g_fw.p, m), e.wire);
    payload_param_entry(&e, out);
}

uint8_t params_rt_page(uint8_t page, uint8_t *body)
{
    uint16_t first = (uint16_t)((uint16_t)page * PROTO_PARAMS_PER_PAGE);
    uint8_t n = 0u;
    uint16_t i;
    uint8_t pages = (uint8_t)((PARAM_COUNT + PROTO_PARAMS_PER_PAGE - 1u) / PROTO_PARAMS_PER_PAGE);
    for (i = first; i < (uint16_t)PARAM_COUNT && n < PROTO_PARAMS_PER_PAGE; i++) {
        params_rt_entry(&PARAM_TABLE[i], &body[3u + (uint16_t)n * PROTO_PARAM_ENTRY_LEN]);
        n++;
    }
    body[0] = page;
    body[1] = pages;
    body[2] = n;
    return n;
}

bool params_rt_reboot_pending(void)
{
    return g_fw.p.motion.pul_invert != g_fw.boot_pul_invert ||
           g_fw.p.motion.ena_invert != g_fw.boot_ena_invert ||
           g_fw.p.drv.pwr_sense_enable != g_fw.boot_pwr_sense;
}

void params_rt_apply(uint16_t id)
{
    switch (id) {
    case PID_AFE_GAIN_CHANNEL:
    case PID_AFE_RATE_SPS:
        afe_reconfigure();                       /* hal_hx711_config + RATE pin, settle re-armed */
        break;
    case PID_STREAM_FALLBACK_HZ:
        if (g_fw.st.fb_running) {
            CRIT_BEGIN(HAL_CRIT_TICK);
            ss_set_rate(&g_fw.st.fb, g_fw.p.stream.fallback_hz, hal_time_us());
            CRIT_END();
        }
        break;
    default:
        /* read at use (afe.*, safety.*, io.*, drv.*, limits.*, home.*, motion.*); reboot_required
         * parameters are reported by SYSF_REBOOT_PENDING (params_rt_reboot_pending) */
        break;
    }
}

void params_rt_apply_all(void)
{
    params_rt_apply(PID_AFE_RATE_SPS);
    params_rt_apply(PID_STREAM_FALLBACK_HZ);
}
