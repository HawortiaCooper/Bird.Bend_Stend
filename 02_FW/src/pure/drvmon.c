/* Driver monitor. Implements: FW-SW-004, FW-SW-005, SAF-FW-024, SAF-FW-025, SAF-FW-026 */
#include "drvmon.h"

void drvmon_init(drvmon_t *m, bool sense, bool raw_pwr, bool raw_alm)
{
    m->sense = sense;
    m->pwr = raw_pwr;
    m->diff_ms = 0u;
    m->k1_ms = 0u;
    m->k1_tripped = false;
    btn_init(&m->alm, raw_alm);
}

drvmon_ev_t drvmon_sample(drvmon_t *m, bool raw_pwr, bool estop_open, bool raw_alm, uint16_t k1_weld_ms,
                          uint8_t release_ms)
{
    drvmon_ev_t e = {false, false, false, false, false};
    uint8_t a;
    if (m->sense) {
        if (raw_pwr != m->pwr) {
            if (++m->diff_ms >= DRV_PWR_FILTER_MS) {
                m->pwr = raw_pwr;
                m->diff_ms = 0u;
                e.pwr_changed = true;
                e.pwr_lost = !raw_pwr;
                e.pwr_returned = raw_pwr;
            }
        } else {
            m->diff_ms = 0u;
        }
        if (estop_open && m->pwr) {
            if (m->k1_ms != 0xFFFFu) {
                m->k1_ms++;
            }
            if (!m->k1_tripped && m->k1_ms > k1_weld_ms) {
                m->k1_tripped = true;
                e.k1_fault = true;
            }
        } else {
            m->k1_ms = 0u;
            m->k1_tripped = false;
        }
    } else {
        m->pwr = true;
    }
    a = btn_sample(&m->alm, raw_alm, false, release_ms);
    if (a != BTN_NONE) {
        e.alm_changed = true;
    }
    return e;
}
