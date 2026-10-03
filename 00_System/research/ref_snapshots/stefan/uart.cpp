#include "uart.hpp"

#include "mcu.hpp"

namespace stepctl {

namespace {

/**
 * Registry that maps a HAL handle back to its Uart instance.
 *
 * The HAL callbacks are free functions that only receive UART_HandleTypeDef*,
 * so something has to bridge back to the object. Three slots is plenty for
 * this board and costs twelve bytes.
 */
constexpr std::size_t kMaxUarts = 3;
Uart* g_instances[kMaxUarts] = {};

void registerInstance(Uart* self)
{
    for (auto& slot : g_instances) {
        if (slot == nullptr || slot == self) { slot = self; return; }
    }
}

}  // namespace

Uart* Uart::instanceFor(const UART_HandleTypeDef* huart)
{
    for (auto* slot : g_instances) {
        if (slot != nullptr && slot->huart_ == huart) return slot;
    }
    return nullptr;
}

// ----------------------------------------------------------------- lifecycle

void Uart::init(UART_HandleTypeDef* huart)
{
    huart_ = huart;
    tx_.clear();
    rx_lines_.clear();
    rx_tail_ = 0;
    partial_len_ = 0;
    tx_busy_ = false;
    overrun_ = false;

    registerInstance(this);
    startReceive();
}

/**
 * Starts the circular receive DMA. It then runs forever and is only restarted
 * after a line error.
 *
 * The DMA stream must be configured as CIRCULAR in CubeMX. In NORMAL mode this
 * call would stop after the first 256 bytes and the link would go quiet with
 * no diagnostic at all.
 */
void Uart::startReceive()
{
    if (huart_ == nullptr) return;

    partial_len_ = 0;

    // In circular mode RxState never returns to READY, so a second
    // HAL_UART_Receive_DMA would answer HAL_BUSY and quietly do nothing.
    // Abort first, otherwise a restart after a line error is a no-op.
    if (HAL_UART_Receive_DMA(huart_, rx_dma_,
                             static_cast<std::uint16_t>(kRxDmaSize)) != HAL_OK) {
        HAL_UART_AbortReceive(huart_);
        HAL_UART_Receive_DMA(huart_, rx_dma_, static_cast<std::uint16_t>(kRxDmaSize));
    }

    // Always adopt whatever position the hardware is actually at. Assuming
    // zero here is what breaks things: if the restart failed, the DMA is still
    // running mid-buffer, the reader thinks 200 bytes just arrived, and it
    // parses stale buffer contents into a stream of bogus commands.
    rx_tail_ = rxWritePos();
}

// ------------------------------------------------------------------ reception

/**
 * Where the DMA is about to write next. NDTR counts down from the buffer size.
 *
 * The modulo is essential, not defensive. NDTR reads zero both at the wrap
 * instant and when the stream never started, and kRxDmaSize - 0 is 256 — a
 * position rx_tail_ can never reach, since it only ever holds 0..255. Without
 * the wrap, head never equals tail, the byte count never falls to zero, and
 * drainRx re-parses the whole buffer on every pass, manufacturing the same
 * line forever.
 */
std::size_t Uart::rxWritePos() const
{
    if (huart_ == nullptr || huart_->hdmarx == nullptr) return rx_tail_;
    const std::size_t remaining = __HAL_DMA_GET_COUNTER(huart_->hdmarx);
    if (remaining > kRxDmaSize) return rx_tail_;      // stream not running yet
    return (kRxDmaSize - remaining) % kRxDmaSize;
}

void Uart::pushByte(char c)
{
    if (c == '\n' || c == '\r') {
        if (partial_len_ > 0) {
            partial_.text[partial_len_] = '\0';
            if (!rx_lines_.push(partial_)) overrun_ = true;
            partial_len_ = 0;
        }
    } else if (partial_len_ < kLineMax - 1) {
        partial_.text[partial_len_++] = c;
    } else {
        partial_len_ = 0;                   // line too long: discard it
        overrun_ = true;
    }
}

/**
 * Moves everything the DMA produced since the previous call into lines.
 *
 * Runs in thread context, so the step interrupt is never delayed by parsing.
 * The cost is that reception only advances while the main loop keeps calling
 * getLine, which is why the receive window has to be large enough to cover the
 * worst-case loop period: 256 bytes is about 22 ms at 115200 baud.
 */
void Uart::drainRx()
{
    const std::size_t head = rxWritePos();

    std::size_t available = (head >= rx_tail_) ? (head - rx_tail_)
                                               : (kRxDmaSize - rx_tail_ + head);
    // If we ever get close to a full lap, the DMA has probably already
    // overwritten unread bytes. Report it rather than pretend the data is good.
    if (available > (kRxDmaSize * 3) / 4) overrun_ = true;

    while (available--) {
        pushByte(static_cast<char>(rx_dma_[rx_tail_]));
        rx_tail_ = (rx_tail_ + 1) % kRxDmaSize;
    }
}

// --------------------------------------------------------------- transmission

void Uart::write(const char* data, std::size_t len)
{
    for (std::size_t i = 0; i < len; ++i) {
        if (!tx_.push(data[i])) break;      // buffer full: drop the tail
    }
    startTxIfIdle();
}

/**
 * Moves one contiguous chunk out of the ring buffer into the DMA.
 *
 * Called both from the main loop (via write) and from the transfer-complete
 * callback, so the two contexts must not copy into the staging buffer at the
 * same time.
 *
 * Only the claim of tx_busy_ is done with interrupts masked. The copy itself
 * runs with interrupts enabled, because whoever owns the flag also owns the
 * consumer side of the ring and the staging buffer exclusively. That matters:
 * copying a full 256-byte chunk takes roughly 19 us, and the step interrupt
 * fires every 13 us at the top speed this axis supports. Holding PRIMASK
 * across the copy would drop steps.
 */
void Uart::startTxIfIdle()
{
    if (huart_ == nullptr) return;

    for (;;) {
        {
            CriticalSection cs;
            if (tx_busy_) return;
            if (huart_->gState != HAL_UART_STATE_READY) return;
            tx_busy_ = true;                    // claim exclusive ownership
        }

        std::size_t n = 0;
        char c;
        while (n < kTxChunk && tx_.pop(c)) {
            tx_chunk_[n++] = static_cast<std::uint8_t>(c);
        }

        if (n > 0) {
            if (HAL_UART_Transmit_DMA(huart_, tx_chunk_,
                                      static_cast<std::uint16_t>(n)) != HAL_OK) {
                // The bytes have already left the ring, so they are lost.
                // Deliberately NOT reported through overrun_: that flag is
                // read by the main loop, which answers by writing a message —
                // straight back into this function. Reporting a transmit
                // failure over the failing transmitter is a feedback loop that
                // floods the console.
                tx_busy_ = false;
            }
            return;
        }

        // Nothing was queued. Release the claim, then look again: a producer
        // may have pushed between our empty pop loop and the release, and it
        // would have seen the flag still taken and given up.
        tx_busy_ = false;
        if (tx_.empty()) return;
    }
}

void Uart::onTxComplete()
{
    tx_busy_ = false;
    startTxIfIdle();
}

// ---------------------------------------------------------------------- errors

/**
 * A line error (overrun, framing, noise, parity) aborts the receive stream.
 * If it is not restarted the link dies silently, which is the classic trap of
 * the HAL DMA API: everything keeps compiling and running, the board just
 * stops answering.
 */
void Uart::onError()
{
    const std::uint32_t err = huart_ ? huart_->ErrorCode : 0u;

    if (err & (HAL_UART_ERROR_ORE | HAL_UART_ERROR_NE |
               HAL_UART_ERROR_FE  | HAL_UART_ERROR_PE)) {
        overrun_ = true;
    }

    // A corrupted byte means the line in flight can no longer be trusted.
    partial_len_ = 0;

    if (huart_ != nullptr) {
        huart_->ErrorCode = HAL_UART_ERROR_NONE;
        if (err & HAL_UART_ERROR_DMA) {
            tx_busy_ = false;               // a transmit stream also died
        }
    }
    startReceive();
}

// --------------------------------------------------------------------- access

bool Uart::getLine(char* dst, std::size_t dst_size)
{
    drainRx();

    Line line;
    if (!rx_lines_.pop(line)) return false;

    std::size_t i = 0;
    for (; i + 1 < dst_size && line.text[i]; ++i) dst[i] = line.text[i];
    dst[i] = '\0';
    return true;
}

bool Uart::takeOverrun()
{
    CriticalSection cs;
    const bool o = overrun_;
    overrun_ = false;
    return o;
}

}  // namespace stepctl

// ---------------------------------------------------------------- HAL callbacks
//
// These override the weak definitions inside the HAL. They must have C linkage
// and must not be defined anywhere else in the project — CubeMX does not
// generate them, but example code often does, and a duplicate definition is a
// link error.
//
// There is deliberately no HAL_UART_RxCpltCallback: reception is polled out of
// the circular buffer by the main loop, so it needs no interrupt of its own.

extern "C" {

void HAL_UART_TxCpltCallback(UART_HandleTypeDef* huart)
{
    if (auto* u = stepctl::Uart::instanceFor(huart)) u->onTxComplete();
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef* huart)
{
    if (auto* u = stepctl::Uart::instanceFor(huart)) u->onError();
}

}  // extern "C"