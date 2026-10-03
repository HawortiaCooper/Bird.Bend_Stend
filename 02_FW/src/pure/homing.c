/* Homing phase logic (START only). Implements: FW-HOM-001, FW-HOM-002, FW-HOM-004 */
#include "homing.h"

#include "proto_gen.h"

static int32_t sat_add(int32_t a, int64_t b)
{
    int64_t v = (int64_t)a + b;
    if (v > INT32_MAX) {
        return INT32_MAX;
    }
    if (v < INT32_MIN) {
        return INT32_MIN;
    }
    return (int32_t)v;
}

static home_next_t seg(uint8_t phase, int8_t dir, int32_t end, bool slow)
{
    home_next_t n;
    n.action = HOME_ACT_SEGMENT;
    n.phase = phase;
    n.dir = dir;
    n.end_steps = end;
    n.slow = slow;
    n.fail = 0u;
    return n;
}

static home_next_t fail(uint8_t phase, uint8_t why)
{
    home_next_t n = seg(phase, 0, 0, false);
    n.action = HOME_ACT_FAIL;
    n.fail = why;
    return n;
}

static home_next_t fast_seek(int32_t pos, const home_geo_t *g)
{
    return seg((uint8_t)HP_FAST_SEEK, -1, sat_add(pos, -(int64_t)g->max_travel), false);
}

home_next_t home_begin(bool start_active, int32_t pos, const home_geo_t *g)
{
    if (start_active) {
        return seg((uint8_t)HP_RELEASE, 1, sat_add(pos, g->release_max), true);
    }
    return fast_seek(pos, g);
}

home_next_t home_segment_end(uint8_t phase, const home_in_t *in, const home_geo_t *g)
{
    if (in->end_edge && phase != (uint8_t)HP_MOVE_TO_ZERO) {
        return fail(phase, (uint8_t)HF_WIRING);            /* END reached during HOME */
    }
    switch (phase) {
    case HP_RELEASE:
    case HP_BACKOFF:
        if (in->released) {
            if (phase == (uint8_t)HP_RELEASE) {
                return fast_seek(in->pos, g);
            }
            return seg((uint8_t)HP_SLOW_APPROACH, -1,
                       sat_add(in->pos, -((int64_t)g->backoff + (int64_t)g->slow_extra)), true);
        }
        if (in->start_edge && !in->planned_end) {
            /* stopped by a bounce edge of the switch being left: continue to the same end point */
            return seg(phase, 1, sat_add(in->origin, g->release_max), true);
        }
        return fail(phase, (uint8_t)HF_WIRING);            /* stuck switch or inverted DIR */
    case HP_FAST_SEEK:
        if (in->start_edge) {
            return seg((uint8_t)HP_BACKOFF, 1, sat_add(in->pos, g->release_max), true);
        }
        return fail(phase, (uint8_t)HF_NOT_FOUND);
    case HP_SLOW_APPROACH: {
        home_next_t n;
        if (!in->start_edge) {
            return fail(phase, (uint8_t)HF_NOT_FOUND);
        }
        n = seg((uint8_t)HP_MOVE_TO_ZERO, 0, 0, false);     /* direction from the new zero */
        n.action = HOME_ACT_ZERO;
        return n;
    }
    case HP_MOVE_TO_ZERO: {
        home_next_t n = seg((uint8_t)HP_DONE, 0, 0, false);
        n.action = HOME_ACT_DONE;
        return n;
    }
    default:
        return fail(phase, (uint8_t)HF_ABORTED);
    }
}

home_zero_t home_zero(int32_t pos, int32_t edge_steps, int32_t offset_steps, bool was_homed,
                      int32_t edge_um_old, int32_t offset_um, uint32_t drift_tol_um)
{
    home_zero_t z;
    int64_t d = (int64_t)edge_um_old + (int64_t)offset_um;            /* edge - (-offset) */
    int64_t ad = d < 0 ? -d : d;
    z.new_count = sat_add(pos, -((int64_t)edge_steps + (int64_t)offset_steps));
    z.drift_um = was_homed ? sat_add(0, d) : 0;
    z.drift_fault = was_homed && ad > (int64_t)drift_tol_um;
    return z;
}
