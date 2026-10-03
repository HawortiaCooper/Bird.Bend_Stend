/* GENERATED - do not edit.
 * Source : 00_System/specs/protocol.yaml (ICD_protocol.md v0.5, PROTO 1.0, PAYLOAD 1)
 * Tool   : 00_System/tools/gen_protocol.py (run via gen_params.py)
 * Names and codes of commands, NACK codes, flag/status/FAULT/IO/BLOCK bits, EVENT codes
 * and their argument enums. FW code uses these identifiers only (no hand-listed codes).
 * Implements: IF-010, IF-012, FW-STR-003, FW-STR-006
 */
#ifndef PROTO_GEN_H
#define PROTO_GEN_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PROTO_ICD_VERSION      "0.5"
#define PROTO_MAJOR            1u
#define PROTO_MINOR            0u
#define PROTO_PAYLOAD_VERSION  1u

/* ---- constants (ICD §2, §3, §7) ---- */
#define PROTO_SYNC0                  0xA5u                  /* first sync byte (D-05 separator) */
#define PROTO_SYNC1                  0x5Au                  /* second sync byte */
#define PROTO_HEADER_LEN             6u                     /* SYNC0 SYNC1 TYPE SEQ LEN_lo LEN_hi */
#define PROTO_FRAME_OVERHEAD         8u                     /* header + CRC-16 */
#define PROTO_MAX_LEN                160u                   /* maximum payload length (frame <= 168 B) */
#define PROTO_RX_BUF_MIN             336u                   /* minimum receiver buffer (2 maximum frames, ICD §2.3) */
#define PROTO_INTERBYTE_TIMEOUT_MS   20u                    /* receiver inter-byte timeout */
#define PROTO_CRC_INIT               0xFFFFu                /* CRC-16/CCITT-FALSE init (poly 0x1021) */
#define PROTO_CRC_POLY               0x1021u                /* CRC-16/CCITT-FALSE polynomial */
#define PROTO_RESP_BIT               0x80u                  /* response TYPE = command TYPE | RESP_BIT */
#define PROTO_NACK_LEN               3u                     /* NACK payload: u8 status, u16 detail */
#define PROTO_INFO_LEN               44u                    /* INFO body (GET_INFO OK response after the STATUS byte) */
#define PROTO_STATUS_LEN             86u                    /* STATUS body (GET_STATUS OK response after the STATUS byte) */
#define PROTO_DATA_LEN               18u                    /* DATA payload, PAYLOAD_VERSION 1 */
#define PROTO_EVENT_LEN              16u                    /* EVENT payload */
#define PROTO_PARAM_ENTRY_LEN        7u                     /* u16 id, u8 type, u8[4] value */
#define PROTO_PARAMS_PER_PAGE        20u                    /* PARAM_ENTRYs per GET_ALL_PARAMS page */
#define PROTO_REBOOT_MAGIC           0xB007B007UL           /* REBOOT request magic */
#define PROTO_JOG_NO_BOUND           ((int32_t)(-2147483647 - 1)) /* JOG bound_um = 0x80000000: no bound */
#define PROTO_AFE_NO_DATA            ((int32_t)(-2147483647 - 1)) /* afe_raw / afe_raw_last = 0x80000000: no AFE sample */
#define PROTO_RAW_MIN                ((int32_t)-8388608)    /* HX711 negative rail (saturated) */
#define PROTO_RAW_MAX                ((int32_t)8388607)     /* HX711 positive rail (saturated) */
#define PROTO_DETAIL_CAUSE_INPUT     0xFFFFu                /* E_CAUSE_ACTIVE detail of ESTOP_CLEAR / HALT_CLEAR: input still active */
#define PROTO_TWIN_TCP_PORT          5760u                  /* FW host twin serial-over-TCP port (127.0.0.1) */
#define PROTO_TWIN_CTL_PORT          5761u                  /* FW host twin world-control port (JSON lines, tools/README) */
#define PROTO_HOME_RELEASE_MAX_UM    0x00002710UL           /* HOME: START not released within this travel in RELEASE / BACKOFF -> HOME_WIRING (ICD §5.4, OI-FW-23) */
#define PROTO_HOME_SLOW_EXTRA_UM     0x00002710UL           /* HOME: no START edge within home.backoff_um + this travel in SLOW_APPROACH -> HOME_NOT_FOUND (ICD §5.4, OI-FW-23) */

