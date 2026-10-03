/* FW load limit. Implements: SAF-FW-008, SAF-FW-009, SAF-FW-010, SAF-FW-011 */
#include "loadlim.h"

void loadlim_config(loadlim_t *l, int32_t min, int32_t max, uint8_t trip_samples, int32_t regrow)
{
    l->min = min;
    l->max = max;
    l->trip_samples = (trip_samples == 0u) ? 1u : trip_samples;
    l->regrow = (regrow < 0) ? 0 : regrow;
}

void loadlim_init(loadlim_t *l, int32_t min, int32_t max, uint8_t trip_samples, int32_t regrow)
{
    loadlim_config(l, min, max, trip_samples, regrow);
    l->count = 0u;
    l->regrow_on = false;
    l->ref = 0;
}

static bool violates(const loadlim_t *l, int32_t raw, bool sat)
{
    return sat || raw > l->max || raw < l->min;
}

bool loadlim_check(loadlim_t *l, int32_t raw, bool sat)
{
    if (!violates(l, raw, sat)) {
        l->count = 0u;
        l->regrow_on = false;
        return false;
    }
    if (l->regrow_on) {
        int64_t r = (int64_t)raw;
        bool grow = (raw > l->max && r > (int64_t)l->ref + (int64_t)l->regrow) ||
                    (raw < l->min && r < (int64_t)l->ref - (int64_t)l->regrow);
        if (!grow) {
            l->count = 0u;
            return false;                       /* unloading allowed (SAF-FW-011) */
        }
        l->regrow_on = false;
        l->count = l->trip_samples;
        return true;                            /* immediate re-trip on regrow */
    }
    if (l->count < 0xFFu) {
        l->count++;
    }
    return l->count >= l->trip_samples;
}

void loadlim_on_clear(loadlim_t *l, int32_t raw, bool sat)
{
    l->count = 0u;
    l->ref = raw;
    l->regrow_on = violates(l, raw, sat);
}
