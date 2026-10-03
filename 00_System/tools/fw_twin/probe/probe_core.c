/* fw_twin HARNESS PROBE — a stand-in "core" for self-testing the twin harness and the integration
 * test code while Implementer A's 02_FW/src/core is not available. THIS IS NOT THE FIRMWARE:
 * it implements a small, ICD-shaped subset of the M1 behaviour (link, parameters, a simple NVM
 * record log, stream, SET_VALID, STOP/HALT/PAUSE/RESUME/clears without motion) so that every seam
 * of the twin is exercised. Integration evidence is only valid against A's core (build.py --core fw).
 * Owner: Integrator. Implements: SYS-008 (twin self-test only).
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
#include "params_gen.h"
#include "proto_gen.h"

void app_init(void);
void app_loop(void);

/* ------------------------------------------------------------------ helpers */
static uint16_t crc16(const uint8_t *d, size_t n)
{
    uint16_t c = 0xFFFFu;
    for (size_t i = 0; i < n; i++) {
        c ^= (uint16_t)(d[i] << 8);
        for (int b = 0; b < 8; b++) c = (uint16_t)((c & 0x8000u) ? ((unsigned)c << 1) ^ 0x1021u : (unsigned)c << 1);
    }
    return c;
}
static uint32_t crc32(const uint8_t *d, size_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) { c ^= d[i]; for (int b = 0; b < 8; b++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u))); }
    return ~c;
}
static void put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
static uint32_t get32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

/* ------------------------------------------------------------------ state */
static params_t P, P_boot;
static bool halt_l, paused, stream_on, valid_old, valid_new, nvm_defaulted, have_record, reboot_req;
static uint8_t halt_src, pause_src, ev_seq;
static uint32_t valid_t_apply, nvm_seq, rx_ok, rx_crc, rx_ferr, tx_drops, ev_ovf, last_tick_kick, ticks;
static uint16_t frame_seq; static bool overrun_next;
static int32_t raw_last = PROTO_AFE_NO_DATA;
static uint8_t evq[16][16]; static unsigned evq_n;
static uint32_t sniff_cursor; static uint8_t sniff_buf[16]; static unsigned sniff_n;
static uint32_t link_ms;
static uint32_t probe_steps_left, probe_period;   /* PROBE-only step train (TYPE 0x3F), harness test */
enum { NV_IDLE, NV_QUIESCE } ;
static int nvm_state; static uint8_t nvm_seq_resp; static uint32_t reboot_at;

/* ------------------------------------------------------------------ TX */
static void send(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t n, hal_tx_class_t cls)
{
    uint8_t f[168];
    f[0] = PROTO_SYNC0; f[1] = PROTO_SYNC1; f[2] = type; f[3] = seq; put16(f + 4, n);
    if (n) memcpy(f + 6, pl, n);
    put16(f + 6 + n, crc16(f + 2, 4u + n));
    if (!hal_uart_write(f, 8u + n, cls) && cls == HAL_TX_DATA) { tx_drops++; overrun_next = true; }
}
static void resp(uint8_t cmd, uint8_t seq, const uint8_t *body, uint16_t n)
{
    uint8_t pl[161]; pl[0] = ST_OK; if (n) memcpy(pl + 1, body, n);
    send((uint8_t)(cmd | PROTO_RESP_BIT), seq, pl, (uint16_t)(n + 1u), HAL_TX_RESP);
}
static void nack(uint8_t cmd, uint8_t seq, uint8_t st, uint16_t detail)
{
    uint8_t pl[3] = { st, (uint8_t)detail, (uint8_t)(detail >> 8) };
    send((uint8_t)(cmd | PROTO_RESP_BIT), seq, pl, 3, HAL_TX_RESP);
}
static void event(uint16_t code, uint16_t arg, int32_t value)
{
    uint8_t *e = evq[evq_n % 16u];
    if (evq_n >= 16u) { ev_ovf++; ev_seq++; return; }
    put32(e, hal_time_us()); put16(e + 4, code); put16(e + 6, arg); put32(e + 8, (uint32_t)value); put32(e + 12, 0);
    evq_n++;
}
static void events_flush(void)
{
    while (evq_n && hal_uart_tx_free(HAL_TX_EVENT) >= 24u) {
        send(ASYNC_EVENT, ev_seq++, evq[0], 16, HAL_TX_EVENT);
        memmove(evq[0], evq[1], (size_t)(evq_n - 1u) * 16u); evq_n--;
    }
}