/* ---- command TYPEs (ICD §3.2); response TYPE = TYPE | PROTO_RESP_BIT ---- */
typedef enum {
    CMD_PING                 = 0x01,
    CMD_GET_INFO             = 0x02,
    CMD_GET_STATUS           = 0x03,
    CMD_REBOOT               = 0x04,
    CMD_GET_ALL_PARAMS       = 0x10,
    CMD_GET_PARAM            = 0x11,
    CMD_SET_PARAM            = 0x12,
    CMD_SAVE_PARAMS          = 0x13,
    CMD_LOAD_PARAMS          = 0x14,
    CMD_DEFAULT_PARAMS       = 0x15,
    CMD_STREAM_START         = 0x20,
    CMD_STREAM_STOP          = 0x21,
    CMD_SET_VALID            = 0x22,
    CMD_ENABLE               = 0x30,
    CMD_DISABLE              = 0x31,
    CMD_HOME                 = 0x32,
    CMD_MOVE_ABS             = 0x33,
    CMD_JOG                  = 0x34,
    CMD_MOVE_UNTIL_LOAD      = 0x35,
    CMD_STOP                 = 0x36,
    CMD_HALT                 = 0x37,
    CMD_HALT_CLEAR           = 0x38,
    CMD_ESTOP_CLEAR          = 0x39,
    CMD_FAULT_CLEAR          = 0x3A,
    CMD_PAUSE                = 0x3B,
    CMD_RESUME               = 0x3C,
} proto_cmd_t;

/* fixed request LEN per command (E_LENGTH otherwise) */
#define CMD_REQ_LEN_PING                 0u
#define CMD_REQ_LEN_GET_INFO             0u
#define CMD_REQ_LEN_GET_STATUS           0u
#define CMD_REQ_LEN_REBOOT               4u
#define CMD_REQ_LEN_GET_ALL_PARAMS       1u
#define CMD_REQ_LEN_GET_PARAM            2u
#define CMD_REQ_LEN_SET_PARAM            7u
#define CMD_REQ_LEN_SAVE_PARAMS          0u
#define CMD_REQ_LEN_LOAD_PARAMS          0u
#define CMD_REQ_LEN_DEFAULT_PARAMS       0u
#define CMD_REQ_LEN_STREAM_START         0u
#define CMD_REQ_LEN_STREAM_STOP          0u
#define CMD_REQ_LEN_SET_VALID            1u
#define CMD_REQ_LEN_ENABLE               0u
#define CMD_REQ_LEN_DISABLE              0u
#define CMD_REQ_LEN_HOME                 1u
#define CMD_REQ_LEN_MOVE_ABS             12u
#define CMD_REQ_LEN_JOG                  12u
#define CMD_REQ_LEN_MOVE_UNTIL_LOAD      17u
#define CMD_REQ_LEN_STOP                 1u
#define CMD_REQ_LEN_HALT                 0u
#define CMD_REQ_LEN_HALT_CLEAR           0u
#define CMD_REQ_LEN_ESTOP_CLEAR          0u
#define CMD_REQ_LEN_FAULT_CLEAR          0u
#define CMD_REQ_LEN_PAUSE                0u
#define CMD_REQ_LEN_RESUME               0u

/* commands the SW sends through its priority path (ICD §2.4) */
#define CMD_IS_PRIORITY(t) \
    ((t) == CMD_STOP || \
     (t) == CMD_HALT || \
     (t) == CMD_HALT_CLEAR || \
     (t) == CMD_ESTOP_CLEAR || \
     (t) == CMD_FAULT_CLEAR || \
     (t) == CMD_PAUSE)

/* commands the FW stop sniffer acts on ahead of normal processing (ICD §2) */
#define CMD_IS_SNIFFED(t) \
    ((t) == CMD_STOP || \
     (t) == CMD_HALT || \
     (t) == CMD_PAUSE)

/* ---- Asynchronous frame TYPEs (FW → PC) (ICD §3.1) ---- */
typedef enum {
    ASYNC_DATA                     = 0xC0, /* DATA frame (§7.3), one per HX711 conversion while the stream is on */
    ASYNC_EVENT                    = 0xC1, /* EVENT frame (§7.4), sent regardless of the stream state */
} proto_async_type_t;

