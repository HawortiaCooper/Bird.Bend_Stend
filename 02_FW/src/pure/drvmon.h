/* Driver monitor (FW_design §5.7.2; D-28, D-29 c, D-33 f): DRV_PWR 20 ms stability filter (both
 * directions), K1 weld timer, ALM first-sample / stable-release filter. One call per 1 kHz tick.
 * Pure C11.
 *  - DRV_PWR: a level change counts only after DRV_PWR_FILTER_MS consecutive samples at the new
 *    level (19 ms toggles are ignored, loss reaction <= 20 ms + RC + 1 tick <= 25 ms, FW-SW-005).
 *    With drv.pwr_sense_enable = false (boot value, reboot-required) power always reads present.
 *  - K1_WELDED (only with drv.pwr_sense_enable AND drv.k1_check_enable, boot values; SRS OI-18,
 *    ICD v0.7): the timer runs while the E-stop sense is open AND the filtered power is present and
 *    resets otherwise; > drv.k1_weld_ms -> one fault per episode (latched in (k1, k1 + 2 ms] after
 *    the E-stop edge, SAF-FW-025, OBS-P1-02). With the check off, power present during an E-stop is
 *    only reported (STATUS DRV_PWR, EVENT DRIVER_POWER); no fault, no FAULT_CLEAR cause.
 *  - ALM: active at the first active sample (start-block is fail-safe), inactive after a stable
 *    io.release_ms (OBS-P1-10); one ALM_CHANGED per accepted change.
 * Implements: FW-SW-004, FW-SW-005, SAF-FW-024 (loss detection), SAF-FW-025, SAF-FW-026 (ALM state)
 */
#ifndef PURE_DRVMON_H
#define PURE_DRVMON_H

#include <stdbool.h>
#include <stdint.h>

#include "inputs.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DRV_PWR_FILTER_MS 20u

typedef struct {
    bool     sense;          /* drv.pwr_sense_enable (boot value) */
    bool     k1_check;       /* drv.k1_check_enable && sense (boot values) */
    bool     pwr;            /* filtered: driver power present */
    uint8_t  diff_ms;        /* consecutive samples at the other level */
    uint16_t k1_ms;          /* E-stop open with power present (ms) */
    bool     k1_tripped;     /* fault reported for this episode */
    btn_t    alm;            /* filtered ALM (pressed = active) */
} drvmon_t;

typedef struct {
    bool     pwr_changed;    /* EVENT DRIVER_POWER(pwr) */
    bool     pwr_lost;       /* present -> absent (SAF-FW-024 reaction) */
    bool     pwr_returned;   /* absent -> present (settle reference, FW-MOT-008) */
    bool     alm_changed;    /* EVENT ALM_CHANGED(alm) */
    bool     k1_fault;       /* latch K1_WELDED, value = k1_ms */
} drvmon_ev_t;

/** Boot: filters start at the current levels (no event). */
void        drvmon_init(drvmon_t *m, bool sense, bool k1_check, bool raw_pwr, bool raw_alm);
drvmon_ev_t drvmon_sample(drvmon_t *m, bool raw_pwr, bool estop_open, bool raw_alm, uint16_t k1_weld_ms,
                          uint8_t release_ms);
/** Driver power present for the motion logic (always true with sensing disabled). */
static inline bool drvmon_power(const drvmon_t *m) { return !m->sense || m->pwr; }
static inline bool drvmon_alm(const drvmon_t *m) { return m->alm.pressed; }
/** FAULT_CLEAR cause of K1_WELDED: E-stop open and power present (sensing and K1 check enabled). */
static inline bool drvmon_k1_cause(const drvmon_t *m, bool estop_open) { return m->k1_check && estop_open && m->pwr; }

#ifdef __cplusplus
}
#endif

#endif /* PURE_DRVMON_H */
