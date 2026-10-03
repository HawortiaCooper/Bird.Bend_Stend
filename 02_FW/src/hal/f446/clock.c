/* SystemClock_Config strong override (FW_design §3.1, pinout §3): 180 MHz from the 8 MHz ST-LINK
 * MCO (HSE bypass, PLL M4/N180/P2), over-drive, 5 WS, AHB/APB1/APB2 = 180/45/90 MHz; HSE start
 * bounded to 5 ms and PLL / over-drive / switch bounded to 2 ms each by DWT; on any timeout the same
 * PLL from HSI 16 MHz (M8) gives identical bus clocks and sets CLK_FALLBACK. CSS on in the HSE case
 * (HSE loss -> NMI, sys_f4.c). Called by the core from HAL_Init() context before setup().
 * Origin: structure of Thrust_Stand_HAW/02_FW/src/sys/clock.cpp @37c8747 (rewritten for the F4).
 * Implements: FW-PLT-002
 */
#include "f446.h"
#include "stm32_def.h"

#define HSE_TIMEOUT_MS   5u
#define STEP_TIMEOUT_MS  2u
#define HSI_CYCLES_PER_MS 16000u           /* reset clock HSI 16 MHz */

static bool s_hsi_fallback;

bool clock_hsi_fallback(void) { return s_hsi_fallback; }

static bool wait_bits(volatile uint32_t *reg, uint32_t mask, uint32_t val, uint32_t ms)
{
    uint32_t t0 = dwt_cycles();
    while ((*reg & mask) != val) {
        if ((uint32_t)(dwt_cycles() - t0) > ms * HSI_CYCLES_PER_MS) {
            return false;
        }
    }
    return true;
}

static void back_to_hsi(void)
{
    RCC->CFGR &= ~RCC_CFGR_SW;                              /* SYSCLK = HSI */
    (void)wait_bits(&RCC->CFGR, RCC_CFGR_SWS, RCC_CFGR_SWS_HSI, STEP_TIMEOUT_MS);
    RCC->CR &= ~RCC_CR_PLLON;
    (void)wait_bits(&RCC->CR, RCC_CR_PLLRDY, 0u, STEP_TIMEOUT_MS);
}

/* PLL to 180 MHz from the selected source, over-drive, flash wait states, bus dividers, switch */
static bool start_pll(bool hse)
{
    uint32_t m = hse ? 4u : 8u;                             /* -> 2 MHz VCO input */
    RCC->PLLCFGR = (m << RCC_PLLCFGR_PLLM_Pos) | (180u << RCC_PLLCFGR_PLLN_Pos) |
                   (0u << RCC_PLLCFGR_PLLP_Pos) |          /* P = 2 -> 180 MHz */
                   (8u << RCC_PLLCFGR_PLLQ_Pos) | (2u << RCC_PLLCFGR_PLLR_Pos) |
                   (hse ? RCC_PLLCFGR_PLLSRC_HSE : RCC_PLLCFGR_PLLSRC_HSI);
    RCC->CR |= RCC_CR_PLLON;
    if (!wait_bits(&RCC->CR, RCC_CR_PLLRDY, RCC_CR_PLLRDY, STEP_TIMEOUT_MS)) {
        back_to_hsi();
        return false;
    }
    PWR->CR |= PWR_CR_ODEN;                                 /* over-drive for 180 MHz */
    if (!wait_bits(&PWR->CSR, PWR_CSR_ODRDY, PWR_CSR_ODRDY, STEP_TIMEOUT_MS)) {
        back_to_hsi();
        return false;
    }
    PWR->CR |= PWR_CR_ODSWEN;
    if (!wait_bits(&PWR->CSR, PWR_CSR_ODSWRDY, PWR_CSR_ODSWRDY, STEP_TIMEOUT_MS)) {
        back_to_hsi();
        return false;
    }
    FLASH->ACR = FLASH_ACR_LATENCY_5WS | FLASH_ACR_PRFTEN | FLASH_ACR_ICEN | FLASH_ACR_DCEN;
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_HPRE | RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2 | RCC_CFGR_SW)) |
                RCC_CFGR_HPRE_DIV1 | RCC_CFGR_PPRE1_DIV4 | RCC_CFGR_PPRE2_DIV2 | RCC_CFGR_SW_PLL;
    if (!wait_bits(&RCC->CFGR, RCC_CFGR_SWS, RCC_CFGR_SWS_PLL, STEP_TIMEOUT_MS)) {
        back_to_hsi();
        return false;
    }
    return true;
}

void SystemClock_Config(void)
{
    bool hse;
    dwt_enable();
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    PWR->CR |= PWR_CR_VOS;                                  /* voltage scale 1 */
    FLASH->ACR = FLASH_ACR_LATENCY_5WS;                     /* before any clock increase */

    RCC->CR &= ~RCC_CR_HSEON;
    RCC->CR |= RCC_CR_HSEBYP;                               /* 8 MHz MCO from the ST-LINK on PH0 */
    RCC->CR |= RCC_CR_HSEON;
    hse = wait_bits(&RCC->CR, RCC_CR_HSERDY, RCC_CR_HSERDY, HSE_TIMEOUT_MS);
    if (hse && start_pll(true)) {
        RCC->CR |= RCC_CR_CSSON;                            /* HSE loss -> NMI */
    } else {
        RCC->CR &= ~(RCC_CR_HSEON | RCC_CR_HSEBYP);
        s_hsi_fallback = true;                              /* CLK_FALLBACK (FW-PLT-002) */
        (void)start_pll(false);                             /* identical bus clocks from HSI */
    }
    SystemCoreClockUpdate();
    (void)HAL_InitTick(TICK_INT_PRIORITY);
}
