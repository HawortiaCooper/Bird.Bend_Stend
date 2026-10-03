/* RCC->CSR reset flags -> ICD reset cause RST_* (FW_design §5.1). Precedence: LPWR, WWDG, IWDG,
 * SFT, POR, BOR, PIN, else UNKNOWN (PINRSTF is set by every internal reset, BORRSTF by every POR).
 * Bit positions of RM0390 §6.3.21 are given here as plain constants (no CMSIS in pure/).
 * Implements: SAF-FW-018 (reset cause reported), SAF-FW-019 (IWDG reset recognised), FW-CMD-004
 */
#ifndef PURE_RESETCAUSE_H
#define PURE_RESETCAUSE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RCSR_BORRSTF   (1UL << 25)
#define RCSR_PINRSTF   (1UL << 26)
#define RCSR_PORRSTF   (1UL << 27)
#define RCSR_SFTRSTF   (1UL << 28)
#define RCSR_IWDGRSTF  (1UL << 29)
#define RCSR_WWDGRSTF  (1UL << 30)
#define RCSR_LPWRRSTF  (1UL << 31)

uint8_t resetcause(uint32_t csr);

#ifdef __cplusplus
}
#endif

#endif /* PURE_RESETCAUSE_H */
