/* HX711 read sequence (pure/hx711_seq.h) against a datasheet model of the device: 24 bits MSB first,
 * exactly 25 / 26 / 27 SCK pulses (gain / channel of the next conversion), DOUT high after the last
 * pulse, interrupts masked exactly while SCK is high, SCK high <= 0.6 us in the model (<= 50 us rule).
 * Origin: Thrust_Stand_HAW/02_FW/test/test_val_hx711/test_val_hx711.c @37c8747 (D-39; device model and
 * the first two test functions adapted; the TS averaging tests do not apply here).
 * Verifies: FW-AFE-001, FW-AFE-002
 */
#include <string.h>
#include <unity.h>

#include "hx711_math.h"
#include "params_gen.h"

typedef struct {
    uint32_t word;
    uint8_t  pulses;
    bool     sck;
    bool     dout;
    uint8_t  next_pulses;
    bool     masked;
    uint32_t violations;
    uint32_t t_ns, high_since_ns, max_high_ns;
    bool     powered_down;
} hxsim_t;
static hxsim_t H;

static void sim_load(uint32_t w)
{
    memset(&H, 0, sizeof H);
    H.word = w & 0xFFFFFFu;
    H.dout = false;
}
static void sim_rise(void)
{
    if (!H.masked) {
        H.violations++;
    }
    H.sck = true;
    H.high_since_ns = H.t_ns;
    H.pulses++;
    if (H.pulses <= 24u) {
        H.dout = ((H.word >> (24u - H.pulses)) & 1u) != 0u;
    } else {
        H.dout = true;
        H.next_pulses = H.pulses;
    }
}
static void sim_fall(void)
{
    uint32_t hi = H.t_ns - H.high_since_ns;
    if (hi > H.max_high_ns) {
        H.max_high_ns = hi;
    }
    if (hi > 60000u) {
        H.powered_down = true;
    }
    H.sck = false;
}
#define HX_SCK_HI()   sim_rise()
#define HX_SCK_LO()   sim_fall()
#define HX_DT()       (H.dout)
#define HX_MASK_ON()  do { if (H.masked) { H.violations++; } H.masked = true; } while (0)
#define HX_MASK_OFF() do { if (H.sck) { H.violations++; } H.masked = false; } while (0)
#define HX_T_HIGH()   (H.t_ns += 500u)
#define HX_T_LOW()    (H.t_ns += 500u)
#include "hx711_seq.h"

void setUp(void) {}
void tearDown(void) {}

static uint32_t rng = 0x2468ACE1u;
static uint32_t rnd(void)
{
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

static void test_shift_in_against_device_model(void)
{
    static const uint32_t fixed[8] = {0u, 1u, 0x7FFFFFu, 0x800000u, 0xFFFFFFu, 0x800001u, 0x123456u, 0xEDCBAAu};
    uint8_t p;
    uint32_t k;
    for (k = 0u; k < 20000u; k++) {
        uint32_t w = (k < 8u) ? fixed[k] : (rnd() & 0xFFFFFFu);
        for (p = 25u; p <= 27u; p++) {
            bool high = false;
            uint32_t v;
            sim_load(w);
            v = hx711_shift_in(p, &high);
            TEST_ASSERT_EQUAL_HEX32(w, v);
            TEST_ASSERT_EQUAL_UINT8(p, H.pulses);
            TEST_ASSERT_EQUAL_UINT8(p, H.next_pulses);
            TEST_ASSERT_TRUE(high);
            TEST_ASSERT_EQUAL_UINT32(0u, H.violations);
            TEST_ASSERT_FALSE(H.masked);
            TEST_ASSERT_FALSE(H.powered_down);
            TEST_ASSERT_TRUE(H.max_high_ns <= 600u);
        }
    }
}

static void test_gain_pulses_select_next_conversion(void)
{
    bool high = false;
    sim_load(0x123456u);
    (void)hx711_shift_in(hx711_pulses(AFE_GAIN_CHANNEL_A64), &high);
    TEST_ASSERT_EQUAL_UINT8(27u, H.next_pulses);
    sim_load(0x123456u);
    (void)hx711_shift_in(hx711_pulses(AFE_GAIN_CHANNEL_B32), &high);
    TEST_ASSERT_EQUAL_UINT8(26u, H.next_pulses);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_shift_in_against_device_model);
    RUN_TEST(test_gain_pulses_select_next_conversion);
    return UNITY_END();
}