/* ------------------------------------------------------------------ VALID / flags */
static bool valid_at(uint32_t t) { return (int32_t)(t - valid_t_apply) >= 0 ? valid_new : valid_old; }
static void valid_clear(uint8_t cause)
{
    uint32_t now = hal_time_us();
    bool was = valid_at(now);
    valid_old = was; valid_new = false; valid_t_apply = now;
    if (was) event(EV_VALID_CLEARED, cause, 0);
}
static uint16_t data_status(void)
{
    uint16_t raw = hal_inputs_raw(), s = 0;
    if (paused) s |= DS_PAUSED;
    if (!(raw & (1u << 7))) s |= DS_DRV_PWR;                 /* DRV_PWR low = present */
    return s;
}
static uint8_t data_flags(uint32_t t) { return (uint8_t)((valid_at(t) ? DF_VALID : 0u) | (halt_l ? DF_HALT : 0u)); }

/* ------------------------------------------------------------------ NVM (probe record log) */
#define SLOT 512u
#define REC_MAGIC 0x4D564E42u   /* "BNVM" */
static uint8_t rec[SLOT];
static const uint8_t *slot_ptr(unsigned k) { return (const uint8_t *)hal_flash_map(0x08004000u + k * SLOT); }
static bool slot_valid(const uint8_t *s) { return s && get32(s) == REC_MAGIC && get32(s + SLOT - 4u) == crc32(s, SLOT - 4u); }
static int newest_slot(void)
{
    int best = -1; uint32_t bs = 0;
    for (unsigned k = 0; k < 64u; k++) { const uint8_t *s = slot_ptr(k); if (slot_valid(s) && (best < 0 || (int32_t)(get32(s + 4) - bs) > 0)) { best = (int)k; bs = get32(s + 4); } }
    return best;
}
static void build_record(uint32_t seq)
{
    memset(rec, 0xFF, SLOT);
    put32(rec, REC_MAGIC); put32(rec + 4, seq); put32(rec + 8, PARAM_DICT_HASH);
    unsigned n = 0;
    for (unsigned i = 0; i < PARAM_COUNT; i++) {
        const param_meta_t *m = &PARAM_TABLE[i];
        if (!(m->flags & PARAM_F_NVM)) continue;
        uint8_t *e = rec + 16 + 8u * n++;
        put16(e, m->id); e[2] = m->type; e[3] = 0; put32(e + 4, param_get_raw(&P, m));
    }
    put16(rec + 12, (uint16_t)n);
    put32(rec + SLOT - 4u, crc32(rec, SLOT - 4u));
}
static bool apply_record(const uint8_t *s)
{
    if (get32(s + 8) != PARAM_DICT_HASH) return false;
    unsigned n = get16(s + 12);
    for (unsigned i = 0; i < n; i++) {
        const uint8_t *e = s + 16 + 8u * i;
        const param_meta_t *m = param_find(get16(e));
        if (m && (m->flags & PARAM_F_NVM) && param_raw_in_range(m, get32(e + 4))) param_set_raw(&P, m, get32(e + 4));
    }
    return true;
}
static bool cfg_dirty(void)
{
    int k = newest_slot();
    if (k < 0) return true;
    const uint8_t *s = slot_ptr((unsigned)k);
    if (get32(s + 8) != PARAM_DICT_HASH) return true;
    unsigned n = get16(s + 12);
    for (unsigned i = 0; i < n; i++) {
        const uint8_t *e = s + 16 + 8u * i;
        const param_meta_t *m = param_find(get16(e));
        if (m && param_get_raw(&P, m) != get32(e + 4)) return true;
    }
    return false;
}
static int nvm_save(void)
{
    int k = newest_slot();
    nvm_seq = k < 0 ? 1u : get32(slot_ptr((unsigned)k) + 4) + 1u;
    /* next blank slot after the newest record in its sector; never erase the sector holding it */
    unsigned next = k < 0 ? 0u : (unsigned)k + 1u, sec = k < 0 ? 0u : (unsigned)k / 32u;
    for (;;) {
        if (next / 32u != sec || next >= 64u) {               /* sector full: erase the OTHER sector */
            sec ^= 1u; next = sec * 32u;
            if (!hal_flash_erase(sec + 1u)) return NVMD_ERASE_PROGRAM;
            break;
        }
        const uint8_t *s = slot_ptr(next);
        unsigned i = 0;
        while (i < SLOT && s[i] == 0xFFu) i++;
        if (i == SLOT) break;                                 /* blank slot */
        next++;                                               /* torn / used slot: skip it */
    }
    build_record(nvm_seq);
    uint32_t a = 0x08004000u + next * SLOT;
    if (!hal_flash_program(a, rec, SLOT - 4u) || !hal_flash_program(a + SLOT - 4u, rec + SLOT - 4u, 4u)) return NVMD_ERASE_PROGRAM;
    if (!slot_valid(slot_ptr(next))) return NVMD_VERIFY;
    have_record = true;
    return 0;
}

