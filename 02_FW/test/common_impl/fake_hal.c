/* Fake seams for the core host tests. Verifies: (harness for) FW-CMD-001, FW-NVM-001...003,
 * FW-STR-001...006
 */
#include "fake_hal.h"

#include <string.h>

#include "frame.h"
#include "fw_config.h"
#include "hal_flash.h"
#include "hal_hx711.h"
#include "hal_inputs.h"
#include "hal_outputs.h"
#include "hal_step.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "txsched.h"

void app_loop(void);   /* core */

fake_frame_t fake_cap[FAKE_CAP_MAX];
uint32_t     fake_cap_n;

/* ---------------- time ----------------
 * One 64-bit clock in step-timer ticks (FAKE_F_TICK = 90 MHz); hal_time_us() = ticks / 90. */
#define TPU (FAKE_F_TICK / 1000000u)                 /* ticks per µs */
static uint64_t s_tk;
#define s_us ((uint32_t)(s_tk / TPU))
void fake_set_time_us(uint32_t t) { s_tk = (uint64_t)t * TPU; }
uint32_t fake_now_us(void) { return s_us; }
uint32_t hal_time_us(void) { return s_us; }
uint32_t hal_time_ms(void) { return s_us / 1000u; }

/* ---------------- step timer model (= the twin's hal_step semantics, tools/README) ---------------- */
static struct {
    bool     running, high, last, stop_after, uncertain;
    int      dir;
    int32_t  count;
    uint32_t pw, dir_setup, cur, pre, stop_gen;
    uint64_t t_rise, t_end;
    bool     pul_invert, ena_invert;
} S;
bool     fake_ena_enabled = true;
uint32_t fake_ena_changes;
int      fake_dir_arg;
uint32_t fake_set_now_calls, fake_stop_now_calls, fake_abort_calls, fake_step_inits;
uint64_t fake_rise_tk[FAKE_RISE_LOG];
uint32_t fake_rise_n;
bool     fake_step_skip_isr;                         /* inject: next update without step_isr */
static bool s_world_on;
static int32_t s_world_start, s_world_end;
static void world_update(void);
void fake_world_shift(int32_t steps) { s_world_start += steps; s_world_end += steps; world_update(); }

static void schedule(uint64_t start, uint32_t period, uint64_t min_rise)
{
    uint64_t rise = start + (uint64_t)((period > S.pw) ? period - S.pw : 0u);
    if (rise < min_rise) {
        rise = min_rise;
    }
    S.cur = period;
    S.t_rise = rise;
    S.t_end = rise + S.pw;
    S.high = false;
}

static void halt(void)
{
    S.running = false;
    S.high = false;
    S.stop_gen++;
}

uint32_t hal_step_init(const hal_step_cfg_t *cfg)
{
    S.pw = cfg->pw_ticks;
    S.dir_setup = cfg->dir_setup_ticks;
    S.pul_invert = cfg->pul_invert;
    S.ena_invert = cfg->ena_invert;
    fake_step_inits++;
    return FAKE_F_TICK;
}

void hal_step_set_dir(int dir)
{
    fake_dir_arg = dir;
    if (!S.running) {
        S.dir = (dir > 0) ? 1 : -1;
    }
}

void hal_step_start(uint32_t first)
{
    if (S.dir == 0) {
        S.dir = 1;
    }
    S.running = true;
    S.last = false;
    S.stop_after = false;
    S.pre = 0u;
    schedule(s_tk, first, s_tk + S.dir_setup);
}

void hal_step_set_period(uint32_t ticks) { S.pre = ticks; }

void hal_step_set_period_now(uint32_t ticks)
{
    uint64_t start;
    fake_set_now_calls++;
    if (!S.running || S.high) {
        return;                                      /* stretch only before the pulse */
    }
    start = S.t_rise - (uint64_t)((S.cur > S.pw) ? S.cur - S.pw : 0u);
    schedule(start, ticks, s_tk);
}

void hal_step_arm_last(void) { S.last = true; }

bool hal_step_stop_now(void)
{
    fake_stop_now_calls++;
    if (!S.running) {
        S.stop_gen++;                                /* idle halt visible, like the target (FWR-01) */
        return false;
    }
    if (S.high) {
        S.stop_after = true;                         /* CLEAN: the pulse completes */
        return true;
    }
    halt();
    return false;
}

