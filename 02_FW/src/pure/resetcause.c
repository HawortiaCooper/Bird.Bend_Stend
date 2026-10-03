/* Reset cause mapping. Implements: SAF-FW-018, SAF-FW-019, FW-CMD-004 */
#include "resetcause.h"

#include "proto_gen.h"

uint8_t resetcause(uint32_t csr)
{
    if ((csr & RCSR_LPWRRSTF) != 0u) {
        return (uint8_t)RST_LOW_POWER;
    }
    if ((csr & RCSR_WWDGRSTF) != 0u) {
        return (uint8_t)RST_WWDG;
    }
    if ((csr & RCSR_IWDGRSTF) != 0u) {
        return (uint8_t)RST_IWDG;
    }
    if ((csr & RCSR_SFTRSTF) != 0u) {
        return (uint8_t)RST_SOFTWARE;
    }
    if ((csr & RCSR_PORRSTF) != 0u) {
        return (uint8_t)RST_POWER_ON;
    }
    if ((csr & RCSR_BORRSTF) != 0u) {
        return (uint8_t)RST_BROWN_OUT;
    }
    if ((csr & RCSR_PINRSTF) != 0u) {
        return (uint8_t)RST_PIN;
    }
    return (uint8_t)RST_UNKNOWN;
}
