/* FW host twin — seam v1 implementations (tools/README "Seam v1" = FW_design §8.1).
 * Compiled against A's headers 02_FW/src/hal/hal_*.h when present, else against the contract
 * copies in fw_twin/contract/ (build.py decides and checks that both agree).
 * Implements: SYS-008, D-07. Twin semantics per seam: tools/README.md seam table.
 */
#include <string.h>

#include "hal_flash.h"
#include "hal_hx711.h"
#include "hal_inputs.h"
#include "hal_outputs.h"
#include "hal_step.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "twin_internal.h"

/* ================================ hal_time ================================ */
static void busy_guard(void)
{
    /* the twin advances time only between passes: a FW busy-wait on the clock would spin forever */
    if (T.in_main && ++T.time_calls > 2000000u) {
        tw_out("! busy-wait: > 2e6 hal_time_* calls in one main-loop pass at t=%llu", (unsigned long long)T.now);
        tw_reset("hang");
    }
}
uint32_t hal_time_us(void) { busy_guard(); return tw_fw_us(T.now); }
uint32_t hal_time_ms(void) { busy_guard(); return (uint32_t)((T.now - T.boot_ns) / 1000000ull); }

/* ================================ hal_uart ================================ */
size_t hal_uart_read(uint8_t *buf, size_t max)
{
    size_t n = 0;
    while (n < max && T.rx_rd != T.rx_wr) buf[n++] = T.rx_ring[T.rx_rd++ % RX_RING];
    return n;
}

size_t hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor)
{
    if ((uint32_t)(T.rx_wr - *cursor) > RX_RING) *cursor = T.rx_wr - RX_RING;   /* lapped: bytes lost */
    size_t n = 0;
    while (n < max && *cursor != T.rx_wr) buf[n++] = T.rx_ring[(*cursor)++ % RX_RING];
    return n;
}

static bool cong_d(void) { return T.now < T.congestion_until; }

size_t hal_uart_tx_free(hal_tx_class_t cls)
{
    if ((unsigned)cls > 2u) return 0;
    const txq_t *q = &T.txq[cls];
    if (cls == HAL_TX_DATA && cong_d()) return 0;
    if (q->count >= q->max_frames) return 0;
    return q->cap_bytes - q->used_bytes;
}

static void hexline(const char *tag, int cls, int ok, const uint8_t *b, size_t n)
{
    static const char hx[] = "0123456789ABCDEF";
    char s[2 * TXF_MAX + 1];
    size_t m = n > TXF_MAX ? TXF_MAX : n;
    for (size_t i = 0; i < m; i++) { s[2 * i] = hx[b[i] >> 4]; s[2 * i + 1] = hx[b[i] & 15]; }
    s[2 * m] = 0;
    tw_out("%s %llu %d %d %s", tag, (unsigned long long)T.now, cls, ok, s);
}

bool hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls)
{
    if ((unsigned)cls > 2u || n == 0 || n > TXF_MAX) { hexline("W", (int)cls, 0, frame, n); return false; }
    txq_t *q = &T.txq[cls];
    bool ok = !(cls == HAL_TX_DATA && cong_d()) && q->count < q->max_frames && q->used_bytes + n <= q->cap_bytes;
    hexline("W", (int)cls, ok, frame, n);              /* every produced frame, incl. dropped ("sent") */
    if (!ok) return false;
    txframe_t *f = &q->f[(q->head + q->count) % 64u];
    f->n = (uint16_t)n; memcpy(f->b, frame, n);
    q->count++; q->used_bytes += (unsigned)n;
    tw_tx_kick();
    return true;
}

bool hal_uart_tx_idle(void) { return !T.tx_busy && !T.tx_tc_pending && T.txq[0].count == 0 && T.txq[1].count == 0 && T.txq[2].count == 0; }
uint32_t hal_uart_rx_overruns(void) { return T.rx_overruns; }

/* ================================ hal_step ================================ */
static void set_pul(int high) { tw_edge("PUL", T.pul_invert ? !high : high); }

uint32_t hal_step_init(const hal_step_cfg_t *cfg)
{
    T.pw_ticks = cfg->pw_ticks; T.dir_setup_ticks = cfg->dir_setup_ticks;
    T.pul_invert = cfg->pul_invert; T.ena_invert = cfg->ena_invert;
    tw_out("L %llu hal_step_init pw=%u dir_setup=%u pul_inv=%d ena_inv=%d", (unsigned long long)T.now,
           (unsigned)cfg->pw_ticks, (unsigned)cfg->dir_setup_ticks, cfg->pul_invert, cfg->ena_invert);
    return F_TICK_HZ;
}

