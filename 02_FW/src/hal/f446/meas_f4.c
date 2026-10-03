/* On-chip measurement resources of the measurement images nucleo_f446re_meas / _meas_dwt (CR-02,
 * D-35 G5, D-40 c; FW_test_plan v0.3 §6.1-§6.3, REQ-A-M2-03) behind the seam v1.3 forwarder
 * hal_meas_cmd() (ICD v0.6 §5.6 + Appendix C word layouts). Compiled to nothing in every other image
 * (the whole file is under HW_MEAS): the release image links the weak defaults (hal_meas_cmd() -> 0 in
 * sys_f4.c, meas_start() empty in board_init.c), so the safety handlers, the core and the pure objects
 * are byte-identical between the release and the measurement images (TC-SYS-009-02). The only shared
 * runtime hook is the RAM flag g_meas_static (step_tim2.c) that releases a STATIC_LEVEL at
 * hal_step_start() / hal_ena_set(); it is always 0 in the release image.
 * Measurement header pins (pinout §1.5, wiring MH; 1 kOhm series at the MCU end of every jumper):
 *   MT-2 independent PUL counter : TIM4 CH2 PB7 (AF2), external clock mode 1 on TI2FP2, 16 bit extended
 *                                  in software by the TIM4 update IRQ (lowest priority) -> 32 bit
 *   MT-3 event latency probe     : TIM8 (APB2 timer clock 180 MHz, 16 bit) CH1 PC6 = EVT (TI1FP1 slave
 *                                  trigger / reset), CH2 PC7 = PUL rising, CH3 PC8 = ENA, CH4 PC9 = DIR
 *                                  (both edges); PWM-input mode on PUL (TI2: CH2 period, CH1 width, min/max
 *                                  in the TIM8 CC IRQ at the lowest priority)
 *   MT-4 device-time stamps      : DMA2 copies TIM5->CNT (the FW's t_us) into .noinit rings on every TIM8
 *                                  CH1 / CH2 / CH4 and TIM1 CH4 (PA11, J-AUX) capture; ring laps counted in
 *                                  the DMA TC IRQs (lowest priority); TIM1 update at 10 kHz copies TIM5->CNT
 *                                  into the .noinit heartbeat; rings and heartbeat survive an IWDG reset.
 *                                  DMA2 map ASSUMED per RM0390 Table 29 (verify at HG-29): S2 ch7 TIM8_CH1,
 *                                  S3 ch7 TIM8_CH2, S7 ch7 TIM8_CH4, S4 ch6 TIM1_CH4, S5 ch6 TIM1_UP;
 *                                  DMA2 reading the APB1 register TIM5->CNT is ASSUMED (HG-29 self-test)
 *   MT-7 stimulus                : TIM10 CH1 PB8 (AF3, 10 MHz) one pulse per run after a seeded delay of
 *                                  0 ... 1 running step period, hold sel>>1 ms, polarity sel bit 0
 *   HANG (test image)            : MAIN = busy loop in the dispatcher (no kick), TICK = TIM13 IRQ at the
 *                                  tick level spinning, ISR1 = EXTI2 software-retriggered at level 1;
 *                                  for `a` ms (0 = until the IWDG resets)
 *   STATIC_LEVEL                 : PUL forced active / inactive (TIM2 OC1, timer stopped) or DIR level
 * The request was validated by the core (cmd_check, ranges and MEAS_STATE) before this call.
 * DWT section statistics (op 9) are not implemented in this increment: w0 = 0 and the INFO variant
 * carries only MEAS also in the _meas_dwt image (open item OI-FW-37).
 * Implements: SYS-009 (on-chip measurement means), D-40 c, REQ-A-M2-03
 */
#if defined(HW_MEAS) && HW_MEAS
#include <string.h>

#include "f446.h"
#include "board_pins.h"
#include "hal_sys.h"
#include "irq_prio.h"
#include "le.h"
#include "proto_gen.h"

#define PRIO_MEAS 15u                         /* below the link (5): never delays the FW */
#define RING      2048u                       /* stamps per channel (INFO w4) */
#define STIM_HZ   10000000u

typedef struct {
    uint32_t magic;
    uint32_t heartbeat_t_us;                  /* TIM5->CNT, refreshed at 10 kHz by DMA */
    uint32_t hang_t_us;
    uint32_t ring[4][RING];                   /* MEAS_CHAN_EVT / PUL / DIR / AUX */
} meas_noinit_t;

__attribute__((section(".noinit.meas"))) static meas_noinit_t s_ni;