/* ---- Response STATUS codes (ICD §4.2) ---- */
typedef enum {
    ST_OK                       = 0, /* accepted / executed */
    ST_E_UNKNOWN_CMD            = 1, /* TYPE in 0x01..0x3F not defined */
    ST_E_LENGTH                 = 2, /* LEN ≠ the command's fixed LEN */
    ST_E_PARAM_ID               = 3, /* unknown parameter id */
    ST_E_TYPE                   = 4, /* PARAM_ENTRY type byte ≠ the parameter's type */
    ST_E_RANGE                  = 5, /* argument out of range (never clamped) */
    ST_E_CONFIG                 = 6, /* SET_PARAM violates a hard rule (§11.4) */
    ST_E_BUSY                   = 7, /* not possible now, retry later */
    ST_E_STATE                  = 8, /* motion / enable / RESUME refused in the current state */
    ST_E_CAUSE_ACTIVE           = 9, /* clear refused, cause still present */
    ST_E_CONFIRM                = 10, /* HOME with load above home.max_load_raw without the confirmed flag */
    ST_E_NVM                    = 11, /* NVM failure */
    ST_E_INTERNAL               = 12, /* implementation error or command not in this build; nothing executed */
} proto_status_code_t;

/* ---- E_BUSY detail (ICD §4.2) ---- */
typedef enum {
    BUSY_MOTION                   = 1, /* a motion is running (motion state MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING or STOPPING) */
    BUSY_ENABLING                 = 2, /* ENA settle (motion.ena_settle_ms) running */
} proto_busy_detail_t;

/* ---- E_INTERNAL detail (ICD §4.2) ---- */
typedef enum {
    INTERNAL_NOT_IN_BUILD             = 1, /* command defined in the ICD but implemented in a later milestone (its feature bit is 0); nothing executed */
    INTERNAL_INVARIANT                = 2, /* internal consistency check failed; nothing executed */
} proto_internal_detail_t;

/* ---- E_NVM detail / NVM_ERROR arg (ICD §4.2) ---- */
typedef enum {
    NVMD_NO_RECORD                = 1, /* no valid NVM record (LOAD_PARAMS), or a record that violates a hard rule */
    NVMD_ERASE_PROGRAM            = 2, /* flash erase or program error */
    NVMD_VERIFY                   = 3, /* read-back verify error */
} proto_nvm_detail_t;

/* ---- BLOCK mask (detail of E_STATE) (ICD §4.3) ---- */
#define BLOCK_ESTOP_BIT                  0u
#define BLOCK_ESTOP                      0x0001u /* ESTOP latched or E-stop sense input open (also evaluated by RESUME) */
#define BLOCK_HALT_BIT                   1u
#define BLOCK_HALT                       0x0002u /* HALT latched (PC: HALT command / Pause-Break key) (also evaluated by RESUME) */
#define BLOCK_FAULT_BIT                  2u
#define BLOCK_FAULT                      0x0004u /* any FAULT latched (§7.6) (also evaluated by RESUME) */
#define BLOCK_NOT_ENABLED_BIT            3u
#define BLOCK_NOT_ENABLED                0x0008u /* motion state NOT_ENABLED (ENABLE never done, or DISABLE / E-stop / idle disable / driver power loss since) */
#define BLOCK_NOT_HOMED_BIT              4u
#define BLOCK_NOT_HOMED                  0x0010u /* MOVE_ABS, MOVE_UNTIL_LOAD or JOG with a bound while not homed */
#define BLOCK_LIMIT_BIT                  5u
#define BLOCK_LIMIT                      0x0020u /* motion toward an active or latched limit switch (direction = sign(target − x) or sign(v)); only motion away is accepted while latched (D-33h) */
#define BLOCK_AFE_STALE_BIT              6u
#define BLOCK_AFE_STALE                  0x0040u /* no HX711 sample for afe.timeout_ms */
#define BLOCK_AFE_SATURATED_BIT          7u
#define BLOCK_AFE_SATURATED              0x0080u /* last HX711 sample at a rail */
#define BLOCK_DRV_UNPOWERED_BIT          8u
#define BLOCK_DRV_UNPOWERED              0x0100u /* drv.pwr_sense_enable and the DRV_POWER input reads 'off' (R5 §1.5, D-28, D-29c) */
#define BLOCK_DRIVER_ALARM_BIT           9u
#define BLOCK_DRIVER_ALARM               0x0200u /* ALM start-block (SAF-FW-026, D-28): ALM active and driver power present (sense disabled → assumed present); new motion starts only (MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0 while not jogging) */
#define BLOCK_PAUSED_BIT                 10u
#define BLOCK_PAUSED                     0x0400u /* PAUSED latched (D-30): MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 (incl. refreshes of a running jog) refused; cleared by RESUME (clears only PAUSED) or HALT_CLEAR (clears HALT and PAUSED) (D-31) */
#define BLOCK_DEFINED_MASK               0x07FFu

