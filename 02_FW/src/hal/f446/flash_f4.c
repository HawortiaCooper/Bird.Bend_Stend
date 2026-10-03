/* NVM flash access, sectors 1 and 2 (FW_design §5.11): erase / word program with the busy loops in
 * RAM (.RamFunc), the vector table copied to RAM (VTOR) and BASEPRI 0x20 during each operation
 * (levels >= 2 masked; M2 adds RAM-resident level-0/1 safety handlers that stay live), data cache
 * reset afterwards (stale reads after an erase). Single-bank F446: code fetches from flash stall
 * while the operation runs; DMA from SRAM continues.
 * Origin: role of Thrust_Stand_HAW/02_FW/src/drv/flash_f1.cpp @37c8747 (rewritten for F4 sectors).
 * Implements: FW-NVM-001, FW-NVM-002 (word program, commit order by the caller), FW-NVM-003
 */
#include <string.h>

#include "f446.h"
#include "fw_config.h"
#include "hal_flash.h"
#include "irq_prio.h"

#define RAMFUNC __attribute__((section(".RamFunc"), noinline, long_call))
#define FLASH_ERR (FLASH_SR_PGSERR | FLASH_SR_PGPERR | FLASH_SR_PGAERR | FLASH_SR_WRPERR | FLASH_SR_OPERR)
#define VTOR_WORDS 128u                              /* F446: 16 + 97 vectors -> 113, 512 B aligned */

static uint32_t s_ram_vtor[VTOR_WORDS] __attribute__((aligned(512)));

RAMFUNC static bool ram_erase(uint32_t snb)
{
    FLASH->CR = FLASH_CR_SER | (snb << FLASH_CR_SNB_Pos) | FLASH_CR_PSIZE_1;   /* x32 */
    FLASH->CR |= FLASH_CR_STRT;
    while ((FLASH->SR & FLASH_SR_BSY) != 0u) {
    }
    FLASH->CR &= ~FLASH_CR_SER;
    return (FLASH->SR & FLASH_ERR) == 0u;
}

RAMFUNC static bool ram_program(volatile uint32_t *dst, const uint32_t *src, uint32_t words)
{
    uint32_t i;
    bool ok = true;
    FLASH->CR = FLASH_CR_PG | FLASH_CR_PSIZE_1;
    for (i = 0u; i < words && ok; i++) {
        dst[i] = src[i];
        __DSB();
        while ((FLASH->SR & FLASH_SR_BSY) != 0u) {
        }
        ok = (FLASH->SR & FLASH_ERR) == 0u;
    }
    FLASH->CR &= ~FLASH_CR_PG;
    return ok;
}

typedef struct { uint32_t vtor; uint32_t basepri; } op_ctx_t;

static op_ctx_t op_begin(void)
{
    op_ctx_t c;
    memcpy(s_ram_vtor, (const void *)SCB->VTOR, sizeof s_ram_vtor);
    c.vtor = SCB->VTOR;
    c.basepri = __get_BASEPRI();
    __set_BASEPRI_MAX(BASEPRI_MOTION);
    SCB->VTOR = (uint32_t)s_ram_vtor;
    __DSB();
    __ISB();
    FLASH->KEYR = 0x45670123u;
    FLASH->KEYR = 0xCDEF89ABu;
    FLASH->SR = FLASH_ERR | FLASH_SR_EOP;
    return c;
}

static void op_end(op_ctx_t c)
{
    FLASH->CR |= FLASH_CR_LOCK;
    FLASH->ACR &= ~FLASH_ACR_DCEN;                    /* drop stale data-cache lines */
    FLASH->ACR |= FLASH_ACR_DCRST;
    FLASH->ACR &= ~FLASH_ACR_DCRST;
    FLASH->ACR |= FLASH_ACR_DCEN;
    SCB->VTOR = c.vtor;
    __DSB();
    __ISB();
    __set_BASEPRI(c.basepri);
}

bool hal_flash_erase(uint32_t sector)
{
    op_ctx_t c;
    bool ok;
    if (sector != NVM_SECTOR_A && sector != NVM_SECTOR_B) {
        return false;                                 /* only the NVM sectors, never code */
    }
    c = op_begin();
    ok = ram_erase(sector);
    op_end(c);
    return ok;
}

bool hal_flash_program(uint32_t addr, const void *src, size_t n)
{
    op_ctx_t c;
    bool ok;
    uint32_t words[128];                              /* <= 512 B per call (one record slot) */
    if ((addr % 4u) != 0u || (n % 4u) != 0u || n > sizeof words || addr < NVM_ADDR_A ||
        addr + n > NVM_ADDR_B + NVM_SECTOR_BYTES) {
        return false;
    }
    memcpy(words, src, n);                            /* word-aligned copy in RAM */
    c = op_begin();
    ok = ram_program((volatile uint32_t *)addr, words, (uint32_t)(n / 4u));
    op_end(c);
    return ok;
}

const void *hal_flash_map(uint32_t addr) { return (const void *)addr; }
