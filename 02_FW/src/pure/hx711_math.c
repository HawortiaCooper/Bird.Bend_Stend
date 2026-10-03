/* HX711 AFE logic. Origin: Thrust_Stand_HAW/02_FW/src/pure/hx711_math.c @37c8747 (D-39), first
 * three functions copied. Implements: FW-AFE-001, FW-AFE-002, FW-AFE-003, SAF-FW-009
 */
#include "hx711_math.h"

#include "params_gen.h"
#include "proto_gen.h"

int32_t hx711_sign_extend(uint32_t raw24)
{
    raw24 &= 0xFFFFFFu;
    return (raw24 & 0x800000u) != 0u ? (int32_t)(raw24 | 0xFF000000u) : (int32_t)raw24;
}

bool hx711_is_saturated(uint32_t raw24)
{
    raw24 &= 0xFFFFFFu;
    return raw24 == HX_CODE_POS_SAT || raw24 == HX_CODE_NEG_SAT;
}

bool hx711_raw_at_rail(int32_t raw)
{
    return raw >= PROTO_RAW_MAX || raw <= PROTO_RAW_MIN;
}

uint8_t hx711_pulses(uint8_t gain_channel)
{
    return gain_channel == (uint8_t)AFE_GAIN_CHANNEL_B32 ? 26u
         : (gain_channel == (uint8_t)AFE_GAIN_CHANNEL_A64 ? 27u : 25u);
}

void hx_settle_arm(hx_settle_t *s, uint8_t settle_discard)
{
    s->left = settle_discard;              /* exactly afe.settle_discard samples (FW-AFE-003) */
}

bool hx_settle_take(hx_settle_t *s)
{
    if (s->left == 0u) {
        return false;
    }
    s->left--;
    return true;
}
