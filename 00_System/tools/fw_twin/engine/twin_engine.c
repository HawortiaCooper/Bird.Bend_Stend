/* FW host twin — scheduler, world model and the launcher protocol (stdin/stdout lines).
 * Owner: Integrator. Implements: SYS-008, D-07. Driven by 00_System/tools/fw_twin/twin.py.
 *
 * Launcher -> engine (one command per line)
 *   W <key> <args...>      world / injection setting, effective at the current virtual time
 *   R <t0_ns> <hex>        RX bytes: byte i completes at t0 + i * byte time (twin.py paces; times monotone)
 *   K <type>|-1            end the next advance when a frame of this TYPE starts on the wire
 *   G                      boot (after the initial W lines): app_init()
 *   A <until_ns>           run until the virtual time until_ns  -> "D <now_ns>"
 *   Q                      state query -> "Y <key> <value>" lines, then "Y ."
 *   X                      save flash, exit
 * Engine -> launcher
 *   B <boot_ns>  D <now_ns>  W <t> <cls> <ok> <hex> (frame produced)  T <start> <end> <cls> <hex> (on the
 *   wire)  E <t> <pin> <level>  L <t> <seam call ...>  O <t> <output> <level>  S <t> <fw_t_us> <raw> <delivered>
 *   I <t> <id> <level> (electrical input change)  Z <cause> <t> (reset, process exits)  ! <message>
 */
#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "hal_hx711.h"
#include "hal_inputs.h"
#include "hal_step.h"
#include "hal_time.h"
#include "twin_internal.h"

twin_t T;

void app_init(void);   /* FW core entry points (FW_design §3.2, §4.2) */
void app_loop(void);
void twin_step_event(void);
void twin_input_edge(uint8_t id, uint8_t level);
void twin_deliver_deferred(void);
static void load_update(void);

/* ------------------------------------------------------------------ output */
void tw_out(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
    fputc('\n', stdout);
}

uint32_t tw_fw_us(vt_t t) { return (uint32_t)(T.t0_us + (uint32_t)((t - T.boot_ns) / 1000ull)); }

void tw_edge(const char *pin, int level) { tw_out("E %llu %s %d", (unsigned long long)T.now, pin, level); tw_meas_edge(pin, level); }

void tw_flash_save(void)
{
    FILE *f = fopen(T.flash_path, "wb");
    if (!f) { tw_out("! cannot write %s", T.flash_path); return; }
    fwrite(T.flash, 1, FLASH_SIZE, f);
    fclose(f);
}

void tw_reset(const char *cause)
{
    tw_meas_noinit_out();                     /* .noinit survives the reset (DIAG_MEAS model) */
    load_update();                            /* world load states survive an MCU reset (replayed by twin.py) */
    tw_out("V %.9g %.9g %.9g %llu", T.creep_counts, T.relax_n, T.drift_acc + T.drift_cps * (double)(T.now - T.drift_t) / 1e9,
           (unsigned long long)T.now);
    tw_out("Z %s %llu %.6f", cause, (unsigned long long)T.now, tw_x_um());   /* world x persists */
    fflush(stdout);
    tw_flash_save();
    exit(0);
}

/* ------------------------------------------------------------------ world */
/* world position: integrated from PUL + DIR pin (independent of the FW counter, REQ-C-M2-06) */
double tw_x_um(void) { return T.x0_um + (double)T.wsteps * 1000.0 / T.spm_world + T.shift_um; }

static uint8_t limit_level(int k)
{
    if (T.lim_forced[k] >= 0) return (uint8_t)T.lim_forced[k];
    double x = tw_x_um();
    return (uint8_t)(k == 0 ? x <= T.lim_pos_um[0] : x >= T.lim_pos_um[1]);
}

static void set_input(uint8_t id, uint8_t level)
{
    level = level ? 1 : 0;
    if (T.lvl[id] == level) return;
    T.lvl[id] = level;
    tw_out("I %llu %u %u", (unsigned long long)T.now, id, level);
    tw_meas_input(id, level);
    twin_input_edge(id, level);
}

static void update_limits(void)
{
    set_input(IN_LIM_START, limit_level(0));
    set_input(IN_LIM_END, limit_level(1));
}

void tw_step_counted(void) { update_limits(); }

/* ------------------------------------------------------------------ AFE model */
static double rnd_u(void)
{
    T.rng ^= T.rng << 13; T.rng ^= T.rng >> 7; T.rng ^= T.rng << 17;
    return ((double)(T.rng >> 11) + 0.5) / 9007199254740992.0;
}
static double rnd_n(void) { return sqrt(-2.0 * log(rnd_u())) * cos(6.283185307179586 * rnd_u()); }

