/* FW host twin — model of the HW_MEAS diagnostic seam (seam v1.3 hal_meas_cmd, ICD v0.6 Appendix C).
 * Owner: Integrator. Implements: REQ-C-M2-08 (Validator E dry runs of hil_*.py before the HW gate), D-40c.
 *
 * Without --hw-meas 1 the seam answers 0 (= no HW_MEAS in this build, like the release image): the core
 * then reports FEAT_HW_MEAS = 0 and answers DIAG_MEAS with E_INTERNAL NOT_IN_BUILD.
 * With --hw-meas 1 it models the measurement build from the twin's own edge / input / sample log:
 *   MT-2 counter  = rising PUL edges (electrical, after pul_invert) since the last COUNTER reset
 *   MT-3 probe    = probe timer at 180 MHz / (PSC + 1), 16 bit, started (TRIGGER) / restarted (RESET) by the
 *                   selected event source, captures of the last PUL rising edge, ENA and DIR edges after the
 *                   event; PWM_INPUT = min / max PUL period and high time (probe ticks) since arming
 *   MT-4 stamps   = t_us rings (4096 per channel) of EVT (selected source), PUL (rising), DIR, AUX (unused: 0)
 *   .noinit       = last PUL t_us, heartbeat (t_us now), hang start t_us; survives a twin reset (twin.py
 *                   passes it back with --noinit)
 *   STIM_RUN      = forwarded to twin.py ("M stim ..."), which toggles the selected input at seeded times
 *   HANG          = the twin's hang model (main / tick / isr1) for a duration
 *   STATIC_LEVEL  = PUL / DIR output driven to the level (edge log), released by the next DIAG_MEAS or
 *                   hal_step_start / hal_ena_set
 *   DWT           = not modelled (w0 = 0)
 * The request was validated by the core (LEN, op / sel / a / b ranges, MEAS_STATE) before this call.
 */
#include <string.h>

#include "twin_internal.h"

#define RING 4096u
#define PROBE_HZ 180000000.0

typedef struct { uint32_t v[RING]; uint32_t n; } ring_t;

static struct {
    bool on;
    uint32_t pul_count;
    ring_t ch[4];                                /* EVT, PUL, DIR, AUX */
    /* probe */
    bool armed, triggered, ovf, before;
    uint8_t src; uint8_t mode; bool falling; uint32_t psc;
    vt_t t_evt;
    uint32_t ccr2, ccr3, ccr4, pul_caps;
    vt_t last_rise, last_fall; bool have_rise;
    uint32_t pwm_min_p, pwm_max_p, pwm_min_h, pwm_max_h, pwm_n;
    /* noinit */
    uint32_t ni_magic, ni_last_pul, ni_hang;
    /* static level */
    int static_pin;                               /* -1 none */
} M;

void tw_meas_init(bool on, uint32_t ni_magic, uint32_t ni_last_pul, uint32_t ni_hang)
{
    memset(&M, 0, sizeof M);
    M.on = on;
    M.static_pin = -1;
    M.ni_magic = ni_magic; M.ni_last_pul = ni_last_pul; M.ni_hang = ni_hang;
}

void tw_meas_noinit_out(void)                     /* for twin.py at a reset: carried into the next boot */
{
    tw_out("N %lx %lu %lu", (unsigned long)M.ni_magic, (unsigned long)M.ni_last_pul, (unsigned long)M.ni_hang);
}

static void push(unsigned ch, uint32_t t_us) { M.ch[ch].v[M.ch[ch].n % RING] = t_us; M.ch[ch].n++; }

static uint32_t probe_ticks(vt_t t)
{
    double ticks = (double)(t - M.t_evt) * 1e-9 * PROBE_HZ / (double)(M.psc + 1u);
    if (ticks >= 65536.0) { M.ovf = true; return 0; }
    return (uint32_t)ticks;
}

static void event(void)
{
    push(0, tw_fw_us(T.now));
    if (!M.armed || M.mode == 2u) return;
    if (M.mode == 0u && M.triggered) return;          /* TRIGGER: single shot */
    M.triggered = true; M.t_evt = T.now; M.ovf = false;
    M.ccr2 = M.ccr3 = M.ccr4 = 0; M.pul_caps = 0;
}

