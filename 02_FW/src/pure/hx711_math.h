/* HX711 AFE logic (FW_design §5.8): 24-bit decode, saturation (rail) codes, gain/channel pulse
 * counts, settle-discard counter. The bit-bang sequence itself is in hx711_seq.h (pin macros
 * supplied by the target shim hal/f446/hx711_f4.c or by a host pin model).
 * Origin: Thrust_Stand_HAW/02_FW/src/pure/hx711_math.{h,c} @37c8747 (D-39): hx711_sign_extend,
 * hx711_is_saturated and hx711_pulses copied; the TS dual-channel averaging / invert / ok-window
 * logic is not used here (one AFE, raw samples on the wire, SYS-002) and is replaced by the
 * single-channel settle counter below. Pure C11.
 * Implements: FW-AFE-001 (24-bit decode), FW-AFE-002 (25/27/26 pulses), FW-AFE-003 (settle flagging),
 *             SAF-FW-009 (rail codes)
 */
#ifndef PURE_HX711_MATH_H
#define PURE_HX711_MATH_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HX_CODE_POS_SAT 0x7FFFFFu
#define HX_CODE_NEG_SAT 0x800000u

/** 24-bit two's complement -> int32. */
int32_t hx711_sign_extend(uint32_t raw24);
/** Saturation codes 0x7FFFFF / 0x800000 (rails, SAF-FW-009). */
bool    hx711_is_saturated(uint32_t raw24);
/** A sign-extended sample at a rail (+8 388 607 / -8 388 608). */
bool    hx711_raw_at_rail(int32_t raw);
/** SCK pulses per read for afe.gain_channel (A128 -> 25, B32 -> 26, A64 -> 27); the pulse count of a
 *  read selects the gain/channel of the NEXT conversion. */
uint8_t hx711_pulses(uint8_t gain_channel);

/* settle discard (FW-AFE-003): after power-up, a gain/rate change or a re-init the next n samples
 * are flagged AFE_SETTLING (exactly afe.settle_discard, FW-AFE-003) */
typedef struct {
    uint8_t left;
} hx_settle_t;
void hx_settle_arm(hx_settle_t *s, uint8_t settle_discard);
/** Called per sample: true if this sample is flagged AFE_SETTLING. */
bool hx_settle_take(hx_settle_t *s);

#ifdef __cplusplus
}
#endif

#endif /* PURE_HX711_MATH_H */
