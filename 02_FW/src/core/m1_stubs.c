/* M1 stubs of core callbacks whose producers are M2 seams (FW_design §9): the step ISR callback
 * (no step generation in M1: any call requests a stop) and the input-edge callback (no input is
 * sampled in M1). They exist so that the twin can link the seam v1 set.
 * Implements: SYS-008 (complete callback set for the twin)
 */
#include "hal_inputs.h"
#include "hal_step.h"

step_next_t step_isr(void)
{
    step_next_t n;
    n.period = 0u;
    n.last = true;
    n.stop = true;
    return n;
}

void on_input_edge(uint8_t id, bool level, uint32_t t_us)
{
    (void)id;
    (void)level;
    (void)t_us;
}
