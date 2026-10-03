/* Core state and module interfaces (FW_design §1, §4). The core is C11 and calls only the
 * hal_*.h seams, so it compiles unchanged for the target and the host twin (SYS-008).
 *
 * M1 scope (FW_design §9.1): link, dispatcher, parameters, NVM, synthetic AFE stream, VALID, EVENTs,
 * STOP/HALT/PAUSE/RESUME/clears without motion. Motion, homing and the real inputs are M2: motion
 * commands that pass the full check answer E_INTERNAL NOT_IN_BUILD (JOG 0 = OK no-op); no input is
 * sampled, so the input-derived state is "inactive" and the driver power is "not confirmed"
 * (fail-safe; FEAT_DRV_SIGNALS = 0).
 *
 * Contexts: main loop (thread), core_tick_1ms (level 4), on_afe_sample (level 3). Shared data
 * rules (FW_design §4.4): VALID and the stream state under CRIT_DATA, the EVENT ring and the hold
 * under CRIT_TICK; 32-bit fields written by one context only.
 * Implements: SYS-002, SYS-008, NFR-005 (static state only)
 */
#ifndef CORE_FW_H
#define CORE_FW_H

#include <stdbool.h>
#include <stdint.h>

#include "afe_rate.h"
#include "cmd_check.h"
#include "evq.h"
#include "flags.h"
#include "fw_config.h"
#include "latches.h"
#include "params_gen.h"
#include "payload.h"
#include "stream_sched.h"
#include "valid.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifndef FW_VERSION_MAJOR
#define FW_VERSION_MAJOR 0
#endif
#ifndef FW_VERSION_MINOR
#define FW_VERSION_MINOR 1
#endif
#ifndef FW_VERSION_PATCH
#define FW_VERSION_PATCH 0
#endif
/* feature bits of this build (ICD §7.1); the twin build adds FEAT_TWIN via FW_FEATURE_EXTRA */
#ifndef FW_FEATURE_EXTRA
#define FW_FEATURE_EXTRA 0u
#endif
#define FW_FEATURES ((uint32_t)(FEAT_AFE_SYNTHETIC | FEAT_NVM) | (uint32_t)(FW_FEATURE_EXTRA))

typedef struct {
    volatile uint32_t last_t_us;     /* time of the last sample (missed-conversion check) */
    volatile uint32_t stale_ref_us;  /* stale timer start: last sample or re-arm */
    volatile int32_t  raw_last;      /* PROTO_AFE_NO_DATA until the first sample */
    volatile bool     have_sample;
    volatile bool     saturated;     /* last sample at a rail */
    volatile bool     stale;         /* set by the tick, cleared by the sample ISR */
    volatile uint32_t samples;
    volatile uint8_t  settle_left;   /* samples still flagged AFE_SETTLING */
    volatile uint32_t period_us;     /* configured conversion period */
    bool     stale_reported;         /* tick: last AFE_STALE value sent */
    /* timestamp ring sample ISR -> main loop (rate statistics) */
    volatile uint32_t ring_t[AFE_RATE_PERIODS];
    volatile uint8_t  ring_w;
    uint8_t  ring_r;
    afe_rate_t rate;                 /* main loop */
} afe_state_t;

typedef struct {
    volatile bool     on;
    volatile uint16_t frame_seq;     /* next DATA frame_seq (ICD §2.2) */
    volatile bool     overrun;       /* the next sent frame carries DF_OVERRUN */
    volatile uint32_t tx_drops;
    volatile bool     hold;          /* NVM operation: no fallback frames */
    stream_sched_t    fb;            /* fallback schedule (tick) */
    bool              fb_running;
} stream_state_t;

typedef struct {
    params_t p;                      /* RAM parameter image */
    bool     boot_pul_invert, boot_ena_invert, boot_pwr_sense;   /* reboot_required as applied */
    uint8_t  reset_cause;
    bool     clk_fallback;
    latch_t  lat;
    valid_t  valid;
    evq_t    evq;
    uint8_t  motion_state;           /* MS_* (M1: NOT_ENABLED) */
    bool     homed;
    uint8_t  home_phase;
    int32_t  pos_steps;              /* M1: 0 (no step generator) */
    afe_state_t    afe;
    stream_state_t st;
    /* NVM (§5.11) */
    bool     nvm_record_valid;
    uint32_t nvm_record_seq;
    bool     cfg_dirty;
    bool     nvm_defaulted;
    uint16_t nvm_save_ms;
    uint32_t nvm_save_uptime_ms;
    /* link */
    volatile uint32_t last_cmd_rx_ms;
    uint32_t rx_invalid_type;
    /* main loop */
    uint16_t loop_max_us;
    volatile uint32_t tick_count;
    uint32_t boot_ms;
} fw_t;

extern fw_t g_fw;

/* ---- app.c ---- */
void app_init(void);
void app_loop(void);

/* ---- events.c ---- */
void fw_event(uint16_t code, uint16_t arg, int32_t value, int32_t value2);
void events_flush(void);

/* ---- link.c ---- */
void link_init(void);
void link_poll(void);
void link_tick(uint32_t now_ms);
/** Response frame (TYPE | 0x80, same SEQ) with STATUS byte + body into TX class R. */
void link_respond(uint8_t type, uint8_t seq, uint8_t status, const uint8_t *body, uint16_t n);
void link_nack(uint8_t type, uint8_t seq, uint8_t status, uint16_t detail);
void link_counters(uint32_t *ok, uint32_t *crc, uint32_t *frame_err);
bool link_motion_start_allowed(void);          /* sniffed-stop hold (M2 motion_start gate) */

/* ---- cmd.c ---- */
void cmd_execute(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len);
void cmd_reboot_service(void);

/* ---- params_rt.c ---- */
void     params_rt_apply(uint16_t id);
void     params_rt_apply_all(void);
bool     params_rt_reboot_pending(void);
uint8_t  params_rt_page(uint8_t page, uint8_t *body);   /* returns entries; body = page header + entries */
void     params_rt_entry(const param_meta_t *m, uint8_t out[PROTO_PARAM_ENTRY_LEN]);

/* ---- nvm.c ---- */
void nvm_boot(uint16_t *ev, uint16_t *arg);
void nvm_cfg_dirty_update(void);
bool nvm_load_cmd(void);
void nvm_default_cmd(void);
void nvm_save_start(uint8_t seq);
bool nvm_busy(void);
bool nvm_service(void);                         /* true if this pass ran the flash operation */

/* ---- afe.c ---- */
void afe_init(void);
void afe_reconfigure(void);
void afe_rearm_after_hold(void);
void afe_tick(uint32_t now_us);
void afe_service(void);
uint8_t afe_gain_pulses(uint8_t gain_channel);

/* ---- stream.c ---- */
void stream_on_sample(uint32_t t_us, int32_t raw, int32_t pos_steps, bool settling, bool saturated);
void stream_tick(uint32_t now_us);
void stream_set(bool on);

/* ---- status.c ---- */
void     fw_flags(flags_in_t *f, uint32_t t_us);
void     fw_cmd_ctx(cmd_ctx_t *c);
void     status_build(status_t *s);
void     info_build(info_t *i);
int32_t  fw_pos_um(void);
uint32_t fw_v_limit(void);

/* ---- led.c ---- */
void led_service(uint32_t now_ms);

#ifdef __cplusplus
}
#endif

#endif /* CORE_FW_H */