bool hal_step_abort(void)
{
    bool cut;
    fake_abort_calls++;
    if (!S.running) {
        S.stop_gen++;                                /* idle halt visible, like the target (FWR-01) */
        return false;
    }
    cut = S.high;
    if (cut) {
        S.uncertain = true;
    }
    halt();
    return cut;
}

int32_t hal_step_count(void) { return S.count; }
void hal_step_set_count(int32_t steps)
{
    if (!S.running) {
        s_world_start += steps - S.count;            /* the world does not move (twin: shift_um) */
        s_world_end += steps - S.count;
        S.count = steps;
    }
}
bool hal_step_running(void) { return S.running; }
uint32_t hal_step_stop_gen(void) { return S.stop_gen; }

void hal_ena_set(bool enabled)
{
    if (enabled != fake_ena_enabled) {
        fake_ena_changes++;
    }
    fake_ena_enabled = enabled;
}

int32_t fake_step_count(void) { return S.count; }
bool fake_step_uncertain(void) { return S.uncertain; }
uint32_t fake_step_pw(void) { return S.pw; }

/* ---------------- inputs ---------------- */
static uint16_t s_in_raw = (uint16_t)((1u << 4) | (1u << 6));   /* PAUSE NO released, PEND in position */
uint16_t hal_inputs_raw(void) { return s_in_raw; }
uint8_t  fake_pause_level_cfg;
void hal_inputs_config(const hal_in_cfg_t *c) { fake_pause_level_cfg = c->pause_active_level; }
void hal_inputs_rearm(uint8_t id) { (void)id; }

void fake_input_set(uint8_t id, bool level)
{
    bool cur = ((s_in_raw >> id) & 1u) != 0u;
    if (cur == level) {
        return;
    }
    s_in_raw = (uint16_t)(level ? (s_in_raw | (1u << id)) : (s_in_raw & ~(1u << id)));
    switch (id) {                                    /* HAL fixed reactions (FW_design §5.2) */
    case 0u:
        if (level) {
            (void)hal_step_abort();
            hal_ena_set(false);
        }
        break;
    case 1u:
    case 2u:
        if (level) {
            (void)hal_step_stop_now();
        }
        break;
    case 4u:
        break;
    default:
        return;                                      /* ALM / PEND / DRV_PWR: polled only */
    }
    on_input_edge(id, level, s_us);
}

void fake_world_limits(bool on, int32_t start_le, int32_t end_ge)
{
    s_world_on = on;
    s_world_start = start_le;
    s_world_end = end_ge;
    world_update();
}

static void world_update(void)
{
    if (s_world_on) {
        fake_input_set(1u, S.count <= s_world_start);
        fake_input_set(2u, S.count >= s_world_end);
    }
}

static void step_event(void)
{
    step_next_t r;
    uint32_t next;
    if (!S.high) {
        S.high = true;
        if (fake_rise_n < FAKE_RISE_LOG) {
            fake_rise_tk[fake_rise_n] = s_tk;
        }
        fake_rise_n++;
        return;
    }
    S.high = false;                                  /* end of the pulse = update event */
    S.count += S.dir;
    world_update();
    if (!S.running) {
        return;
    }
    if (S.stop_after || S.last) {
        halt();
        return;
    }
    next = S.pre ? S.pre : S.cur;
    S.pre = 0u;
    schedule(s_tk, next, s_tk);
    if (fake_step_skip_isr) {
        fake_step_skip_isr = false;                  /* missed update: ISR late by one period */
        return;
    }
    r = step_isr();
    if (r.period != 0u) {
        S.pre = r.period;
    }
    if (r.stop) {
        halt();
        return;
    }
    if (r.last) {
        S.last = true;
    }
}

void fake_advance_tk(uint64_t dt)
{
    uint64_t until = s_tk + dt;
    while (S.running) {
        uint64_t t = S.high ? S.t_end : S.t_rise;
        if (t > until) {
            break;
        }
        s_tk = t;
        step_event();
    }
    s_tk = until;
}

/* ---------------- UART ---------------- */
#define FAKE_RX_SIZE 8192u
static uint8_t  s_rx[FAKE_RX_SIZE];
static uint32_t s_rx_w, s_rx_r;              /* absolute byte counts */
static uint32_t s_rx_ovr;
static uint8_t  s_bd[TX_D_BYTES], s_br[TX_R_BYTES], s_be[TX_E_BYTES];
static txq_t    s_qd, s_qr, s_qe;
static bool     s_auto = true;