/* ---- STOP mode (ICD §5.5) ---- */
typedef enum {
    STOPMODE_IMMEDIATE                = 0, /* no further PUL edge ≤ 2 ms after the last command byte (SAF-FW-002) */
    STOPMODE_CONTROLLED               = 1, /* planned deceleration at motion.a_stop_um_s2 (clean halt only at step period > 2 ms and planned stop distance <= 1 step, §6.5) */
} proto_stop_mode_t;

/* ---- MOVE_UNTIL_LOAD cmp (ICD §5.4) ---- */
typedef enum {
    CMP_GE                       = 0, /* stop when raw ≥ raw_stop */
    CMP_LE                       = 1, /* stop when raw ≤ raw_stop */
} proto_mul_cmp_t;

/* ---- HOME flags (ICD §5.4) ---- */
#define HOMEF_LOAD_CONFIRMED_BIT         0u
#define HOMEF_LOAD_CONFIRMED             0x01u /* operator confirmed homing with load above home.max_load_raw (SAF-FW-021) */
#define HOMEF_DEFINED_MASK               0x01u

/* ---- Motion state (STATUS motion_state) (ICD §6.1) ---- */
typedef enum {
    MS_NOT_ENABLED              = 0, /* after boot, DISABLE, E-stop, idle disable or driver power loss; no pulse possible (DATA ENABLED = 0) */
    MS_ENABLING                 = 1, /* ENA asserted, motion.ena_settle_ms running (ENABLED = 0) */
    MS_IDLE                     = 2, /* enabled, standing (holding) (ENABLED = 1, MOVING = 0) */
    MS_MOVE_ABS                 = 3, /* executing MOVE_ABS (MOVING = 1) */
    MS_JOG                      = 4, /* executing JOG (MOVING = 1) */
    MS_MOVE_UNTIL_LOAD          = 5, /* executing MOVE_UNTIL_LOAD (MOVING = 1) */
    MS_HOMING                   = 6, /* executing HOME, see home_phase (MOVING = 1) */
    MS_STOPPING                 = 7, /* controlled deceleration in progress (MOVING = 1) */
} proto_motion_state_t;

/* ---- Homing phase (STATUS home_phase) (ICD §7.2) ---- */
typedef enum {
    HP_NONE                     = 0, /* no homing since boot */
    HP_PRECHECK                 = 1, /* load pre-check */
    HP_RELEASE                  = 2, /* moving off an active START switch (+x) */
    HP_FAST_SEEK                = 3, /* fast seek toward START (−x) at home.v_fast_um_s */
    HP_BACKOFF                  = 4, /* back-off home.backoff_um (+x) */
    HP_SLOW_APPROACH            = 5, /* slow approach toward START at home.v_slow_um_s, edge capture */
    HP_MOVE_TO_ZERO             = 6, /* move to x = 0 */
    HP_DONE                     = 7, /* last homing completed or failed */
} proto_home_phase_t;

/* ---- Latch source (STATUS halt_src / pause_src, EVENT HALT_SET / PAUSED arg) (ICD §7.2) ---- */
typedef enum {
    SRC_NONE                     = 0, /* not latched */
    SRC_PC                       = 1, /* PC command (HALT, PAUSE) */
    SRC_BUTTON                   = 2, /* physical PAUSE button (pause_src / PAUSED only; halt_src is never BUTTON since v0.5, D-36) */
} proto_source_t;

/* ---- Reset cause (STATUS reset_cause, EVENT BOOT arg) (ICD §7.2) ---- */
typedef enum {
    RST_UNKNOWN                  = 0, /* no flag recognised */
    RST_POWER_ON                 = 1, /* power-on / POR */
    RST_PIN                      = 2, /* NRST pin */
    RST_SOFTWARE                 = 3, /* software reset (REBOOT) */
    RST_IWDG                     = 4, /* independent watchdog */
    RST_WWDG                     = 5, /* window watchdog */
    RST_LOW_POWER                = 6, /* low-power reset */
    RST_BROWN_OUT                = 7, /* brown-out reset */
} proto_reset_cause_t;