static vt_t ticks_ns(uint32_t ticks) { return ((uint64_t)ticks * 1000000000ull + F_TICK_HZ / 2u) / F_TICK_HZ; }

void hal_step_set_dir(int dir)
{
    tw_out("L %llu hal_step_set_dir %d", (unsigned long long)T.now, dir);
    if (T.step_running) { tw_out("L %llu seam_violation set_dir_while_running", (unsigned long long)T.now); return; }
    int lv = dir > 0;
    if (lv != (T.dir > 0) || T.dir == 0) tw_edge("DIR", lv);
    T.dir = dir > 0 ? 1 : -1;
}

static void schedule_period(vt_t start, uint32_t period, vt_t min_rise)
{
    uint32_t pw = T.pw_ticks ? T.pw_ticks : 1u;
    vt_t rise = start + ticks_ns(period > pw ? period - pw : 0u);
    if (rise < min_rise) rise = min_rise;
    T.period_cur = period;
    T.step_rise = rise;
    T.step_end = rise + ticks_ns(pw);
    T.step_high = false;
}

void hal_step_start(uint32_t first_period_ticks)
{
    tw_out("L %llu hal_step_start %u", (unsigned long long)T.now, (unsigned)first_period_ticks);
    if (T.dir == 0) T.dir = 1;
    T.step_running = true; T.step_last = false; T.step_stop_after = false; T.period_pre = 0;
    schedule_period(T.now, first_period_ticks, T.now + ticks_ns(T.dir_setup_ticks));
}

void hal_step_set_period(uint32_t ticks) { tw_out("L %llu hal_step_set_period %u", (unsigned long long)T.now, (unsigned)ticks); T.period_pre = ticks; }

void hal_step_set_period_now(uint32_t ticks)
{
    tw_out("L %llu hal_step_set_period_now %u", (unsigned long long)T.now, (unsigned)ticks);
    if (!T.step_running || T.step_high) return;             /* stretch only before the pulse */
    vt_t start = T.step_rise - ticks_ns(T.period_cur > T.pw_ticks ? T.period_cur - T.pw_ticks : 0u);
    schedule_period(start, ticks, T.now);
}

void hal_step_arm_last(void) { tw_out("L %llu hal_step_arm_last", (unsigned long long)T.now); T.step_last = true; }

static void step_halt(void) { T.step_running = false; T.step_high = false; T.stop_gen++; }

bool hal_step_stop_now(void)
{
    tw_out("L %llu hal_step_stop_now running=%d high=%d", (unsigned long long)T.now, T.step_running, T.step_high);
    if (!T.step_running) return false;
    if (T.step_high) { T.step_stop_after = true; return true; }   /* CLEAN: the pulse completes */
    step_halt();
    return false;
}

bool hal_step_abort(void)
{
    tw_out("L %llu hal_step_abort running=%d high=%d", (unsigned long long)T.now, T.step_running, T.step_high);
    if (!T.step_running) return false;
    bool cut = T.step_high;
    if (cut) { set_pul(0); T.step_uncertain = true; }               /* TRUNCATE: pulse cut, not counted */
    step_halt();
    return cut;
}

int32_t hal_step_count(void) { return T.count; }
void hal_step_set_count(int32_t steps)
{
    tw_out("L %llu hal_step_set_count %ld", (unsigned long long)T.now, (long)steps);
    if (T.step_running) { tw_out("L %llu seam_violation set_count_while_running", (unsigned long long)T.now); return; }
    T.shift_um += (double)(T.count - steps) * 1000.0 / T.spm_world;   /* the world does not move */
    T.count = steps;
}
bool hal_step_running(void) { return T.step_running; }
uint32_t hal_step_stop_gen(void) { return T.stop_gen; }

void hal_ena_set(bool enabled)
{
    tw_out("L %llu hal_ena_set %d", (unsigned long long)T.now, enabled);
    T.ena_enabled = enabled;
    int lv = (!enabled) ^ (T.ena_invert ? 1 : 0);        /* LED current (high) = driver disabled */
    if (lv != T.ena_level) { T.ena_level = lv; tw_edge("ENA", lv); }
}

