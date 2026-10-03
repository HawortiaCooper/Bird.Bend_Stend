/* NVM: record codec, two-sector slot log across many saves, boot rules 2..5 (blank, CRC error,
 * equal hash with an out-of-range entry, migration with a retired id, hard-rule violation), LOAD /
 * DEFAULT semantics, CFG_DIRTY definition, power cut after every program word and during the
 * erase (the previous record stays valid), program failure (E_NVM + NVM_ERROR), seq wrap.
 * Verifies: FW-NVM-001, FW-NVM-002, FW-NVM-003, FW-CFG-003 (no flash write on SET), SAF-FW-010
 */
#include <unity.h>

#include "crc32.h"
#include "harness.h"
#include "nvm_log.h"

void setUp(void) { h_boot(true); }
void tearDown(void) {}

static uint16_t ev_arg(uint16_t code)
{
    int32_t i = fake_cap_event(code, 0u);
    TEST_ASSERT_TRUE_MESSAGE(i >= 0, "event missing");
    return le_get16(&fake_cap[i].payload[6]);
}

static void save_ok(void)
{
    h_expect_ok(h_cmd(CMD_SAVE_PARAMS, 0x13u, NULL, 0u));
    fake_run_ms(2);
}

/* ---- pure codec ---- */
static void test_codec_roundtrip_and_crc(void)
{
    static uint8_t img[NVM_SLOT_SIZE];
    params_t p, q;
    nvm_hdr_t h;
    nvm_result_t r;
    uint16_t len;
    params_set_defaults(&p);
    p.motion.steps_per_mm = 636.0778198242188f;
    len = nvm_build(&p, 42u, 0x0001u, img);
    TEST_ASSERT_EQUAL_UINT16(NVM_HDR_SIZE + 8u * nvm_entry_count(), len);
    TEST_ASSERT_EQUAL_UINT16(44u, nvm_entry_count());                /* 47 - 3 session values (dict 4) */
    TEST_ASSERT_EQUAL(NVM_SLOT_VALID, nvm_slot_check(img, &h));
    TEST_ASSERT_EQUAL_UINT32(42u, h.seq);
    TEST_ASSERT_EQUAL_HEX32(PARAM_DICT_HASH, h.dict_hash);
    TEST_ASSERT_TRUE(nvm_matches(img, &h, &p));
    nvm_apply_boot(img, &h, true, &q, &r);
    TEST_ASSERT_EQUAL_UINT16(EV_PARAMS_LOADED, r.event);
    TEST_ASSERT_EQUAL_UINT16(0u, r.arg);
    TEST_ASSERT_FALSE(r.nvm_defaulted);
    TEST_ASSERT_EQUAL_FLOAT(p.motion.steps_per_mm, q.motion.steps_per_mm);
    img[40] ^= 1u;                                                   /* payload bit flip */
    TEST_ASSERT_EQUAL(NVM_SLOT_INVALID, nvm_slot_check(img, &h));
    img[40] ^= 1u;
    img[29] ^= 1u;                                                   /* header CRC flip */
    TEST_ASSERT_EQUAL(NVM_SLOT_INVALID, nvm_slot_check(img, &h));
}

/* ---- boot rules ---- */
static void test_blank_boot_and_load_refused(void)
{
    h_status_t s = h_status();
    TEST_ASSERT_EQUAL_UINT16(PDEF_NO_RECORD, ev_arg(EV_PARAMS_DEFAULTED));
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED, s.b[19] & (SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED));
    TEST_ASSERT_EQUAL_UINT32(0u, le_get32(&s.b[72]));
    h_expect_nack(h_cmd(CMD_LOAD_PARAMS, 1u, NULL, 0u), ST_E_NVM, NVMD_NO_RECORD);
}

