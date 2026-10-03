/* Measured AFE conversion rate (FW_design §5.8, FW-AFE-004): median of the last 16 data-ready
 * periods -> rate in 0.1 SPS (STATUS afe_rate_dsps, 0 = not yet 16 periods), AFE_RATE_MISMATCH when
 * it deviates from the configured rate by more than afe.rate_tol_pct. M1: fed by the synthetic
 * source (same path as the HX711 in M2). Pure C11.
 * Implements: FW-AFE-004, FW-CMD-004 (afe_rate_dsps)
 */
#ifndef PURE_AFE_RATE_H
#define PURE_AFE_RATE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AFE_RATE_N 16u

typedef struct {
    uint32_t per[AFE_RATE_N];
    uint8_t  n;              /* periods collected (<= 16) */
    uint8_t  idx;
    uint32_t last_t;
    bool     have_last;
    bool     mismatch;
} afe_rate_t;

void     afe_rate_reset(afe_rate_t *r);          /* also after a rate reconfiguration */
void     afe_rate_push(afe_rate_t *r, uint32_t t_us);
/** Median period -> 0.1 SPS (rounded); 0 until 16 periods are known. */
uint16_t afe_rate_dsps(const afe_rate_t *r);
/** Re-evaluate the mismatch flag against nominal_sps +- tol_pct; true if it changed. */
bool     afe_rate_eval(afe_rate_t *r, uint32_t nominal_sps, uint8_t tol_pct);

#ifdef __cplusplus
}
#endif

#endif /* PURE_AFE_RATE_H */