/* called by the engine at step_rise / step_end */
void twin_step_event(void)
{
    if (!T.step_running) return;
    if (!T.step_high) { T.step_high = true; set_pul(1); return; }
    /* end of the pulse = update event */
    set_pul(0); T.step_high = false;
    T.count += T.dir; tw_step_counted();
    if (T.step_stop_after || T.step_last) { step_halt(); return; }
    uint32_t next = T.period_pre ? T.period_pre : T.period_cur;
    T.period_pre = 0;
    schedule_period(T.now, next, T.now);
    if (T.step_fault_next) {                              /* inject step_fault: the ISR is late by one period */
        T.step_fault_next = false;
        tw_out("L %llu step_fault_injected", (unsigned long long)T.now);
        return;
    }
    step_next_t r = step_isr();
    if (r.period) T.period_pre = r.period;
    if (r.stop) { step_halt(); return; }
    if (r.last) T.step_last = true;
}

/* ================================ hal_inputs ================================ */
uint16_t hal_inputs_raw(void)
{
    uint16_t v = 0;
    for (unsigned i = 0; i < IN_COUNT; i++) v |= (uint16_t)(T.lvl[i] ? 1u << i : 0u);
    return v;
}
void hal_inputs_config(const hal_in_cfg_t *c)
{
    T.stop_active_level = c->stop_active_level; T.pause_active_level = c->pause_active_level;
    tw_out("L %llu hal_inputs_config stop=%u pause=%u", (unsigned long long)T.now, c->stop_active_level, c->pause_active_level);
}
void hal_inputs_rearm(uint8_t id) { (void)id; }          /* twin: lines are never masked (README) */

/* electrical edge on an EXTI input: HAL fixed reaction (RAM) now, core callback now or after the stall */
void twin_input_edge(uint8_t id, uint8_t level)
{
    uint32_t t = tw_fw_us(T.now);
    bool active;
    switch (id) {
    case IN_ESTOP: active = level; if (active) { (void)hal_step_abort(); hal_ena_set(false); } break;
    case IN_LIM_START: case IN_LIM_END: active = level; if (active) (void)hal_step_stop_now(); break;
    case IN_STOP: active = (T.stop_active_level == 0) ? level : !level; if (active) (void)hal_step_stop_now(); break;
    case IN_PAUSE: break;
    default: return;                                      /* ALM, PEND, DRV_PWR: polled, no EXTI */
    }
    if (T.in_stall) {
        if (T.n_deferred < 64u) { T.deferred[T.n_deferred].id = id; T.deferred[T.n_deferred].level = level; T.deferred[T.n_deferred].t_us = t; T.n_deferred++; }
        return;
    }
    on_input_edge(id, level != 0, t);
}

void twin_deliver_deferred(void)
{
    for (unsigned i = 0; i < T.n_deferred; i++) on_input_edge(T.deferred[i].id, T.deferred[i].level != 0, T.deferred[i].t_us);
    T.n_deferred = 0;
}

/* ================================ hal_outputs ================================ */
void hal_rate_pin(bool high)
{
    if ((int)high != T.rate_pin) { T.rate_pin = high; tw_out("O %llu RATE %d", (unsigned long long)T.now, high); }
    int sps = high ? 80 : 10;
    if (sps != T.afe_rate_sps) { T.afe_rate_sps = sps; T.next_sample = VT_NEVER; }   /* engine re-arms */
}
void hal_trip_relay(bool trip) { if ((int)trip != T.trip) { T.trip = trip; tw_out("O %llu TRIP %d", (unsigned long long)T.now, trip); } }
void hal_led(bool on) { if ((int)on != T.led) { T.led = on; tw_out("O %llu LED %d", (unsigned long long)T.now, on); } }

/* ================================ hal_hx711 ================================ */
void hal_hx711_config(uint8_t gain_pulses, bool rate80)
{
    tw_out("L %llu hal_hx711_config gain_pulses=%u rate80=%d", (unsigned long long)T.now, gain_pulses, rate80);
    T.afe_gain_pulses = gain_pulses;
    hal_rate_pin(rate80);
}
void hal_hx711_powerdown(bool on) { tw_out("L %llu hal_hx711_powerdown %d", (unsigned long long)T.now, on); T.afe_pd = on; T.next_sample = VT_NEVER; }
void hal_hx711_kick(void) { tw_out("L %llu hal_hx711_kick", (unsigned long long)T.now); }
void hal_hx711_hold(bool on) { tw_out("L %llu hal_hx711_hold %d", (unsigned long long)T.now, on); T.afe_hold = on; }

/* ================================ hal_flash ================================ */
static long sector_index(uint32_t s)
{
    if (s == 1u || s == 2u) return (long)s - 1;
    if (s >= FLASH_BASE && s < FLASH_BASE + FLASH_SIZE) return (long)((s - FLASH_BASE) / FLASH_SECTOR_SIZE);
    return -1;
}

