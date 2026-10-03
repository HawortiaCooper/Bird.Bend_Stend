/* Origin: Thrust_Stand_HAW/02_FW/src/pure/crc32.h @37c8747 (copied, tags adapted).
 */
/* CRC-32/ISO-HDLC (NVM records). Implements: FW-NVM-002 */
#ifndef PURE_CRC32_H
#define PURE_CRC32_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CRC32_INIT 0xFFFFFFFFu

/** Raw (non-finalised) update; final CRC = state ^ 0xFFFFFFFF. */
uint32_t crc32_update(uint32_t state, const uint8_t *d, uint32_t n);
/** One-shot CRC-32/ISO-HDLC. */
uint32_t crc32_iso(const uint8_t *d, uint32_t n);

#ifdef __cplusplus
}
#endif

#endif
