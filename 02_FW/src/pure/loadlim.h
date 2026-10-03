/* FW hard load limit (FW_design §5.8; D-12): every HX711 sample is compared with
 * safety.load_raw_min/max; safety.load_trip_samples consecutive violations trip; a rail sample
 * (0x7FFFFF / 0x800000) always counts as a violation (SAF-FW-009); after a FAULT_CLEAR with the load
 * still beyond the threshold ("regrow" mode) the check trips immediately only when the violation grows
 * by more than safety.load_regrow_raw beyond the value at clear (SAF-FW-011); the mode ends with the
 * first sample inside the thresholds. The check cannot be disabled (no parameter, no build flag).
 * Called from the sample ISR (level 3); thresholds are copied in under CRIT_DATA and act from the
 * next sample (SAF-FW-010). Pure C11.
 * Implements: SAF-FW-008, SAF-FW-009, SAF-FW-010 (next-sample effect), SAF-FW-011
 */
#ifndef PURE_LOADLIM_H
#define PURE_LOADLIM_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int32_t  min, max;           /* safety.load_raw_min / max */
    uint8_t  trip_samples;       /* >= 1 */
    int32_t  regrow;             /* safety.load_regrow_raw */
    uint8_t  count;              /* consecutive violating samples */
    bool     regrow_on;
    int32_t  ref;                /* raw value at FAULT_CLEAR */
} loadlim_t;

void loadlim_init(loadlim_t *l, int32_t min, int32_t max, uint8_t trip_samples, int32_t regrow);
/** New thresholds (keeps the counters and the regrow state). */
void loadlim_config(loadlim_t *l, int32_t min, int32_t max, uint8_t trip_samples, int32_t regrow);
/** One sample: true = trip (immediate stop + LOAD_LIMIT). `sat` = the sample is a rail code. */
bool loadlim_check(loadlim_t *l, int32_t raw, bool sat);
/** Accepted FAULT_CLEAR of LOAD_LIMIT with the last sample raw (sat = at a rail). */
void loadlim_on_clear(loadlim_t *l, int32_t raw, bool sat);

#ifdef __cplusplus
}
#endif

#endif /* PURE_LOADLIM_H */