static void test_set_save_reboot_restore(void)
{
    uint32_t w0 = fake_flash_words;
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 400u);
    h_set_param(PID_SAFETY_ZERO_RAW, PARAM_T_I32, 1234u);           /* session value */
    TEST_ASSERT_EQUAL_UINT32(w0, fake_flash_words);                  /* SET never writes flash */
    save_ok();
    TEST_ASSERT_EQUAL_UINT32(1u, (uint32_t)le_get32(&fake_cap[fake_cap_event(EV_PARAMS_SAVED, 0u)].payload[8]));
    {
        h_status_t s = h_status();
        TEST_ASSERT_EQUAL_HEX8(0u, s.b[19] & (SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED));
        TEST_ASSERT_EQUAL_UINT32(1u, le_get32(&s.b[72]));
    }
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 300u);
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY, h_status().b[19] & SYSF_CFG_DIRTY);
    h_set_param(PID_SAFETY_ZERO_RAW, PARAM_T_I32, 99u);              /* session: never dirty */
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 400u);
    TEST_ASSERT_EQUAL_HEX8(0u, h_status().b[19] & SYSF_CFG_DIRTY);
    h_reboot();
    TEST_ASSERT_EQUAL_UINT16(0u, ev_arg(EV_PARAMS_LOADED));
    TEST_ASSERT_EQUAL_UINT32(400u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
    TEST_ASSERT_EQUAL_UINT32(0u, h_get_param_raw(PID_SAFETY_ZERO_RAW)); /* session: default */
}

static void test_load_and_default(void)
{
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 400u);
    save_ok();
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 100u);
    h_set_param(PID_SAFETY_LOAD_RAW_MAX, PARAM_T_I32, 5000000u);    /* session value kept by LOAD */
    fake_cap_clear();
    h_expect_ok(h_cmd(CMD_LOAD_PARAMS, 2u, NULL, 0u));
    events_flush();
    TEST_ASSERT_EQUAL_UINT16(0u, ev_arg(EV_PARAMS_LOADED));
    TEST_ASSERT_EQUAL_UINT32(400u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
    TEST_ASSERT_EQUAL_UINT32(5000000u, h_get_param_raw(PID_SAFETY_LOAD_RAW_MAX));
    TEST_ASSERT_EQUAL_HEX8(0u, h_status().b[19] & SYSF_CFG_DIRTY);
    fake_cap_clear();
    h_expect_ok(h_cmd(CMD_DEFAULT_PARAMS, 3u, NULL, 0u));
    events_flush();
    TEST_ASSERT_EQUAL_UINT16(PDEF_COMMAND, ev_arg(EV_PARAMS_DEFAULTED));
    TEST_ASSERT_EQUAL_UINT32(250u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
    TEST_ASSERT_EQUAL_UINT32(7022271u, h_get_param_raw(PID_SAFETY_LOAD_RAW_MAX)); /* incl. session */
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY, h_status().b[19] & SYSF_CFG_DIRTY);
    TEST_ASSERT_EQUAL_UINT32(1u, le_get32(&h_status().b[72]));       /* NVM untouched */
}

/* 70 saves walk both sectors (32 slots each), with erases; every reboot loads the newest */
static void test_log_across_sectors(void)
{
    uint32_t k, erases = 0u;
    for (k = 1u; k <= 70u; k++) {
        h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 100u + k);
        save_ok();
        TEST_ASSERT_EQUAL_UINT32(k, le_get32(&h_status().b[72]));
        if (k % 17u == 0u || k == 32u || k == 33u || k == 64u || k == 65u) {
            erases += fake_flash_erases;                         /* counter restarts at reboot */
            h_reboot();
            TEST_ASSERT_EQUAL_UINT32(100u + k, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
        }
    }
    erases += fake_flash_erases;
    TEST_ASSERT_EQUAL_UINT32(1u, erases);                            /* sector A erased at save 65 */
}

/* power loss at every word of a SAVE (incl. the commit word): after the reboot the previous record
 * (or, only when the last word made it, the new one) is loaded - never defaults */
static void test_power_cut_every_word(void)
{
    int32_t cut;
    const int32_t words = (int32_t)((NVM_HDR_SIZE + NVM_ENTRY_SIZE * nvm_entry_count()) / 4u);
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 111u);
    save_ok();
    TEST_ASSERT_EQUAL_INT32(96, words);
    for (cut = 0; cut < words; cut++) {                              /* the commit word is #96 */
        uint32_t v;
        h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 222u);
        fake_flash_cut_after_words(cut);
        h_expect_nack(h_cmd(CMD_SAVE_PARAMS, 0x13u, NULL, 0u), ST_E_NVM, NVMD_ERASE_PROGRAM);
        h_reboot();
        v = h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(111u, v, "cut before the commit word: old record");
        TEST_ASSERT_TRUE(fake_cap_event(EV_PARAMS_LOADED, 0u) >= 0);
        h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 111u);
    }
    fake_flash_cut_after_words(words);                               /* all words: committed */
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 222u);
    save_ok();
    h_reboot();
    TEST_ASSERT_EQUAL_UINT32(222u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
}

