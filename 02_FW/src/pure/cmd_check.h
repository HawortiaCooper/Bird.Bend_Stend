/* Pure command acceptance (ICD §4.4 check order, §4.3 BLOCK mask, §5 argument rules; FW_design
 * §5.4.2). No side effects. The context is a field-by-field mirror of ref_cmdcheck.FwState, so
 * every vector of check_vectors.json maps 1:1 onto a call (test_impl_check). The live FW fills
 * the context from its state (core/link.c).
 * Origin: pattern of Thrust_Stand_HAW/02_FW/src/pure/cmd_check.* @37c8747 (rewritten).
 * Implements: FW-CMD-001, FW-CFG-003, SAF-FW-006, SAF-FW-012 (check), SAF-FW-020, SAF-FW-021,
 *             SAF-FW-023 (BLOCK PAUSED, RESUME), SAF-FW-024/026 (check part),
 *             FW-CMD-003 (FAULT_CLEAR cause rule), FW-MOT-009 (speed cap), D-30, D-31,
 *             D-40 c (DIAG_MEAS, ICD v0.6 Appendix C)
 */
#ifndef PURE_CMD_CHECK_H
#define PURE_CMD_CHECK_H

#include <stdbool.h>
#include <stdint.h>

#include "params_gen.h"
#include "proto.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const params_t *p;              /* FwState.params (RAM image) */
    uint8_t  motion_state;          /* MS_* */
    uint16_t enabling_left_ms;
    bool     homed;
    int32_t  pos_um;
    bool     estop_latched;
    bool     estop_input_open;
    uint32_t estop_closed_ms;       /* sense input closed continuously */
    bool     halt_latched;          /* (stop_btn_* retired: state_schema 3, D-36) */
    uint16_t faults;                /* latched FAULT_* mask */
    uint16_t fault_causes;          /* FAULT_* bits whose cause is still present */
    bool     limit_start;           /* input active or latched */
    bool     limit_end;
    bool     afe_stale;
    bool     afe_saturated;
    int32_t  raw;                   /* last HX711 sample */
    bool     drv_power;             /* DRV_PWR (evaluated if drv.pwr_sense_enable) */
    bool     alm_active;
    bool     nvm_record_valid;
    bool     paused;                /* PAUSED latch (D-30) */
    bool     hw_meas;               /* build has FEAT_HW_MEAS (not a FwState key: replay parameter) */
    int32_t  unhomed_origin_um;     /* D-43 b: origin of the un-homed travel window (state_schema 3) */
} cmd_ctx_t;

typedef struct {
    uint8_t  status;                /* ST_* */
    uint16_t detail;
} cmd_verdict_t;

/** Steps 1..5 of ICD §4.4 for one CRC-valid command frame (TYPE 0x01..0x3F). */
cmd_verdict_t cmd_check(const cmd_ctx_t *c, uint8_t type, const uint8_t *payload, uint16_t len);

/** Motion states 3..7 (MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING, STOPPING). */
bool     cmd_is_moving(uint8_t motion_state);
/** 'loaded' for the speed cap (ICD §5.4): AFE stale, or |raw - zero_raw| >= release_band_raw. */
bool     cmd_loaded(const cmd_ctx_t *c);
/** Speed cap in µm/s: min(v_max_load|travel, step-rate cap) [, v_unhomed for an un-homed JOG]. */
uint32_t cmd_v_limit(const cmd_ctx_t *c, bool loaded, bool unhomed_jog);
/** BLOCK mask (ICD §4.3) of a motion command / ENABLE in direction dir (-1/0/+1). */
uint16_t cmd_block_mask(const cmd_ctx_t *c, uint8_t type, int dir, bool jog_bound);

#ifdef __cplusplus
}
#endif

#endif /* PURE_CMD_CHECK_H */
