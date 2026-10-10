/* Build a cmd_ctx_t from one check_vectors.json vector (state_defaults + state + param overrides).
 * Header-only, used by test_impl_check and test_impl_latch_m1.
 * Verifies: (helper for) FW-CMD-001
 */
#ifndef CHECK_CTX_H
#define CHECK_CTX_H

#include <unity.h>

#include "cmd_check.h"
#include "params_gen.h"
#include "vec_check.h"

static inline void cc_setup(const vec_check_t *v, params_t *p, cmd_ctx_t *c)
{
    uint8_t i;
    params_set_defaults(p);
    for (i = 0u; i < v->n_par; i++) {
        const param_meta_t *m = param_find(v->par[i].id);
        TEST_ASSERT_NOT_NULL_MESSAGE(m, v->name);
        param_set_raw(p, m, v->par[i].raw);
    }
    c->p = p;
    c->motion_state = v->motion_state;
    c->enabling_left_ms = v->enabling_left_ms;
    c->homed = v->homed;
    c->pos_um = v->pos_um;
    c->estop_latched = v->estop_latched;
    c->estop_input_open = v->estop_input_open;
    c->estop_closed_ms = v->estop_closed_ms;
    c->halt_latched = v->halt_latched;
    c->faults = v->faults;
    c->fault_causes = v->fault_causes;
    c->limit_start = v->limit_start;
    c->limit_end = v->limit_end;
    c->afe_stale = v->afe_stale;
    c->afe_saturated = v->afe_saturated;
    c->raw = v->raw;
    c->drv_power = v->drv_power;
    c->alm_active = v->alm_active;
    c->nvm_record_valid = v->nvm_record_valid;
    c->paused = v->paused;
    c->hw_meas = v->hw_meas;
    c->unhomed_origin_um = v->unhomed_origin_um;    /* 0 with state_schema 2 */
    c->ena_on = v->ena_on;                          /* true below state_schema 4 (FWR-09) */
}

#endif /* CHECK_CTX_H */