/* ------------------------------------------------------------------ STATUS */
static void status_body(uint8_t *b)
{
    memset(b, 0, PROTO_STATUS_LEN);
    uint32_t now = hal_time_us();
    uint16_t raw = hal_inputs_raw();
    put32(b + 0, hal_time_ms()); put32(b + 4, now);
    b[8] = data_flags(now); b[9] = MS_NOT_ENABLED; put16(b + 10, data_status()); put16(b + 12, 0);
    put16(b + 14, (uint16_t)((raw & 0xFFu) | (raw & 1u ? IO_ENA_DISABLED : 0u)));
    b[16] = HP_NONE; b[17] = halt_src; b[18] = hal_reset_cause();
    b[19] = (uint8_t)((hal_clk_fallback() ? SYSF_CLK_FALLBACK : 0u) | (cfg_dirty() ? SYSF_CFG_DIRTY : 0u) |
                      (stream_on ? SYSF_STREAM_ON : 0u) | (reboot_req ? SYSF_REBOOT_PENDING : 0u) | (nvm_defaulted ? SYSF_NVM_DEFAULTED : 0u));
    put32(b + 32, (uint32_t)raw_last);
    put32(b + 40, rx_ok); put32(b + 44, rx_crc); put32(b + 48, rx_ferr); put32(b + 52, hal_uart_rx_overruns()); put32(b + 56, tx_drops);
    put16(b + 60, (uint16_t)ev_ovf); put16(b + 64, (uint16_t)(hal_time_ms() - link_ms)); put16(b + 66, hal_stack_free_min());
    put32(b + 72, nvm_seq);
    b[84] = pause_src;
}