/* Specimen elastic force (N, + = tension) at the world position; M4 model (tools/README "specimen"):
 * deflection d = x - xc (mm); side pull: only d > 0, push: only d < 0 (force negative), both: either sign;
 * |F| = k|d| (bilinear: fy + k2 (|d| - fy/k) above fy) + k3 |d|^3, never below 0 (a softening k3 saturates
 * at 0); grip slip (once, world state): when |F| first reaches slip_at the contact point moves by slip_um in the
 * deflection direction (the force drops by about k * slip); break (latched, world state) when |F| >= f_break or
 * |d| >= break travel: the force drops to the residual fraction of the intact curve (0 = a clean break). */
static double specimen_elastic(double a)
{
    double f = T.spec_k * a;
    if (T.spec_kind == 2 && T.spec_fy > 0.0 && T.spec_k > 0.0 && f > T.spec_fy) f = T.spec_fy + T.spec_k2 * (a - T.spec_fy / T.spec_k);
    f += T.spec_k3 * a * a * a;
    return f < 0.0 ? 0.0 : f;
}

static double specimen_force_n(void)
{
    if (T.spec_kind == 0) return 0.0;
    double d_mm = (tw_x_um() - T.spec_xc) / 1000.0;
    if (T.spec_side == 0 && d_mm <= 0.0) return 0.0;
    if (T.spec_side == 1 && d_mm >= 0.0) return 0.0;
    double a = fabs(d_mm), sg = d_mm < 0.0 ? -1.0 : 1.0;
    double f = specimen_elastic(a);
    if (!T.spec_slipped && T.spec_slip_at > 0.0 && f >= T.spec_slip_at) {
        T.spec_slipped = true;
        T.spec_xc += sg * T.spec_slip_um;
        tw_out("L %llu specimen_slip %.6f %.6f", (unsigned long long)T.now, sg * f, T.spec_xc);
        d_mm = (tw_x_um() - T.spec_xc) / 1000.0;
        if (d_mm * sg <= 0.0) return 0.0;
        a = fabs(d_mm);
        f = specimen_elastic(a);
    }
    if (!T.spec_broken && ((T.spec_fb > 0.0 && f >= T.spec_fb) || (T.spec_bt > 0.0 && a * 1000.0 >= T.spec_bt))) {
        T.spec_broken = true;
        T.relax_n = 0.0;                                   /* the relaxing material is gone with the break */
        tw_out("L %llu specimen_break %.6f %.3f", (unsigned long long)T.now, sg * f, tw_x_um());
    }
    if (T.spec_broken) return sg * T.spec_res * f;
    return sg * f;
}

/* M3 load model (ICD v0.7.2): force on the cell = specimen (elastic - relaxation) + hung weights; the cell adds
 * creep (first order toward creep_frac x the load counts, tau creep_tau_s), non-linearity (nonlin_frac x FS x
 * 4u(1-u), u = |F| / FS, odd in F) and a linear zero drift (counts/s). States advance with virtual time at every
 * conversion and query; all terms are 0 by default (M1/M2 results unchanged). */
static void load_update(void)
{
    if (T.now <= T.load_t) return;
    double dt = (double)(T.now - T.load_t) / 1e9;
    T.load_t = T.now;
    if (T.relax_frac > 0.0 && T.relax_tau_s > 0.0) {
        double fe = specimen_force_n();
        T.relax_n += (T.relax_frac * fe - T.relax_n) * (1.0 - exp(-dt / T.relax_tau_s));
    } else T.relax_n = 0.0;
    if (T.creep_frac > 0.0 && T.creep_tau_s > 0.0) {
        double fs = specimen_force_n();
        double fc = T.afe_cpn * ((fs != 0.0 ? fs - T.relax_n : 0.0) + T.weight_n);
        T.creep_counts += (T.creep_frac * fc - T.creep_counts) * (1.0 - exp(-dt / T.creep_tau_s));
    } else T.creep_counts = 0.0;
}

static double world_force_n(void)
{
    double fs = specimen_force_n();
    if (fs != 0.0) fs -= T.relax_n;                     /* relaxation acts on either side (M4) */
    return fs + T.weight_n;
}

static double drift_counts(void) { return T.drift_acc + T.drift_cps * (double)(T.now - T.drift_t) / 1e9; }

