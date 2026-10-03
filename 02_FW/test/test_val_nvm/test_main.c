/* Validator E - NVM record log (independent suite; FW_test_plan v0.1 TC-FW-NVM-002-01).
 * Oracle: ICD §11.2/§11.3 rules and the record layout documented in FW_design v0.4 §9.6 item 12
 * (header 32 B: magic "BDNV", layout 1, header size, seq, entry count, entry size 8, dict hash,
 * dict version, fw version, CRC-32 of the entries, CRC-32 of header bytes 0..27; entries
 * {u16 id, u8 type, u8 0, u32 raw} of every `nvm` parameter ascending; 512 B slots, 32 per 16 KB
 * sector). Sector images are built by the validator's own writer and CRC-32 (not nvm_build /
 * crc32_iso), so the FW decoder is checked against an independent encoder, and the FW encoder is
 * compared byte for byte with it.
 *
 * Verifies: FW-NVM-001 (CFG_DIRTY definition), FW-NVM-002
 * TC: TC-FW-NVM-002-01
 */
#include "../val_common/val_io.h"

#include "le.h"
#include "nvm_log.h"
#include "param_rules.h"

void setUp(void) {}
void tearDown(void) {}

#define SEC 16384u
#define SLOT 512u
static uint8_t A[SEC], B[SEC];

/* ---------------------------------------------------------------- validator CRC-32/ISO-HDLC */
static uint32_t vcrc32(const uint8_t *d, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu, i;
    int k;
    for (i = 0u; i < n; i++) {
        c ^= d[i];
        for (k = 0; k < 8; k++) {
            c = (c & 1u) ? (c >> 1) ^ 0xEDB88320u : (c >> 1);
        }
    }
    return c ^ 0xFFFFFFFFu;
}

static void wr16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void wr32(uint8_t *p, uint32_t v) { wr16(p, v & 0xFFFFu); wr16(p + 2, v >> 16); }

typedef struct {
    uint16_t id;
    uint8_t  type;
    uint32_t raw;
} ent_t;

/* every nvm parameter of p (ascending id) */
static uint16_t entries_of(const params_t *p, ent_t *e)
{
    uint16_t i, n = 0u;
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        const param_meta_t *m = &PARAM_TABLE[i];
        if ((m->flags & PARAM_F_NVM) != 0u) {
            e[n].id = m->id;
            e[n].type = m->type;
            e[n].raw = param_get_raw(p, m);
            n++;
        }
    }
    return n;
}

static void write_record(uint8_t *slot, uint32_t seq, uint32_t hash, const ent_t *e, uint16_t n, uint16_t fwv)
{
    uint16_t i;
    memset(slot, 0xFF, SLOT);
    for (i = 0u; i < n; i++) {
        uint8_t *q = &slot[32u + 8u * i];
        wr16(q, e[i].id);
        q[2] = e[i].type;
        q[3] = 0u;
        wr32(&q[4], e[i].raw);
    }
    wr32(&slot[0], 0x564E4442u);
    wr16(&slot[4], 1u);
    wr16(&slot[6], 32u);
    wr32(&slot[8], seq);
    wr16(&slot[12], n);
    wr16(&slot[14], 8u);
    wr32(&slot[16], hash);
    wr16(&slot[20], PARAM_DICT_VERSION);
    wr16(&slot[22], fwv);
    wr32(&slot[24], vcrc32(&slot[32], 8u * (uint32_t)n));
    wr32(&slot[28], vcrc32(slot, 28u));
}

static uint8_t *slot_of(uint8_t s, uint16_t i) { return (s == 0u ? A : B) + (uint32_t)i * SLOT; }

static void blank(void)
{
    memset(A, 0xFF, sizeof A);
    memset(B, 0xFF, sizeof B);
}

static params_t P_DEF;

static void rec_defaults(uint8_t s, uint16_t i, uint32_t seq)
{
    ent_t e[64];
    uint16_t n = entries_of(&P_DEF, e);
    write_record(slot_of(s, i), seq, (uint32_t)PARAM_DICT_HASH, e, n, 0x0001u);
}