extern volatile uint8_t g_meas_static;        /* step_tim2.c */

static DMA_Stream_TypeDef *const s_dma[4] = {DMA2_Stream2, DMA2_Stream3, DMA2_Stream7, DMA2_Stream4};
static volatile uint32_t s_laps[4];
static volatile uint32_t s_cnt_hi;
static volatile bool     s_armed, s_pwm;
static volatile uint32_t s_pwm_min_p, s_pwm_max_p, s_pwm_min_h, s_pwm_max_h, s_pwm_n;
static uint32_t s_pul_at_arm;
static volatile uint32_t s_stim_left, s_stim_seed, s_stim_hold, s_stim_pol;
static volatile uint32_t s_hang_until;
static volatile bool     s_hang_tick, s_hang_storm;

static void af(GPIO_TypeDef *g, uint32_t pin, uint32_t a)
{
    gpio_af(g, pin, a);
    gpio_mode(g, pin, GPIO_MODE_AF_, GPIO_PUPD_NONE_, GPIO_SPEED_LOW_);
}

static void dma_ring(DMA_Stream_TypeDef *s, uint32_t ch, volatile const uint32_t *src, uint32_t *dst, uint32_t n,
                     bool tcie)
{
    s->CR = 0u;
    while ((s->CR & DMA_SxCR_EN) != 0u) {
    }
    s->PAR = (uint32_t)src;
    s->M0AR = (uint32_t)dst;
    s->NDTR = n;
    s->FCR = 0u;                                                        /* direct mode */
    s->CR = (ch << DMA_SxCR_CHSEL_Pos) | DMA_SxCR_MSIZE_1 | DMA_SxCR_PSIZE_1 |
            ((n > 1u) ? DMA_SxCR_MINC : 0u) | DMA_SxCR_CIRC | DMA_SxCR_PL_0 | (tcie ? DMA_SxCR_TCIE : 0u);
    s->CR |= DMA_SxCR_EN;
}

static uint32_t stamps_total(uint8_t ch)
{
    uint32_t laps, idx;
    do {
        laps = s_laps[ch];
        idx = RING - s_dma[ch]->NDTR;
    } while (laps != s_laps[ch]);
    return laps * RING + idx;
}

/* TC interrupts of the four stamp streams: count ring laps */
static void lap(uint8_t ch, volatile uint32_t *ifcr, uint32_t tcif, volatile uint32_t *isr)
{
    if ((*isr & tcif) != 0u) {
        *ifcr = tcif;
        s_laps[ch]++;
    }
}
void DMA2_Stream2_IRQHandler(void) { lap(0u, &DMA2->LIFCR, DMA_LISR_TCIF2, &DMA2->LISR); }
void DMA2_Stream3_IRQHandler(void) { lap(1u, &DMA2->LIFCR, DMA_LISR_TCIF3, &DMA2->LISR); }
void DMA2_Stream7_IRQHandler(void) { lap(2u, &DMA2->HIFCR, DMA_HISR_TCIF7, &DMA2->HISR); }
void DMA2_Stream4_IRQHandler(void) { lap(3u, &DMA2->HIFCR, DMA_HISR_TCIF4, &DMA2->HISR); }

static uint32_t counter_read(void)
{
    uint32_t hi, lo;
    do {
        hi = s_cnt_hi;
        lo = TIM4->CNT;
    } while (hi != s_cnt_hi || (TIM4->SR & TIM_SR_UIF) != 0u);
    return (hi << 16) | lo;
}

void TIM4_IRQHandler(void)
{
    if ((TIM4->SR & TIM_SR_UIF) != 0u) {
        TIM4->SR = ~TIM_SR_UIF;
        s_cnt_hi++;
    }
}