static int32_t afe_raw(void)
{
    if (T.afe_script_i < T.afe_script_n) return T.afe_script[T.afe_script_i++];
    if (T.afe_saturate > 0) return 8388607;
    if (T.afe_saturate < 0) return -8388608;
    load_update();
    double f = world_force_n();
    double load_c = T.afe_cpn * f;
    if (T.nonlin_frac != 0.0 && T.fs_n > 0.0) {
        double u = fabs(f) / T.fs_n;
        load_c += (f < 0.0 ? -1.0 : 1.0) * T.nonlin_frac * T.afe_cpn * T.fs_n * 4.0 * u * (1.0 - u);
    }
    double v = T.afe_offset + load_c + T.creep_counts + drift_counts()
               + (T.afe_noise > 0.0 ? T.afe_noise * rnd_n() : 0.0);
    double r = v >= 0.0 ? floor(v + 0.5) : -floor(-v + 0.5);       /* round half away from zero */
    if (r > 8388607.0) r = 8388607.0;
    if (r < -8388608.0) r = -8388608.0;
    return (int32_t)r;
}

static vt_t sample_period(void) { return (vt_t)(1e9 / ((double)T.afe_rate_sps * (1.0 + T.afe_rate_error))); }

static bool afe_converting(void) { return T.afe_on && !T.afe_pd && !T.afe_stall; }

static void afe_conversion(void)
{
    T.next_sample = T.now + sample_period();
    T.afe_conv_n++;
    int32_t raw = afe_raw();
    bool deliver = !T.afe_hold;
    bool missed = false;
    if (T.afe_miss_next) { T.afe_miss_next--; deliver = false; missed = true; }
    if (T.afe_drop_every && T.afe_conv_n % T.afe_drop_every == 0u) { deliver = false; missed = true; }
    uint32_t t_us = tw_fw_us(T.now);
    tw_meas_dout();
    /* S <t> <fw_t_us> <raw> <delivered> <gain_pulses> <sps>  (REQ-C-M2-05: gain / channel per conversion) */
    tw_out("S %llu %lu %ld %d %u %d", (unsigned long long)T.now, (unsigned long)t_us, (long)raw, deliver,
           (unsigned)T.afe_gain_pulses, T.afe_rate_sps);
    if (!deliver) { if (missed) T.afe_missed_flag = true; return; }
    T.pend_sample.t_us = t_us; T.pend_sample.raw = raw; T.pend_sample.pos = T.count;
    /* afe_sample_t.status (tools/README seam semantics, protocol.yaml afe_sample_status):
     * bit 0 SCK_OVERRUN, bit 1 MISSED_EDGE (>= 1 DOUT-ready edge missed before this sample) */
    T.pend_sample.st = (uint8_t)((T.afe_sck_overrun ? 1u : 0u) | (T.afe_missed_flag ? 2u : 0u));
    T.afe_sck_overrun = false; T.afe_missed_flag = false;
    T.sample_pending = true;
}

static void deliver_sample(void)
{
    T.sample_pending = false;
    afe_sample_t s;
    memset(&s, 0, sizeof s);
    s.t_us = T.pend_sample.t_us; s.raw = T.pend_sample.raw; s.pos_steps = T.pend_sample.pos; s.status = T.pend_sample.st;
    on_afe_sample(&s);
}

/* ------------------------------------------------------------------ UART TX line */
void tw_tx_kick(void)
{
    if (T.tx_busy || T.in_stall) return;
    for (int c = 0; c < 3; c++) {                         /* wire order D > R > E (ICD §2.4) */
        txq_t *q = &T.txq[c];
        if (!q->count) continue;
        T.tx_cur = q->f[q->head];
        q->head = (q->head + 1u) % 64u; q->count--; q->used_bytes -= T.tx_cur.n;
        T.tx_busy = true;
        T.tx_end = byte_end(T.now, (uint64_t)T.tx_cur.n - 1u);
        static const char hx[] = "0123456789ABCDEF";
        char s[2 * TXF_MAX + 1];
        for (unsigned i = 0; i < T.tx_cur.n; i++) { s[2 * i] = hx[T.tx_cur.b[i] >> 4]; s[2 * i + 1] = hx[T.tx_cur.b[i] & 15]; }
        s[2u * T.tx_cur.n] = 0;
        tw_out("T %llu %llu %d %s", (unsigned long long)T.now, (unsigned long long)T.tx_end, c, s);
        if (T.break_type >= 0 && T.tx_cur.n > 2u && T.tx_cur.b[2] == (uint8_t)T.break_type) T.break_hit = true;
        return;
    }
}