static const param_meta_t *M(uint16_t id)
{
    const param_meta_t *m = param_find(id);
    TEST_ASSERT_NOT_NULL(m);
    return m;
}

/* ---------------------------------------------------------------- tests */
static void test_layout_fw_encoder_equals_validator_writer(void)
{
    params_t p = P_DEF;
    ent_t e[64];
    uint8_t mine[SLOT], fw[SLOT];
    uint16_t n, len, i, j;
    param_set_raw(&p, M(PID_MOTION_STEPS_PER_MM), 0x44C80000u);   /* 1600.0f */
    param_set_raw(&p, M(PID_LIMITS_SOFT_MAX_UM), 250000u);
    param_set_raw(&p, M(PID_SAFETY_ZERO_RAW), 12345u);             /* session: never written */
    n = entries_of(&p, e);
    write_record(mine, 77u, (uint32_t)PARAM_DICT_HASH, e, n, 0x0102u);
    len = nvm_build(&p, 77u, 0x0102u, fw);
    TEST_ASSERT_EQUAL_UINT16(32u + 8u * n, len);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(mine, fw, SLOT);
    TEST_ASSERT_EQUAL_UINT16(n, nvm_entry_count());
    /* session parameters (nvm: false) are not in the record (FW-NVM-002) */
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        if ((PARAM_TABLE[i].flags & PARAM_F_NVM) == 0u) {
            for (j = 0u; j < n; j++) {
                TEST_ASSERT_NOT_EQUAL(PARAM_TABLE[i].id, le_get16(&fw[32u + 8u * j]));
            }
        }
    }
    /* dict_version 4 (ICD v0.5, CR-01: io.stop_active_level retired): 47 parameters - 3 session values */
    TEST_ASSERT_EQUAL_UINT16(47u, (uint16_t)PARAM_COUNT);
    TEST_ASSERT_EQUAL_UINT16(44u, n);
    TEST_ASSERT_TRUE(32u + 8u * n <= SLOT);
}

static void test_blank_and_crc_bad_boot(void)
{
    nvm_newest_t nw;
    params_t p;
    nvm_result_t r;
    blank();
    nvm_scan(A, B, &nw);
    TEST_ASSERT_FALSE(nw.found);
    TEST_ASSERT_FALSE(nw.any_nonblank);
    nvm_apply_boot(NULL, &nw.hdr, nw.any_nonblank, &p, &r);
    TEST_ASSERT_EQUAL_UINT16(EV_PARAMS_DEFAULTED, r.event);
    TEST_ASSERT_EQUAL_UINT16(1u, r.arg);                           /* empty (ICD B.15) */
    TEST_ASSERT_TRUE(r.nvm_defaulted);
    TEST_ASSERT_EQUAL_MEMORY(&P_DEF, &p, sizeof p);
    /* both records CRC-bad -> PARAMS_DEFAULTED(2) */
    rec_defaults(0u, 0u, 1u);
    rec_defaults(1u, 0u, 2u);
    slot_of(0u, 0u)[40] ^= 0x01u;                                  /* entry bit -> entries CRC */
    slot_of(1u, 0u)[28] ^= 0x80u;                                  /* header CRC */
    nvm_scan(A, B, &nw);
    TEST_ASSERT_FALSE(nw.found);
    TEST_ASSERT_TRUE(nw.any_nonblank);
    nvm_apply_boot(NULL, &nw.hdr, nw.any_nonblank, &p, &r);
    TEST_ASSERT_EQUAL_UINT16(2u, r.arg);
    TEST_ASSERT_TRUE(r.nvm_defaulted);
}