/* MT-3: mode TRIGGER / RESET / PWM_INPUT, falling = event polarity, psc = probe prescaler */
static void probe_arm(uint8_t mode, bool falling, uint16_t psc)
{
    TIM8->CR1 = 0u;
    TIM8->DIER = 0u;
    TIM8->SMCR = 0u;
    TIM8->CCER = 0u;
    TIM8->PSC = psc;
    TIM8->ARR = 0xFFFFu;
    TIM8->CNT = 0u;
    s_pwm = mode == (uint8_t)MEAS_MODE_PWM_INPUT;
    s_pwm_min_p = s_pwm_max_p = s_pwm_min_h = s_pwm_max_h = s_pwm_n = 0u;
    if (s_pwm) {
        TIM8->CCMR1 = TIM_CCMR1_CC1S_1 | TIM_CCMR1_CC2S_0;              /* IC1 <- TI2 (fall), IC2 <- TI2 */
        TIM8->CCMR2 = 0u;
        TIM8->CCER = TIM_CCER_CC1E | TIM_CCER_CC1P | TIM_CCER_CC2E;
        TIM8->SMCR = (6u << TIM_SMCR_TS_Pos) | (4u << TIM_SMCR_SMS_Pos);  /* TI2FP2 resets the counter */
        TIM8->EGR = TIM_EGR_UG;
        TIM8->SR = 0u;
        TIM8->DIER = TIM_DIER_CC2IE;
        TIM8->CR1 = TIM_CR1_CEN;
    } else {
        TIM8->CCMR1 = TIM_CCMR1_CC1S_0 | TIM_CCMR1_CC2S_0;              /* IC1 <- TI1, IC2 <- TI2 */
        TIM8->CCMR2 = TIM_CCMR2_CC3S_0 | TIM_CCMR2_CC4S_0;
        TIM8->CCER = TIM_CCER_CC1E | (falling ? TIM_CCER_CC1P : 0u) | TIM_CCER_CC2E |
                     TIM_CCER_CC3E | TIM_CCER_CC3P | TIM_CCER_CC3NP |
                     TIM_CCER_CC4E | TIM_CCER_CC4P | TIM_CCER_CC4NP;
        TIM8->SMCR = (5u << TIM_SMCR_TS_Pos) |
                     ((mode == (uint8_t)MEAS_MODE_TRIGGER) ? (6u << TIM_SMCR_SMS_Pos) : (4u << TIM_SMCR_SMS_Pos));
        TIM8->EGR = TIM_EGR_UG;
        TIM8->CCR2 = 0u;
        TIM8->CCR3 = 0u;
        TIM8->CCR4 = 0u;
        TIM8->SR = 0u;
        TIM8->DIER = TIM_DIER_CC1DE | TIM_DIER_CC2DE | TIM_DIER_CC4DE;  /* MT-4 stamps */
        if (mode == (uint8_t)MEAS_MODE_RESET) {
            TIM8->CR1 = TIM_CR1_CEN;
        }
    }
    s_pul_at_arm = stamps_total((uint8_t)MEAS_CHAN_PUL);
    s_armed = true;
}

void TIM8_CC_IRQHandler(void)                 /* PWM-input statistics (lowest priority) */
{
    uint32_t p, h;
    if ((TIM8->SR & TIM_SR_CC2IF) == 0u) {
        return;
    }
    p = TIM8->CCR2;                           /* period (clears CC2IF) */
    h = TIM8->CCR1;                           /* high width of the previous pulse */
    if (s_pwm_n == 0u || p < s_pwm_min_p) {
        s_pwm_min_p = p;
    }
    if (p > s_pwm_max_p) {
        s_pwm_max_p = p;
    }
    if (s_pwm_n == 0u || h < s_pwm_min_h) {
        s_pwm_min_h = h;
    }
    if (h > s_pwm_max_h) {
        s_pwm_max_h = h;
    }
    s_pwm_n++;
}