/* ------------------------------------------------------------------ commands */
static void dispatch(uint8_t type, uint8_t seq, const uint8_t *pl, uint16_t len)
{
    static const int8_t req_len[0x40] = {
        [CMD_PING] = 0, [CMD_GET_INFO] = 0, [CMD_GET_STATUS] = 0, [CMD_REBOOT] = 4, [CMD_GET_ALL_PARAMS] = 1,
        [CMD_GET_PARAM] = 2, [CMD_SET_PARAM] = 7, [CMD_SAVE_PARAMS] = 0, [CMD_LOAD_PARAMS] = 0,
        [CMD_DEFAULT_PARAMS] = 0, [CMD_STREAM_START] = 0, [CMD_STREAM_STOP] = 0, [CMD_SET_VALID] = 1,
        [CMD_ENABLE] = 0, [CMD_DISABLE] = 0, [CMD_HOME] = 1, [CMD_MOVE_ABS] = 12, [CMD_JOG] = 12,
        [CMD_MOVE_UNTIL_LOAD] = 17, [CMD_STOP] = 1, [CMD_HALT] = 0, [CMD_HALT_CLEAR] = 0, [CMD_ESTOP_CLEAR] = 0,
        [CMD_FAULT_CLEAR] = 0, [CMD_PAUSE] = 0, [CMD_RESUME] = 0 };
    static uint8_t defined[0x40];
    static const uint8_t cmds[] = { CMD_PING, CMD_GET_INFO, CMD_GET_STATUS, CMD_REBOOT, CMD_GET_ALL_PARAMS, CMD_GET_PARAM,
        CMD_SET_PARAM, CMD_SAVE_PARAMS, CMD_LOAD_PARAMS, CMD_DEFAULT_PARAMS, CMD_STREAM_START, CMD_STREAM_STOP,
        CMD_SET_VALID, CMD_ENABLE, CMD_DISABLE, CMD_HOME, CMD_MOVE_ABS, CMD_JOG, CMD_MOVE_UNTIL_LOAD, CMD_STOP,
        CMD_HALT, CMD_HALT_CLEAR, CMD_ESTOP_CLEAR, CMD_FAULT_CLEAR, CMD_PAUSE, CMD_RESUME };
    for (unsigned i = 0; i < sizeof cmds; i++) defined[cmds[i]] = 1;
    if (type == 0 || type >= 0x40) { rx_ferr++; return; }
    link_ms = hal_time_ms();
    if (type == 0x3Fu && len == 12u) {     /* PROBE-only (undefined in the ICD): i32 steps, u32 period ticks, u32 0 */
        int32_t n = (int32_t)get32(pl);
        probe_period = get32(pl + 4);
        probe_steps_left = (uint32_t)(n < 0 ? -n : n);
        if (probe_steps_left) { hal_step_set_dir(n < 0 ? -1 : 1); hal_step_start(probe_period); }
        resp(type, seq, NULL, 0); return;
    }
    if (!defined[type]) { nack(type, seq, ST_E_UNKNOWN_CMD, type); return; }
    if (len != (uint16_t)req_len[type]) { nack(type, seq, ST_E_LENGTH, (uint16_t)req_len[type]); return; }
    uint8_t b[160];
    switch (type) {
    case CMD_PING: resp(type, seq, NULL, 0); break;
    case CMD_GET_INFO: {
        memset(b, 0, 44);
        b[0] = PROTO_MAJOR; b[1] = PROTO_MINOR; b[2] = PROTO_PAYLOAD_VERSION; b[3] = 0; b[4] = 1; b[5] = 0;
        put32(b + 6, PARAM_DICT_HASH); hal_uid(b + 10); memcpy(b + 22, "PROBE-NOT-FW", 12);
        put16(b + 38, PARAM_COUNT); put32(b + 40, FEAT_AFE_SYNTHETIC | FEAT_NVM | FEAT_TWIN);
        resp(type, seq, b, 44); break;
    }
    case CMD_GET_STATUS: status_body(b); resp(type, seq, b, PROTO_STATUS_LEN); break;
    case CMD_REBOOT:
        if (get32(pl) != PROTO_REBOOT_MAGIC) { nack(type, seq, ST_E_RANGE, 0); break; }
        resp(type, seq, NULL, 0); reboot_at = hal_time_ms() | 1u; break;
    case CMD_GET_ALL_PARAMS: {
        unsigned pc = (PARAM_COUNT + 19u) / 20u;
        if (pl[0] >= pc) { nack(type, seq, ST_E_RANGE, 0); break; }
        unsigned n = 0;
        for (unsigned i = pl[0] * 20u; i < PARAM_COUNT && n < 20u; i++, n++) {
            const param_meta_t *m = &PARAM_TABLE[i]; uint8_t *e = b + 3 + 7u * n;
            put16(e, m->id); e[2] = m->type; param_raw_to_wire(m, param_get_raw(&P, m), e + 3);
        }
        b[0] = pl[0]; b[1] = (uint8_t)pc; b[2] = (uint8_t)n;
        resp(type, seq, b, (uint16_t)(3u + 7u * n)); break;
    }
    case CMD_GET_PARAM: case CMD_SET_PARAM: {
        const param_meta_t *m = param_find(get16(pl));
        if (!m) { nack(type, seq, ST_E_PARAM_ID, get16(pl)); break; }
        if (type == CMD_SET_PARAM) {
            uint32_t raw; uint8_t st = param_validate_set(m, pl[2], pl + 3, &raw);
            if (st != PARAM_ST_OK) { nack(type, seq, st, m->id); break; }
            param_set_raw(&P, m, raw);
            reboot_req = P.motion.pul_invert != P_boot.motion.pul_invert || P.motion.ena_invert != P_boot.motion.ena_invert || P.drv.pwr_sense_enable != P_boot.drv.pwr_sense_enable;
            if (m->id == PID_AFE_RATE_SPS) hal_hx711_config(25, P.afe.rate_sps != 0);
        }
        put16(b, m->id); b[2] = m->type; param_raw_to_wire(m, param_get_raw(&P, m), b + 3);
        resp(type, seq, b, 7); break;
    }
    case CMD_SAVE_PARAMS: nvm_state = NV_QUIESCE; nvm_seq_resp = seq; hal_hx711_hold(true); break;
    case CMD_LOAD_PARAMS: {
        int k = newest_slot();
        if (k < 0) { nack(type, seq, ST_E_NVM, NVMD_NO_RECORD); break; }
        apply_record(slot_ptr((unsigned)k)); event(EV_PARAMS_LOADED, 0, 0); resp(type, seq, NULL, 0); break;
    }
    case CMD_DEFAULT_PARAMS: params_set_defaults(&P); event(EV_PARAMS_DEFAULTED, 0, 0); resp(type, seq, NULL, 0); break;
    case CMD_STREAM_START: stream_on = true; resp(type, seq, NULL, 0); break;
    case CMD_STREAM_STOP: stream_on = false; resp(type, seq, NULL, 0); break;
    case CMD_SET_VALID: {
        if (pl[0] > 1u) { nack(type, seq, ST_E_RANGE, 0); break; }
        uint32_t now = hal_time_us();
        valid_old = valid_at(now); valid_new = pl[0] != 0; valid_t_apply = now;
        put32(b, now); resp(type, seq, b, 4); break;
    }
    case CMD_STOP:
        if (pl[0] > 1u) { nack(type, seq, ST_E_RANGE, 0); break; }
        valid_clear(pl[0] ? SC_PC_STOP_CONTROLLED : SC_PC_STOP); resp(type, seq, NULL, 0); break;
    case CMD_HALT:
        (void)hal_step_stop_now();
        if (!halt_l) { halt_l = true; halt_src = SRC_PC; event(EV_HALT_SET, SRC_PC, 0); }
        valid_clear(SC_PC_HALT); resp(type, seq, NULL, 0); break;
    case CMD_PAUSE:
        if (!paused) { paused = true; pause_src = SRC_PC; event(EV_PAUSED, SRC_PC, 0); }
        valid_clear(SC_PC_PAUSE); resp(type, seq, NULL, 0); break;
    case CMD_RESUME:
        if (halt_l) { nack(type, seq, ST_E_STATE, BLOCK_HALT); break; }
        if (paused) { paused = false; pause_src = SRC_NONE; event(EV_PAUSE_CLEARED, 3, 0); }
        resp(type, seq, NULL, 0); break;
    case CMD_HALT_CLEAR:
        if (halt_l) { halt_l = false; halt_src = SRC_NONE; event(EV_HALT_CLEARED, 0, 0); }
        if (paused) { paused = false; pause_src = SRC_NONE; event(EV_PAUSE_CLEARED, 2, 0); }
        resp(type, seq, NULL, 0); break;
    case CMD_ESTOP_CLEAR: resp(type, seq, NULL, 0); break;
    case CMD_FAULT_CLEAR: put16(b, 0); resp(type, seq, b, 2); break;
    case CMD_JOG:
        if (get32(pl) == 0u) { resp(type, seq, NULL, 0); break; }
        /* fall through */
    default: {   /* motion family: probe = NOT_ENABLED / NOT_HOMED state check, else NOT_IN_BUILD */
        uint16_t blk = (uint16_t)((halt_l ? BLOCK_HALT : 0u) | (paused && type != CMD_ENABLE && type != CMD_DISABLE ? BLOCK_PAUSED : 0u));
        if (type != CMD_ENABLE && type != CMD_DISABLE) blk |= BLOCK_NOT_ENABLED;
        if (type == CMD_ENABLE) blk = 0;
        if (blk && type != CMD_DISABLE) nack(type, seq, ST_E_STATE, blk);
        else nack(type, seq, ST_E_INTERNAL, INTERNAL_NOT_IN_BUILD);
    }
    }
}