/* hooks from the engine */
void tw_meas_edge(const char *pin, int level)
{
    if (!M.on) return;
    uint32_t t = tw_fw_us(T.now);
    if (!strcmp(pin, "PUL")) {
        if (level) {
            M.pul_count++; push(1, t); M.ni_last_pul = t;
            if (M.armed && M.mode == 2u) {
                if (M.have_rise) {
                    uint32_t p = (uint32_t)((double)(T.now - M.last_rise) * 1e-9 * PROBE_HZ / (double)(M.psc + 1u));
                    if (!M.pwm_n || p < M.pwm_min_p) M.pwm_min_p = p;
                    if (p > M.pwm_max_p) M.pwm_max_p = p;
                }
                M.last_rise = T.now; M.have_rise = true;
            } else if (M.armed && M.triggered) {
                uint32_t c = probe_ticks(T.now);
                if (!M.ovf) { M.ccr2 = c; M.pul_caps++; }
            } else if (M.armed && !M.triggered) {
                M.before = true;
            }
        } else if (M.armed && M.mode == 2u && M.have_rise) {
            uint32_t h = (uint32_t)((double)(T.now - M.last_rise) * 1e-9 * PROBE_HZ / (double)(M.psc + 1u));
            if (!M.pwm_n || h < M.pwm_min_h) M.pwm_min_h = h;
            if (h > M.pwm_max_h) M.pwm_max_h = h;
            M.pwm_n++;
        }
    } else if (!strcmp(pin, "DIR")) {
        push(2, t);
        if (M.armed && M.triggered && M.mode != 2u) { uint32_t c = probe_ticks(T.now); if (!M.ovf) M.ccr4 = c; }
        if (M.armed && M.src == 7u && (level ? !M.falling : M.falling)) event();
    } else if (!strcmp(pin, "ENA")) {
        if (M.armed && M.triggered && M.mode != 2u) { uint32_t c = probe_ticks(T.now); if (!M.ovf) M.ccr3 = c; }
    }
}

void tw_meas_input(uint8_t id, uint8_t level)    /* electrical input change */
{
    static const int8_t src_of_in[8] = {0, 1, 2, -1, 3, -1, -1, 5};   /* IN_* -> meas_src */
    if (!M.on || id >= 8u || src_of_in[id] < 0 || !M.armed || (uint8_t)src_of_in[id] != M.src) return;
    if (level ? !M.falling : M.falling) event();
}

void tw_meas_dout(void) { if (M.on && M.armed && M.src == 4u) event(); }          /* DOUT ready */
void tw_meas_rx(void) { if (M.on && M.armed && M.src == 6u) event(); }            /* RX byte */
void tw_meas_stim_edge(uint8_t level) { if (M.on && M.armed && M.src == 8u && (level ? !M.falling : M.falling)) event(); }

void tw_meas_release_static(void)
{
    if (M.static_pin < 0) return;
    tw_out("L %llu meas_static_release %d", (unsigned long long)T.now, M.static_pin);
    tw_edge(M.static_pin == 0 ? "PUL" : "DIR", 0);
    M.static_pin = -1;
}

static void put32(uint8_t *b, unsigned i, uint32_t v) { b[4 * i] = (uint8_t)v; b[4 * i + 1] = (uint8_t)(v >> 8); b[4 * i + 2] = (uint8_t)(v >> 16); b[4 * i + 3] = (uint8_t)(v >> 24); }

