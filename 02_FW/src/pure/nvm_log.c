/* NVM record log. Origin: Thrust_Stand_HAW/02_FW/src/pure/nvm_codec.c @37c8747 (adapted).
 * Implements: FW-NVM-001, FW-NVM-002
 */
#include "nvm_log.h"

#include "crc32.h"
#include "le.h"
#include "param_rules.h"
#include "proto_gen.h"

static bool all_ff(const uint8_t *d, uint32_t n)
{
    uint32_t i;
    for (i = 0u; i < n; i++) {
        if (d[i] != 0xFFu) {
            return false;
        }
    }
    return true;
}

nvm_slot_state_t nvm_slot_check(const uint8_t *slot, nvm_hdr_t *hdr)
{
    uint16_t n;
    uint32_t plen;
    if (all_ff(slot, NVM_SLOT_SIZE)) {
        return NVM_SLOT_BLANK;
    }
    if (le_get32(&slot[0]) != NVM_MAGIC || le_get16(&slot[4]) != NVM_LAYOUT_VERSION ||
        le_get16(&slot[6]) != NVM_HDR_SIZE || le_get16(&slot[14]) != NVM_ENTRY_SIZE) {
        return NVM_SLOT_INVALID;
    }
    n = le_get16(&slot[12]);
    plen = (uint32_t)n * NVM_ENTRY_SIZE;
    if (n > NVM_MAX_ENTRIES) {
        return NVM_SLOT_INVALID;
    }
    if (crc32_iso(slot, 28u) != le_get32(&slot[28])) {
        return NVM_SLOT_INVALID;
    }
    if (crc32_iso(&slot[NVM_HDR_SIZE], plen) != le_get32(&slot[24])) {
        return NVM_SLOT_INVALID;
    }
    hdr->seq = le_get32(&slot[8]);
    hdr->entry_count = n;
    hdr->dict_hash = le_get32(&slot[16]);
    hdr->dict_version = le_get16(&slot[20]);
    hdr->fw_version = le_get16(&slot[22]);
    return NVM_SLOT_VALID;
}

void nvm_scan(const uint8_t *sec_a, const uint8_t *sec_b, nvm_newest_t *out)
{
    uint8_t s;
    uint16_t i;
    out->found = false;
    out->any_nonblank = false;
    out->sector = 0u;
    out->slot = 0u;
    for (s = 0u; s < 2u; s++) {
        const uint8_t *sec = (s == 0u) ? sec_a : sec_b;
        for (i = 0u; i < NVM_SLOTS; i++) {
            nvm_hdr_t h;
            nvm_slot_state_t st = nvm_slot_check(&sec[(uint32_t)i * NVM_SLOT_SIZE], &h);
            if (st != NVM_SLOT_BLANK) {
                out->any_nonblank = true;
            }
            if (st == NVM_SLOT_VALID && (!out->found || (int32_t)(h.seq - out->hdr.seq) > 0)) {
                out->found = true;
                out->sector = s;
                out->slot = i;
                out->hdr = h;
            }
        }
    }
}

/* index of the first slot after the highest non-blank slot (NVM_SLOTS if none is free) */
static uint16_t first_free(const uint8_t *sec)
{
    uint16_t i = NVM_SLOTS;
    while (i > 0u && all_ff(&sec[(uint32_t)(i - 1u) * NVM_SLOT_SIZE], NVM_SLOT_SIZE)) {
        i--;
    }
    return i;
}

void nvm_next_target(const uint8_t *sec_a, const uint8_t *sec_b, const nvm_newest_t *newest,
                     nvm_target_t *out)
{
    uint8_t cur = newest->found ? newest->sector : 0u;
    uint8_t other = (uint8_t)(1u - cur);
    const uint8_t *sc = (cur == 0u) ? sec_a : sec_b;
    const uint8_t *so = (other == 0u) ? sec_a : sec_b;
    uint16_t f = first_free(sc);
    out->seq = newest->found ? newest->hdr.seq + 1u : 1u;
    if (out->seq == 0u) {
        out->seq = 1u;                       /* 0 = "no record" in STATUS */
    }
    if (f < NVM_SLOTS) {
        out->sector = cur;
        out->slot = f;
        out->erase_first = false;
        return;
    }
    if (!newest->found) {
        uint16_t g = first_free(so);         /* no valid record anywhere: use free space of B */
        if (g < NVM_SLOTS) {
            out->sector = other;
            out->slot = g;
            out->erase_first = false;
            return;
        }
    }
    out->sector = other;                     /* active sector full: erase the other one */
    out->slot = 0u;
    out->erase_first = !all_ff(so, NVM_LOG_SECTOR_BYTES);
}

uint16_t nvm_entry_count(void)
{
    uint16_t i, n = 0u;
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        if ((PARAM_TABLE[i].flags & PARAM_F_NVM) != 0u) {
            n++;
        }
    }
    return n;
}