static void test_newest_by_seq_skips_bad_and_wraps(void)
{
    nvm_newest_t nw;
    uint16_t i;
    blank();
    for (i = 0u; i < 4u; i++) rec_defaults(0u, i, 10u + i);      /* A: 10..13 */
    rec_defaults(1u, 0u, 3u);                                      /* B: old */
    nvm_scan(A, B, &nw);
    TEST_ASSERT_TRUE(nw.found);
    TEST_ASSERT_EQUAL_UINT8(0u, nw.sector);
    TEST_ASSERT_EQUAL_UINT16(3u, nw.slot);
    TEST_ASSERT_EQUAL_UINT32(13u, nw.hdr.seq);
    slot_of(0u, 3u)[12] ^= 0x01u;                                  /* newest torn -> previous one */
    nvm_scan(A, B, &nw);
    TEST_ASSERT_EQUAL_UINT16(2u, nw.slot);
    TEST_ASSERT_EQUAL_UINT32(12u, nw.hdr.seq);
    /* modular sequence: 0xFFFFFFFF older than 1 */
    blank();
    rec_defaults(0u, 0u, 0xFFFFFFFEu);
    rec_defaults(0u, 1u, 0xFFFFFFFFu);
    rec_defaults(1u, 0u, 1u);
    nvm_scan(A, B, &nw);
    TEST_ASSERT_EQUAL_UINT8(1u, nw.sector);
    TEST_ASSERT_EQUAL_UINT32(1u, nw.hdr.seq);
}

static void test_next_target_log_rule(void)
{
    nvm_newest_t nw;
    nvm_target_t t;
    uint16_t i;
    blank();
    nvm_scan(A, B, &nw);
    nvm_next_target(A, B, &nw, &t);
    TEST_ASSERT_EQUAL_UINT8(0u, t.sector);
    TEST_ASSERT_EQUAL_UINT16(0u, t.slot);
    TEST_ASSERT_FALSE(t.erase_first);
    TEST_ASSERT_EQUAL_UINT32(1u, t.seq);                           /* 0 is "no record" in STATUS */
    rec_defaults(0u, 0u, 1u);
    rec_defaults(0u, 1u, 2u);
    memset(slot_of(0u, 2u), 0x00, 8u);                             /* torn write after the newest */
    nvm_scan(A, B, &nw);
    nvm_next_target(A, B, &nw, &t);
    TEST_ASSERT_EQUAL_UINT8(0u, t.sector);
    TEST_ASSERT_EQUAL_UINT16(3u, t.slot);                          /* never re-programs a used slot */
    TEST_ASSERT_EQUAL_UINT32(3u, t.seq);
    /* sector A full -> erase B (non-blank) and use B slot 0; the newest record (A) stays */
    blank();
    for (i = 0u; i < 32u; i++) rec_defaults(0u, i, 100u + i);
    rec_defaults(1u, 5u, 50u);
    nvm_scan(A, B, &nw);
    nvm_next_target(A, B, &nw, &t);
    TEST_ASSERT_EQUAL_UINT8(1u, t.sector);
    TEST_ASSERT_EQUAL_UINT16(0u, t.slot);
    TEST_ASSERT_TRUE(t.erase_first);
    TEST_ASSERT_EQUAL_UINT32(132u, t.seq);
    /* blank other sector: no erase needed */
    memset(B, 0xFF, sizeof B);
    nvm_scan(A, B, &nw);
    nvm_next_target(A, B, &nw, &t);
    TEST_ASSERT_EQUAL_UINT8(1u, t.sector);
    TEST_ASSERT_FALSE(t.erase_first);
    /* seq wrap */
    blank();
    rec_defaults(1u, 0u, 0xFFFFFFFFu);
    nvm_scan(A, B, &nw);
    nvm_next_target(A, B, &nw, &t);
    TEST_ASSERT_EQUAL_UINT8(1u, t.sector);
    TEST_ASSERT_EQUAL_UINT16(1u, t.slot);
    TEST_ASSERT_EQUAL_UINT32(1u, t.seq);
}

