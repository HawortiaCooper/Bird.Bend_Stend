/* Application skeleton (FW_design §3.2 boot steps 2/4/7/8/9, §4.2 main loop, §4.3 control tick).
 * app_init() runs after the target HAL brought up clocks, time base, UART and outputs (hal/f446
 * board_init); app_loop() is one main-loop pass; core_tick_1ms() is the level-4 tick callback.
 * Implements: SAF-FW-018 (boot: NOT_ENABLED, not homed, VALID 0, stream off, PAUSED 0, BOOT
 *             event with the reset cause and the HardFault record), SAF-FW-019 (watchdog kicked
 *             only while the tick runs), SAF-FW-007 (boot inputs),
 *             FW-PLT-002 (CLK_FALLBACK event), NFR-006 (loop_max_us, non-blocking pass)
 */
#include <string.h>

#include "fw.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "proto_gen.h"

fw_t g_fw;

static uint32_t s_last_kick_tick;

void app_init(void)
{
    uint16_t nvm_ev = 0u, nvm_arg = 0u;
    uint32_t now_us = hal_time_us();
    memset(&g_fw, 0, sizeof g_fw);
    g_fw.boot_ms = hal_time_ms();
    g_fw.reset_cause = hal_reset_cause();
    g_fw.clk_fallback = hal_clk_fallback();
    evq_init(&g_fw.evq);
    latch_init(&g_fw.lat);
    valid_init(&g_fw.valid, now_us);                 /* VALID = 0 at boot, never persisted */
    g_fw.motion_state = (uint8_t)MS_NOT_ENABLED;     /* D-13: motion disabled, axis not homed */
    g_fw.homed = false;
    g_fw.home_phase = (uint8_t)HP_NONE;
    g_fw.afe.raw_last = PROTO_AFE_NO_DATA;
    g_fw.last_cmd_rx_ms = g_fw.boot_ms;

    nvm_boot(&nvm_ev, &nvm_arg);                     /* ICD §11.3 rules 2..5 -> g_fw.p */
    g_fw.boot_p = g_fw.p;                            /* reboot_required values as applied */

    /* EVENT order (FW_design §3.2 step 8): BOOT (value / value2 = HardFault record of the previous
     * run, seam v1.2, OI-FW-32), CLK_FALLBACK, NVM result; then the boot-time input latches */
    {
        uint32_t pc = 0u, cfsr = 0u;
        if (!hal_fault_record(&pc, &cfsr)) {
            pc = 0u;
            cfsr = 0u;
        }
        fw_event((uint16_t)EV_BOOT, g_fw.reset_cause, (int32_t)pc, (int32_t)cfsr);
    }
    if (g_fw.clk_fallback) {
        fw_event((uint16_t)EV_CLK_FALLBACK, 0u, 0, 0);
    }
    fw_event(nvm_ev, nvm_arg, 0, 0);

    {
        uint8_t req[CMD_REQ_LEN_DIAG_MEAS] = {0};             /* op INFO: does this build have HW_MEAS? */
        uint8_t resp[PROTO_MEAS_BODY_LEN];
        g_fw.hw_meas = hal_meas_cmd(req, sizeof req, resp, sizeof resp) == PROTO_MEAS_BODY_LEN;
    }
    link_init();
    motion_init();                                   /* step HAL: PUL idle, DIR, ENA "no current" */
    safety_init();                                   /* boot inputs, ENA boot rule (ICD §6.4) */
    afe_init();                                      /* HX711 at afe.rate_sps, load limit, settle */
    s_last_kick_tick = g_fw.tick_count;
}

void app_loop(void)
{
    uint32_t t0 = hal_time_us();
    uint32_t dt;
    bool long_pass;

    link_poll();
    if (!nvm_busy()) {
        events_flush();                              /* NVM: the line must drain (QUIESCE) */
    }
    afe_service();
    long_pass = nvm_service();
    cmd_reboot_service();
    led_service(hal_time_ms());
    if (g_fw.tick_count != s_last_kick_tick) {       /* SAF-FW-019: only while the tick runs */
        s_last_kick_tick = g_fw.tick_count;
        hal_wdg_kick();
    }
    dt = hal_time_us() - t0;
    if (!long_pass) {                                /* NFR-006: the NVM program pass is exempt */
        uint16_t d = (dt > 0xFFFFu) ? 0xFFFFu : (uint16_t)dt;
        if (d > g_fw.loop_max_us) {
            g_fw.loop_max_us = d;                    /* not reset on read (ICD §7.2) */
        }
    }
}

void core_tick_1ms(void)
{
    uint32_t now_us = hal_time_us();
    uint32_t now_ms = hal_time_ms();
    g_fw.tick_count++;
    safety_tick(now_ms, now_us);                     /* inputs, driver monitor, link wdg, idle */
    link_tick(now_ms);                               /* stop sniffer (motion part), hold */
    afe_tick(now_us);                                /* load-limit trip fold, AFE stale / fault */
    motion_tick(now_ms);                             /* completion, homing, settle, dead-man */
    CRIT_BEGIN(HAL_CRIT_DATA);
    valid_settle(&g_fw.valid, now_us);
    CRIT_END();
    stream_tick(now_us);                             /* fallback frames while stale */
}