uint16_t nvm_build(const params_t *p, uint32_t seq, uint16_t fw_version, uint8_t *out)
{
    uint16_t i, n = 0u;
    uint32_t plen;
    for (i = 0u; i < (uint16_t)NVM_SLOT_SIZE; i++) {
        out[i] = 0xFFu;
    }
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        const param_meta_t *m = &PARAM_TABLE[i];
        uint8_t *e;
        if ((m->flags & PARAM_F_NVM) == 0u) {
            continue;
        }
        e = &out[NVM_HDR_SIZE + (uint32_t)n * NVM_ENTRY_SIZE];
        le_put16(&e[0], m->id);
        e[2] = m->type;
        e[3] = 0u;
        le_put32(&e[4], param_get_raw(p, m));
        n++;
    }
    plen = (uint32_t)n * NVM_ENTRY_SIZE;
    le_put32(&out[0], NVM_MAGIC);
    le_put16(&out[4], (uint16_t)NVM_LAYOUT_VERSION);
    le_put16(&out[6], (uint16_t)NVM_HDR_SIZE);
    le_put32(&out[8], seq);
    le_put16(&out[12], n);
    le_put16(&out[14], (uint16_t)NVM_ENTRY_SIZE);
    le_put32(&out[16], (uint32_t)PARAM_DICT_HASH);
    le_put16(&out[20], (uint16_t)PARAM_DICT_VERSION);
    le_put16(&out[22], fw_version);
    le_put32(&out[24], crc32_iso(&out[NVM_HDR_SIZE], plen));
    le_put32(&out[28], crc32_iso(out, 28u));
    return (uint16_t)(NVM_HDR_SIZE + plen);
}

/* entry acceptance (rules 3/4): id known, `nvm` parameter, same type, value in range */
static const param_meta_t *entry_accept(const uint8_t *e, uint32_t *raw)
{
    const param_meta_t *m = param_find(le_get16(&e[0]));
    if (m == NULL || (m->flags & PARAM_F_NVM) == 0u || e[2] != m->type || e[3] != 0u) {
        return NULL;
    }
    *raw = le_get32(&e[4]);
    if (!param_raw_in_range(m, *raw)) {
        return NULL;
    }
    return m;
}

/* defaults, then every acceptable entry; returns the number of rejected entries */
static uint16_t load_entries(const uint8_t *rec, const nvm_hdr_t *hdr, params_t *out)
{
    uint16_t i, rejected = 0u;
    params_set_defaults(out);
    for (i = 0u; i < hdr->entry_count; i++) {
        const uint8_t *e = &rec[NVM_HDR_SIZE + (uint32_t)i * NVM_ENTRY_SIZE];
        uint32_t raw = 0u;
        const param_meta_t *m = entry_accept(e, &raw);
        if (m == NULL) {
            rejected++;
            continue;
        }
        param_set_raw(out, m, raw);
    }
    return rejected;
}

bool nvm_matches(const uint8_t *rec, const nvm_hdr_t *hdr, const params_t *p)
{
    uint16_t i, j = 0u;
    if (hdr->dict_hash != (uint32_t)PARAM_DICT_HASH) {
        return false;
    }
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {       /* merge walk, both ascending by id */
        const param_meta_t *m = &PARAM_TABLE[i];
        const uint8_t *e = NULL;
        uint32_t raw = 0u;
        if ((m->flags & PARAM_F_NVM) == 0u) {
            continue;
        }
        while (j < hdr->entry_count) {
            const uint8_t *c = &rec[NVM_HDR_SIZE + (uint32_t)j * NVM_ENTRY_SIZE];
            uint16_t cid = le_get16(&c[0]);
            if (cid >= m->id) {
                if (cid == m->id) {
                    e = c;
                }
                break;
            }
            j++;
        }
        if (e == NULL || entry_accept(e, &raw) == NULL || raw != param_get_raw(p, m)) {
            return false;
        }
    }
    return true;
}

void nvm_apply_boot(const uint8_t *rec, const nvm_hdr_t *hdr, bool any_nonblank, params_t *out,
                    nvm_result_t *res)
{
    bool migrated;
    uint16_t rejected;
    if (rec == NULL) {                                    /* rule 2 */
        params_set_defaults(out);
        res->event = (uint16_t)EV_PARAMS_DEFAULTED;
        res->arg = any_nonblank ? (uint16_t)PDEF_CRC_ERROR : (uint16_t)PDEF_NO_RECORD;
        res->nvm_defaulted = true;
        return;
    }
    migrated = hdr->dict_hash != (uint32_t)PARAM_DICT_HASH;
    rejected = load_entries(rec, hdr, out);               /* rule 3 / rule 4 (migration by id) */
    if (!param_rules_check_all(out)) {                    /* rule 5 */
        params_set_defaults(out);
        res->event = (uint16_t)EV_PARAMS_DEFAULTED;
        res->arg = (uint16_t)PDEF_HARD_RULE;
        res->nvm_defaulted = true;
        return;
    }
    if (migrated) {
        res->event = (uint16_t)EV_PARAMS_DEFAULTED;
        res->arg = (uint16_t)PDEF_MIGRATION;
        res->nvm_defaulted = true;
    } else {
        res->event = (uint16_t)EV_PARAMS_LOADED;
        res->arg = rejected;
        res->nvm_defaulted = false;
    }
}

bool nvm_apply_load(const uint8_t *rec, const nvm_hdr_t *hdr, params_t *ram, nvm_result_t *res)
{
    params_t t;
    uint16_t i, rejected;
    bool migrated = hdr->dict_hash != (uint32_t)PARAM_DICT_HASH;
    rejected = load_entries(rec, hdr, &t);
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {        /* session values keep their RAM value */
        const param_meta_t *m = &PARAM_TABLE[i];
        if ((m->flags & PARAM_F_NVM) == 0u) {
            param_set_raw(&t, m, param_get_raw(ram, m));
        }
    }
    if (!param_rules_check_all(&t)) {                     /* rule 5: RAM unchanged */
        return false;
    }
    *ram = t;
    res->event = migrated ? (uint16_t)EV_PARAMS_DEFAULTED : (uint16_t)EV_PARAMS_LOADED;
    res->arg = migrated ? (uint16_t)PDEF_MIGRATION : rejected;
    res->nvm_defaulted = migrated;
    return true;
}
