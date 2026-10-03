/* Seam v1 - flash (tools/README "Seam v1", FW_design §5.11, §8.1).
 * Target: hal/f446/flash_f4.c (sectors 1/2, RAM-resident busy loop). Twin: flash.bin.
 * Implements: FW-NVM-001...003, SYS-008
 */
#ifndef HAL_FLASH_H
#define HAL_FLASH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

bool hal_flash_erase(uint32_t sector);
bool hal_flash_program(uint32_t addr, const void *src, size_t n);
const void *hal_flash_map(uint32_t addr);

#ifdef __cplusplus
}
#endif

#endif /* HAL_FLASH_H */
