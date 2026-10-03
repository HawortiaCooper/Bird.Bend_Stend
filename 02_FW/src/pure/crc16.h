/* Origin: Thrust_Stand_HAW/02_FW/src/pure/crc16.h @37c8747 (copied, tags adapted).
 */
/* CRC-16/CCITT-FALSE (ICD §2.1). Implements: IF-004 */
#ifndef PURE_CRC16_H
#define PURE_CRC16_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CRC16_INIT 0xFFFFu

/** Continue a CRC-16/CCITT-FALSE over n bytes (start with CRC16_INIT). */
uint16_t crc16_ccitt(const uint8_t *d, uint32_t n, uint16_t crc);

#ifdef __cplusplus
}
#endif

#endif