/* ------------------------------------------------------------------ RX bytes (DMA) */
typedef struct { vt_t t; uint8_t b; } rxb_t;
static rxb_t rxq[1u << 16]; static unsigned rxq_head, rxq_count;

static void rx_byte(uint8_t b)
{
    T.rx_ring[T.rx_wr % RX_RING] = b;
    T.rx_wr++;
    tw_meas_rx();
    if (T.rx_wr - T.rx_rd > RX_RING) { T.rx_overruns++; T.rx_rd = T.rx_wr - RX_RING; }
}

/* ------------------------------------------------------------------ scheduler */
static vt_t min_t(vt_t a, vt_t b) { return a < b ? a : b; }

static vt_t next_event(void)
{
    vt_t t = VT_NEVER;
    if (rxq_count) t = min_t(t, rxq[rxq_head].t);
    if (T.tx_busy) t = min_t(t, T.tx_end);
    t = min_t(t, T.next_tick);
    if (afe_converting()) {
        if (T.next_sample == VT_NEVER) T.next_sample = T.now + sample_period();
        t = min_t(t, T.next_sample);
    }
    if (T.step_running) t = min_t(t, T.step_high ? T.step_end : T.step_rise);
    if (T.wdg_armed) t = min_t(t, T.wdg_deadline);
    if (T.storm_until > T.now) t = min_t(t, T.storm_until);
    if (T.main_busy_until > T.now) t = min_t(t, T.main_busy_until);
    return t;
}

/* everything due at exactly T.now; ISR order 0..5 (FW_design §8.2) */
static void dispatch_now(void)
{
    if (T.wdg_armed && T.wdg_deadline <= T.now) tw_reset("iwdg");
    while (rxq_count && rxq[rxq_head].t <= T.now) { rx_byte(rxq[rxq_head].b); rxq_head = (rxq_head + 1u) % (1u << 16); rxq_count--; }
    if (T.step_running && (T.step_high ? T.step_end : T.step_rise) <= T.now) {
        if (T.in_stall) { /* timer HW keeps pulsing; the ISR would be late: not modelled (idle-only flash ops) */ }
        twin_step_event();
    }
    if (afe_converting() && T.next_sample <= T.now) afe_conversion();
    if (T.sample_pending && !T.in_stall && T.now >= T.storm_until) deliver_sample();
    if (T.next_tick <= T.now) {
        while (T.next_tick <= T.now) T.next_tick += 1000000ull;
        T.tick_pending = true;
    }
    if (T.tick_pending && !T.in_stall && T.now >= T.storm_until) {
        T.tick_pending = false;
        if (T.now >= T.hang_tick_until) core_tick_1ms();
    }
    if (T.storm_until && T.now >= T.storm_until) {     /* storm over: pending ISRs / callbacks are taken */
        T.storm_until = 0;
        if (!T.in_stall) twin_deliver_deferred();
    }
    if (T.tx_busy && T.tx_end <= T.now) {
        T.tx_busy = false;
        if (T.in_stall) T.tx_tc_pending = true; else tw_tx_kick();
    }
}

void tw_stall(vt_t ns)
{
    vt_t end = T.now + ns;
    T.in_stall++;
    for (;;) {
        vt_t t = next_event();
        if (t > end) break;
        T.now = t;
        dispatch_now();
    }
    T.now = end;
    T.in_stall--;
    if (!T.in_stall) {
        /* pending interrupts are taken when the flash op ends (deferred core callbacks first) */
        twin_deliver_deferred();
        if (T.tx_tc_pending) { T.tx_tc_pending = false; tw_tx_kick(); }
        if (T.wdg_armed && T.wdg_deadline <= T.now) tw_reset("iwdg");
    }
}

static void run_main(void)
{
    if (T.now < T.hang_main_until || T.now < T.storm_until) return;
    T.in_main = true; T.time_calls = 0;
    app_loop();
    T.in_main = false;
    /* inject loop_load: this pass consumes loop_load_ns of virtual time; ISRs keep running meanwhile, the
     * next pass starts when it is over (REQ-C-M2-02) */
    if (T.loop_load_ns && T.now < T.loop_load_until) T.main_busy_until = T.now + T.loop_load_ns;
    /* interrupts that became pending during a stall inside the pass */
    if (T.sample_pending) deliver_sample();
    if (T.tick_pending) { T.tick_pending = false; if (T.now >= T.hang_tick_until) core_tick_1ms(); }
}

