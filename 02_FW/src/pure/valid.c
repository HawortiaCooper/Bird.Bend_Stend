/* DATA validity flag. Implements: FW-CMD-002, SAF-FW-001 */
#include "valid.h"

void valid_init(valid_t *v, uint32_t now_us)
{
    v->old_v = false;
    v->new_v = false;
    v->t_apply = now_us;
}

bool valid_at(const valid_t *v, uint32_t t_us)
{
    return ((int32_t)(t_us - v->t_apply) >= 0) ? v->new_v : v->old_v;
}

uint32_t valid_set(valid_t *v, bool val, uint32_t now_us)
{
    v->old_v = valid_at(v, now_us);
    v->new_v = val;
    v->t_apply = now_us;
    return now_us;
}

bool valid_clear(valid_t *v, uint32_t t_stop)
{
    bool was = valid_at(v, t_stop);
    if (!was && !v->new_v) {
        return false;                 /* already 0: keep the earlier boundary (frame history) */
    }
    v->old_v = was;
    v->new_v = false;
    v->t_apply = t_stop;
    return was;
}

void valid_settle(valid_t *v, uint32_t now_us)
{
    int32_t age = (int32_t)(now_us - v->t_apply);
    if (age > (int32_t)VALID_SETTLE_US) {
        v->old_v = v->new_v;
        v->t_apply = now_us - VALID_SETTLE_US;       /* keep the boundary inside the window */
    }
}
