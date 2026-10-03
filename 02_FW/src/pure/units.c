/* µm <-> steps (ICD §0.1). Implements: SYS-003, FW-MOT-002, FW-MOT-009 */
#include "units.h"

#include <math.h>

static int32_t sat_i32(double x)
{
    if (x >= 2147483647.0) {
        return INT32_MAX;
    }
    if (x <= -2147483648.0) {
        return INT32_MIN;
    }
    return (int32_t)x;
}

int32_t units_um_to_steps(int32_t um, float spm)
{
    double v = (double)um * (double)spm / 1000.0;
    return sat_i32(round(v));
}

int32_t units_steps_to_um(int32_t steps, float spm)
{
    double v = (double)steps * 1000.0 / (double)spm;
    return sat_i32(round(v));
}

uint32_t units_rate_cap_um_s(uint32_t max_step_rate_hz, float spm)
{
    double v = floor((double)max_step_rate_hz * 1000.0 / (double)spm);
    if (v <= 0.0) {
        return 0u;
    }
    if (v >= 4294967295.0) {
        return UINT32_MAX;
    }
    return (uint32_t)v;
}
