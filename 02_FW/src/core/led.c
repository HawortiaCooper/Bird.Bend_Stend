/* Status LED LD2 (FW_design §5.12): slow blink = NOT_ENABLED, double blink = a latch is active
 * (HALT, PAUSED, ESTOP, FAULT), triple blink = CLK_FALLBACK. M1 never leaves NOT_ENABLED.
 * Implements: FW-PLT-001 (board status indication)
 */
#include "fw.h"

#include "hal_outputs.h"

void led_service(uint32_t now_ms)
{
    uint32_t ph = now_ms % 2000u;
    bool on;
    bool latched = g_fw.lat.halt || g_fw.lat.paused || g_fw.lat.estop || g_fw.lat.faults != 0u;
    if (g_fw.clk_fallback) {
        on = ph < 900u && (ph % 300u) < 150u;                  /* triple blink */
    } else if (latched) {
        on = ph < 600u && (ph % 300u) < 150u;                  /* double blink */
    } else {
        on = ph < 1000u;                                       /* slow blink, 0.5 Hz */
    }
    hal_led(on);
}