bool hal_flash_erase(uint32_t sector)
{
    tw_out("L %llu hal_flash_erase %lu", (unsigned long long)T.now, (unsigned long)sector);
    long k = sector_index(sector);
    if (k < 0) return false;
    uint8_t *p = &T.flash[(size_t)k * FLASH_SECTOR_SIZE];
    T.flash_erases++;
    if (T.cut_in_erase > 0 && --T.cut_in_erase == 0) {       /* power cut in the middle of this erase */
        T.cut_in_erase = -1;
        memset(p, 0xFF, FLASH_SECTOR_SIZE / 2u);
        tw_stall(FLASH_ERASE_NS / 2u);
        tw_flash_save();
        tw_reset("power");
    }
    tw_stall(FLASH_ERASE_NS);
    memset(p, 0xFF, FLASH_SECTOR_SIZE);
    tw_flash_save();
    return true;
}

bool hal_flash_program(uint32_t addr, const void *src, size_t n)
{
    tw_out("L %llu hal_flash_program 0x%08lX %u", (unsigned long long)T.now, (unsigned long)addr, (unsigned)n);
    if (addr < FLASH_BASE || addr + n > FLASH_BASE + FLASH_SIZE || (addr & 3u)) return false;
    const uint8_t *s = (const uint8_t *)src;
    bool ok = true;
    for (size_t i = 0; i < n; i += 4u) {
        if (T.cut_after_word == 0) { T.cut_after_word = -1; tw_flash_save(); tw_reset("power"); }
        size_t m = n - i < 4u ? n - i : 4u;
        uint8_t *d = &T.flash[addr - FLASH_BASE + i];
        for (size_t j = 0; j < m; j++) {
            if ((d[j] & s[i + j]) != s[i + j]) ok = false;    /* a 0 -> 1 bit needs an erase */
            d[j] &= s[i + j];
        }
        T.flash_writes++;
        if (T.cut_after_word > 0) T.cut_after_word--;
        tw_stall(FLASH_WORD_NS);
    }
    tw_flash_save();
    if (!ok) tw_out("L %llu flash_program_not_erased 0x%08lX", (unsigned long long)T.now, (unsigned long)addr);
    return ok;
}

const void *hal_flash_map(uint32_t addr)
{
    if (addr < FLASH_BASE || addr >= FLASH_BASE + FLASH_SIZE) return NULL;
    return &T.flash[addr - FLASH_BASE];
}

/* ================================ hal_sys ================================ */
/* seam semantics (twin, conservative; OI-C-M1-03): `ms` = the WORST-CASE timeout, reached at the slowest LSI
 * 17 kHz; at LSI f the IWDG fires after ms * 17000 / f (default 32 kHz: 90 -> 47.8 ms = the target run window
 * PR /8, RLR 190). Armed by the first kick or set_timeout; default 90 ms (FW_design §5.13). */
void hal_wdg_kick(void)
{
    if (!T.wdg_armed) { T.wdg_armed = true; if (!T.wdg_timeout_ns) T.wdg_timeout_ns = 90000000ull; }
    T.wdg_deadline = T.now + (vt_t)((double)T.wdg_timeout_ns * 17000.0 / T.lsi_hz);
}
void hal_wdg_set_timeout(uint32_t ms)
{
    tw_out("L %llu hal_wdg_set_timeout %lu", (unsigned long long)T.now, (unsigned long)ms);
    T.wdg_timeout_ns = (vt_t)ms * 1000000ull;
    hal_wdg_kick();
}
uint8_t hal_reset_cause(void) { return T.reset_cause; }
void hal_reset(void) { tw_out("L %llu hal_reset", (unsigned long long)T.now); tw_reset("software"); }
void hal_uid(uint8_t uid[12]) { memcpy(uid, T.uid, 12); }
bool hal_clk_fallback(void) { return T.hse_fail; }
uint16_t hal_stack_free_min(void) { return 3072u; }   /* twin: no stack painting; fixed plausible value */

/* critical sections (seam v1.1): single-threaded twin, ISRs never preempt -> no-ops; nesting is checked */
static unsigned crit_depth;
hal_crit_t hal_crit_enter(hal_crit_level_t level) { (void)level; return (hal_crit_t)crit_depth++; }
void hal_crit_exit(hal_crit_t saved)
{
    if (crit_depth == 0u || saved != (hal_crit_t)(crit_depth - 1u)) tw_out("L %llu seam_violation crit_unbalanced", (unsigned long long)T.now);
    if (crit_depth) crit_depth--;
}