void meas_start(void)                         /* strong definition of board_init.c's weak hook */
{
    RCC->APB1ENR |= RCC_APB1ENR_TIM4EN | RCC_APB1ENR_TIM13EN;
    RCC->APB2ENR |= RCC_APB2ENR_TIM8EN | RCC_APB2ENR_TIM1EN | RCC_APB2ENR_TIM10EN;
    RCC->AHB1ENR |= RCC_AHB1ENR_DMA2EN;
    (void)RCC->AHB1ENR;
    if (s_ni.magic != PROTO_MEAS_MAGIC) {
        memset(&s_ni, 0, sizeof s_ni);
        s_ni.magic = PROTO_MEAS_MAGIC;
    }
    af(PIN_MH_PUL_B_PORT, PIN_MH_PUL_B_BIT, 2u);
    af(PIN_MH_EVT_PORT, PIN_MH_EVT_BIT, 3u);
    af(PIN_MH_PUL_A_PORT, PIN_MH_PUL_A_BIT, 3u);
    af(PIN_MH_ENA_PORT, PIN_MH_ENA_BIT, 3u);
    af(PIN_MH_DIR_PORT, PIN_MH_DIR_BIT, 3u);
    af(PIN_MH_AUX_PORT, PIN_MH_AUX_BIT, 1u);
    /* MT-2 */
    TIM4->CR1 = 0u;
    TIM4->CCMR1 = TIM_CCMR1_CC2S_0;
    TIM4->CCER = 0u;
    TIM4->SMCR = (6u << TIM_SMCR_TS_Pos) | (7u << TIM_SMCR_SMS_Pos);
    TIM4->ARR = 0xFFFFu;
    TIM4->EGR = TIM_EGR_UG;
    TIM4->SR = 0u;
    TIM4->DIER = TIM_DIER_UIE;
    NVIC_SetPriority(TIM4_IRQn, PRIO_MEAS);
    NVIC_EnableIRQ(TIM4_IRQn);
    TIM4->CR1 = TIM_CR1_CEN;
    /* MT-4 */
    dma_ring(DMA2_Stream2, 7u, &TIM5->CNT, s_ni.ring[MEAS_CHAN_EVT], RING, true);
    dma_ring(DMA2_Stream3, 7u, &TIM5->CNT, s_ni.ring[MEAS_CHAN_PUL], RING, true);
    dma_ring(DMA2_Stream7, 7u, &TIM5->CNT, s_ni.ring[MEAS_CHAN_DIR], RING, true);
    dma_ring(DMA2_Stream4, 6u, &TIM5->CNT, s_ni.ring[MEAS_CHAN_AUX], RING, true);
    dma_ring(DMA2_Stream5, 6u, &TIM5->CNT, &s_ni.heartbeat_t_us, 1u, false);
    NVIC_SetPriority(DMA2_Stream2_IRQn, PRIO_MEAS);
    NVIC_SetPriority(DMA2_Stream3_IRQn, PRIO_MEAS);
    NVIC_SetPriority(DMA2_Stream7_IRQn, PRIO_MEAS);
    NVIC_SetPriority(DMA2_Stream4_IRQn, PRIO_MEAS);
    NVIC_EnableIRQ(DMA2_Stream2_IRQn);
    NVIC_EnableIRQ(DMA2_Stream3_IRQn);
    NVIC_EnableIRQ(DMA2_Stream7_IRQn);
    NVIC_EnableIRQ(DMA2_Stream4_IRQn);
    TIM1->CR1 = 0u;
    TIM1->PSC = (2u * time_timer_hz()) / 10000000u - 1u;               /* 180 MHz -> 10 MHz */
    TIM1->ARR = 999u;                                                   /* 10 kHz */
    TIM1->CCMR2 = TIM_CCMR2_CC4S_0;
    TIM1->CCER = TIM_CCER_CC4E | TIM_CCER_CC4P | TIM_CCER_CC4NP;
    TIM1->DIER = TIM_DIER_CC4DE | TIM_DIER_UDE;
    TIM1->EGR = TIM_EGR_UG;
    TIM1->SR = 0u;
    TIM1->CR1 = TIM_CR1_CEN;
    probe_arm((uint8_t)MEAS_MODE_RESET, false, 179u);                  /* 1 us ticks until re-armed */
    s_armed = false;
    NVIC_SetPriority(TIM8_CC_IRQn, PRIO_MEAS);
    NVIC_EnableIRQ(TIM8_CC_IRQn);
    NVIC_SetPriority(TIM1_UP_TIM10_IRQn, PRIO_MEAS);
    NVIC_EnableIRQ(TIM1_UP_TIM10_IRQn);
    NVIC_SetPriority(TIM8_UP_TIM13_IRQn, PRIO_TICK);
    NVIC_SetPriority(EXTI2_IRQn, PRIO_INPUTS);
}

/* ---------------- MT-7 stimulus ---------------- */
static uint32_t lcg(void)
{
    s_stim_seed = s_stim_seed * 1664525u + 1013904223u;
    return s_stim_seed;
}

static void stim_one(void)
{
    /* delay 0 ... 1 running step period (TIM2 ARR at 90 MHz -> 10 MHz ticks), else 0 ... 1 ms */
    uint32_t span = (TIM2->CR1 & TIM_CR1_CEN) != 0u ? (TIM2->ARR + 1u) / 9u : 10000u;
    uint32_t delay = (span != 0u) ? (lcg() >> 8) % span : 0u;
    TIM10->CR1 = 0u;
    TIM10->PSC = (2u * time_timer_hz()) / STIM_HZ - 1u;
    TIM10->CCR1 = delay + 1u;
    TIM10->ARR = delay + 1u + s_stim_hold;
    TIM10->CCMR1 = TIM_CCMR1_OC1M_2 | TIM_CCMR1_OC1M_1 | TIM_CCMR1_OC1M_0;   /* PWM2: active after CCR1 */
    TIM10->CCER = TIM_CCER_CC1E | (s_stim_pol != 0u ? TIM_CCER_CC1P : 0u);
    TIM10->CNT = 0u;
    TIM10->EGR = TIM_EGR_UG;
    TIM10->SR = 0u;
    TIM10->DIER = TIM_DIER_UIE;
    TIM10->CR1 = TIM_CR1_OPM | TIM_CR1_CEN;
}

