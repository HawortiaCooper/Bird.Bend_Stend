/* HX711 shim on the F446 (FW_design §5.8; pinout §1.1): DOUT PB4 falling edge -> EXTI4 (own vector,
 * level 3); the ISR first latches the data-ready time (TIM5) and the step count (FW-AFE-005), then
 * bit-bangs 25 / 26 / 27 SCK pulses on PB10 with pure/hx711_seq.h, interrupts masked only while SCK
 * is high (BASEPRI 0x20 = CRIT_AFE: NVIC levels 0-1 - E-stop, limits, PAUSE - never masked, the
 * step ISR waits <= ~0.6 us), and delivers the sample to on_afe_sample() in the same ISR.
 * RATE PB5 (10 / 80 SPS) via hal_outputs. afe_sample_t.status (seam v1.2): SCK_OVERRUN = an SCK-high
 * phase longer than HX711_SCK_HIGH_MAX_US or DOUT not high after the last pulse (power-down
 * suspected), MISSED_EDGE = this read was started by the missed-edge kick.
 * Origin: Thrust_Stand_HAW/02_FW/src/sens/hx711.cpp @37c8747 (D-39; ported: one AFE, EXTI4 own vector
 * instead of the shared EXTI9_5, DWT-measured SCK-high, samples handed to the core in the ISR instead
 * of the TS ring + main-loop channel logic, kick flag, hold).
 * Not built in the bring-up image FW_AFE_SYNTHETIC (afe_synth.c provides the seam there).
 * Implements: FW-AFE-001, FW-AFE-002, FW-AFE-003 (overrun symptom), FW-AFE-005, FW-TIM-001, FW-NVM-003
 */
#if !(defined(FW_AFE_SYNTHETIC) && FW_AFE_SYNTHETIC)
#include "f446.h"
#include "board_pins.h"
#include "hal_hx711.h"
#include "hal_outputs.h"
#include "hal_step.h"
#include "hx711_math.h"
#include "irq_prio.h"
#include "proto_gen.h"

#define L_DOUT (1u << PIN_DOUT_BIT)
#define HX711_SCK_HIGH_MAX_US 50u

static volatile uint8_t s_pulses = 25u;
static volatile bool    s_hold;
static volatile bool    s_pd;
static volatile bool    s_kicked;
static uint32_t s_spin;                 /* cycles of 0.5 us */
static uint32_t s_bp, s_t_hi, s_max_hi;

static inline void spin(uint32_t cyc)
{
    uint32_t t0 = dwt_cycles();
    while ((uint32_t)(dwt_cycles() - t0) < cyc) {
    }
}

#define HX_SCK_HI()   do { PIN_SCK_PORT->BSRR = 1u << PIN_SCK_BIT; s_t_hi = dwt_cycles(); } while (0)
#define HX_SCK_LO()   do { PIN_SCK_PORT->BSRR = 1u << (PIN_SCK_BIT + 16u); \
                           if ((uint32_t)(dwt_cycles() - s_t_hi) > s_max_hi) { s_max_hi = dwt_cycles() - s_t_hi; } } while (0)
#define HX_DT()       ((PIN_DOUT_PORT->IDR & L_DOUT) != 0u)
#define HX_MASK_ON()  do { s_bp = __get_BASEPRI(); __set_BASEPRI_MAX(BASEPRI_MOTION); } while (0)
#define HX_MASK_OFF() __set_BASEPRI(s_bp)
#define HX_T_HIGH()   spin(s_spin)
#define HX_T_LOW()    spin(s_spin)
#include "hx711_seq.h"

static bool dout_low(void) { return (PIN_DOUT_PORT->IDR & L_DOUT) == 0u; }

void hal_hx711_config(uint8_t gain_pulses, bool rate80)
{
    s_pulses = gain_pulses;                          /* selects the gain of the next conversion */
    hal_rate_pin(rate80);
}