/* ---- STATUS sys_flags (ICD §7.2) ---- */
#define SYSF_CLK_FALLBACK_BIT           0u
#define SYSF_CLK_FALLBACK               0x01u /* running on HSI fallback clock (FW-PLT-002); timing ±1 % */
#define SYSF_CFG_DIRTY_BIT              1u
#define SYSF_CFG_DIRTY                  0x02u /* RAM parameters differ from the NVM record (§11.2) */
#define SYSF_STREAM_ON_BIT              2u
#define SYSF_STREAM_ON                  0x04u /* DATA stream on */
#define SYSF_REBOOT_PENDING_BIT         3u
#define SYSF_REBOOT_PENDING             0x08u /* a reboot_required parameter was changed (effective after SAVE_PARAMS + REBOOT) */
#define SYSF_NVM_DEFAULTED_BIT          4u
#define SYSF_NVM_DEFAULTED              0x10u /* defaults after boot/LOAD rules 2/4/5, until the next SAVE or clean LOAD */
#define SYSF_DEFINED_MASK               0x1Fu

/* ---- INFO feature_mask (ICD §7.1) ---- */
#define FEAT_AFE_BIT                    0u
#define FEAT_AFE                        0x00000001UL /* real HX711 */
#define FEAT_AFE_SYNTHETIC_BIT          1u
#define FEAT_AFE_SYNTHETIC              0x00000002UL /* M1 placeholder samples */
#define FEAT_MOTION_BIT                 2u
#define FEAT_MOTION                     0x00000004UL /* step generation */
#define FEAT_HOMING_BIT                 3u
#define FEAT_HOMING                     0x00000008UL /* HOME command */
#define FEAT_MOVE_UNTIL_LOAD_BIT        4u
#define FEAT_MOVE_UNTIL_LOAD            0x00000010UL /* MOVE_UNTIL_LOAD command */
#define FEAT_NVM_BIT                    5u
#define FEAT_NVM                        0x00000020UL /* SAVE/LOAD_PARAMS */
#define FEAT_TWIN_BIT                   6u
#define FEAT_TWIN                       0x00000040UL /* host twin build */
#define FEAT_BUTTONS_BIT                7u
#define FEAT_BUTTONS                    0x00000080UL /* PAUSE button input (the STOP/BREAK input is retired, D-36) */
#define FEAT_DRV_SIGNALS_BIT            8u
#define FEAT_DRV_SIGNALS                0x00000100UL /* ALM/PEND/DRV_POWER inputs */
#define FEAT_DEFINED_MASK               0x000001FFUL

/* ---- DATA flags (u8; also STATUS flags) (ICD §7.6) ---- */
#define DF_VALID_BIT                  0u
#define DF_VALID                      0x01u /* data validity (D-05): SET_VALID, cleared by every operational stop except the jog dead-man */
#define DF_MOVING_BIT                 1u
#define DF_MOVING                     0x02u /* motion state MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING or STOPPING */
#define DF_HOMED_BIT                  2u
#define DF_HOMED                      0x04u /* machine zero valid */
#define DF_ENABLED_BIT                3u
#define DF_ENABLED                    0x08u /* driver enabled and settled (motion state ≥ IDLE) */
#define DF_ESTOP_BIT                  4u
#define DF_ESTOP                      0x10u /* ESTOP latched or E-stop sense input open */
#define DF_HALT_BIT                   5u
#define DF_HALT                       0x20u /* HALT latched (PC HALT command / Pause-Break key; STATUS halt_src = PC) */
#define DF_FAULT_BIT                  6u
#define DF_FAULT                      0x40u /* any FAULT latched (STATUS faults) */
#define DF_OVERRUN_BIT                7u
#define DF_OVERRUN                    0x80u /* ≥ 1 DATA frame dropped or ≥ 1 conversion missed by the FW since the previous sent frame */
#define DF_DEFINED_MASK               0xFFu

