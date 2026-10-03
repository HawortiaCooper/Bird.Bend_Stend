/* Every DATA flag / status bit, IO bit and sys_flag from a constructed state, each bit alone.
 * Verifies: FW-STR-003, FW-CMD-004
 */
#include <string.h>
#include <unity.h>

#include "flags.h"
#include "proto_gen.h"

void setUp(void) {}
void tearDown(void) {}

#define CHECK_D(field, bit) do { flags_in_t f; memset(&f, 0, sizeof f); f.field = true; \
    TEST_ASSERT_EQUAL_HEX8_MESSAGE((uint8_t)(bit), flags_data(&f), #field); \
    TEST_ASSERT_EQUAL_HEX16_MESSAGE(0u, flags_status(&f), #field); } while (0)
#define CHECK_S(field, bit) do { flags_in_t f; memset(&f, 0, sizeof f); f.field = true; \
    TEST_ASSERT_EQUAL_HEX16_MESSAGE((uint16_t)(bit), flags_status(&f), #field); \
    TEST_ASSERT_EQUAL_HEX8_MESSAGE(0u, flags_data(&f), #field); } while (0)
#define CHECK_IO(field, bit) do { io_in_t f; memset(&f, 0, sizeof f); f.field = true; \
    TEST_ASSERT_EQUAL_HEX16_MESSAGE((uint16_t)(bit), flags_io(&f), #field); } while (0)

static void test_data_flags(void)
{
    CHECK_D(valid, DF_VALID); CHECK_D(moving, DF_MOVING); CHECK_D(homed, DF_HOMED);
    CHECK_D(enabled, DF_ENABLED); CHECK_D(estop, DF_ESTOP); CHECK_D(halt, DF_HALT);
    CHECK_D(fault, DF_FAULT); CHECK_D(overrun, DF_OVERRUN);
}

static void test_data_status(void)
{
    CHECK_S(paused, DS_PAUSED); CHECK_S(limit_start, DS_LIMIT_START); CHECK_S(limit_end, DS_LIMIT_END);
    CHECK_S(load_limit, DS_LOAD_LIMIT); CHECK_S(afe_stale, DS_AFE_STALE);
    CHECK_S(afe_saturated, DS_AFE_SATURATED); CHECK_S(afe_settling, DS_AFE_SETTLING);
    CHECK_S(afe_rate_mismatch, DS_AFE_RATE_MISMATCH); CHECK_S(link_wdg, DS_LINK_WDG);
    CHECK_S(stop_btn, DS_STOP_BTN); CHECK_S(pause_btn, DS_PAUSE_BTN); CHECK_S(alm, DS_ALM);
    CHECK_S(pend, DS_PEND); CHECK_S(pos_uncertain, DS_POS_UNCERTAIN);
    CHECK_S(no_afe_data, DS_NO_AFE_DATA); CHECK_S(drv_pwr, DS_DRV_PWR);
}

static void test_io(void)
{
    CHECK_IO(estop_open, IO_ESTOP_OPEN); CHECK_IO(limit_start, IO_LIMIT_START);
    CHECK_IO(limit_end, IO_LIMIT_END); CHECK_IO(stop_btn, IO_STOP_BTN); CHECK_IO(pause_btn, IO_PAUSE_BTN);
    CHECK_IO(alm, IO_ALM); CHECK_IO(pend, IO_PEND); CHECK_IO(drv_pwr, IO_DRV_PWR);
    CHECK_IO(ena_disabled, IO_ENA_DISABLED); CHECK_IO(rate_80, IO_RATE_80);
}

static void test_sys_and_all(void)
{
    flags_in_t f;
    TEST_ASSERT_EQUAL_HEX8(SYSF_CLK_FALLBACK, flags_sys(true, false, false, false, false));
    TEST_ASSERT_EQUAL_HEX8(SYSF_CFG_DIRTY, flags_sys(false, true, false, false, false));
    TEST_ASSERT_EQUAL_HEX8(SYSF_STREAM_ON, flags_sys(false, false, true, false, false));
    TEST_ASSERT_EQUAL_HEX8(SYSF_REBOOT_PENDING, flags_sys(false, false, false, true, false));
    TEST_ASSERT_EQUAL_HEX8(SYSF_NVM_DEFAULTED, flags_sys(false, false, false, false, true));
    memset(&f, 1, sizeof f);
    TEST_ASSERT_EQUAL_HEX8(DF_DEFINED_MASK, flags_data(&f));
    TEST_ASSERT_EQUAL_HEX16(DS_DEFINED_MASK, flags_status(&f));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_data_flags);
    RUN_TEST(test_data_status);
    RUN_TEST(test_io);
    RUN_TEST(test_sys_and_all);
    return UNITY_END();
}