static void test_boot_rule3_load_and_out_of_range(void)
{
    nvm_newest_t nw;
    params_t p;
    nvm_result_t r;
    ent_t e[64];
    uint16_t n, i;
    params_t want = P_DEF;
    param_set_raw(&want, M(PID_LIMITS_SOFT_MAX_UM), 250000u);
    param_set_raw(&want, M(PID_IO_RELEASE_MS), 50u);
    n = entries_of(&want, e);
    for (i = 0u; i < n; i++) {
        if (e[i].id == PID_MOTION_DIR_SETUP_US) e[i].raw = 4u;    /* below min 5 -> default */
    }
    blank();
    write_record(slot_of(0u, 0u), 9u, (uint32_t)PARAM_DICT_HASH, e, n, 1u);
    nvm_scan(A, B, &nw);
    TEST_ASSERT_TRUE(nw.found);
    nvm_apply_boot(slot_of(0u, 0u), &nw.hdr, nw.any_nonblank, &p, &r);
    TEST_ASSERT_EQUAL_UINT16(EV_PARAMS_LOADED, r.event);
    TEST_ASSERT_EQUAL_UINT16(1u, r.arg);                           /* one value replaced by default */
    TEST_ASSERT_FALSE(r.nvm_defaulted);
    TEST_ASSERT_EQUAL_MEMORY(&want, &p, sizeof p);                 /* dir_setup default in want */
    /* CFG_DIRTY (ICD §11.2): RAM = record except the defaulted entry -> dirty */
    TEST_ASSERT_FALSE(nvm_matches(slot_of(0u, 0u), &nw.hdr, &p));
}

static void test_boot_rule4_migration_by_id(void)
{
    nvm_newest_t nw;
    params_t p;
    nvm_result_t r;
    ent_t e[64];
    uint16_t n, i, k = 0u;
    params_t want = P_DEF;
    ent_t m[70];
    param_set_raw(&want, M(PID_LIMITS_SOFT_MAX_UM), 260000u);     /* kept (same id/type, in range) */
    n = entries_of(&want, e);
    for (i = 0u; i < n; i++) {
        if (e[i].id == PID_HOME_DRIFT_TOL_UM) continue;            /* "new" id absent -> default */
        m[k] = e[i];
        if (e[i].id == PID_IO_RELEASE_MS) { m[k].type = (uint8_t)(m[k].type + 1u); m[k].raw = 30u; }
        if (e[i].id == PID_MOTION_JOG_TIMEOUT_MS) m[k].raw = 5000u;                 /* out of range */
        k++;
        if (e[i].id == 0x0302u) {                                   /* retired id 0x0401 after group 3 */
            m[k].id = 0x0401u; m[k].type = 1u; m[k].raw = 1u; k++;
        }
    }
    blank();
    write_record(slot_of(1u, 7u), 41u, 0x13961802u, m, k, 1u);      /* dict v1 hash */
    nvm_scan(A, B, &nw);
    TEST_ASSERT_TRUE(nw.found);
    nvm_apply_boot(slot_of(1u, 7u), &nw.hdr, nw.any_nonblank, &p, &r);
    TEST_ASSERT_EQUAL_UINT16(EV_PARAMS_DEFAULTED, r.event);
    TEST_ASSERT_EQUAL_UINT16(3u, r.arg);                           /* MIGRATION */
    TEST_ASSERT_TRUE(r.nvm_defaulted);
    TEST_ASSERT_EQUAL_MEMORY(&want, &p, sizeof p);
    TEST_ASSERT_FALSE(nvm_matches(slot_of(1u, 7u), &nw.hdr, &p));  /* other hash -> dirty */
}

static void test_boot_rule5_hard_rule_defaults(void)
{
    nvm_newest_t nw;
    params_t p, bad = P_DEF;
    nvm_result_t r;
    ent_t e[64];
    uint16_t n;
    param_set_raw(&bad, M(PID_LIMITS_SOFT_MIN_UM), 300000u);
    param_set_raw(&bad, M(PID_LIMITS_SOFT_MAX_UM), 200000u);      /* H1 violated, both in range */
    n = entries_of(&bad, e);
    blank();
    write_record(slot_of(0u, 0u), 2u, (uint32_t)PARAM_DICT_HASH, e, n, 1u);
    nvm_scan(A, B, &nw);
    nvm_apply_boot(slot_of(0u, 0u), &nw.hdr, nw.any_nonblank, &p, &r);
    TEST_ASSERT_EQUAL_UINT16(EV_PARAMS_DEFAULTED, r.event);
    TEST_ASSERT_EQUAL_UINT16(4u, r.arg);
    TEST_ASSERT_TRUE(r.nvm_defaulted);
    TEST_ASSERT_EQUAL_MEMORY(&P_DEF, &p, sizeof p);
}

