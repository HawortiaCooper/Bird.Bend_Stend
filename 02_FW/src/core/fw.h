/* Core state and module interfaces (FW_design §1, §4). The core is C11 and calls only the
 * hal_*.h seams, so it compiles unchanged for the target and the host twin (SYS-008).
 *
 * M2 scope (FW_design §9.7): real HX711 samples (load limit, saturation, settle, re-init, missed
 * edges), step generation (motion.c: ENABLE/DISABLE, MOVE_ABS, JOG, HOME, STOP/HALT/PAUSE with real
 * motion), inputs and supervision (safety.c: E-stop sense, limits, PAUSE button, DRV_PWR / K1 / ALM /
 * PEND, link watchdog, jog dead-man, AFE stale, idle disable). MOVE_UNTIL_LOAD stays M4
 * (E_INTERNAL NOT_IN_BUILD, FEAT_MOVE_UNTIL_LOAD = 0). No STOP-button input (D-36, CR-01).
 *
 * Contexts (FW_design §4.1): main loop (thread), core_tick_1ms (level 4), on_afe_sample (level 3),
 * step_isr (level 2), on_input_edge (levels 0/1, after the HAL fixed reaction). Shared data rules
 * (FW_design §4.4): motion state / latches / EVENTs are changed only by the thread under CRIT_TICK
 * and by the tick; latch and VALID words that the sample ISR also writes are changed under
 * CRIT_DATA; the ramp descriptor (step ISR) under CRIT_MOTION; ISRs at levels 0..3 only post
 * records that the tick folds (CRIT_HALT snapshot).
 * Implements: SYS-002, SYS-008, NFR-005 (static state only)
 */
#ifndef CORE_FW_H
#define CORE_FW_H

#include <stdbool.h>
#include <stdint.h>

#include "afe_rate.h"
#include "cmd_check.h"
#include "drvmon.h"
#include "evq.h"
#include "flags.h"
#include "fw_config.h"
#include "hx711_math.h"
#include "inputs.h"
#include "latches.h"
#include "loadlim.h"
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
/* feature bits of this build (ICD §7.1); the twin build adds FEAT_TWIN via FW_FEATURE_EXTRA; a target
 * image with the synthetic AFE source (env nucleo_f446re_synth, bring-up without an HX711) sets
 * FW_FEAT_AFE_SRC = FEAT_AFE_SYNTHETIC. MOVE_UNTIL_LOAD is M4 (bit 0). */
#ifndef FW_FEATURE_EXTRA
#define FW_FEATURE_EXTRA 0u
#endif
#ifndef FW_FEAT_AFE_SRC
#define FW_FEAT_AFE_SRC FEAT_AFE
#endif
#define FW_FEATURES ((uint32_t)(FW_FEAT_AFE_SRC) | \
                     (uint32_t)(FEAT_MOTION | FEAT_HOMING | FEAT_NVM | FEAT_BUTTONS | FEAT_DRV_SIGNALS) | \
                     (uint32_t)(FW_FEATURE_EXTRA))

typedef struct {
    volatile uint32_t last_t_us;     /* time of the last sample (missed-conversion check) */
    volatile uint32_t stale_ref_us;  /* stale timer start: last sample or re-arm */
    volatile int32_t  raw_last;      /* PROTO_AFE_NO_DATA until the first sample */
    volatile bool     have_sample;
    volatile bool     saturated;     /* last sample at a rail */
    volatile bool     stale;         /* set by the tick, cleared by the sample ISR */
    volatile uint32_t samples;
    hx_settle_t       settle;        /* samples still flagged AFE_SETTLING (ISR-owned, re-armed under CRIT_DATA) */
    loadlim_t         ll;            /* FW load limit (ISR-owned, thresholds copied under CRIT_DATA) */
    /* records sample ISR -> tick (single writer, folded by the tick) */
    volatile bool     trip_rec;      /* load limit tripped and LOAD_LIMIT newly latched by the ISR */
    volatile int32_t  trip_raw;
    volatile bool     trip_valid_was;/* VALID was 1 at the trip sample */
    volatile uint32_t trip_t_us;
    volatile bool     reinit_rec;    /* AFES_SCK_OVERRUN: re-initialise the HX711 */
    uint16_t          reinit_count;  /* STATUS afe_reinit_count (tick) */
    uint32_t          kick_ms;       /* last missed-edge kick (tick) */
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
    volatile bool     hold_gap;      /* an AFE hold ended: the next sample checks for missed conversions */
    volatile bool     hold_stream_on; /* stream state latched when the hold started (DEF-M1-04) */
    stream_sched_t    fb;            /* fallback schedule (tick) */
    bool              fb_running;
} stream_state_t;

/* one record per input (on_input_edge, levels 0/1 -> tick) */
typedef struct {
    volatile bool     edge;          /* any edge since the last fold */
    volatile bool     act_edge;      /* an active edge since the last fold */
    volatile uint32_t t_us;          /* time of the first active edge */
    volatile int32_t  steps;         /* hal_step_count() at that edge (homing capture, LIMIT_SET) */
    volatile bool     was_running;   /* the step timer was running at that edge */
} in_rec_t;

typedef struct {
    in_rec_t rec[8];                 /* by ICD IO bit index */
    uint16_t raw;                    /* electrical levels of the last tick sample */
    relf_t   estop_closed;           /* E-stop sense closed time (io.estop_release_ms) */
    relf_t   lim_rel[2];             /* START / END release (latch auto-clear, re-arm) */
    btn_t    pause;                  /* PAUSE button press / release */
    drvmon_t drv;                    /* DRV_PWR filter, K1 timer, ALM filter */
    bool     pend;                   /* PEND active (polarity applied) */
    bool     link_wdg;               /* DS_LINK_WDG */
    uint32_t link_wdg_rx;            /* cmd_rx_count at the trip */
    uint32_t idle_ms;                /* idle-disable counter */
    bool     idle_counting;
} inputs_t;