void TIM1_UP_TIM10_IRQHandler(void)            /* TIM1 update serves DMA only (no UIE) */
{
    if ((TIM10->SR & TIM_SR_UIF) != 0u) {
        TIM10->SR = ~TIM_SR_UIF;
        if (s_stim_left != 0u && --s_stim_left != 0u) {
            stim_one();
        }
    }
}

/* ---------------- HANG ---------------- */
static bool hang_on(void)
{
    return s_hang_until == 0u || (int32_t)(TIM5->CNT - s_hang_until) < 0;
}

void TIM8_UP_TIM13_IRQHandler(void)            /* HANG TICK: blocks the tick and the main loop */
{
    TIM13->SR = 0u;
    while (s_hang_tick && hang_on()) {
    }
    s_hang_tick = false;
    TIM13->DIER = 0u;
}

void EXTI2_IRQHandler(void)                    /* HANG ISR1: level-1 storm */
{
    EXTI->PR = 1u << 2;
    if (s_hang_storm && hang_on()) {
        EXTI->SWIER = 1u << 2;
    } else {
        s_hang_storm = false;
        EXTI->IMR &= ~(1u << 2);
    }
}

static void put(uint8_t *r, uint32_t i, uint32_t v) { le_put32(&r[4u * i], v); }

size_t hal_meas_cmd(const uint8_t *req, size_t n, uint8_t *resp, size_t max)
{
    uint8_t op, sel;
    uint16_t a;
    uint32_t b;
    if (n < 8u || max < PROTO_MEAS_BODY_LEN) {
        return 0u;
    }
    op = req[0];
    sel = req[1];
    a = le_get16(&req[2]);
    b = le_get32(&req[4]);
    memset(resp, 0, PROTO_MEAS_BODY_LEN);
    if (op != (uint8_t)MEAS_OP_STATIC_LEVEL && g_meas_static != 0u) {
        g_meas_static = 0u;                    /* STATIC_LEVEL released before any other op */
        TIM2->CCMR1 = (TIM2->CCMR1 & ~TIM_CCMR1_OC1M) | TIM_CCMR1_OC1M_2;
    }
    switch (op) {
    case MEAS_OP_INFO:
        put(resp, 0u, MEAS_VAR_MEAS);          /* DWT statistics not built yet (OI-FW-37) */
        put(resp, 1u, 2u * time_timer_hz());   /* TIM8 on the APB2 timer clock (180 MHz) */
        put(resp, 2u, 32u);
        put(resp, 3u, 1000000u);
        put(resp, 4u, RING);
        put(resp, 5u, 1000u);
        put(resp, 6u, 0u);
        put(resp, 7u, STIM_HZ);
        break;
    case MEAS_OP_PROBE_ARM:                    /* sel = source (J-EVT jumper position, informative) */
        probe_arm((uint8_t)(a & 3u), (a & 0x100u) != 0u, (uint16_t)b);
        break;
    case MEAS_OP_PROBE_READ: {
        uint32_t sr = TIM8->SR;
        uint32_t fl = (s_armed ? MEAS_PF_ARMED : 0u) |
                      (((TIM8->CR1 & TIM_CR1_CEN) != 0u && !s_pwm) ? MEAS_PF_TRIGGERED : 0u) |
                      (((sr & (TIM_SR_CC1OF | TIM_SR_CC2OF | TIM_SR_CC3OF | TIM_SR_CC4OF)) != 0u) ? MEAS_PF_OVERCAPTURE : 0u) |
                      (((sr & TIM_SR_UIF) != 0u && !s_pwm) ? MEAS_PF_WINDOW_OVERFLOW : 0u) |
                      (((sr & TIM_SR_CC2IF) != 0u && TIM8->CCR2 == 0u) ? MEAS_PF_EDGE_BEFORE_EVENT : 0u);
        put(resp, 0u, fl);
        put(resp, 1u, 0u);
        put(resp, 2u, s_pwm ? 0u : TIM8->CCR2);
        put(resp, 3u, TIM8->CCR3);
        put(resp, 4u, TIM8->CCR4);
        put(resp, 5u, stamps_total((uint8_t)MEAS_CHAN_PUL) - s_pul_at_arm);
        put(resp, 6u, TIM8->CNT);
        put(resp, 7u, TIM8->PSC);
        put(resp, 8u, s_pwm_min_p);
        put(resp, 9u, s_pwm_max_p);
        put(resp, 10u, s_pwm_min_h);
        put(resp, 11u, s_pwm_max_h);
        put(resp, 12u, s_pwm_n);
        TIM8->SR = 0u;
        break;
    }
    case MEAS_OP_COUNTER:
        put(resp, 0u, counter_read());
        put(resp, 1u, TIM5->CNT);
        if (sel == 1u) {
            TIM4->CNT = 0u;
            s_cnt_hi = 0u;
        }
        break;
    case MEAS_OP_STAMPS: {                     /* newest first: stamp k = entry w0 - 1 - (14 a + k) */
        uint32_t tot = stamps_total(sel), k;
        put(resp, 0u, tot);
        put(resp, 1u, RING);
        for (k = 0u; k < PROTO_MEAS_STAMPS_PER_PAGE; k++) {
            uint32_t back = (uint32_t)a * PROTO_MEAS_STAMPS_PER_PAGE + k;
            if (back < tot && back < RING) {
                put(resp, 2u + k, s_ni.ring[sel][(tot - 1u - back) % RING]);
            }
        }
        break;
    }
    case MEAS_OP_NOINIT: {
        uint32_t tot = stamps_total((uint8_t)MEAS_CHAN_PUL);
        put(resp, 0u, s_ni.magic);
        put(resp, 1u, (tot != 0u) ? s_ni.ring[MEAS_CHAN_PUL][(tot - 1u) % RING] : 0u);
        put(resp, 2u, s_ni.heartbeat_t_us);
        put(resp, 3u, s_ni.hang_t_us);
        if (sel == 1u) {
            memset(s_ni.ring, 0, sizeof s_ni.ring);
            s_ni.hang_t_us = 0u;
            s_ni.magic = PROTO_MEAS_MAGIC;
        }
        break;
    }
    case MEAS_OP_STIM_RUN:
        s_stim_seed = b;
        s_stim_pol = sel & 1u;
        s_stim_hold = (uint32_t)(sel >> 1) * (STIM_HZ / 1000u);
        s_stim_left = a;
        af(PIN_MH_STIM_PORT, PIN_MH_STIM_BIT, 3u);
        stim_one();
        break;
    case MEAS_OP_HANG:
        s_ni.hang_t_us = TIM5->CNT;
        s_hang_until = (a == 0u) ? 0u : TIM5->CNT + (uint32_t)a * 1000u;
        if (s_hang_until == 0u && a != 0u) {
            s_hang_until = 1u;
        }
        if (sel == (uint8_t)MEAS_HANG_MAIN) {
            while (hang_on()) {                /* the main loop stops kicking the IWDG */
            }
        } else if (sel == (uint8_t)MEAS_HANG_TICK) {
            s_hang_tick = true;
            TIM13->DIER = TIM_DIER_UIE;
            TIM13->EGR = TIM_EGR_UG;
            NVIC_EnableIRQ(TIM8_UP_TIM13_IRQn);
        } else {
            s_hang_storm = true;
            EXTI->IMR |= 1u << 2;
            NVIC_EnableIRQ(EXTI2_IRQn);
            EXTI->SWIER = 1u << 2;
        }
        break;
    case MEAS_OP_STATIC_LEVEL:                 /* only NOT_ENABLED (core); timer stopped */
        if ((TIM2->CR1 & TIM_CR1_CEN) == 0u) {
            if (sel == (uint8_t)MEAS_PIN_PUL) {
                TIM2->CCMR1 = (TIM2->CCMR1 & ~TIM_CCMR1_OC1M) |
                              ((a != 0u) ? (TIM_CCMR1_OC1M_2 | TIM_CCMR1_OC1M_0) : TIM_CCMR1_OC1M_2);
            } else {
                gpio_write(PIN_DIR_PORT, PIN_DIR_BIT, a != 0u);
            }
            g_meas_static = 1u;                /* released at the next op, hal_step_start, hal_ena_set */
        }
        break;
    case MEAS_OP_DWT:                          /* w0 = 0: not available in this increment */
    default:
        break;
    }
    return PROTO_MEAS_BODY_LEN;
}
#endif /* HW_MEAS */