/* ------------------------------------------------------------------ RX parser (ICD §2.3) */
static uint8_t rb[512]; static unsigned rn; static uint32_t r_last_ms;
static void parse(void)
{
    for (;;) {
        unsigned i = 0;
        while (i < rn && !(rb[i] == PROTO_SYNC0 && (i + 1u >= rn || rb[i + 1] == PROTO_SYNC1))) i++;
        if (i) { memmove(rb, rb + i, rn - i); rn -= i; }
        if (rn < 6u) return;
        uint16_t len = get16(rb + 4);
        if (len > PROTO_MAX_LEN) { memmove(rb, rb + 1, --rn); continue; }
        if (rn < 8u + len) return;
        if (crc16(rb + 2, 4u + len) != get16(rb + 6 + len)) { rx_crc++; memmove(rb, rb + 1, --rn); continue; }
        rx_ok++;
        dispatch(rb[2], rb[3], rb + 6, len);
        memmove(rb, rb + 8 + len, rn - 8u - len); rn -= 8u + len;
    }
}

/* ------------------------------------------------------------------ seam callbacks */
void on_afe_sample(const afe_sample_t *s)
{
    raw_last = s->raw;
    if (!stream_on) return;
    uint8_t pl[18];
    put32(pl, s->t_us); pl[4] = PROTO_PAYLOAD_VERSION;
    pl[5] = (uint8_t)(data_flags(s->t_us) | (overrun_next ? DF_OVERRUN : 0u));
    put32(pl + 6, (uint32_t)s->raw); put32(pl + 10, 0); put16(pl + 14, frame_seq); put16(pl + 16, data_status());
    uint16_t fs = frame_seq++;
    bool had_ovr = overrun_next; overrun_next = false;
    send(ASYNC_DATA, (uint8_t)fs, pl, 18, HAL_TX_DATA);
    (void)had_ovr;
}

