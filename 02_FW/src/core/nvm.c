/* NVM service (FW_design §5.11, ICD §11): boot rules 2..5, LOAD / DEFAULT (RAM only), CFG_DIRTY,
 * and the non-blocking SAVE state machine HOLD -> QUIESCE -> PROGRAM -> VERIFY -> DONE: dispatch
 * paused, AFE held and fallback frames suppressed, TX drained (no frame split by the flash stall),
 * one long PROGRAM pass (excluded from loop_max_us), read-back verify, response + EVENT.
 * Implements: FW-NVM-001, FW-NVM-002, FW-NVM-003, NFR-006 (non-blocking quiesce), NFR-008
 */
#include "fw.h"

#include "hal_flash.h"
#include "hal_hx711.h"
#include "hal_sys.h"
#include "hal_time.h"
#include "hal_uart.h"
#include "nvm_log.h"

typedef enum { NS_IDLE = 0, NS_HOLD, NS_QUIESCE, NS_PROGRAM, NS_VERIFY, NS_DONE } nvm_state_t;

static struct {
    nvm_state_t  st;
    uint8_t      seq;            /* SEQ of the SAVE command (deferred response) */
    uint32_t     t0_ms;
    uint32_t     q0_ms;
    nvm_target_t tgt;
    uint8_t      fail;           /* 0 or NVMD_* */
    uint8_t      img[NVM_SLOT_SIZE];
} s;

static nvm_newest_t s_newest;    /* location of the active (newest valid) record */

static const uint8_t *sec(uint8_t i)
{
    return (const uint8_t *)hal_flash_map(i == 0u ? NVM_ADDR_A : NVM_ADDR_B);
}

static uint32_t slot_addr(uint8_t sector, uint16_t slot)
{
    return (sector == 0u ? NVM_ADDR_A : NVM_ADDR_B) + (uint32_t)slot * NVM_SLOT_SIZE;
}

static const uint8_t *active_record(void)
{
    return s_newest.found ? &sec(s_newest.sector)[(uint32_t)s_newest.slot * NVM_SLOT_SIZE] : NULL;
}

void nvm_cfg_dirty_update(void)
{
    const uint8_t *rec = active_record();
    g_fw.cfg_dirty = (rec == NULL) || !nvm_matches(rec, &s_newest.hdr, &g_fw.p);
}

void nvm_boot(uint16_t *ev, uint16_t *arg)
{
    nvm_result_t res;
    nvm_scan(sec(0u), sec(1u), &s_newest);
    nvm_apply_boot(active_record(), &s_newest.hdr, s_newest.any_nonblank, &g_fw.p, &res);
    g_fw.nvm_record_valid = s_newest.found;
    g_fw.nvm_record_seq = s_newest.found ? s_newest.hdr.seq : 0u;
    g_fw.nvm_defaulted = res.nvm_defaulted;
    nvm_cfg_dirty_update();
    *ev = res.event;
    *arg = res.arg;
    s.st = NS_IDLE;
}

bool nvm_load_cmd(void)
{
    nvm_result_t res;
    params_t ram;
    const uint8_t *rec = active_record();
    if (rec == NULL) {
        return false;
    }
    ram = g_fw.p;
    if (!nvm_apply_load(rec, &s_newest.hdr, &ram, &res)) {
        return false;                            /* rule 5: RAM unchanged */
    }
    CRIT_BEGIN(HAL_CRIT_DATA);
    g_fw.p = ram;
    CRIT_END();
    g_fw.nvm_defaulted = res.nvm_defaulted;
    nvm_cfg_dirty_update();
    fw_event(res.event, res.arg, 0, 0);
    return true;
}

void nvm_default_cmd(void)
{
    params_t d;
    params_set_defaults(&d);                     /* ALL parameters incl. session values */
    CRIT_BEGIN(HAL_CRIT_DATA);
    g_fw.p = d;
    CRIT_END();
    nvm_cfg_dirty_update();
    fw_event((uint16_t)EV_PARAMS_DEFAULTED, (uint16_t)PDEF_COMMAND, 0, 0);
}

void nvm_save_start(uint8_t seq)
{
    s.st = NS_HOLD;
    s.seq = seq;
    s.t0_ms = hal_time_ms();
    s.fail = 0u;
}

bool nvm_busy(void)
{
    return s.st != NS_IDLE;
}

