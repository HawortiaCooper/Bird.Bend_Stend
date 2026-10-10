/* Validator E - §16: interface between test_main.c and the two step-HAL instances (tu_core.c, tu_seam.c). */
#ifndef ISRC_API_H
#define ISRC_API_H

#include <stdbool.h>
#include <stdint.h>

#define ISRC_RISE_LOG 4096u

typedef struct {
    uint64_t rise_t[ISRC_RISE_LOG];
    uint32_t rises, done, runts, upd, pw;
    uint64_t rise_at, t;
    int32_t  count;
    bool     running;
    uint32_t stop_gen, cnt, arr, ccr1, cr1, ccmr1;
} isrc_obs_t;

/* probes (test_main.c): the target entry passes the HAL's count; the seam entry reads hal_step_count()
 * itself, after the injection point (old read timing) */
uint64_t val_probe_core(int32_t count);
uint64_t val_probe_seam(int32_t (*count_now)(void));

#define ISRC_DECL(P)                                                                                 \
    void P##_reset(uint32_t c1, uint32_t c2, int32_t count0, int dir, uint32_t pw);                  \
    void P##_run(uint64_t n);                                                                        \
    void P##_advance(uint64_t n);                                                                    \
    void P##_hold(bool on);                                                                          \
    const isrc_obs_t *P##_obs(void);                                                                 \
    int32_t P##_count(void);                                                                         \
    uint32_t P##_cnt_reg(void);                                                                      \
    uint32_t P##_ccr_shadow(void);                                                                   \
    bool P##_uif(void);                                                                              \
    bool P##_stop_now(void);                                                                         \
    bool P##_abort(void);                                                                            \
    void P##_estop(void);                                                                            \
    void P##_set_period(uint32_t p);                                                                 \
    void P##_set_period_now(uint32_t p);                                                             \
    uint32_t P##_cur(void);                                                                          \
    uint32_t P##_pre(void);                                                                          \
    bool P##_late(void);
ISRC_DECL(vcore)
ISRC_DECL(vseam)

#endif