/* ---- DATA status (u16; also STATUS status) (ICD §7.6) ---- */
#define DS_PAUSED_BIT                 0u
#define DS_PAUSED                     0x0001u /* PAUSED latch (source: STATUS pause_src, EVENT PAUSED arg); blocks new motion (BLOCK PAUSED); cleared by RESUME (clears only PAUSED) or HALT_CLEAR (clears HALT and PAUSED) (§5.5, D-30, D-31) */
#define DS_LIMIT_START_BIT            1u
#define DS_LIMIT_START                0x0002u /* START limit input active or LIMIT_START latched */
#define DS_LIMIT_END_BIT              2u
#define DS_LIMIT_END                  0x0004u /* END limit input active or LIMIT_END latched */
#define DS_LOAD_LIMIT_BIT             3u
#define DS_LOAD_LIMIT                 0x0008u /* FAULT LOAD_LIMIT latched */
#define DS_AFE_STALE_BIT              4u
#define DS_AFE_STALE                  0x0010u /* no HX711 sample for afe.timeout_ms */
#define DS_AFE_SATURATED_BIT          5u
#define DS_AFE_SATURATED              0x0020u /* this sample at a rail */
#define DS_AFE_SETTLING_BIT           6u
#define DS_AFE_SETTLING               0x0040u /* sample within afe.settle_discard after a (re)configuration */
#define DS_AFE_RATE_MISMATCH_BIT      7u
#define DS_AFE_RATE_MISMATCH          0x0080u /* measured rate deviates more than afe.rate_tol_pct */
#define DS_LINK_WDG_BIT               8u
#define DS_LINK_WDG                   0x0100u /* link watchdog tripped, until the next valid command frame */
#define DS_STOP_BTN_BIT               9u
#define DS_STOP_BTN                   0x0200u /* RETIRED in ICD v0.5: reserved, sent as 0, never reused. was: physical STOP/BREAK button input active. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (power cut + sense) */
#define DS_PAUSE_BTN_BIT              10u
#define DS_PAUSE_BTN                  0x0400u /* physical PAUSE button input active [valid only with FEAT_BUTTONS] */
#define DS_ALM_BIT                    11u
#define DS_ALM                        0x0800u /* driver ALM active [valid only with FEAT_DRV_SIGNALS] */
#define DS_PEND_BIT                   12u
#define DS_PEND                       0x1000u /* driver PEND (in position) active [valid only with FEAT_DRV_SIGNALS] */
#define DS_POS_UNCERTAIN_BIT          13u
#define DS_POS_UNCERTAIN              0x2000u /* an immediate stop may have truncated a pulse (±1 step), cleared by the next HOME */
#define DS_NO_AFE_DATA_BIT            14u
#define DS_NO_AFE_DATA                0x4000u /* fallback frame (afe_raw = 0x80000000) */
#define DS_DRV_PWR_BIT                15u
#define DS_DRV_PWR                    0x8000u /* driver power present (reads 1 when drv.pwr_sense_enable = false and FEAT_DRV_SIGNALS = 1) [valid only with FEAT_DRV_SIGNALS] */
#define DS_DEFINED_MASK               0xFDFFu
#define DS_RETIRED_MASK               0x0200u

/* ---- FAULT mask (u16) (ICD §7.6) ---- */
#define FAULT_LOAD_LIMIT_BIT             0u
#define FAULT_LOAD_LIMIT                 0x0001u /* FW load limit or rail sample; always clearable (re-trip on regrow, SAF-FW-011) */
#define FAULT_AFE_FAULT_BIT              1u
#define FAULT_AFE_FAULT                  0x0002u /* AFE stale while moving; cause: AFE stale or last sample saturated */
#define FAULT_STEP_FAULT_BIT             2u
#define FAULT_STEP_FAULT                 0x0004u /* step overrun / count fault; HOMED cleared; no persistent cause */
#define FAULT_LIMIT_WIRING_BIT           3u
#define FAULT_LIMIT_WIRING               0x0008u /* both limit inputs active; cause: both still active */
#define FAULT_HOME_NOT_FOUND_BIT         4u
#define FAULT_HOME_NOT_FOUND             0x0010u /* no START edge within home.max_travel_um; no persistent cause */
#define FAULT_HOME_WIRING_BIT            5u
#define FAULT_HOME_WIRING                0x0020u /* END switch reached during homing; no persistent cause */
#define FAULT_K1_WELDED_BIT              6u
#define FAULT_K1_WELDED                  0x0040u /* E-stop sense open while driver power stays present > drv.k1_weld_ms (D-29c); cause: E-stop open and power present */
#define FAULT_HOME_DRIFT_BIT             7u
#define FAULT_HOME_DRIFT                 0x0080u /* re-homing edge deviates > home.drift_tol_um; no persistent cause */
#define FAULT_DEFINED_MASK               0x00FFu

