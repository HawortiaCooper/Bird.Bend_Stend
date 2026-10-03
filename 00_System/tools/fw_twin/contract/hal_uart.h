/* Seam v1 CONTRACT COPY (00_System/tools/README.md "Seam v1" = FW_design v0.3 §8.1).
 * Owner of the real header: Implementer A (02_FW/src/hal/hal_uart.h). This copy is used by the
 * twin build ONLY while A's header is absent; build.py prefers 02_FW/src/hal/ and checks that
 * A's prototypes equal this contract (tools/fw_twin/build.py --check-seams). Never edit to
 * "fix" a mismatch: a seam change goes through FW_design §8.1 + tools/README (Integrator).
 * Implements: SYS-008 (twin seam), D-07 */
#ifndef HAL_UART_H
#define HAL_UART_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

size_t   hal_uart_read(uint8_t *buf, size_t max);                     /* consume RX bytes */
size_t   hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor);   /* sniffer: new RX bytes, no consume */
typedef enum { HAL_TX_DATA = 0, HAL_TX_RESP = 1, HAL_TX_EVENT = 2 } hal_tx_class_t;
bool     hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls); /* whole frame or false */
size_t   hal_uart_tx_free(hal_tx_class_t cls);
bool     hal_uart_tx_idle(void);                                      /* all classes empty and line idle */
uint32_t hal_uart_rx_overruns(void);
#ifdef __cplusplus
}
#endif
#endif /* HAL_UART_H */