/* the 65th save erases the full other sector: a cut inside the erase keeps the newest record */
static void test_power_cut_in_erase(void)
{
    uint32_t k;
    for (k = 1u; k <= 64u; k++) {
        h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 100u + k);
        save_ok();
    }
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 999u);
    fake_flash_cut_in_erase(5000);
    h_expect_nack(h_cmd(CMD_SAVE_PARAMS, 0x13u, NULL, 0u), ST_E_NVM, NVMD_ERASE_PROGRAM);
    h_reboot();
    TEST_ASSERT_EQUAL_UINT32(164u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
    TEST_ASSERT_EQUAL_UINT32(64u, le_get32(&h_status().b[72]));
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 777u);       /* the log continues */
    save_ok();
    h_reboot();
    TEST_ASSERT_EQUAL_UINT32(777u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
    TEST_ASSERT_EQUAL_UINT32(65u, le_get32(&h_status().b[72]));
}

static void test_program_failure_reported(void)
{
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 111u);
    save_ok();
    fake_cap_clear();
    fake_flash_fail_program = true;
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 222u);
    h_expect_nack(h_cmd(CMD_SAVE_PARAMS, 0x13u, NULL, 0u), ST_E_NVM, NVMD_ERASE_PROGRAM);
    events_flush();
    TEST_ASSERT_EQUAL_UINT16(NVMD_ERASE_PROGRAM, ev_arg(EV_NVM_ERROR));
    TEST_ASSERT_EQUAL_UINT32(222u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));   /* RAM unchanged */
    TEST_ASSERT_EQUAL_UINT32(1u, le_get32(&h_status().b[72]));       /* previous record active */
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY, h_status().b[19] & SYSF_CFG_DIRTY);
    fake_flash_fail_program = false;
}

/* write a crafted record into sector A slot 0 */
static void put_record(const uint8_t *img, uint16_t len)
{
    fake_flash_blank();
    memcpy(fake_flash_sector(0u), img, len);
}

static void reseal(uint8_t *img)
{
    uint16_t n = le_get16(&img[12]);
    le_put32(&img[24], crc32_iso(&img[NVM_HDR_SIZE], (uint32_t)n * NVM_ENTRY_SIZE));
    le_put32(&img[28], crc32_iso(img, 28u));
}

static void test_out_of_range_entry_replaced(void)
{
    static uint8_t img[NVM_SLOT_SIZE];
    params_t p;
    uint16_t len, i;
    params_set_defaults(&p);
    p.motion.jog_timeout_ms = 333u;
    len = nvm_build(&p, 5u, 1u, img);
    for (i = 0u; i < 44u; i++) {                                     /* jog_timeout_ms := 5 (< 50) */
        uint8_t *e = &img[NVM_HDR_SIZE + 8u * i];
        if (le_get16(e) == PID_MOTION_JOG_TIMEOUT_MS) {
            le_put32(&e[4], 5u);
        }
    }
    reseal(img);
    put_record(img, len);
    h_reboot();
    TEST_ASSERT_EQUAL_UINT16(1u, ev_arg(EV_PARAMS_LOADED));
    TEST_ASSERT_EQUAL_UINT32(250u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY, h_status().b[19] & (SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED));
}

static void test_migration_retired_id(void)
{
    static uint8_t img[NVM_SLOT_SIZE];
    params_t p;
    uint16_t len;
    params_set_defaults(&p);
    p.motion.jog_timeout_ms = 333u;
    len = nvm_build(&p, 5u, 1u, img);
    le_put16(&img[NVM_HDR_SIZE], 0x0401u);                           /* first entry -> retired id */
    le_put32(&img[16], 0xB046DD01u);                                 /* dict v2 hash */
    reseal(img);
    put_record(img, len);
    h_reboot();
    TEST_ASSERT_EQUAL_UINT16(PDEF_MIGRATION, ev_arg(EV_PARAMS_DEFAULTED));
    TEST_ASSERT_EQUAL_UINT32(333u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));    /* kept by id */
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED,
                           h_status().b[19] & (SYSF_CFG_DIRTY | SYSF_NVM_DEFAULTED));
}