/* ---- IO mask (u16, STATUS io; 'active' after polarity) (ICD §7.6) ---- */
#define IO_ESTOP_OPEN_BIT             0u
#define IO_ESTOP_OPEN                 0x0001u /* E-stop sense input open */
#define IO_LIMIT_START_BIT            1u
#define IO_LIMIT_START                0x0002u /* START limit input active */
#define IO_LIMIT_END_BIT              2u
#define IO_LIMIT_END                  0x0004u /* END limit input active */
#define IO_STOP_BTN_BIT               3u
#define IO_STOP_BTN                   0x0008u /* RETIRED in ICD v0.5: reserved, sent as 0, never reused. was: STOP/BREAK button input active (PC7 is no longer an input). D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (power cut + sense) */
#define IO_PAUSE_BTN_BIT              4u
#define IO_PAUSE_BTN                  0x0010u /* PAUSE button input active [valid only with FEAT_BUTTONS] */
#define IO_ALM_BIT                    5u
#define IO_ALM                        0x0020u /* driver ALM input active [valid only with FEAT_DRV_SIGNALS] */
#define IO_PEND_BIT                   6u
#define IO_PEND                       0x0040u /* driver PEND input active [valid only with FEAT_DRV_SIGNALS] */
#define IO_DRV_PWR_BIT                7u
#define IO_DRV_PWR                    0x0080u /* raw driver-power sense input 'powered' [valid only with FEAT_DRV_SIGNALS] */
#define IO_ENA_DISABLED_BIT           8u
#define IO_ENA_DISABLED               0x0100u /* ENA output at the disabled level */
#define IO_RATE_80_BIT                9u
#define IO_RATE_80                    0x0200u /* HX711 RATE output high */
#define IO_DEFINED_MASK               0x03F7u
#define IO_RETIRED_MASK               0x0008u

/* ---- EVENT codes (ICD §8.1) ---- */
typedef enum {
    EV_BOOT                     = 1,
    EV_STOPPED                  = 2,
    EV_MOVE_DONE                = 3,
    EV_ESTOP_SET                = 4,
    EV_ESTOP_CLEARED            = 5,
    EV_HALT_SET                 = 6,
    EV_HALT_CLEARED             = 7,
    EV_PAUSED                   = 8,
    EV_PAUSE_CLEARED            = 9,
    EV_RESUME_REQUEST           = 10,
    EV_FAULT_SET                = 11,
    EV_FAULT_CLEARED            = 12,
    EV_LIMIT_SET                = 13,
    EV_LIMIT_CLEARED            = 14,
    EV_LINK_WDG                 = 15,
    EV_LINK_RESTORED            = 16,
    EV_VALID_CLEARED            = 17,
    EV_HOMED                    = 18,
    EV_HOME_FAILED              = 19,
    EV_DRIVER_ENABLED           = 20,
    EV_DRIVER_DISABLED          = 21,
    EV_STOP_BUTTON              = 22, /* RETIRED in ICD v0.5: never sent, code never reused. was: STOP/BREAK button pressed / released. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (power cut + sense) */
    EV_PAUSE_BUTTON             = 23,
    EV_ALM_CHANGED              = 24,
    EV_AFE_REINIT               = 25,
    EV_AFE_RATE_MISMATCH        = 26,
    EV_AFE_STALE                = 27,
    EV_PARAMS_SAVED             = 28,
    EV_PARAMS_LOADED            = 29,
    EV_PARAMS_DEFAULTED         = 30,
    EV_NVM_ERROR                = 31,
    EV_CLK_FALLBACK             = 32,
    EV_DRIVER_POWER             = 33,
    EV_NOT_SETTLED              = 34,
} proto_event_t;

/* ---- Stop causes (STOPPED arg, VALID_CLEARED arg) (ICD §8.2) ---- */
typedef enum {
    SC_NONE                     = 0, /* — */
    SC_PC_STOP                  = 1, /* STOP mode 0 */
    SC_PC_STOP_CONTROLLED       = 2, /* STOP mode 1 */
    SC_PC_HALT                  = 3, /* HALT command */
    SC_STOP_BUTTON              = 4, /* RETIRED in ICD v0.5: never sent, code never reused. was: physical STOP/BREAK button. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (power cut + sense) */
    SC_PAUSE_BUTTON             = 5, /* physical PAUSE button */
    SC_PC_PAUSE                 = 6, /* PAUSE command */
    SC_ESTOP                    = 7, /* E-stop sense opened */
    SC_LIMIT_START              = 8, /* START limit switch */
    SC_LIMIT_END                = 9, /* END limit switch */
    SC_LIMIT_WIRING             = 10, /* both limit inputs active */
    SC_LOAD_LIMIT               = 11, /* FW load limit (incl. rail sample) */
    SC_AFE_FAULT                = 12, /* AFE stale while moving */
    SC_LINK_WDG                 = 13, /* link watchdog */
    SC_JOG_DEADMAN              = 14, /* jog dead-man (VALID unchanged) */
    SC_STEP_FAULT               = 15, /* step overrun / count fault */
    SC_HOME_FAIL                = 16, /* homing failure */
    SC_DRV_POWER_LOST           = 17, /* driver power lost while a motion was running (SAF-FW-024, D-29c) */
} proto_stop_cause_t;

