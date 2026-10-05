/* MOVE_UNTIL_LOAD decisions. Implements: FW-MOT-006 (decision part) */
#include "mul.h"

#include "proto.h"

bool mul_beyond(int32_t raw, int32_t raw_stop, uint8_t cmp)
{
    if (cmp == (uint8_t)CMP_GE) {
        return raw >= raw_stop;
    }
    if (cmp == (uint8_t)CMP_LE) {
        return raw <= raw_stop;
    }
    return true;                                /* undefined cmp: fail-safe (never armed) */
}

bool mul_precheck(bool have_sample, int32_t raw_last, int32_t raw_stop, uint8_t cmp)
{
    if (!have_sample || raw_last == PROTO_AFE_NO_DATA) {
        return false;
    }
    return mul_beyond(raw_last, raw_stop, cmp);
}

bool mul_sample_stop(bool armed, bool hit, bool running, bool load_trip, int32_t raw, int32_t raw_stop,
                     uint8_t cmp)
{
    if (!armed || hit || !running || load_trip) {
        return false;
    }
    return mul_beyond(raw, raw_stop, cmp);
}

int8_t mul_dir(int32_t bound_steps, int32_t pos_steps)
{
    return (int8_t)((bound_steps > pos_steps) - (bound_steps < pos_steps));
}
