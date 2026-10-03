/* Validator E - DATA flags/status, STATUS io and sys_flags composition (independent suite).
 * Oracle: bit positions of ICD §7.6 from ref_codec tables (bits.txt via gen_val_vectors.py); each
 * input of the pure composer must set exactly its own bit and nothing else.
 *
 * Verifies: FW-STR-003, FW-CMD-004 (io / sys_flags bits), FW-PLT-002 (CLK_FALLBACK only in sys_flags)
 * TC: TC-FW-STR-003-01 (U part), TC-FW-CMD-004-01 (U part)
 */
#include "../val_common/val_io.h"

#include <stddef.h>

#include "flags.h"

void setUp(void) {}
void tearDown(void) {}

static char T[VAL_TOK];

typedef struct {
    const char *tab, *name;
    size_t off;        /* offset of the bool in flags_in_t / io_in_t, or the sys argument index */
} map_t;

#define FO(f) offsetof(flags_in_t, f)
#define IOO(f) offsetof(io_in_t, f)

static const map_t MAP[] = {
    {"DF", "VALID", FO(valid)}, {"DF", "MOVING", FO(moving)}, {"DF", "HOMED", FO(homed)},
    {"DF", "ENABLED", FO(enabled)}, {"DF", "ESTOP", FO(estop)}, {"DF", "HALT", FO(halt)},
    {"DF", "FAULT", FO(fault)}, {"DF", "OVERRUN", FO(overrun)},
    {"DS", "PAUSED", FO(paused)}, {"DS", "LIMIT_START", FO(limit_start)}, {"DS", "LIMIT_END", FO(limit_end)},
    {"DS", "LOAD_LIMIT", FO(load_limit)}, {"DS", "AFE_STALE", FO(afe_stale)},
    {"DS", "AFE_SATURATED", FO(afe_saturated)}, {"DS", "AFE_SETTLING", FO(afe_settling)},
    {"DS", "AFE_RATE_MISMATCH", FO(afe_rate_mismatch)}, {"DS", "LINK_WDG", FO(link_wdg)},
    {"DS", "STOP_BTN", FO(stop_btn)}, {"DS", "PAUSE_BTN", FO(pause_btn)}, {"DS", "ALM", FO(alm)},
    {"DS", "PEND", FO(pend)}, {"DS", "POS_UNCERTAIN", FO(pos_uncertain)},
    {"DS", "NO_AFE_DATA", FO(no_afe_data)}, {"DS", "DRV_PWR", FO(drv_pwr)},
    {"IO", "ESTOP_OPEN", IOO(estop_open)}, {"IO", "LIMIT_START", IOO(limit_start)},
    {"IO", "LIMIT_END", IOO(limit_end)}, {"IO", "STOP_BTN", IOO(stop_btn)}, {"IO", "PAUSE_BTN", IOO(pause_btn)},
    {"IO", "ALM", IOO(alm)}, {"IO", "PEND", IOO(pend)}, {"IO", "DRV_PWR", IOO(drv_pwr)},
    {"IO", "ENA_DISABLED", IOO(ena_disabled)}, {"IO", "RATE_80", IOO(rate_80)},
    {"SY", "CLK_FALLBACK", 0u}, {"SY", "CFG_DIRTY", 1u}, {"SY", "STREAM_ON", 2u}, {"SY", "REBOOT_PENDING", 3u},
    {"SY", "NVM_DEFAULTED", 4u},
};

static const map_t *find(const char *tab, const char *name)
{
    size_t i;
    for (i = 0u; i < sizeof MAP / sizeof MAP[0]; i++) {
        if (strcmp(MAP[i].tab, tab) == 0 && strcmp(MAP[i].name, name) == 0) {
            return &MAP[i];
        }
    }
    return NULL;
}

static void test_each_input_sets_exactly_its_bit(void)
{
    val_file_t v;
    uint32_t done = 0u;
    val_open(&v, "bits.txt");
    while (val_tok(&v, T)) {
        char tab[8], name[64], msg[96];
        uint32_t bit;
        const map_t *m;
        TEST_ASSERT_EQUAL_STRING("B", T);
        val_tok(&v, tab);
        val_tok(&v, name);
        bit = val_u32(&v);
        snprintf(msg, sizeof msg, "%s.%s bit %u", tab, name, bit);
        m = find(tab, name);
        TEST_ASSERT_NOT_NULL_MESSAGE(m, msg);    /* every ICD bit has an input in the composer */
        if (strcmp(tab, "DF") == 0 || strcmp(tab, "DS") == 0) {
            flags_in_t f;
            memset(&f, 0, sizeof f);
            *(bool *)((uint8_t *)&f + m->off) = true;
            if (strcmp(tab, "DF") == 0) {
                TEST_ASSERT_EQUAL_HEX8_MESSAGE(1u << bit, flags_data(&f), msg);
                TEST_ASSERT_EQUAL_HEX16_MESSAGE(0u, flags_status(&f), msg);
            } else {
                TEST_ASSERT_EQUAL_HEX16_MESSAGE(1u << bit, flags_status(&f), msg);
                TEST_ASSERT_EQUAL_HEX8_MESSAGE(0u, flags_data(&f), msg);
            }
        } else if (strcmp(tab, "IO") == 0) {
            io_in_t io;
            memset(&io, 0, sizeof io);
            *(bool *)((uint8_t *)&io + m->off) = true;
            TEST_ASSERT_EQUAL_HEX16_MESSAGE(1u << bit, flags_io(&io), msg);
        } else {
            bool a[5] = {false, false, false, false, false};
            a[m->off] = true;
            TEST_ASSERT_EQUAL_HEX8_MESSAGE(1u << bit, flags_sys(a[0], a[1], a[2], a[3], a[4]), msg);
        }
        done++;
    }
    val_close(&v, done);
}

static void test_all_inputs_all_bits(void)
{
    flags_in_t f;
    io_in_t io;
    memset(&f, 1, sizeof f);
    memset(&io, 1, sizeof io);
    TEST_ASSERT_EQUAL_HEX8(0xFFu, flags_data(&f));
    TEST_ASSERT_EQUAL_HEX16(0xFFFFu, flags_status(&f));        /* 16/16 DATA status bits used */
    TEST_ASSERT_EQUAL_HEX16(0x03FFu, flags_io(&io));
    TEST_ASSERT_EQUAL_HEX8(0x1Fu, flags_sys(true, true, true, true, true));
}

int main(void)
{
    val_case_t c[] = {
        VAL_CASE(test_each_input_sets_exactly_its_bit),
        VAL_CASE(test_all_inputs_all_bits),
    };
    return val_run(c, sizeof c / sizeof c[0]);
}