void core_tick_1ms(void)
{
    ticks++;
    /* stop sniffer: STOP / HALT / PAUSE motion part on new RX bytes (FW_design §5.9.3, probe version) */
    uint8_t nb[64]; size_t n;
    while ((n = hal_uart_peek(nb, sizeof nb, &sniff_cursor)) > 0) {
        for (size_t i = 0; i < n; i++) {
            if (sniff_n == sizeof sniff_buf) { memmove(sniff_buf, sniff_buf + 1, sizeof sniff_buf - 1u); sniff_n--; }
            sniff_buf[sniff_n++] = nb[i];
            for (unsigned L = 8; L <= 9; L++) {
                if (sniff_n < L) continue;
                const uint8_t *f = sniff_buf + sniff_n - L;
                if (f[0] != PROTO_SYNC0 || f[1] != PROTO_SYNC1 || get16(f + 4) != L - 8u) continue;
                if (!((L == 9 && f[2] == CMD_STOP && f[6] <= 1u) || (L == 8 && (f[2] == CMD_HALT || f[2] == CMD_PAUSE)))) continue;
                if (crc16(f + 2, L - 4u) != get16(f + L - 2u)) continue;
                (void)hal_step_stop_now();
            }
        }
    }
}

step_next_t step_isr(void)
{
    step_next_t r = { 0, false, false };
    if (probe_steps_left) probe_steps_left--;
    if (probe_steps_left <= 1u) r.last = true;       /* the period just started is the last one */
    if (probe_steps_left == 0u) r.stop = true;
    r.period = probe_period;
    return r;
}
void on_input_edge(uint8_t id, bool level, uint32_t t_us) { (void)id; (void)level; (void)t_us; }

