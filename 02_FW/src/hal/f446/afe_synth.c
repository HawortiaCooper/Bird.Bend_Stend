/* Synthetic AFE source - M1, and since M2 only the bring-up image nucleo_f446re_synth
 * (FW_AFE_SYNTHETIC = 1, FEAT_AFE_SYNTHETIC; FW_design §9.7): TIM5 CC2 at the
 * configured conversion period (80 / 10 SPS) records the "data-ready" time and pends the EXTI4
 * vector (level 3, the HX711 DOUT vector of M2); the EXTI4 handler delivers a deterministic sample
 * (sawtooth +- 400 000 counts over 10 s + pseudo-noise, never at a rail, scaled by the gain) through
 * the same on_afe_sample() path, so M1 exercises the real DATA path end to end. Implements the
 * hal_hx711 seam for M1.
 * Implements: FW-STR-002 (M1), FW-TIM-001 (t_us = data-ready compare time), FW-AFE-002 (rate /
 *             gain applied), FW-NVM-003 (hold -> conversions missed)
 */
#if defined(FW_AFE_SYNTHETIC) && FW_AFE_SYNTHETIC
#include "f446.h"
#include "board_pins.h"
#include "hal_hx711.h"
#include "hal_step.h"
#include "irq_prio.h"

static volatile uint32_t s_period_us = 12500u;
static volatile uint8_t  s_gain_pulses = 25u;
static volatile bool     s_hold;
static volatile bool     s_powerdown;
static volatile uint32_t s_t_ready;
static uint32_t s_n;
static uint32_t s_lcg = 0x1234567u;

void hal_hx711_config(uint8_t gain_pulses, bool rate80)
{
    s_gain_pulses = gain_pulses;
    s_period_us = rate80 ? 12500u : 100000u;
}

void hal_hx711_powerdown(bool on) { s_powerdown = on; }
void hal_hx711_kick(void) {}
void hal_hx711_hold(bool on) { s_hold = on; }

void afe_synth_start(void)
{
    TIM5->CCR2 = TIM5->CNT + s_period_us;
    TIM5->SR = ~TIM_SR_CC2IF;
    TIM5->DIER |= TIM_DIER_CC2IE;
    NVIC_SetPriority(EXTI4_IRQn, PRIO_AFE);
    NVIC_EnableIRQ(EXTI4_IRQn);
}

void afe_synth_on_cc2(uint32_t t_us)
{
    uint32_t next = t_us + s_period_us;
    if ((int32_t)(TIM5->CNT - next) >= 0) {
        next = TIM5->CNT + s_period_us;                  /* fell behind: re-base, no burst */
    }
    TIM5->CCR2 = next;
    if (!s_hold && !s_powerdown) {
        s_t_ready = t_us;
        NVIC_SetPendingIRQ(EXTI4_IRQn);
    }
}

static int32_t synth_raw(void)
{
    int32_t saw = (int32_t)(s_n % 800u) * 1000 - 400000;
    int32_t noise;
    s_lcg = s_lcg * 1664525u + 1013904223u;
    noise = (int32_t)((s_lcg >> 24) & 0x7Fu) - 64;
    s_n++;
    switch (s_gain_pulses) {
    case 27u: return saw / 2 + noise;                    /* A64 */
    case 26u: return saw / 4 + noise;                    /* B32 */
    default:  return saw + noise;                        /* A128 */
    }
}

void EXTI4_IRQHandler(void)
{
    afe_sample_t s;
#if defined(FW_DEBUG_PINS) && FW_DEBUG_PINS
    gpio_write(GPIOC, PIN_DBG1_BIT, true);
#endif
    s.t_us = s_t_ready;
    s.raw = synth_raw();
    s.pos_steps = hal_step_count();
    s.status = 0u;
    on_afe_sample(&s);
#if defined(FW_DEBUG_PINS) && FW_DEBUG_PINS
    gpio_write(GPIOC, PIN_DBG1_BIT, false);
#endif
}
#endif /* FW_AFE_SYNTHETIC */
