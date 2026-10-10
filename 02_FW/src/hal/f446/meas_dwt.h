/* DWT section statistics of the measurement image nucleo_f446re_meas_dwt (OI-FW-37; FW_test_plan §6.3
 * "Exception HW_MEAS_DWT", MT-1; ICD Appendix C op 9 DWT). Every macro is empty unless HW_MEAS_DWT,
 * so the release and nucleo_f446re_meas objects are unchanged (TC-SYS-009-02); in the DWT image the
 * handlers, the CRIT_* sections (sys_f4.c) and the stop primitives take a CYCCNT stamp at entry and
 * record the cycles at exit into a RAM-resident statistics table (meas_f4.c: count, min, max, 64-bit
 * sum, 10-bin histogram per section). The record call runs after the end stamp (not part of the
 * section's own figure); an outer section that contains an inner recorded one includes the inner
 * record call (section MDWT_REC_CALL, calibrated at boot). INFO w6 = the empty stamp pair (cycles).
 * Histogram bins (cycles c): bin 0 c < 512, bin k (1...8) 2^(k+8) <= c < 2^(k+9), bin 9 c >= 2^17.
 * Implements: NFR-007 / NFR-006 measurement means (HG-18), SYS-009
 */
#ifndef HAL_F446_MEAS_DWT_H
#define HAL_F446_MEAS_DWT_H

#include <stdint.h>

/* section numbers = DIAG_MEAS op 9 `a` (proposed for ICD Appendix C, table meas_dwt_section) */
#define MDWT_MAIN_LOOP      0u   /* one app_loop() pass (main.cpp) */
#define MDWT_ISR_STEP       1u   /* TIM2 update ISR (step count + step_isr) */
#define MDWT_ISR_ESTOP      2u   /* EXTI15_10 E-stop handler */
#define MDWT_ISR_LIM_START  3u   /* EXTI0 START limit handler */
#define MDWT_ISR_LIM_END    4u   /* EXTI1 END limit handler */
#define MDWT_ISR_PAUSE      5u   /* EXTI9_5 PAUSE handler */
#define MDWT_ISR_HX711      6u   /* EXTI4 data-ready handler (read + on_afe_sample) */
#define MDWT_ISR_TICK       7u   /* TIM5 1 kHz tick (core_tick_1ms) */
#define MDWT_ISR_UART       8u   /* USART2 error IRQ */
#define MDWT_ISR_DMA_RX     9u   /* DMA1 stream 5 (RX ring lap) */
#define MDWT_ISR_DMA_TX     10u  /* DMA1 stream 6 (TX complete -> next frame) */
#define MDWT_CRIT_HALT      11u  /* CRIT_HALT (PRIMASK) window */
#define MDWT_CRIT_AFE       12u  /* CRIT_AFE (BASEPRI 0x20) window */
#define MDWT_CRIT_MOTION    13u  /* CRIT_MOTION (BASEPRI 0x20) window */
#define MDWT_CRIT_DATA      14u  /* CRIT_DATA (BASEPRI 0x30) window */
#define MDWT_CRIT_TICK      15u  /* CRIT_TICK (BASEPRI 0x40) window */
#define MDWT_STOP_NOW       16u  /* hal_step_stop_now() PRIMASK window */
#define MDWT_ABORT          17u  /* hal_step_abort() PRIMASK window */
#define MDWT_SET_NOW        18u  /* hal_step_set_period_now() PRIMASK window */
#define MDWT_HX_SCK_HIGH    19u  /* longest SCK-high of one HX711 read (cycles) */
#define MDWT_HX_READ        20u  /* one HX711 shift-in (24 + 1...3 bits, BASEPRI windows inside) */
#define MDWT_EMPTY_PAIR     21u  /* calibration: empty stamp pair (16 at boot) */
#define MDWT_REC_CALL       22u  /* calibration: one record call (16 at boot) */
#define MDWT_ISR_ESTOP_CB   23u  /* EXTI3 deferred E-stop core callback (level 1, v0.8) */
#define MDWT_CALIB_SCRATCH  31u  /* internal: target of the calibration record calls (kept 0) */
#define MDWT_SECTIONS       32u

#if defined(HW_MEAS_DWT) && HW_MEAS_DWT
#ifdef __cplusplus
extern "C" {
#endif
void meas_dwt_rec(uint32_t sec, uint32_t cycles);          /* meas_f4.c, .RamFunc */
#ifdef __cplusplus
}
#endif
#define MDWT_T0(v)         const uint32_t v = DWT->CYCCNT
#define MDWT_END(sec, v)   meas_dwt_rec((sec), DWT->CYCCNT - (v))
#define MDWT_VAL(sec, x)   meas_dwt_rec((sec), (x))
#else
#define MDWT_T0(v)         ((void)0)
#define MDWT_END(sec, v)   ((void)0)
#define MDWT_VAL(sec, x)   ((void)0)
#endif

#endif /* HAL_F446_MEAS_DWT_H */
