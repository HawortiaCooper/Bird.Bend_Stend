/* Independent watchdog (FW_design §5.13): run window PR /8, RLR 190 -> 32.5 ... 89.9 ms over the
 * LSI range 47 ... 17 kHz; long window for NVM erase/program PR /32, RLR 4095 -> >= 2.79 s; kicked
 * by the main loop only while the control tick advances (core/app.c). Debug image: frozen on halt.
 * Origin: Thrust_Stand_HAW/02_FW/src/sys/iwdg.cpp @37c8747 (adapted to F4 registers).
 * Implements: SAF-FW-019
 */
#include "f446.h"
#include "fw_config.h"
#include "hal_sys.h"

#define PR_DIV8     1u
#define RLR_RUN     190u
#define PR_DIV32    3u
#define RLR_LONG    4095u

static bool s_running;

static void wait_update(void)
{
    uint32_t i;
    for (i = 0u; i < 200000u && IWDG->SR != 0u; i++) {      /* bounded (~5 LSI periods) */
    }
}

static void configure(uint32_t pr, uint32_t rlr)
{
    wait_update();
    IWDG->KR = 0x5555u;                                     /* unlock PR / RLR */
    IWDG->PR = pr;
    IWDG->RLR = rlr;
    wait_update();
    IWDG->KR = 0xAAAAu;
}

void iwdg_start(void)
{
#if defined(FW_DBG_FREEZE_WDG) && FW_DBG_FREEZE_WDG
    DBGMCU->APB1FZ |= DBGMCU_APB1_FZ_DBG_IWDG_STOP;
#endif
    IWDG->KR = 0xCCCCu;                                     /* start (LSI on) */
    configure(PR_DIV8, RLR_RUN);
    s_running = true;
}

void hal_wdg_kick(void)
{
    if (s_running) {
        IWDG->KR = 0xAAAAu;
    }
}

void hal_wdg_set_timeout(uint32_t ms)
{
    if (!s_running) {
        return;
    }
    if (ms > WDG_RUN_TIMEOUT_MS) {
        configure(PR_DIV32, RLR_LONG);
    } else {
        configure(PR_DIV8, RLR_RUN);
    }
}