static void test_load_keeps_session_values_and_rejects_hard_rule(void)
{
    nvm_newest_t nw;
    params_t ram = P_DEF, before;
    nvm_result_t r;
    ent_t e[64];
    uint16_t n;
    params_t rec = P_DEF;
    param_set_raw(&rec, M(PID_LIMITS_SOFT_MAX_UM), 270000u);
    n = entries_of(&rec, e);
    blank();
    write_record(slot_of(0u, 4u), 5u, (uint32_t)PARAM_DICT_HASH, e, n, 1u);
    nvm_scan(A, B, &nw);
    param_set_raw(&ram, M(PID_SAFETY_ZERO_RAW), (uint32_t)-4321);
    param_set_raw(&ram, M(PID_SAFETY_LOAD_RAW_MAX), 1000000u);
    param_set_raw(&ram, M(PID_LIMITS_SOFT_MAX_UM), 100000u);
    TEST_ASSERT_TRUE(nvm_apply_load(slot_of(0u, 4u), &nw.hdr, &ram, &r));
    TEST_ASSERT_EQUAL_UINT16(EV_PARAMS_LOADED, r.event);
    TEST_ASSERT_EQUAL_HEX32(270000u, param_get_raw(&ram, M(PID_LIMITS_SOFT_MAX_UM)));
    TEST_ASSERT_EQUAL_HEX32((uint32_t)-4321, param_get_raw(&ram, M(PID_SAFETY_ZERO_RAW)));
    TEST_ASSERT_EQUAL_HEX32(1000000u, param_get_raw(&ram, M(PID_SAFETY_LOAD_RAW_MAX)));
    TEST_ASSERT_TRUE(nvm_matches(slot_of(0u, 4u), &nw.hdr, &ram));   /* session values ignored */
    /* record + RAM session values violating H2 -> refused, RAM unchanged (rule 5, LOAD) */
    param_set_raw(&ram, M(PID_SAFETY_LOAD_RAW_MIN), 5000000u);
    param_set_raw(&ram, M(PID_SAFETY_LOAD_RAW_MAX), 4000000u);
    before = ram;
    TEST_ASSERT_FALSE(nvm_apply_load(slot_of(0u, 4u), &nw.hdr, &ram, &r));
    TEST_ASSERT_EQUAL_MEMORY(&before, &ram, sizeof ram);
}

static void test_cfg_dirty_definition(void)
{
    nvm_newest_t nw;
    ent_t e[64];
    uint16_t n;
    params_t p = P_DEF;
    n = entries_of(&p, e);
    blank();
    write_record(slot_of(0u, 0u), 1u, (uint32_t)PARAM_DICT_HASH, e, n, 1u);
    nvm_scan(A, B, &nw);
    TEST_ASSERT_TRUE(nvm_matches(slot_of(0u, 0u), &nw.hdr, &p));
    param_set_raw(&p, M(PID_SAFETY_LOAD_RAW_MIN), (uint32_t)-1000);   /* session: never dirties */
    param_set_raw(&p, M(PID_SAFETY_ZERO_RAW), 7u);
    TEST_ASSERT_TRUE(nvm_matches(slot_of(0u, 0u), &nw.hdr, &p));
    param_set_raw(&p, M(PID_STREAM_FALLBACK_HZ), 11u);                /* nvm parameter -> dirty */
    TEST_ASSERT_FALSE(nvm_matches(slot_of(0u, 0u), &nw.hdr, &p));
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_layout_fw_encoder_equals_validator_writer),
        VAL_CASE(test_blank_and_crc_bad_boot),
        VAL_CASE(test_newest_by_seq_skips_bad_and_wraps),
        VAL_CASE(test_next_target_log_rule),
        VAL_CASE(test_boot_rule3_load_and_out_of_range),
        VAL_CASE(test_boot_rule4_migration_by_id),
        VAL_CASE(test_boot_rule5_hard_rule_defaults),
        VAL_CASE(test_load_keeps_session_values_and_rejects_hard_rule),
        VAL_CASE(test_cfg_dirty_definition),
    };
    params_set_defaults(&P_DEF);
    return val_run(c, sizeof c / sizeof c[0]);
}
