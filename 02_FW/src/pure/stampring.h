/* Newest entry of a device-time stamp ring (measurement images, DEF-M2-02 / OBS-M2-08). The ring is
 * written circularly from index 0 by one boot only (cleared at boot, so unwritten entries are 0) with
 * 32-bit microsecond stamps that wrap modulo 2^32.
 *  - Not yet filled (last entry still 0): the newest entry is the one before the first 0 - exact for
 *    any stamp values (a genuine stamp of exactly 0 would need a TIM5 wrap at that microsecond).
 *  - Filled (laps): in time order consecutive entries differ by the (small) gap between two events,
 *    the single descent newest -> oldest by 2^32 - span; the newest entry is the one with the LARGEST
 *    unsigned forward difference to its successor (wrap pair last -> first included). Valid while the
 *    ring span plus the largest gap stays below 2^32 us (71.6 min).
 * The v0.6 signed compare needed a span < 2^31 us (35.8 min) and, with a partly filled ring, returned 0
 * once the newest stamp was >= 2^31 us, i.e. after 35.8 min of uptime (OBS-M2-08). Pure C11.
 * Implements: FW-TIM-001 (measurement support), REQ-A-M2-08 (NOINIT previous-boot record)
 */
#ifndef PURE_STAMPRING_H
#define PURE_STAMPRING_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Newest stamp of the ring r[0..n-1]; 0 for an empty (all-zero) ring or n = 0. */
uint32_t stampring_newest(const volatile uint32_t *r, uint32_t n);

#ifdef __cplusplus
}
#endif

#endif /* PURE_STAMPRING_H */