void fake_rx(const uint8_t *d, size_t n)
{
    size_t i;
    for (i = 0u; i < n; i++) {
        s_rx[s_rx_w % FAKE_RX_SIZE] = d[i];
        s_rx_w++;
    }
}

void fake_rx_overrun(void) { s_rx_ovr++; }

size_t hal_uart_read(uint8_t *buf, size_t max)
{
    size_t n = 0u;
    while (n < max && s_rx_r != s_rx_w) {
        buf[n++] = s_rx[s_rx_r % FAKE_RX_SIZE];
        s_rx_r++;
    }
    return n;
}

void (*fake_peek_hook)(void);                        /* called once at the next hal_uart_peek() (tick: link_tick) */

size_t hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor)
{
    size_t n = 0u;
    if (fake_peek_hook != NULL) {
        void (*h)(void) = fake_peek_hook;
        fake_peek_hook = NULL;
        h();
    }
    while (n < max && *cursor != s_rx_w) {
        buf[n++] = s_rx[*cursor % FAKE_RX_SIZE];
        (*cursor)++;
    }
    return n;
}

static void capture(const uint8_t *fr, uint16_t n, uint8_t cls)
{
    fake_frame_t *c;
    if (fake_cap_n >= FAKE_CAP_MAX || n < PROTO_FRAME_OVERHEAD) {
        return;
    }
    c = &fake_cap[fake_cap_n++];
    c->cls = cls;
    c->type = fr[2];
    c->seq = fr[3];
    c->len = (uint16_t)(fr[4] | (fr[5] << 8));
    memcpy(c->payload, &fr[FRAME_HEADER_LEN], c->len);
}

uint32_t fake_tx_drain(uint32_t max_frames)
{
    uint8_t out[256];
    uint8_t cls = 0u;
    uint32_t k = 0u;
    while (k < max_frames) {
        uint16_t n = txsched_next(&s_qd, &s_qr, &s_qe, out, &cls);
        if (n == 0u) {
            break;
        }
        capture(out, n, cls);
        k++;
    }
    return k;
}

bool hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls)
{
    txq_t *q = (cls == HAL_TX_DATA) ? &s_qd : (cls == HAL_TX_RESP) ? &s_qr : &s_qe;
    if (!txq_push(q, frame, (uint16_t)n)) {
        return false;
    }
    if (s_auto) {
        (void)fake_tx_drain(UINT32_MAX);
    }
    return true;
}

size_t hal_uart_tx_free(hal_tx_class_t cls)
{
    const txq_t *q = (cls == HAL_TX_DATA) ? &s_qd : (cls == HAL_TX_RESP) ? &s_qr : &s_qe;
    return txq_free(q);
}

bool hal_uart_tx_idle(void) { return txq_empty(&s_qd) && txq_empty(&s_qr) && txq_empty(&s_qe); }
uint32_t hal_uart_rx_overruns(void) { return s_rx_ovr; }
void fake_tx_auto(bool on) { s_auto = on; }
void fake_cap_clear(void) { fake_cap_n = 0u; }

int32_t fake_cap_find(uint8_t type, int32_t seq, uint32_t from)
{
    uint32_t i;
    for (i = from; i < fake_cap_n; i++) {
        if (fake_cap[i].type == type && (seq < 0 || fake_cap[i].seq == (uint8_t)seq)) {
            return (int32_t)i;
        }
    }
    return -1;
}

uint32_t fake_cap_count(uint8_t type)
{
    uint32_t i, n = 0u;
    for (i = 0u; i < fake_cap_n; i++) {
        if (fake_cap[i].type == type) {
            n++;
        }
    }
    return n;
}

int32_t fake_cap_event(uint16_t code, uint32_t from)
{
    uint32_t i;
    for (i = from; i < fake_cap_n; i++) {
        if (fake_cap[i].type == (uint8_t)ASYNC_EVENT &&
            (uint16_t)(fake_cap[i].payload[4] | (fake_cap[i].payload[5] << 8)) == code) {
            return (int32_t)i;
        }
    }
    return -1;
}

