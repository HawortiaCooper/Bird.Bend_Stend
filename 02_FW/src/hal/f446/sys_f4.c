/* System seam on the F446 (FW_design §5.1): reset cause (RCC->CSR via pure resetcause, flags
 * cleared), software reset, 96-bit UID, clock fallback flag, stack painting / high-water mark,
 * critical sections (PRIMASK / BASEPRI), HardFault and NMI (CSS) handlers with a .noinit fault
 * record reported once through hal_fault_record() (seam v1.2) in the BOOT EVENT.
 * Origin: Thrust_Stand_HAW/02_FW/src/sys/{reset_cause,stack,faults}.cpp @37c8747 (adapted, C).
 * Implements: SAF-FW-018, SAF-FW-019 (IWDG cause), NFR-005 (stack_free_min), NFR-007 (critical
 *             sections), FW-CFG-004 (UID)
 */
#include <string.h>

#include "f446.h"
#include "hal_step.h"
#include "hal_sys.h"
#include "irq_prio.h"
#include "resetcause.h"

#define PAINT        0xA5C3A5C3u
#define CRIT_PRIMASK 0x80000000u
#define FAULT_MAGIC  0xFA017EC0u

typedef struct {
    uint32_t magic;
    uint32_t pc;
    uint32_t cfsr;
    uint32_t count;
} fault_rec_t;

__attribute__((section(".noinit.fault"))) static fault_rec_t s_fault;

extern uint32_t end;                 /* ldscript: start of ._user_heap_stack (heap size 0) */

static uint8_t   s_cause;
static uint32_t *s_lo;
static uint32_t *s_hi;

void sys_capture_reset_cause(void)
{
    s_cause = resetcause(RCC->CSR);
    RCC->CSR |= RCC_CSR_RMVF;
}

uint8_t hal_reset_cause(void) { return s_cause; }

void hal_reset(void)
{
    NVIC_SystemReset();
}

void hal_uid(uint8_t uid[12])
{
    memcpy(uid, (const void *)UID_BASE, 12u);        /* 0x1FFF7A10 (RM0390 §39.1) */
}

bool hal_clk_fallback(void) { return clock_hsi_fallback(); }

void sys_stack_paint(void)
{
    uint32_t *p;
    s_lo = (uint32_t *)(((uintptr_t)&end + 7u) & ~(uintptr_t)7u);
    s_hi = (uint32_t *)((__get_MSP() - 256u) & ~3u);
    for (p = s_lo; p < s_hi; p++) {
        *p = PAINT;
    }
}

uint16_t hal_stack_free_min(void)
{
    const uint32_t *p = s_lo;
    uint32_t b;
    if (s_lo == NULL) {
        return 0u;
    }
    while (p < s_hi && *p == PAINT) {
        p++;
    }
    b = (uint32_t)((uintptr_t)p - (uintptr_t)s_lo);
    return (b > 0xFFFFu) ? 0xFFFFu : (uint16_t)b;
}

hal_crit_t hal_crit_enter(hal_crit_level_t level)
{
    uint32_t old;
    switch (level) {
    case HAL_CRIT_HALT:
        old = __get_PRIMASK();
        __disable_irq();
        return CRIT_PRIMASK | old;
    case HAL_CRIT_AFE:
    case HAL_CRIT_MOTION:
        old = __get_BASEPRI();
        __set_BASEPRI_MAX(BASEPRI_MOTION);
        return old;
    case HAL_CRIT_DATA:
        old = __get_BASEPRI();
        __set_BASEPRI_MAX(BASEPRI_DATA);
        return old;
    case HAL_CRIT_TICK:
    default:
        old = __get_BASEPRI();
        __set_BASEPRI_MAX(BASEPRI_TICK);
        return old;
    }
}

void hal_crit_exit(hal_crit_t saved)
{
    if ((saved & CRIT_PRIMASK) != 0u) {
        if ((saved & 1u) == 0u) {
            __enable_irq();
        }
    } else {
        __set_BASEPRI(saved);
    }
}

/* seam v1.2 (OI-FW-32): the record of the previous run, once */
bool hal_fault_record(uint32_t *pc, uint32_t *cfsr)
{
    if (s_fault.magic != FAULT_MAGIC) {
        return false;
    }
    *pc = s_fault.pc;
    *cfsr = s_fault.cfsr;
    s_fault.magic = 0u;
    return true;
}

/* ---- faults: pulses cut first (TRUNCATE), record, reset ---- */
__attribute__((used)) void hardfault_record(const uint32_t *sp)
{
    (void)hal_step_abort();
    s_fault.magic = FAULT_MAGIC;
    s_fault.pc = sp[6];
    s_fault.cfsr = SCB->CFSR;
    s_fault.count++;
    NVIC_SystemReset();
}

__attribute__((naked)) void HardFault_Handler(void)
{
    __asm volatile("tst lr, #4      \n"
                   "ite eq          \n"
                   "mrseq r0, msp   \n"
                   "mrsne r0, psp   \n"
                   "b hardfault_record \n");
}

void NMI_Handler(void)
{
    (void)hal_step_abort();
    if ((RCC->CIR & RCC_CIR_CSSF) != 0u) {
        RCC->CIR |= RCC_CIR_CSSC;                     /* HSE lost (CSS) */
    }
    s_fault.magic = FAULT_MAGIC;
    s_fault.pc = 0u;
    s_fault.cfsr = 0xFFFFFFFFu;
    s_fault.count++;
    NVIC_SystemReset();
}
