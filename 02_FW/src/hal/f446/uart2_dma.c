/* PC link USART2 (PA2 TX / PA3 RX, AF7) at 921 600 Bd 8N1 via the ST-LINK VCP (FW_design §5.9.1,
 * ICD §1): RX = DMA1 Stream5 Ch4 circular into a 2 KB ring (HT/TC count laps; overflow = the writer
 * lapped a reader -> rx_overruns, reader resynced), separate peek cursor for the stop sniffer;
 * TX = DMA1 Stream6 Ch4, exactly ONE whole frame per transfer, next frame picked at the TC in the
 * wire order DATA > responses > EVENT (pure txsched). Queue pushes and the TC pick run under
 * CRIT_DATA (DATA producers are the level-3 sample ISR and the level-4 tick).
 * Origin: role of Thrust_Stand_HAW/02_FW/src/drv/uart_dma.cpp @37c8747 (rewritten for the F4 DMA).
 * Implements: IF-002, IF-011, FW-STR-002, FW-STR-004, FW-CMD-004 (rx_overruns)
 */
#include "f446.h"
#include "board_pins.h"
#include "fw_config.h"
#include "hal_sys.h"
#include "hal_uart.h"
#include "irq_prio.h"
#include "stm32_def.h"
#include "txsched.h"

#define BAUD           921600u
#define RX_MARGIN      256u                  /* bytes skipped beyond the writer on an overrun */
#define S5_FLAGS       (DMA_HIFCR_CFEIF5 | DMA_HIFCR_CDMEIF5 | DMA_HIFCR_CTEIF5 | DMA_HIFCR_CHTIF5 | DMA_HIFCR_CTCIF5)
#define S6_FLAGS       (DMA_HIFCR_CFEIF6 | DMA_HIFCR_CDMEIF6 | DMA_HIFCR_CTEIF6 | DMA_HIFCR_CHTIF6 | DMA_HIFCR_CTCIF6)

static uint8_t           s_rx[RX_RING_BYTES] __attribute__((aligned(4)));
static volatile uint32_t s_laps;
static uint32_t          s_rd;               /* absolute read position */
static volatile uint32_t s_ovr;

static uint8_t s_bd[TX_D_BYTES], s_br[TX_R_BYTES], s_be[TX_E_BYTES];
static txq_t   s_qd, s_qr, s_qe;
static uint8_t s_txbuf[PROTO_FRAME_MAX + 8u] __attribute__((aligned(4)));
static volatile bool s_busy;

void uart_init(void)
{
    uint32_t pclk1;
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_DMA1EN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    (void)RCC->APB1ENR;
    gpio_af(GPIOA, PIN_TX_BIT, USART2_AF);
    gpio_af(GPIOA, PIN_RX_BIT, USART2_AF);
    gpio_mode(GPIOA, PIN_TX_BIT, GPIO_MODE_AF_, GPIO_PUPD_NONE_, GPIO_SPEED_HIGH_);
    gpio_mode(GPIOA, PIN_RX_BIT, GPIO_MODE_AF_, GPIO_PUPD_UP_, GPIO_SPEED_HIGH_);

    txq_init(&s_qd, s_bd, (uint16_t)sizeof s_bd);
    txq_init(&s_qr, s_br, (uint16_t)sizeof s_br);
    txq_init(&s_qe, s_be, (uint16_t)sizeof s_be);

    USART2->CR1 = 0u;
    pclk1 = HAL_RCC_GetPCLK1Freq();
    USART2->BRR = (pclk1 + BAUD / 2u) / BAUD;           /* OVER16: 45 MHz -> 0x31 = 918 367 Bd */
    USART2->CR2 = 0u;
    USART2->CR3 = USART_CR3_DMAR | USART_CR3_DMAT | USART_CR3_EIE;

    /* RX: Stream5 Ch4, periph -> memory, circular, byte, TC interrupt counts the laps */
    DMA1_Stream5->CR = 0u;
    while ((DMA1_Stream5->CR & DMA_SxCR_EN) != 0u) {
    }
    DMA1->HIFCR = S5_FLAGS;
    DMA1_Stream5->PAR = (uint32_t)&USART2->DR;
    DMA1_Stream5->M0AR = (uint32_t)s_rx;
    DMA1_Stream5->NDTR = RX_RING_BYTES;
    DMA1_Stream5->FCR = 0u;
    DMA1_Stream5->CR = (4u << DMA_SxCR_CHSEL_Pos) | DMA_SxCR_MINC | DMA_SxCR_CIRC | DMA_SxCR_PL_1 |
                       DMA_SxCR_TCIE;
    DMA1_Stream5->CR |= DMA_SxCR_EN;

    /* TX: Stream6 Ch4, memory -> periph, one frame per transfer, TC interrupt */
    DMA1_Stream6->CR = 0u;
    while ((DMA1_Stream6->CR & DMA_SxCR_EN) != 0u) {
    }
    DMA1->HIFCR = S6_FLAGS;
    DMA1_Stream6->PAR = (uint32_t)&USART2->DR;
    DMA1_Stream6->FCR = 0u;
    DMA1_Stream6->CR = (4u << DMA_SxCR_CHSEL_Pos) | DMA_SxCR_MINC | DMA_SxCR_DIR_0 | DMA_SxCR_PL_0 |
                       DMA_SxCR_TCIE | DMA_SxCR_TEIE;

    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
    NVIC_SetPriority(DMA1_Stream5_IRQn, PRIO_LINK);
    NVIC_SetPriority(DMA1_Stream6_IRQn, PRIO_LINK);
    NVIC_SetPriority(USART2_IRQn, PRIO_LINK);
}

