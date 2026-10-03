/* Device time base and control tick (FW_design §5.1, pinout §2): TIM5 32-bit free-running at
 * 1 MHz (hal_time_us, FW-TIM-001), CC1 = 1 kHz control tick -> core_tick_1ms() (level 4), CC2 =
 * synthetic AFE schedule (M1). hal_time_ms() accumulates elapsed µs in the tick, so it catches up
 * after a masked flash operation; a compare that fell behind the counter is re-based.
 * Implements: FW-TIM-001, FW-PLT-002 (timer clock derived from PCLK1)
 */
#include "f446.h"
#include "hal_time.h"
#include "irq_prio.h"
#include "stm32_def.h"

#define TICK_US 1000u

static volatile uint32_t s_ms;
static uint32_t s_last_us;
static uint32_t s_rem_us;

uint32_t time_timer_hz(void)
{
    uint32_t pclk1 = HAL_RCC_GetPCLK1Freq();
    return ((RCC->CFGR & RCC_CFGR_PPRE1) == RCC_CFGR_PPRE1_DIV1) ? pclk1 : 2u * pclk1;
}

void time_init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_TIM5EN;
    (void)RCC->APB1ENR;
    TIM5->CR1 = 0u;
    TIM5->PSC = time_timer_hz() / 1000000u - 1u;            /* 90 MHz -> 1 MHz */
    TIM5->ARR = 0xFFFFFFFFu;
    TIM5->CCMR1 = 0u;                                       /* CC1/CC2 frozen output compare, no pin */
    TIM5->CCER = 0u;
    TIM5->EGR = TIM_EGR_UG;                                 /* load PSC */
    TIM5->SR = 0u;
    TIM5->DIER = 0u;
    TIM5->CR1 = TIM_CR1_CEN;
    NVIC_SetPriority(TIM5_IRQn, PRIO_TICK);
}

void time_start_tick(void)
{
    s_last_us = TIM5->CNT;
    TIM5->CCR1 = s_last_us + TICK_US;
    TIM5->SR = ~TIM_SR_CC1IF;
    TIM5->DIER |= TIM_DIER_CC1IE;
    NVIC_EnableIRQ(TIM5_IRQn);
}

uint32_t hal_time_us(void) { return TIM5->CNT; }
uint32_t hal_time_ms(void) { return s_ms; }

void TIM5_IRQHandler(void)
{
    uint32_t sr = TIM5->SR;
#if defined(FW_AFE_SYNTHETIC) && FW_AFE_SYNTHETIC
    if ((sr & TIM_SR_CC2IF) != 0u && (TIM5->DIER & TIM_DIER_CC2IE) != 0u) {
        TIM5->SR = ~TIM_SR_CC2IF;
        afe_synth_on_cc2(TIM5->CCR2);                       /* pends EXTI4 (level 3) */
    }
#endif
    if ((sr & TIM_SR_CC1IF) != 0u) {
        uint32_t now, ccr;
        TIM5->SR = ~TIM_SR_CC1IF;
        now = TIM5->CNT;
        ccr = TIM5->CCR1 + TICK_US;
        if ((int32_t)(now - ccr) >= 0) {                    /* fell behind (masked flash op) */
            ccr = now + TICK_US;
        }
        TIM5->CCR1 = ccr;
        s_rem_us += now - s_last_us;
        s_last_us = now;
        s_ms += s_rem_us / 1000u;
        s_rem_us %= 1000u;
        core_tick_1ms();
    }
}