static void run_until(vt_t until)
{
    bool loop_due = true;
    T.break_hit = false;
    for (;;) {
        if (loop_due) {
            run_main();
            loop_due = false;
            if (T.break_hit) return;
        }
        vt_t t = next_event();
        if (t > until) { if (until > T.now) T.now = until; return; }
        if (t > T.now) T.now = t;
        dispatch_now();
        loop_due = T.now >= T.main_busy_until;
    }
}

/* ------------------------------------------------------------------ commands */
static int hexval(int c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }

static void cmd_world(char *args)
{
    char key[32] = {0};
    int off = 0;
    if (sscanf(args, "%31s %n", key, &off) < 1) return;
    char *a = args + off;
    if (!strcmp(key, "in")) { unsigned id, lv; if (sscanf(a, "%u %u", &id, &lv) == 2 && id < IN_COUNT && id != IN_LIM_START && id != IN_LIM_END) set_input((uint8_t)id, (uint8_t)lv); }
    else if (!strcmp(key, "limf")) { int k, v; if (sscanf(a, "%d %d", &k, &v) == 2 && (k == 0 || k == 1)) { T.lim_forced[k] = v; update_limits(); } }
    else if (!strcmp(key, "limpos")) { int k; double v; if (sscanf(a, "%d %lf", &k, &v) == 2 && (k == 0 || k == 1)) { T.lim_pos_um[k] = v; update_limits(); } }
    else if (!strcmp(key, "spm")) { double v; if (sscanf(a, "%lf", &v) == 1 && v > 0) T.spm_world = v; }
    else if (!strcmp(key, "shift")) { double v; if (sscanf(a, "%lf", &v) == 1) { T.shift_um += v; update_limits(); } }
    else if (!strcmp(key, "afe")) {
        char k2[32]; double v;
        if (sscanf(a, "%31s %lf", k2, &v) != 2) return;
        if (!strcmp(k2, "offset")) T.afe_offset = v;
        else if (!strcmp(k2, "noise")) T.afe_noise = v;
        else if (!strcmp(k2, "rate_error")) T.afe_rate_error = v;
        else if (!strcmp(k2, "stall")) { T.afe_stall = v != 0; T.next_sample = VT_NEVER; }
        else if (!strcmp(k2, "saturate")) T.afe_saturate = (int)v;
        else if (!strcmp(k2, "drop_every")) T.afe_drop_every = (unsigned)v;
        else if (!strcmp(k2, "miss_next")) T.afe_miss_next = (unsigned)v;
        else if (!strcmp(k2, "cpn")) T.afe_cpn = v;
        else if (!strcmp(k2, "sck_overrun")) T.afe_sck_overrun = v != 0;
        else if (!strcmp(k2, "seed")) T.rng = (uint64_t)v | 1u;
        else if (!strcmp(k2, "drift")) {                 /* "W afe drift <counts/s> [<acc> <t_ns>]" (replay) */
            double acc; unsigned long long t0;
            if (sscanf(a, "%*s %*f %lf %llu", &acc, &t0) == 2) { T.drift_acc = acc; T.drift_t = (vt_t)t0; }
            else { T.drift_acc = drift_counts(); T.drift_t = T.now; }
            T.drift_cps = v;
        }
        else if (!strcmp(k2, "creep")) { load_update(); double tau; if (sscanf(a, "%*s %*f %lf", &tau) == 1) T.creep_tau_s = tau; T.creep_frac = v; if (v <= 0.0) T.creep_counts = 0.0; }
        else if (!strcmp(k2, "creep_state")) T.creep_counts = v;
        else if (!strcmp(k2, "nonlin")) { double fs; if (sscanf(a, "%*s %*f %lf", &fs) == 1) T.fs_n = fs; T.nonlin_frac = v; }
    }
    else if (!strcmp(key, "specslipped")) { double xc; if (sscanf(a, "%lf", &xc) == 1) { T.spec_xc = xc; T.spec_slipped = true; } }
    else if (!strcmp(key, "specbroken")) { int v; if (sscanf(a, "%d", &v) == 1) T.spec_broken = v != 0; }   /* world state over a reset */
    else if (!strcmp(key, "weight")) { double v; if (sscanf(a, "%lf", &v) == 1) { load_update(); T.weight_n = v; } }
    else if (!strcmp(key, "relax")) {                    /* "W relax <frac> <tau_s> [<state_n>]" */
        double fr, tau, st;
        int n = sscanf(a, "%lf %lf %lf", &fr, &tau, &st);
        if (n >= 2) { load_update(); T.relax_frac = fr; T.relax_tau_s = tau; if (fr <= 0.0) T.relax_n = 0.0; }
        if (n == 3) T.relax_n = st;
    }
    else if (!strcmp(key, "afescript")) {
        char *p = a; long v; int n;
        T.afe_script_n = 0; T.afe_script_i = 0;
        while (T.afe_script_n < 256u && sscanf(p, "%ld%n", &v, &n) == 1) { T.afe_script[T.afe_script_n++] = (int32_t)v; p += n; }
    }
    else if (!strcmp(key, "spec")) {
        load_update();
        T.spec_kind = 0; T.spec_k = T.spec_xc = T.spec_k2 = T.spec_fy = T.spec_fb = 0; T.spec_broken = false;
        T.spec_side = 0; T.spec_k3 = T.spec_bt = T.spec_res = T.spec_slip_at = T.spec_slip_um = 0; T.spec_slipped = false;
        /* "W spec <kind> <k> <xc> <k2> <fy> <fb> [<side> <k3> <break_travel_um> <residual_frac> <slip_at_n> <slip_um>]"
         * (M4 fields optional) */
        sscanf(a, "%d %lf %lf %lf %lf %lf %d %lf %lf %lf %lf %lf", &T.spec_kind, &T.spec_k, &T.spec_xc, &T.spec_k2,
               &T.spec_fy, &T.spec_fb, &T.spec_side, &T.spec_k3, &T.spec_bt, &T.spec_res, &T.spec_slip_at, &T.spec_slip_um);
        T.relax_n = 0.0;
    }
    else if (!strcmp(key, "cong")) { unsigned long long u; if (sscanf(a, "%llu", &u) == 1) T.congestion_until = u; }
    else if (!strcmp(key, "hang")) {
        char w[16]; unsigned long long u;
        if (sscanf(a, "%15s %llu", w, &u) == 2) {
            if (!strcmp(w, "main")) T.hang_main_until = u;
            else if (!strcmp(w, "tick")) T.hang_tick_until = u;
            else if (!strcmp(w, "isr1")) T.storm_until = u;
        }
    }
    else if (!strcmp(key, "stepfault")) T.step_fault_next = true;
    else if (!strcmp(key, "fcut")) {
        char w[16]; long n;
        if (sscanf(a, "%15s %ld", w, &n) == 2) { if (!strcmp(w, "word")) T.cut_after_word = n; else if (!strcmp(w, "erase")) T.cut_in_erase = n; }
    }
    else if (!strcmp(key, "lsi")) { double v; if (sscanf(a, "%lf", &v) == 1 && v > 1000.0) T.lsi_hz = v; }
    else if (!strcmp(key, "dirwiring")) { int v; if (sscanf(a, "%d", &v) == 1) T.dir_wiring_inv = v != 0; }
    else if (!strcmp(key, "x")) { double v; if (sscanf(a, "%lf", &v) == 1) { T.x0_um = v - T.shift_um; T.wsteps = 0; update_limits(); } }
    else if (!strcmp(key, "pendauto")) { unsigned long long lag; int on; if (sscanf(a, "%d %llu", &on, &lag) == 2) { T.pend_auto = on != 0; T.pend_lag_ns = lag; } }
    else if (!strcmp(key, "loopload")) { unsigned long long per, until; if (sscanf(a, "%llu %llu", &per, &until) == 2) { T.loop_load_ns = per; T.loop_load_until = until; } }
    else if (!strcmp(key, "stimedge")) { int v; if (sscanf(a, "%d", &v) == 1) tw_meas_stim_edge((uint8_t)(v != 0)); }
    else if (!strcmp(key, "faultrec")) {                    /* seam v1.2: planted HardFault record */
        unsigned long pc, cf;
        if (sscanf(a, "%lx %lx", &pc, &cf) == 2) { T.fault_rec_valid = true; T.fault_rec_pc = (uint32_t)pc; T.fault_rec_cfsr = (uint32_t)cf; }
    }
    else tw_out("! unknown world key %s", key);
}