void fake_cmd(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t len)
{
    uint8_t fr[PROTO_FRAME_MAX];
    uint16_t n = frame_build(type, seq, pl, len, fr);
    fake_rx(fr, n);
}

/* ---------------- flash ---------------- */
static uint8_t s_flash[2][NVM_SECTOR_BYTES];
static int32_t s_cut_words = -1, s_cut_erase = -1;
bool     fake_flash_dead;
uint32_t fake_flash_erases, fake_flash_words;
bool     fake_flash_fail_program;

void fake_flash_blank(void) { memset(s_flash, 0xFF, sizeof s_flash); }
uint8_t *fake_flash_sector(uint8_t i) { return s_flash[i & 1u]; }
void fake_flash_cut_after_words(int32_t n) { s_cut_words = n; }
void fake_flash_cut_in_erase(int32_t bytes) { s_cut_erase = bytes; }

static uint8_t *map(uint32_t addr)
{
    if (addr >= NVM_ADDR_A && addr < NVM_ADDR_A + NVM_SECTOR_BYTES) {
        return &s_flash[0][addr - NVM_ADDR_A];
    }
    if (addr >= NVM_ADDR_B && addr < NVM_ADDR_B + NVM_SECTOR_BYTES) {
        return &s_flash[1][addr - NVM_ADDR_B];
    }
    return NULL;
}

const void *hal_flash_map(uint32_t addr) { return map(addr); }

bool hal_flash_erase(uint32_t sector)
{
    uint8_t *p;
    if (fake_flash_dead || (sector != NVM_SECTOR_A && sector != NVM_SECTOR_B)) {
        return false;
    }
    p = s_flash[sector == NVM_SECTOR_A ? 0 : 1];
    fake_flash_erases++;
    if (s_cut_erase >= 0) {
        memset(p, 0xFF, (size_t)s_cut_erase);
        fake_flash_dead = true;
        return false;
    }
    memset(p, 0xFF, NVM_SECTOR_BYTES);
    return true;
}

bool hal_flash_program(uint32_t addr, const void *src, size_t n)
{
    const uint8_t *s = (const uint8_t *)src;
    size_t i;
    if (fake_flash_dead || fake_flash_fail_program || (addr % 4u) != 0u || (n % 4u) != 0u) {
        return false;
    }
    for (i = 0u; i < n; i += 4u) {
        uint8_t *d = map(addr + (uint32_t)i);
        uint8_t k;
        if (d == NULL || map(addr + (uint32_t)i + 3u) == NULL) {
            return false;
        }
        if (s_cut_words == 0) {
            fake_flash_dead = true;                  /* power lost before this word */
            return false;
        }
        for (k = 0u; k < 4u; k++) {
            d[k] = (uint8_t)(d[k] & s[i + k]);       /* NOR flash: bits can only be cleared */
        }
        fake_flash_words++;
        if (s_cut_words > 0) {
            s_cut_words--;
        }
    }
    return true;
}

/* ---------------- AFE / outputs / sys ---------------- */
uint8_t  fake_hx_gain_pulses;
bool     fake_hx_rate80, fake_hx_hold, fake_rate_pin, fake_led, fake_trip;
uint32_t fake_hx_config_calls;
bool     fake_reset_requested;
uint8_t  fake_reset_cause = 1u;
uint32_t fake_wdg_kicks, fake_wdg_timeout_ms;
int32_t  fake_crit_depth, fake_crit_max_depth;

void hal_hx711_config(uint8_t gain_pulses, bool rate80)
{
    fake_hx_gain_pulses = gain_pulses;
    fake_hx_rate80 = rate80;
    fake_hx_config_calls++;
}
void hal_hx711_powerdown(bool on) { (void)on; }
uint32_t fake_hx_kicks;
void hal_hx711_kick(void) { fake_hx_kicks++; }
void hal_hx711_hold(bool on) { fake_hx_hold = on; }
void hal_rate_pin(bool high) { fake_rate_pin = high; }
void hal_trip_relay(bool trip) { fake_trip = trip; }
void hal_led(bool on) { fake_led = on; }

void fake_sample_st(uint32_t t_us, int32_t raw, uint8_t status)
{
    afe_sample_t s;
    s.t_us = t_us;
    s.raw = raw;
    s.pos_steps = S.count;                           /* position latched at data-ready (FW-AFE-005) */
    s.status = status;
    if (!fake_hx_hold) {
        on_afe_sample(&s);
    }
}

