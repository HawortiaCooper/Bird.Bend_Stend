/* FW host twin engine — shared state between the scheduler (twin_engine.c) and the seam
 * implementations (twin_seams.c). Owner: Integrator (00_System/tools/fw_twin).
 * Implements: SYS-008, D-07 (lean twin, R3 §3.6: no register emulation, seams only).
 *
 * Time: one virtual world clock in ns (`T.now`), advanced only by the launcher (twin.py) with
 * "A <until_ns>" lines. Lock-step: twin.py advances on request; realtime: twin.py advances it in
 * step with the wall clock. The FW sees hal_time_us() = t0_us + (now - boot_ns) / 1000 (mod 2^32).
 * Execution rule (FW_design §8.2): ISR callbacks run atomically at their virtual time in NVIC
 * level order (0 E-stop, 1 inputs, 2 step, 3 sample, 4 tick, 5 link); the main loop runs once after
 * every virtual instant at which something happened.
 */
#ifndef TWIN_INTERNAL_H
#define TWIN_INTERNAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef uint64_t vt_t;                      /* virtual world time, ns */
#define VT_NEVER UINT64_MAX

/* 921 600 Bd 8N1 = 92 160 B/s; end of byte i (0-based) of a burst starting at t0 (twin.py: same formula) */
#define BYTES_PER_S 92160ull
static inline vt_t byte_end(vt_t t0, uint64_t i) { return t0 + ((i + 1u) * 1000000000ull + BYTES_PER_S - 1u) / BYTES_PER_S; }

#define TXF_MAX 168u
#define RX_RING 2048u                          /* FW_design §2.5 RX_RING_BYTES */
#define FLASH_BASE 0x08004000u                 /* NVM sectors 1 + 2 (FW_design §5.11) */
#define FLASH_SIZE 0x8000u
#define FLASH_SECTOR_SIZE 0x4000u
#define FLASH_ERASE_NS 500000000ull            /* twin erase model 500 ms (FW_test_plan TC-NFR-008-01) */
#define FLASH_WORD_NS 16000ull                 /* 16 us per 32-bit word */
#define F_TICK_HZ 90000000u                    /* TIM2 tick of the target */

enum { IN_ESTOP = 0, IN_LIM_START = 1, IN_LIM_END = 2, IN_STOP = 3, IN_PAUSE = 4, IN_ALM = 5, IN_PEND = 6,
       IN_DRV_PWR = 7, IN_COUNT = 8 };       /* = ICD IO bit indices = hal_inputs ids */

typedef struct { uint16_t n; uint8_t b[TXF_MAX]; } txframe_t;
typedef struct { txframe_t f[64]; unsigned head, count, max_frames, cap_bytes, used_bytes; } txq_t;

typedef struct {
    /* ---- time ---- */
    vt_t now, boot_ns;
    uint32_t t0_us;
    unsigned in_stall;                       /* > 0 while a flash operation stalls the CPU */
    uint64_t time_calls;                     /* hal_time_* calls in the current main-loop pass */
    bool in_main;
    /* ---- sys ---- */
    uint8_t reset_cause;
    uint8_t uid[12];
    bool hse_fail;
    double lsi_hz;
    bool wdg_armed; vt_t wdg_timeout_ns, wdg_deadline;
    vt_t hang_main_until, hang_tick_until;
    /* ---- uart ---- */
    uint8_t rx_ring[RX_RING]; uint32_t rx_wr, rx_rd, rx_overruns;
    txq_t txq[3];
    bool tx_busy; vt_t tx_end; txframe_t tx_cur;
    bool tx_tc_pending;                      /* TX done during a stall: next frame starts after it */
    vt_t congestion_until;                   /* class D reported full until then (tx_congestion) */
    int break_type;                          /* frame TYPE that ends an advance when it starts (-1 none) */
    bool break_hit;
    /* ---- tick ---- */
    vt_t next_tick; bool tick_pending;
    /* ---- inputs ---- */
    uint8_t lvl[IN_COUNT];                   /* electrical levels (1 = pin high) */
    uint8_t stop_active_level, pause_active_level;   /* enum: 0 OPEN_ACTIVE (high), 1 CLOSED_ACTIVE (low) */
    int lim_forced[2];                       /* -1 = position model, else forced level */
    double lim_pos_um[2];                    /* START switch active at x <= pos, END at x >= pos */
    struct { uint8_t id, level; uint32_t t_us; } deferred[64]; unsigned n_deferred;
    /* ---- outputs ---- */
    int rate_pin, led, trip, ena_level;      /* electrical, -1 = never written */
    bool ena_enabled;
    /* ---- step generator (TIM2 model, FW_design §5.6 / tools README seam table) ---- */
    uint32_t pw_ticks, dir_setup_ticks; bool pul_invert, ena_invert;
    bool step_running, step_high, step_last, step_stop_after, step_uncertain;
    int dir; int32_t count; uint32_t stop_gen;
    uint32_t period_cur, period_pre;         /* ticks */
    vt_t step_rise, step_end;
    bool step_fault_next;
    /* ---- world ---- */
    double spm_world, shift_um;
    /* ---- AFE model ---- */
    bool afe_on, afe_pd, afe_hold, afe_stall, afe_sck_overrun;
    int afe_rate_sps; uint8_t afe_gain_pulses;
    double afe_rate_error, afe_noise, afe_offset, afe_cpn;
    int afe_saturate; unsigned afe_drop_every, afe_miss_next; uint64_t afe_conv_n;
    int32_t afe_script[256]; unsigned afe_script_n, afe_script_i;
    vt_t next_sample; bool sample_pending; struct { uint32_t t_us; int32_t raw; int32_t pos; uint8_t st; } pend_sample;
    int spec_kind; double spec_k, spec_xc, spec_k2, spec_fy, spec_fb; bool spec_broken;
    uint64_t rng;
    /* ---- flash ---- */
    uint8_t flash[FLASH_SIZE];
    char flash_path[512];
    uint32_t flash_writes, flash_erases;
    long cut_after_word; long cut_in_erase;  /* -1 = not armed */
} twin_t;

extern twin_t T;

/* engine services used by the seams */
void tw_out(const char *fmt, ...);           /* one protocol line to stdout */
uint32_t tw_fw_us(vt_t t);
void tw_stall(vt_t ns);                      /* CPU blocked in a flash op for ns of virtual time */
void tw_reset(const char *cause);            /* FW-requested / modelled reset: report and exit */
void tw_tx_kick(void);                       /* start the next TX frame if the line is idle */
void tw_flash_save(void);
void tw_edge(const char *pin, int level);
void tw_step_counted(void);                  /* after every count change: world (limits) update */
double tw_x_um(void);

#endif