static void cmd_rx(char *args)
{
    unsigned long long t0; int off = 0;
    if (sscanf(args, "%llu %n", &t0, &off) < 1) return;
    const char *h = args + off;
    uint64_t i = 0;
    while (h[0] && h[1] && h[0] != '\n' && h[0] != '\r') {
        if (rxq_count >= (1u << 16)) { tw_out("! rx queue full"); return; }
        rxb_t *r = &rxq[(rxq_head + rxq_count) % (1u << 16)];
        r->b = (uint8_t)(hexval(h[0]) << 4 | hexval(h[1]));
        r->t = t0 + (byte_end(0, i) - byte_end(0, 0));      /* t0 = end of the first byte (twin.py paces) */
        rxq_count++; i++; h += 2;
    }
}

static void cmd_query(void)
{
    tw_out("Y now %llu", (unsigned long long)T.now);
    tw_out("Y fw_t_us %lu", (unsigned long)tw_fw_us(T.now));
    tw_out("Y x_um_true %.3f", tw_x_um());
    tw_meas_noinit_y();
    tw_out("Y pos_steps %ld", (long)T.count);
    tw_out("Y world_steps %lld", (long long)T.wsteps);
    tw_out("Y dir_level %d", T.dir_level);
    tw_out("Y step_running %d", T.step_running);
    tw_out("Y pos_uncertain %d", T.step_uncertain);
    tw_out("Y stop_gen %lu", (unsigned long)T.stop_gen);
    tw_out("Y inputs %u", hal_inputs_raw());
    tw_out("Y ena_level %d", T.ena_level);
    tw_out("Y ena_enabled %d", T.ena_enabled);
    tw_out("Y rate_pin %d", T.rate_pin);
    tw_out("Y led %d", T.led);
    tw_out("Y trip %d", T.trip);
    tw_out("Y afe_rate_sps %d", T.afe_rate_sps);
    tw_out("Y afe_gain_pulses %u", T.afe_gain_pulses);
    tw_out("Y afe_conversions %llu", (unsigned long long)T.afe_conv_n);
    tw_out("Y afe_hold %d", T.afe_hold);
    load_update();
    tw_out("Y load_n %.6f", world_force_n());
    tw_out("Y spec_n %.6f", specimen_force_n());
    tw_out("Y spec_broken %d", T.spec_broken ? 1 : 0);
    tw_out("Y weight_n %.6f", T.weight_n);
    tw_out("Y relax_n %.6f", T.relax_n);
    tw_out("Y creep_counts %.6f", T.creep_counts);
    tw_out("Y drift_counts %.6f", drift_counts());
    tw_out("Y load_raw_ideal %.3f", T.afe_offset + T.afe_cpn * world_force_n() + T.creep_counts + drift_counts());
    tw_out("Y rx_overruns %lu", (unsigned long)T.rx_overruns);
    tw_out("Y rx_pending %u", T.rx_wr - T.rx_rd);
    tw_out("Y flash_writes %lu", (unsigned long)T.flash_writes);
    tw_out("Y flash_erases %lu", (unsigned long)T.flash_erases);
    tw_out("Y wdg_armed %d", T.wdg_armed);
    tw_out("Y wdg_timeout_ns %llu", (unsigned long long)T.wdg_timeout_ns);
    tw_out("Y tx_idle %d", !T.tx_busy && !T.txq[0].count && !T.txq[1].count && !T.txq[2].count);
    tw_out("Y .");
}