void uart_start_irqs(void)
{
    NVIC_EnableIRQ(DMA1_Stream5_IRQn);
    NVIC_EnableIRQ(DMA1_Stream6_IRQn);
    NVIC_EnableIRQ(USART2_IRQn);
}

/* ---------------- RX ---------------- */
static uint32_t rx_written(void)
{
    uint32_t laps, ndtr, pos;
    bool tc_pending;
    do {
        laps = s_laps;
        ndtr = DMA1_Stream5->NDTR;
        tc_pending = (DMA1->HISR & DMA_HISR_TCIF5) != 0u;
    } while (laps != s_laps);
    pos = RX_RING_BYTES - ndtr;                          /* ndtr 0 = end of this lap */
    if (tc_pending && pos < RX_RING_BYTES / 2u) {
        laps++;                                          /* wrapped, TC ISR not yet served */
    }
    return laps * RX_RING_BYTES + pos;
}

static size_t rx_copy(uint8_t *buf, size_t max, uint32_t *pos, bool count_overrun)
{
    uint32_t w = rx_written();
    size_t n = 0u;
    if ((uint32_t)(w - *pos) > RX_RING_BYTES) {
        if (count_overrun) {
            s_ovr++;
        }
        *pos = w - (RX_RING_BYTES - RX_MARGIN);          /* resync; the parser re-hunts */
    }
    while (n < max && *pos != w) {
        buf[n++] = s_rx[*pos % RX_RING_BYTES];
        (*pos)++;
    }
    return n;
}

size_t hal_uart_read(uint8_t *buf, size_t max) { return rx_copy(buf, max, &s_rd, true); }

size_t hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor) { return rx_copy(buf, max, cursor, false); }

uint32_t hal_uart_rx_overruns(void) { return s_ovr; }

void DMA1_Stream5_IRQHandler(void)
{
    uint32_t hisr = DMA1->HISR;
    if ((hisr & DMA_HISR_TCIF5) != 0u) {
        DMA1->HIFCR = DMA_HIFCR_CTCIF5;
        s_laps++;
    }
    DMA1->HIFCR = hisr & (DMA_HISR_TEIF5 | DMA_HISR_FEIF5 | DMA_HISR_DMEIF5 | DMA_HISR_HTIF5);
}

void USART2_IRQHandler(void)
{
    uint32_t sr = USART2->SR;
    if ((sr & (USART_SR_ORE | USART_SR_FE | USART_SR_NE)) != 0u) {
        (void)USART2->DR;                                /* SR then DR read clears the flags */
        if ((sr & USART_SR_ORE) != 0u) {
            s_ovr++;
        }
    }
}

/* ---------------- TX ---------------- */
static txq_t *queue(hal_tx_class_t cls)
{
    return (cls == HAL_TX_DATA) ? &s_qd : (cls == HAL_TX_RESP) ? &s_qr : &s_qe;
}

/* caller holds CRIT_DATA */
static void kick_locked(void)
{
    uint8_t cls;
    uint16_t n;
    if (s_busy) {
        return;
    }
    n = txsched_next(&s_qd, &s_qr, &s_qe, s_txbuf, &cls);
    if (n == 0u) {
        return;
    }
    DMA1->HIFCR = S6_FLAGS;
    DMA1_Stream6->M0AR = (uint32_t)s_txbuf;
    DMA1_Stream6->NDTR = n;
    s_busy = true;
    DMA1_Stream6->CR |= DMA_SxCR_EN;
}

bool hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls)
{
    bool ok;
    CRIT_BEGIN(HAL_CRIT_DATA);
    ok = txq_push(queue(cls), frame, (uint16_t)n);
    if (ok) {
        kick_locked();
    }
    CRIT_END();
    return ok;
}

size_t hal_uart_tx_free(hal_tx_class_t cls)
{
    size_t f;
    CRIT_BEGIN(HAL_CRIT_DATA);
    f = txq_free(queue(cls));
    CRIT_END();
    return f;
}

bool hal_uart_tx_idle(void)
{
    bool idle;
    CRIT_BEGIN(HAL_CRIT_DATA);
    idle = !s_busy && txq_empty(&s_qd) && txq_empty(&s_qr) && txq_empty(&s_qe);
    CRIT_END();
    return idle && (USART2->SR & USART_SR_TC) != 0u;
}

void DMA1_Stream6_IRQHandler(void)
{
    uint32_t hisr = DMA1->HISR;
    DMA1->HIFCR = hisr & (DMA_HISR_TCIF6 | DMA_HISR_TEIF6 | DMA_HISR_FEIF6 | DMA_HISR_DMEIF6 | DMA_HISR_HTIF6);
    if ((hisr & (DMA_HISR_TCIF6 | DMA_HISR_TEIF6)) != 0u) {
        CRIT_BEGIN(HAL_CRIT_DATA);
        s_busy = false;
        kick_locked();                                   /* next frame: DATA > RESP > EVENT */
        CRIT_END();
    }
}
