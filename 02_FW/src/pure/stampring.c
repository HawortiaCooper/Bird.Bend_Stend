/* Newest entry of a device-time stamp ring. Implements: FW-TIM-001 (measurement support),
 * REQ-A-M2-08 */
#include "stampring.h"

uint32_t stampring_newest(const volatile uint32_t *r, uint32_t n)
{
    uint32_t i, best = 0u, best_d = 0u;
    if (n == 0u) {
        return 0u;
    }
    if (r[n - 1u] == 0u) {                      /* not filled yet: entries 0..w-1 written, rest cleared */
        for (i = 0u; i < n && r[i] != 0u; i++) {
        }
        return (i == 0u) ? 0u : r[i - 1u];
    }
    for (i = 0u; i < n; i++) {                  /* filled: the single descent newest -> oldest */
        uint32_t d = r[(i + 1u) % n] - r[i];    /* forward difference, modulo 2^32 */
        if (d > best_d) {
            best_d = d;
            best = i;
        }
    }
    return r[best];
}