static void init_defaults(void)
{
    memset(&T, 0, sizeof T);
    T.lsi_hz = 32000.0;
    T.rate_pin = -1; T.led = -1; T.trip = -1; T.ena_level = -1;
    T.lim_forced[0] = T.lim_forced[1] = -1;
    T.lim_pos_um[0] = -1500.0; T.lim_pos_um[1] = 301000.0;   /* scenario defaults (tools/README) */
    T.spm_world = 800.0;
    T.afe_on = true; T.afe_rate_sps = 80;                    /* RATE external pull-up: 80 SPS from reset */
    T.afe_cpn = 3285.0; T.afe_offset = 50000.0; T.afe_noise = 0.0;
    T.fs_n = 1961.33;                                        /* 200 kg cell (R2 §3) */
    T.next_sample = VT_NEVER;
    T.rng = 0x9E3779B97F4A7C15ull;
    T.break_type = -1;
    T.cut_after_word = -1; T.cut_in_erase = -1;
    T.reset_cause = 1;                                       /* RST_POWER_ON */
    T.txq[0].max_frames = 2;  T.txq[0].cap_bytes = 2u * 26u;    /* TX_D_SLOTS (FW_design §2.5) */
    T.txq[1].max_frames = 64; T.txq[1].cap_bytes = 1024u;       /* TX_R_BYTES */
    T.txq[2].max_frames = 16; T.txq[2].cap_bytes = 16u * 24u;   /* TX_E_SLOTS */
    /* electrical idle levels: E-stop closed (low), limits inactive (low), STOP not modelled (D-36: low),
     * PAUSE NO released (high), ALM OK (low), PEND in position (high), DRV_PWR present (low) */
    T.lvl[IN_PAUSE] = 1; T.lvl[IN_PEND] = 1;
    T.dir_level = -1;
    memcpy(T.uid, "TWIN-UID-001", 12);
    strcpy(T.flash_path, "flash.bin");
}

