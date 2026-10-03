/* Fake seams for the core host tests (Implementer A, FW_design §8.5): deterministic time, RX
 * injection, TX class queues (pure txsched, same wire order as the target) with a capture of sent
 * frames, RAM flash with power-cut injection, AFE / output / system recorders. Single-threaded:
 * critical sections are no-ops that only count nesting.
 * Verifies: (harness for) FW-CMD-001, FW-NVM-001...003, FW-STR-001...006
 */
#ifndef FAKE_HAL_H
#define FAKE_HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "proto.h"

typedef struct {
    uint8_t  cls;            /* 0 DATA, 1 RESP, 2 EVENT */
    uint8_t  type;
    uint8_t  seq;
    uint16_t len;
    uint8_t  payload[PROTO_MAX_LEN];
} fake_frame_t;

#define FAKE_CAP_MAX 2048u
extern fake_frame_t fake_cap[FAKE_CAP_MAX];
extern uint32_t     fake_cap_n;

/* state */
void     fake_hal_reset(void);              /* everything except the flash contents */
void     fake_flash_blank(void);
uint8_t *fake_flash_sector(uint8_t i);      /* 0 = sector 1 (A), 1 = sector 2 (B) */

/* time */
void     fake_set_time_us(uint32_t t);
uint32_t fake_now_us(void);
/** n x 1 ms: advance 1000 us, core_tick_1ms(), app_loop(). */
void     fake_run_ms(uint32_t n);

/* link */
void     fake_rx(const uint8_t *d, size_t n);
void     fake_rx_overrun(void);             /* count one RX overrun */
void     fake_tx_auto(bool on);             /* on (default): unlimited line, frames captured at once */
uint32_t fake_tx_drain(uint32_t max_frames);/* line model off: send up to n frames in wire order */
void     fake_cap_clear(void);
/** Index of the first captured frame with this TYPE (and seq if seq >= 0) at/after `from`, or -1. */
int32_t  fake_cap_find(uint8_t type, int32_t seq, uint32_t from);
uint32_t fake_cap_count(uint8_t type);
/** Index of the first captured EVENT with this code at/after `from`, or -1. */
int32_t  fake_cap_event(uint16_t code, uint32_t from);
/** Send one command frame (built here) to the FW RX. */
void     fake_cmd(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t len);

/* flash fault injection */
void     fake_flash_cut_after_words(int32_t n);   /* -1 = off */
void     fake_flash_cut_in_erase(int32_t bytes);  /* erase stops after `bytes` bytes; -1 = off */
extern bool     fake_flash_dead;                   /* a cut happened: further ops fail */
extern uint32_t fake_flash_erases, fake_flash_words;
extern bool     fake_flash_fail_program;           /* program returns false (no cut) */

/* AFE / outputs / sys recorders */
extern uint8_t  fake_hx_gain_pulses;
extern bool     fake_hx_rate80, fake_hx_hold, fake_rate_pin, fake_led, fake_trip;
extern uint32_t fake_hx_config_calls;
extern bool     fake_reset_requested;
extern uint8_t  fake_reset_cause;
extern uint32_t fake_wdg_kicks, fake_wdg_timeout_ms;
extern int32_t  fake_crit_depth, fake_crit_max_depth;
void fake_sample(uint32_t t_us, int32_t raw);      /* calls on_afe_sample at t_us */

#endif /* FAKE_HAL_H */