void hal_hx711_powerdown(bool on)
{
    s_pd = on;
    if (on) {
        EXTI->IMR &= ~L_DOUT;
        PIN_SCK_PORT->BSRR = 1u << PIN_SCK_BIT;      /* SCK high > 60 us: power-down */
    } else {
        PIN_SCK_PORT->BSRR = 1u << (PIN_SCK_BIT + 16u);   /* SCK low: power-up (reset, A/128) */
        if (!s_hold) {
            EXTI->PR = L_DOUT;
            EXTI->IMR |= L_DOUT;
        }
    }
}

void hal_hx711_kick(void)
{
    if (!s_hold && !s_pd && dout_low()) {
        s_kicked = true;                             /* missed-edge recovery */
        EXTI->SWIER = L_DOUT;
    }
}

void hal_hx711_hold(bool on)
{
    s_hold = on;
    if (on) {
        EXTI->IMR &= ~L_DOUT;                        /* NVM: no read across the flash stall */
    } else if (!s_pd) {
        EXTI->PR = L_DOUT;
        EXTI->IMR |= L_DOUT;
        if (dout_low()) {
            s_kicked = true;                         /* conversions were missed during the hold */
            EXTI->SWIER = L_DOUT;
        }
    }
}

/* board_init(): pins (SCK low first, DOUT input), EXTI4 routing */
void hx711_init(void)
{
    s_spin = SystemCoreClock / 2000000u;
    gpio_write(PIN_SCK_PORT, PIN_SCK_BIT, false);
    gpio_mode(PIN_SCK_PORT, PIN_SCK_BIT, GPIO_MODE_OUT_, GPIO_PUPD_NONE_, GPIO_SPEED_HIGH_);
    gpio_mode(PIN_DOUT_PORT, PIN_DOUT_BIT, GPIO_MODE_IN_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);  /* NJTRST off */
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;
    (void)RCC->APB2ENR;
    SYSCFG->EXTICR[1] = (SYSCFG->EXTICR[1] & ~0xFu) | 1u;   /* line 4 -> PB4 */
    EXTI->FTSR |= L_DOUT;
    EXTI->RTSR &= ~L_DOUT;
    EXTI->IMR &= ~L_DOUT;
    NVIC_SetPriority(EXTI4_IRQn, PRIO_AFE);
}

/* board_start() */
void hx711_start(void)
{
    EXTI->PR = L_DOUT;
    NVIC_ClearPendingIRQ(EXTI4_IRQn);
    NVIC_EnableIRQ(EXTI4_IRQn);
    if (!s_hold && !s_pd) {
        EXTI->IMR |= L_DOUT;
        if (dout_low()) {
            EXTI->SWIER = L_DOUT;                    /* a conversion already waiting */
        }
    }
}

void EXTI4_IRQHandler(void)
{
    afe_sample_t s;
    uint32_t raw24;
    bool high = false;
    s.t_us = TIM5->CNT;                              /* FIRST: data-ready time (FW-TIM-001) */
    s.pos_steps = hal_step_count();                  /* and the commanded position (FW-AFE-005) */
    EXTI->PR = L_DOUT;
    if (s_hold || s_pd || !dout_low()) {
        return;                                      /* spurious / held */
    }
#if defined(FW_DEBUG_PINS) && FW_DEBUG_PINS
    gpio_write(GPIOC, PIN_DBG1_BIT, true);
#endif
    EXTI->IMR &= ~L_DOUT;                            /* DOUT toggles with the data during the read */
    s_max_hi = 0u;
    raw24 = hx711_shift_in(s_pulses, &high);
    EXTI->PR = L_DOUT;
    EXTI->IMR |= L_DOUT;
    s.raw = hx711_sign_extend(raw24);
    s.status = 0u;
    if (!high || s_max_hi > HX711_SCK_HIGH_MAX_US * (SystemCoreClock / 1000000u)) {
        s.status |= (uint8_t)AFES_SCK_OVERRUN;
    }
    if (s_kicked) {
        s_kicked = false;
        s.status |= (uint8_t)AFES_MISSED_EDGE;
    }
    on_afe_sample(&s);
#if defined(FW_DEBUG_PINS) && FW_DEBUG_PINS
    gpio_write(GPIOC, PIN_DBG1_BIT, false);
#endif
}
#endif