void fake_sample(uint32_t t_us, int32_t raw) { fake_sample_st(t_us, raw, 0u); }

bool fake_meas_on;                                   /* emulate a HW_MEAS build */
uint8_t fake_meas_last_op = 0xFFu;
size_t hal_meas_cmd(const uint8_t *req, size_t n, uint8_t *resp, size_t max)
{
    if (!fake_meas_on || n < 8u || max < 64u) {
        return 0u;
    }
    memset(resp, 0, 64u);
    fake_meas_last_op = req[0];
    resp[0] = 1u;                                    /* INFO w0: variant MEAS */
    return 64u;
}

bool fake_fault_valid;
uint32_t fake_fault_pc, fake_fault_cfsr;
bool hal_fault_record(uint32_t *pc, uint32_t *cfsr)
{
    if (!fake_fault_valid) {
        return false;
    }
    fake_fault_valid = false;
    *pc = fake_fault_pc;
    *cfsr = fake_fault_cfsr;
    return true;
}

void hal_wdg_kick(void) { fake_wdg_kicks++; }
void hal_wdg_set_timeout(uint32_t ms) { fake_wdg_timeout_ms = ms; }
uint8_t hal_reset_cause(void) { return fake_reset_cause; }
void hal_reset(void) { fake_reset_requested = true; }
void hal_uid(uint8_t uid[12])
{
    uint8_t i;
    for (i = 0u; i < 12u; i++) {
        uid[i] = (uint8_t)(0x10u + i);
    }
}
bool hal_clk_fallback(void) { return false; }
uint16_t hal_stack_free_min(void) { return 3000u; }

hal_crit_t hal_crit_enter(hal_crit_level_t level)
{
    (void)level;
    fake_crit_depth++;
    if (fake_crit_depth > fake_crit_max_depth) {
        fake_crit_max_depth = fake_crit_depth;
    }
    return (hal_crit_t)fake_crit_depth;
}

void hal_crit_exit(hal_crit_t saved)
{
    (void)saved;
    fake_crit_depth--;
}

void fake_run_ms(uint32_t n)
{
    uint32_t i;
    for (i = 0u; i < n; i++) {
        fake_advance_tk(1000u * (uint64_t)TPU);
        core_tick_1ms();
        app_loop();
    }
}

void fake_run_ms_samples(uint32_t n, uint32_t period_us, int32_t raw)
{
    uint32_t i;
    for (i = 0u; i < n; i++) {
        fake_run_ms(1u);
        if (period_us != 0u && (s_us / 1000u) % (period_us / 1000u) == 0u) {
            fake_sample(s_us, raw);
        }
    }
}

void fake_hal_reset(void)
{
    fake_set_time_us(1000000u);
    memset(&S, 0, sizeof S);
    s_in_raw = (uint16_t)((1u << 4) | (1u << 6));
    s_world_on = false;
    fake_ena_enabled = true;
    fake_ena_changes = 0u;
    fake_set_now_calls = 0u;
    fake_stop_now_calls = 0u;
    fake_abort_calls = 0u;
    fake_peek_hook = NULL;
    fake_step_inits = 0u;
    fake_rise_n = 0u;
    fake_step_skip_isr = false;
    fake_hx_kicks = 0u;
    fake_meas_on = false;
    fake_fault_valid = false;
    s_rx_w = 0u;
    s_rx_r = 0u;
    s_rx_ovr = 0u;
    txq_init(&s_qd, s_bd, (uint16_t)sizeof s_bd);
    txq_init(&s_qr, s_br, (uint16_t)sizeof s_br);
    txq_init(&s_qe, s_be, (uint16_t)sizeof s_be);
    s_auto = true;
    fake_cap_n = 0u;
    s_cut_words = -1;
    s_cut_erase = -1;
    fake_flash_dead = false;
    fake_flash_fail_program = false;
    fake_flash_erases = 0u;
    fake_flash_words = 0u;
    fake_hx_hold = false;
    fake_hx_config_calls = 0u;
    fake_reset_requested = false;
    fake_reset_cause = 1u;
    fake_wdg_kicks = 0u;
    fake_crit_depth = 0;
    fake_crit_max_depth = 0;
}
