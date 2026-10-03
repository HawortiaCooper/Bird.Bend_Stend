/* Measured AFE rate. Implements: FW-AFE-004 */
#include "afe_rate.h"

#include <string.h>

void afe_rate_reset(afe_rate_t *r)
{
    memset(r, 0, sizeof *r);
}

void afe_rate_push(afe_rate_t *r, uint32_t t_us)
{
    if (r->have_last) {
        r->per[r->idx] = t_us - r->last_t;
        r->idx = (uint8_t)((r->idx + 1u) % AFE_RATE_N);
        if (r->n < AFE_RATE_N) {
            r->n++;
        }
    }
    r->last_t = t_us;
    r->have_last = true;
}

uint16_t afe_rate_dsps(const afe_rate_t *r)
{
    uint32_t s[AFE_RATE_N];
    uint32_t i, j, med;
    if (r->n < AFE_RATE_N) {
        return 0u;
    }
    memcpy(s, r->per, sizeof s);
    for (i = 1u; i < AFE_RATE_N; i++) {               /* insertion sort, 16 values */
        uint32_t x = s[i];
        j = i;
        while (j > 0u && s[j - 1u] > x) {
            s[j] = s[j - 1u];
            j--;
        }
        s[j] = x;
    }
    med = (s[AFE_RATE_N / 2u - 1u] + s[AFE_RATE_N / 2u]) / 2u;
    if (med == 0u) {
        return 0u;
    }
    {
        uint32_t d = (10000000u + med / 2u) / med;     /* 1e7 / period_us = 0.1 SPS */
        return (d > 0xFFFFu) ? 0xFFFFu : (uint16_t)d;
    }
}

bool afe_rate_eval(afe_rate_t *r, uint32_t nominal_sps, uint8_t tol_pct)
{
    uint16_t d = afe_rate_dsps(r);
    bool mm = r->mismatch;
    if (d != 0u) {
        uint32_t nom = nominal_sps * 10u;
        uint32_t dev = (d > nom) ? (uint32_t)d - nom : nom - (uint32_t)d;
        mm = dev * 100u > (uint32_t)tol_pct * nom;
    }
    if (mm != r->mismatch) {
        r->mismatch = mm;
        return true;
    }
    return false;
}