typedef struct {
    params_t p;                      /* RAM parameter image */
    /* parameter image as applied at boot (DEF-M1-01, OBS-M1-08): behaviour that depends on a
     * reboot_required parameter (PARAM_F_REBOOT) reads it from here, never from p; constant after
     * app_init(), so ISRs may read it */
    params_t boot_p;
    uint8_t  reset_cause;
    bool     clk_fallback;
    latch_t  lat;
    valid_t  valid;
    evq_t    evq;
    uint8_t  motion_state;           /* MS_* (thread under CRIT_TICK / tick; read by ISRs) */
    bool     homed;
    uint8_t  home_phase;
    bool     pos_uncertain;          /* DS_POS_UNCERTAIN, cleared by HOME */
    bool     ena_on;                 /* ENA output at the enabled level (IO_ENA_DISABLED = !ena_on) */
    bool     hw_meas;                /* hal_meas_cmd(INFO) answered at boot: FEAT_HW_MEAS (D-40 c) */
    inputs_t in;
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
    volatile uint32_t cmd_rx_count;  /* valid command frames (dispatcher + sniffer): LINK_RESTORED */
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
bool link_motion_start_allowed(void);          /* sniffed-stop hold (motion start gate) */
uint8_t link_hold_cause(void);                  /* SC_* of the latest sniffed stop frame */

/* ---- cmd.c ---- */
void cmd_execute(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len);
void cmd_reboot_service(void);

/* ---- params_rt.c ---- */
void     params_rt_apply(uint16_t id);
void     params_rt_apply_all(void);
bool     params_rt_reboot_pending(void);
/** Effective configuration = RAM image with every PARAM_F_REBOOT parameter (generated flags) at its
 *  boot value (FW-CFG-003); this is what cmd_check() evaluates. Thread context only. */
const params_t *params_rt_effective(void);
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
void afe_loadlim_apply(void);                 /* copy safety.load_* into the ISR state (CRIT_DATA) */
void afe_loadlim_cleared(void);               /* accepted FAULT_CLEAR of LOAD_LIMIT: regrow reference */
void afe_fold_trip(void);                     /* sample-ISR trip record -> EVENTs + stop (tick / FAULT_CLEAR) */
void afe_reconfigure(void);
void afe_rearm_after_hold(void);
void afe_tick(uint32_t now_us);
void afe_service(void);

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

/* ---- motion.c (thread: call under CRIT_TICK; tick: direct) ---- */
void     motion_init(void);                  /* boot: step HAL config, ENA per the boot rule */
uint16_t motion_enable(uint32_t now_ms);     /* ENABLE: remaining settle ms */
void     motion_disable(uint8_t dd_cause);   /* ENA disabled, NOT_ENABLED, HOMED cleared, EVENT */
void     motion_power_returned(uint32_t now_ms);
void     motion_move_abs(int32_t target_um, uint32_t v_um_s, uint32_t a_um_s2);
void     motion_jog(int32_t v_um_s, uint32_t a_um_s2, int32_t bound_um, uint32_t now_ms);
void     motion_home(void);
/** Stop the running motion: cause SC_* (SC_NONE = no STOPPED event, e.g. JOG 0), immediate (CLEAN)
 *  or controlled (a_stop, FW_design §5.6.4), MOVE_DONE reason md at standstill. Idempotent. */
void     motion_stop(uint8_t cause, bool controlled, uint8_t md);
/** E-stop: TRUNCATE + ENA disabled (also from the tick backup detection). */
void     motion_estop_hw(void);
void     motion_tick(uint32_t now_ms);
bool     motion_active(void);
int8_t   motion_dir(void);                   /* direction of the running segment (0 = none) */
bool     motion_home_edge(uint8_t lim_id, int32_t steps);  /* true: an expected homing edge */
int32_t  motion_target_um(void);             /* STATUS target_um */
int32_t  motion_unhomed_origin_um(void);     /* D-43 b: un-homed travel window origin (cmd_check ctx) */
uint16_t motion_enabling_left_ms(uint32_t now_ms);
void     motion_hold_resolved(uint8_t cause);/* sniffed-stop hold timed out: discard a parked start */
void     motion_ena_out(bool enabled);       /* ENA output + g_fw.ena_on */

/* ---- safety.c ---- */
void     safety_init(void);                  /* boot inputs (SAF-FW-007/018), filters, boot latches */
void     safety_tick(uint32_t now_ms, uint32_t now_us);
bool     safety_active(uint8_t io_bit);      /* polarity-corrected input level of the last sample */
uint32_t safety_estop_closed_ms(void);
bool     safety_drv_power(void);             /* filtered (true with sensing disabled) */
bool     safety_loaded(void);                /* |raw - zero_raw| >= release_band_raw or AFE stale */
uint16_t safety_fault_causes(void);          /* FAULT_* bits whose cause is present (FAULT_CLEAR) */
uint16_t safety_idle_left_s(void);           /* STATUS idle_disable_left_s */
void     safety_inputs_config(void);         /* hal_inputs_config() from the parameters */

/* ---- led.c ---- */
void led_service(uint32_t now_ms);

#ifdef __cplusplus
}
#endif

#endif /* CORE_FW_H */
