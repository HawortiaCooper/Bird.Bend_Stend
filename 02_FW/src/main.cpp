/* Bird Bend Stand firmware entry (stm32duino): setup() -> board_init(), app_init(), board_start();
 * loop() -> app_loop(). SystemClock_Config() (hal/f446/clock.c) runs before setup() from the core.
 * The only C++ file (FW_design §1 principle 8); Arduino.h is not included.
 * Implements: FW-PLT-001
 */
#include "f446.h"
#include "fw.h"

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
    app_loop();
}