size_t hal_meas_cmd(const uint8_t *req, size_t n, uint8_t *resp, size_t max)
{
    if (!M.on) return 0;
    if (n < 8u || max < 64u) return 0;
    uint8_t op = req[0], sel = req[1];
    uint16_t a = (uint16_t)(req[2] | req[3] << 8);
    uint32_t b = (uint32_t)req[4] | (uint32_t)req[5] << 8 | (uint32_t)req[6] << 16 | (uint32_t)req[7] << 24;
    memset(resp, 0, 64);
    tw_out("L %llu hal_meas_cmd op=%u sel=%u a=%u b=%lu", (unsigned long long)T.now, op, sel, a, (unsigned long)b);
    if (op != 8u) tw_meas_release_static();
    switch (op) {
    case 0:                                                      /* INFO */
        put32(resp, 0, 0x1u | 0x4u); put32(resp, 1, 180000000u); put32(resp, 2, 32u); put32(resp, 3, 1000000u);
        put32(resp, 4, RING); put32(resp, 5, 0u); put32(resp, 6, 0u); put32(resp, 7, 90000000u);
        break;
    case 1:                                                      /* PROBE_ARM */
        M.armed = true; M.triggered = false; M.ovf = false; M.before = false; M.have_rise = false;
        M.src = sel; M.mode = (uint8_t)(a & 3u); M.falling = (a & 0x100u) != 0u; M.psc = b;
        M.ccr2 = M.ccr3 = M.ccr4 = 0; M.pul_caps = 0; M.pwm_n = 0;
        M.pwm_min_p = M.pwm_max_p = M.pwm_min_h = M.pwm_max_h = 0;
        break;
    case 2: {                                                    /* PROBE_READ */
        uint32_t fl = (M.armed ? 1u : 0u) | (M.triggered ? 2u : 0u) | (M.ovf ? 8u : 0u) | (M.before ? 16u : 0u);
        put32(resp, 0, fl); put32(resp, 1, 0u); put32(resp, 2, M.ccr2); put32(resp, 3, M.ccr3); put32(resp, 4, M.ccr4);
        put32(resp, 5, M.pul_caps); put32(resp, 6, M.triggered && !M.ovf ? probe_ticks(T.now) : 0u); put32(resp, 7, M.psc);
        put32(resp, 8, M.pwm_min_p); put32(resp, 9, M.pwm_max_p); put32(resp, 10, M.pwm_min_h);
        put32(resp, 11, M.pwm_max_h); put32(resp, 12, M.pwm_n);
        break;
    }
    case 3:                                                      /* COUNTER */
        put32(resp, 0, M.pul_count); put32(resp, 1, tw_fw_us(T.now));
        if (sel == 1u) M.pul_count = 0;
        break;
    case 4: {                                                    /* STAMPS: newest first */
        ring_t *r = &M.ch[sel & 3u];
        put32(resp, 0, r->n); put32(resp, 1, RING);
        for (unsigned j = 0; j < 14u; j++) {
            uint64_t back = (uint64_t)a * 14u + j;
            if (back < r->n && back < RING) put32(resp, 2u + j, r->v[(r->n - 1u - (uint32_t)back) % RING]);
        }
        break;
    }
    case 5:                                                      /* NOINIT */
        put32(resp, 0, M.ni_magic); put32(resp, 1, M.ni_last_pul); put32(resp, 2, tw_fw_us(T.now)); put32(resp, 3, M.ni_hang);
        if (sel == 1u) { M.ni_magic = 0x4D454153u; M.ni_last_pul = 0; M.ni_hang = 0; }
        break;
    case 6:                                                      /* STIM_RUN -> twin.py schedules the pulses */
        tw_out("M stim %u %u %u %lu %u %lu", (unsigned)M.src, (unsigned)(sel & 1u), (unsigned)(sel >> 1), (unsigned long)a,
               (unsigned)(T.period_cur ? T.period_cur : 90000u), (unsigned long)b);
        break;
    case 7: {                                                    /* HANG */
        vt_t until = a ? T.now + (vt_t)a * 1000000ull : VT_NEVER;
        M.ni_hang = tw_fw_us(T.now);
        if (sel == 0u) T.hang_main_until = until; else if (sel == 1u) T.hang_tick_until = until; else T.storm_until = until;
        break;
    }
    case 8:                                                      /* STATIC_LEVEL */
        tw_meas_release_static();
        M.static_pin = sel;
        tw_edge(sel == 0u ? "PUL" : "DIR", a ? 1 : 0);
        break;
    case 9:                                                      /* DWT: not modelled */
    default:
        break;
    }
    return 64u;
}
