/* DATA validity flag (FW_design §5.14, ICD §5.3): SET_VALID publishes {old, new, t_apply}; a frame
 * with time t carries `new` iff (int32)(t - t_apply) >= 0, else `old`. The automatic clear of an
 * operational stop uses the same mechanism with t_apply = the stop's timestamp. VALID = 0 at boot,
 * never persisted. valid_settle() (1 kHz tick) folds a boundary older than 1 s into `old` so the
 * 32-bit modular comparison never ages past 2^31 µs (35.8 min).
 * Pure C11; the core publishes under CRIT_DATA.
 * Implements: FW-CMD-002, SAF-FW-001 (VALID part)
 */
#ifndef PURE_VALID_H
#define PURE_VALID_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool     old_v;
    bool     new_v;
    uint32_t t_apply;
} valid_t;

#define VALID_SETTLE_US 1000000u

void valid_init(valid_t *v, uint32_t now_us);
/** Value of a frame time-stamped t_us. */
bool valid_at(const valid_t *v, uint32_t t_us);
/** SET_VALID(val) processed at now_us; returns t_apply (= now_us, the response body). */
uint32_t valid_set(valid_t *v, bool val, uint32_t now_us);
/** Automatic clear by an operational stop at t_stop; true if VALID was 1 (-> EVENT VALID_CLEARED). */
bool valid_clear(valid_t *v, uint32_t t_stop);
/** Fold an old boundary (> VALID_SETTLE_US ago) into `old`. */
void valid_settle(valid_t *v, uint32_t now_us);

#ifdef __cplusplus
}
#endif

#endif /* PURE_VALID_H */