/* ---- MOVE_DONE reasons (ICD §8.3) ---- */
typedef enum {
    MD_TARGET                   = 0, /* MOVE_ABS / HOME end */
    MD_LOAD_THRESHOLD           = 1, /* MOVE_UNTIL_LOAD threshold */
    MD_BOUND                    = 2, /* MOVE_UNTIL_LOAD or JOG bound */
    MD_SOFT_LIMIT               = 3, /* JOG end at a soft limit or un-homed travel bound */
    MD_JOG_ZERO                 = 4, /* JOG 0 */
    MD_STOPPED                  = 5, /* ended by a stop source; see the preceding STOPPED / HOME_FAILED */
} proto_move_done_reason_t;

/* ---- HOME_FAILED reasons (ICD §8.1) ---- */
typedef enum {
    HF_NOT_FOUND                = 1, /* no START edge within home.max_travel_um (fault HOME_NOT_FOUND) */
    HF_WIRING                   = 2, /* END switch reached (fault HOME_WIRING) */
    HF_ABORTED                  = 3, /* any other stop during homing (no latch of its own) */
} proto_home_fail_reason_t;

/* ---- DRIVER_DISABLED causes (ICD §8.1) ---- */
typedef enum {
    DD_PC_DISABLE               = 1, /* DISABLE command */
    DD_IDLE                     = 2, /* idle auto-disable (safety.idle_disable_s) */
    DD_ESTOP                    = 3, /* E-stop sense opened */
    DD_DRV_POWER_LOST           = 4, /* driver power lost while enabled / enabling (SAF-FW-024, D-29c) */
} proto_driver_disabled_cause_t;

/* ---- PAUSE_CLEARED reasons (ICD §8.1) ---- */
typedef enum {
    PCLR_HALT_CLEAR               = 2, /* accepted HALT_CLEAR (clears HALT and PAUSED, D-31) */
    PCLR_RESUME                   = 3, /* accepted RESUME (clears only PAUSED, D-31) */
} proto_pause_cleared_reason_t;

/* ---- PARAMS_DEFAULTED reasons (ICD §8.1) ---- */
typedef enum {
    PDEF_COMMAND                  = 0, /* DEFAULT_PARAMS */
    PDEF_NO_RECORD                = 1, /* no NVM record (blank) */
    PDEF_CRC_ERROR                = 2, /* both records CRC-bad */
    PDEF_MIGRATION                = 3, /* record with another PARAM_DICT_HASH (migration by id) */
    PDEF_HARD_RULE                = 4, /* resulting image violates a hard rule */
} proto_params_defaulted_reason_t;

/* ---- Limit switch id (LIMIT_SET / LIMIT_CLEARED arg) (ICD §8.1) ---- */
typedef enum {
    LIM_START                    = 0, /* START switch (−x end, home reference) */
    LIM_END                      = 1, /* END switch (+x end) */
} proto_limit_id_t;

/* ---- afe_sample_t.status (seam hal_hx711, tools/README; not on the wire) (ICD tools/README seam v1.2) ---- */
#define AFES_SCK_OVERRUN_BIT            0u
#define AFES_SCK_OVERRUN                0x01u /* the read of this sample overran (SCK high > 60 us or DOUT still low after the last pulse): the HX711 may have entered power-down; the core re-initialises it (afe_reinit_count, EVENT AFE_REINIT) and flags the next afe.settle_discard samples AFE_SETTLING */
#define AFES_MISSED_EDGE_BIT            1u
#define AFES_MISSED_EDGE                0x02u /* at least one DOUT-ready edge was missed before this sample (recovered by hal_hx711_kick or a late edge): the core sets OVERRUN in the next DATA frame */
#define AFES_DEFINED_MASK               0x03u

#ifdef __cplusplus
}
#endif

#endif /* PROTO_GEN_H */
