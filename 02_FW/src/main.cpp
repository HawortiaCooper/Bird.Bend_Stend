/* Bird Bend Stand firmware entry (stm32duino): setup() -> board_init(), app_init(), board_start();
 * loop() -> app_loop(). SystemClock_Config() (hal/f446/clock.c) runs before setup() from the core.
 * The only C++ file (FW_design §1 principle 8); Arduino.h is not included.
 * Implements: FW-PLT-001
 */
#include "f446.h"
#include "fw.h"
#include "meas_dwt.h"

extern "C" void setup(void);
extern "C" void loop(void);

void setup()
{
    board_init();
    app_init();
    board_start();
}

void loop()
{
    MDWT_T0(t0);                                     /* HW_MEAS_DWT only (OI-FW-37) */
    app_loop();
    MDWT_END(MDWT_MAIN_LOOP, t0);
}
