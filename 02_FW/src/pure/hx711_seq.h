/* HX711 read sequence (FW_design §5.8): 25..27 SCK pulses, MSB first, data sampled after the falling
 * edge, DOUT must be high after the last pulse. IRQs are masked only while SCK is high (target:
 * BASEPRI 0x20 = CRIT_AFE, <= 0.6 us window; NVIC levels 0-1 never masked, FW-AFE-001); SCK low is
 * unmasked.
 *
 * Header-only template: the includer defines the pin / timing macros, so the target inlines them
 * (no call overhead inside the masked window) and the host tests drive a simulated HX711:
 *   HX_SCK_HI()  HX_SCK_LO()  HX_DT()  HX_MASK_ON()  HX_MASK_OFF()  HX_T_HIGH()  HX_T_LOW()
 *   HX_PULSE_BEGIN()  HX_PULSE_END()   (optional measurement hooks, may be empty)
 *
 * Origin: Thrust_Stand_HAW/02_FW/src/pure/hx711_seq.h @37c8747 (D-39; copied unchanged except this
 * header comment; the TS shim masked BASEPRI_ESTOP, here the includer chooses the mask macro).
 * Implements: FW-AFE-001
 */
#ifndef PURE_HX711_SEQ_H
#define PURE_HX711_SEQ_H

#include <stdbool.h>
#include <stdint.h>

#ifndef HX_PULSE_BEGIN
#define HX_PULSE_BEGIN()
#endif
#ifndef HX_PULSE_END
#define HX_PULSE_END()
#endif

/** Clock out one conversion. Returns the 24-bit code; *dout_high = DOUT level after the last pulse
 *  (must be 1: the HX711 pulls DOUT high at the 25th pulse). pulses = 25 / 26 / 27. */
static inline uint32_t hx711_shift_in(uint8_t pulses, bool *dout_high)
{
    uint32_t v = 0u;
    uint8_t i;
    for (i = 0u; i < pulses; i++) {
        HX_PULSE_BEGIN();
        HX_MASK_ON();
        HX_SCK_HI();
        HX_T_HIGH();
        HX_SCK_LO();
        HX_MASK_OFF();
        HX_PULSE_END();
        HX_T_LOW();
        if (i < 24u) {
            v = (v << 1) | (HX_DT() ? 1u : 0u);
        }
    }
    *dout_high = HX_DT() ? true : false;
    return v & 0xFFFFFFu;
}

#endif
