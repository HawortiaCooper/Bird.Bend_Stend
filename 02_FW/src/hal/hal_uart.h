/* Seam v1 - UART link (00_System/tools/README.md "Seam v1", FW_design §8.1). Owner: Implementer A.
 * Target: hal/f446/uart2_dma.c (USART2 + DMA1 S5/S6). Twin: Integrator (TCP 127.0.0.1:5760).
 * Implements: IF-002, IF-011, FW-STR-002, SYS-008
 */
#ifndef HAL_UART_H
#define HAL_UART_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

size_t   hal_uart_read(uint8_t *buf, size_t max);                     /* consume RX bytes */
size_t   hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor);   /* stop sniffer: new RX bytes, no consume */
typedef enum { HAL_TX_DATA = 0, HAL_TX_RESP = 1, HAL_TX_EVENT = 2 } hal_tx_class_t;
bool     hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls); /* whole frame or false;
                                                                    HAL_TX_DATA callable from the sample ISR/tick */
size_t   hal_uart_tx_free(hal_tx_class_t cls);
bool     hal_uart_tx_idle(void);                                      /* all classes empty and line idle */
uint32_t hal_uart_rx_overruns(void);

#ifdef __cplusplus
}
#endif

#endif /* HAL_UART_H */