static void test_hard_rule_record(void)
{
    static uint8_t img[NVM_SLOT_SIZE];
    params_t p;
    uint16_t len;
    params_set_defaults(&p);
    p.limits.soft_min_um = 300000;                                   /* > soft_max: H1 violated */
    p.motion.jog_timeout_ms = 333u;
    len = nvm_build(&p, 5u, 1u, img);
    put_record(img, len);
    h_reboot();
    TEST_ASSERT_EQUAL_UINT16(PDEF_HARD_RULE, ev_arg(EV_PARAMS_DEFAULTED));
    TEST_ASSERT_EQUAL_UINT32(250u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));    /* all defaults */
    h_set_param(PID_MOTION_JOG_TIMEOUT_MS, PARAM_T_U16, 444u);
    h_expect_nack(h_cmd(CMD_LOAD_PARAMS, 4u, NULL, 0u), ST_E_NVM, NVMD_NO_RECORD);
    TEST_ASSERT_EQUAL_UINT32(444u, h_get_param_raw(PID_MOTION_JOG_TIMEOUT_MS));    /* RAM unchanged */
}

static void test_crc_error_both(void)
{
    static uint8_t img[NVM_SLOT_SIZE];
    params_t p;
    uint16_t len;
    params_set_defaults(&p);
    len = nvm_build(&p, 5u, 1u, img);
    img[50] ^= 0x10u;
    put_record(img, len);
    memcpy(fake_flash_sector(1u), img, len);
    h_reboot();
    TEST_ASSERT_EQUAL_UINT16(PDEF_CRC_ERROR, ev_arg(EV_PARAMS_DEFAULTED));
    save_ok();                                                       /* the log skips the bad slot */
    h_reboot();
    TEST_ASSERT_TRUE(fake_cap_event(EV_PARAMS_LOADED, 0u) >= 0);
}

static void test_seq_wrap_newest(void)
{
    static uint8_t a[NVM_SLOT_SIZE], b[NVM_SLOT_SIZE];
    params_t p;
    nvm_newest_t nw;
    nvm_target_t t;
    params_set_defaults(&p);
    fake_flash_blank();
    p.motion.jog_timeout_ms = 101u;
    (void)nvm_build(&p, 0xFFFFFFFFu, 1u, a);
    p.motion.jog_timeout_ms = 102u;
    (void)nvm_build(&p, 0x00000002u, 1u, b);                        /* newer across the wrap */
    memcpy(fake_flash_sector(0u), a, NVM_SLOT_SIZE);
    memcpy(&fake_flash_sector(0u)[NVM_SLOT_SIZE], b, NVM_SLOT_SIZE);
    nvm_scan(fake_flash_sector(0u), fake_flash_sector(1u), &nw);
    TEST_ASSERT_TRUE(nw.found);
    TEST_ASSERT_EQUAL_UINT16(1u, nw.slot);
    nvm_next_target(fake_flash_sector(0u), fake_flash_sector(1u), &nw, &t);
    TEST_ASSERT_EQUAL_UINT32(3u, t.seq);
    TEST_ASSERT_EQUAL_UINT16(2u, t.slot);
    TEST_ASSERT_FALSE(t.erase_first);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_codec_roundtrip_and_crc);
    RUN_TEST(test_blank_boot_and_load_refused);
    RUN_TEST(test_set_save_reboot_restore);
    RUN_TEST(test_load_and_default);
    RUN_TEST(test_log_across_sectors);
    RUN_TEST(test_power_cut_every_word);
    RUN_TEST(test_power_cut_in_erase);
    RUN_TEST(test_program_failure_reported);
    RUN_TEST(test_out_of_range_entry_replaced);
    RUN_TEST(test_migration_retired_id);
    RUN_TEST(test_hard_rule_record);
    RUN_TEST(test_crc_error_both);
    RUN_TEST(test_seq_wrap_newest);
    return UNITY_END();
}