int main(int argc, char **argv)
{
    static char obuf[1 << 20];
    setvbuf(stdout, obuf, _IOFBF, sizeof obuf);
    init_defaults();
    for (int i = 1; i + 1 < argc; i += 2) {
        const char *k = argv[i], *v = argv[i + 1];
        if (!strcmp(k, "--boot-ns")) T.boot_ns = strtoull(v, NULL, 0);
        else if (!strcmp(k, "--t0-us")) T.t0_us = (uint32_t)strtoull(v, NULL, 0);
        else if (!strcmp(k, "--reset-cause")) T.reset_cause = (uint8_t)atoi(v);
        else if (!strcmp(k, "--flash")) { strncpy(T.flash_path, v, sizeof T.flash_path - 1); }
        else if (!strcmp(k, "--uid")) { for (int j = 0; j < 12 && v[2 * j] && v[2 * j + 1]; j++) T.uid[j] = (uint8_t)(hexval(v[2 * j]) << 4 | hexval(v[2 * j + 1])); }
        else if (!strcmp(k, "--hse-fail")) T.hse_fail = atoi(v) != 0;
        else if (!strcmp(k, "--lsi")) T.lsi_hz = atof(v);
        else if (!strcmp(k, "--hw-meas")) T.hw_meas = atoi(v) != 0;
        else if (!strcmp(k, "--noinit")) {                 /* "<magic_hex>:<w>:...": up to 9 words (twin_meas.c) */
            unsigned long w[9] = {0};
            int n = sscanf(v, "%lx:%lu:%lu:%lu:%lu:%lu:%lu:%lu:%lu", &w[0], &w[1], &w[2], &w[3], &w[4], &w[5], &w[6], &w[7], &w[8]);
            for (int j = 0; j < 9; j++) T.ni[j] = (j < n) ? (uint32_t)w[j] : 0u;
        }
        else if (!strcmp(k, "--fault-rec")) {          /* "<pc_hex>:<cfsr_hex>" (seam v1.2) */
            unsigned long pc, cf;
            if (sscanf(v, "%lx:%lx", &pc, &cf) == 2) { T.fault_rec_valid = true; T.fault_rec_pc = (uint32_t)pc; T.fault_rec_cfsr = (uint32_t)cf; }
        }
        else if (!strcmp(k, "--seed")) T.rng = strtoull(v, NULL, 0) | 1u;
    }
    tw_meas_init(T.hw_meas, T.ni);
    memset(T.flash, 0xFF, sizeof T.flash);
    FILE *f = fopen(T.flash_path, "rb");
    if (f) { size_t n = fread(T.flash, 1, FLASH_SIZE, f); (void)n; fclose(f); }
    T.now = T.boot_ns;
    T.load_t = T.boot_ns;
    T.next_tick = T.boot_ns + 1000000ull;
    T.hang_main_until = 0; T.hang_tick_until = 0;

    static char line[1 << 17];
    bool booted = false;
    while (fgets(line, sizeof line, stdin)) {
        char c = line[0];
        char *args = line[1] == ' ' ? line + 2 : line + 1;
        switch (c) {
        case 'W': cmd_world(args); break;
        case 'R': cmd_rx(args); break;
        case 'K': T.break_type = atoi(args); break;
        case 'G':
            if (!booted) {
                booted = true;
                app_init();
                tw_out("B %llu", (unsigned long long)T.boot_ns);
            }
            break;
        case 'A': {
            vt_t until = strtoull(args, NULL, 0);
            if (booted && until >= T.now) run_until(until);
            tw_out("D %llu", (unsigned long long)T.now);
            break;
        }
        case 'Q': cmd_query(); break;
        case 'X': tw_flash_save(); fflush(stdout); return 0;
        default: break;
        }
        if (c == 'A' || c == 'Q' || c == 'G' || c == 'X') fflush(stdout);
    }
    tw_flash_save();
    return 0;
}