/* ------------------------------------------------------------------ app */
void app_init(void)
{
    params_set_defaults(&P);
    int k = newest_slot();
    if (k >= 0 && apply_record(slot_ptr((unsigned)k))) { have_record = true; nvm_seq = get32(slot_ptr((unsigned)k) + 4); }
    else nvm_defaulted = true;
    P_boot = P;
    hal_step_cfg_t sc = { 900u, 1800u, P.motion.pul_invert, P.motion.ena_invert };
    (void)hal_step_init(&sc);
    hal_ena_set(true);
    hal_in_cfg_t ic; memset(&ic, 0, sizeof ic); ic.stop_active_level = P.io.stop_active_level; ic.pause_active_level = P.io.pause_active_level;
    hal_inputs_config(&ic);
    hal_hx711_config(25, P.afe.rate_sps != 0);
    event(EV_BOOT, hal_reset_cause(), 0);
    if (hal_clk_fallback()) event(EV_CLK_FALLBACK, 0, 0);
    if (have_record) event(EV_PARAMS_LOADED, 0, 0); else event(EV_PARAMS_DEFAULTED, 1, 0);
    hal_wdg_set_timeout(48);
    last_tick_kick = ticks;
}

void app_loop(void)
{
    uint8_t tmp[64]; size_t n;
    uint32_t now_ms = hal_time_ms();
    if (nvm_state == NV_IDLE) {
        while ((n = hal_uart_read(tmp, sizeof tmp < sizeof rb - rn ? sizeof tmp : sizeof rb - rn)) > 0) { memcpy(rb + rn, tmp, n); rn += (unsigned)n; r_last_ms = now_ms; parse(); }
        if (rn && now_ms - r_last_ms > PROTO_INTERBYTE_TIMEOUT_MS) { memmove(rb, rb + 1, --rn); parse(); r_last_ms = now_ms; }
    } else if (nvm_state == NV_QUIESCE && hal_uart_tx_idle()) {
        hal_wdg_set_timeout(3000);
        uint32_t t0 = hal_time_ms();
        int err = nvm_save();
        hal_wdg_set_timeout(48);
        (void)t0;
        hal_hx711_hold(false);
        overrun_next = true;
        nvm_state = NV_IDLE;
        if (err) { event(EV_NVM_ERROR, (uint16_t)err, 0); nack(CMD_SAVE_PARAMS, nvm_seq_resp, ST_E_NVM, (uint16_t)err); }
        else { event(EV_PARAMS_SAVED, 0, (int32_t)nvm_seq); resp(CMD_SAVE_PARAMS, nvm_seq_resp, NULL, 0); }
    }
    events_flush();
    if (reboot_at && hal_uart_tx_idle() && now_ms - reboot_at < 1000u) hal_reset();
    hal_led(((now_ms / 500u) & 1u) != 0u);
    if (ticks != last_tick_kick) { last_tick_kick = ticks; hal_wdg_kick(); }
}