static bool program(void)
{
    uint32_t a = slot_addr(s.tgt.sector, s.tgt.slot);
    uint16_t len = nvm_build(&g_fw.p, s.tgt.seq,
                             (uint16_t)(((unsigned)FW_VERSION_MAJOR << 8) | (unsigned)FW_VERSION_MINOR), s.img);
    bool ok = true;
    if (s.tgt.erase_first) {
        ok = hal_flash_erase(s.tgt.sector == 0u ? NVM_SECTOR_A : NVM_SECTOR_B);
    }
    /* entries, then header bytes 0..27, then the header CRC (commit marker) last */
    if (ok && len > NVM_HDR_SIZE) {
        ok = hal_flash_program(a + NVM_HDR_SIZE, &s.img[NVM_HDR_SIZE], (size_t)(len - NVM_HDR_SIZE));
    }
    if (ok) {
        ok = hal_flash_program(a, s.img, 28u);
    }
    if (ok) {
        ok = hal_flash_program(a + 28u, &s.img[28], 4u);
    }
    return ok;
}

bool nvm_service(void)
{
    uint32_t now = hal_time_ms();
    switch (s.st) {
    case NS_IDLE:
        return false;
    case NS_HOLD:
        hal_hx711_hold(true);                    /* conversions missed -> OVERRUN (FW-NVM-003) */
        g_fw.st.hold = true;                     /* no fallback frames */
        s.q0_ms = now;
        s.st = NS_QUIESCE;
        return false;
    case NS_QUIESCE:
        if (hal_uart_tx_idle() || (uint32_t)(now - s.q0_ms) >= NVM_QUIESCE_MAX_MS) {
            s.st = NS_PROGRAM;
        }
        return false;
    case NS_PROGRAM:
        nvm_next_target(sec(0u), sec(1u), &s_newest, &s.tgt);
        hal_wdg_set_timeout(NVM_SAVE_WDG_MS);
        s.fail = program() ? 0u : (uint8_t)NVMD_ERASE_PROGRAM;
        hal_wdg_set_timeout(WDG_RUN_TIMEOUT_MS);
        s.st = NS_VERIFY;
        return true;                             /* the one long pass (NFR-006 exemption) */
    case NS_VERIFY: {
        const uint8_t *rec = &sec(s.tgt.sector)[(uint32_t)s.tgt.slot * NVM_SLOT_SIZE];
        nvm_hdr_t h;
        if (s.fail == 0u && (nvm_slot_check(rec, &h) != NVM_SLOT_VALID || h.seq != s.tgt.seq ||
                             !nvm_matches(rec, &h, &g_fw.p))) {
            s.fail = (uint8_t)NVMD_VERIFY;
        }
        if (s.fail == 0u) {
            s_newest.found = true;
            s_newest.sector = s.tgt.sector;
            s_newest.slot = s.tgt.slot;
            s_newest.hdr = h;
            g_fw.nvm_record_valid = true;
            g_fw.nvm_record_seq = h.seq;
            g_fw.nvm_defaulted = false;
            nvm_cfg_dirty_update();
            g_fw.nvm_save_ms = (uint16_t)(((uint32_t)(now - s.t0_ms) > 0xFFFFu) ? 0xFFFFu : (now - s.t0_ms));
            g_fw.nvm_save_uptime_ms = now - g_fw.boot_ms;
            fw_event((uint16_t)EV_PARAMS_SAVED, 0u, (int32_t)h.seq, 0);
            link_respond(CMD_SAVE_PARAMS, s.seq, ST_OK, NULL, 0u);
        } else {
            /* previous record stays valid (another slot / sector), RAM unchanged */
            nvm_scan(sec(0u), sec(1u), &s_newest);
            g_fw.nvm_record_valid = s_newest.found;
            g_fw.nvm_record_seq = s_newest.found ? s_newest.hdr.seq : 0u;
            nvm_cfg_dirty_update();
            fw_event((uint16_t)EV_NVM_ERROR, s.fail, 0, 0);
            link_nack(CMD_SAVE_PARAMS, s.seq, ST_E_NVM, s.fail);
        }
        s.st = NS_DONE;
        return false;
    }
    case NS_DONE:
    default:
        afe_rearm_after_hold();                  /* no stale verdict from the hold itself */
        g_fw.st.hold = false;
        hal_hx711_hold(false);                   /* the next frame carries OVERRUN (missed Δt) */
        s.st = NS_IDLE;                          /* dispatching resumes */
        return false;
    }
}
