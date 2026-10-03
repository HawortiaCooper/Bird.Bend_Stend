/*
 * Bird Bend Stand - linker script for NUCLEO-F446RE (FW_design §2.1, §5.11; pinout §2).
 * = stm32duino variant script variants/STM32F4xx/F446R(C-E)T/ldscript.ld
 *   (framework-arduinoststm32 4.30000.0; sections unchanged) with three changes:
 *   1. FLASH is split: sector 0 (0x08000000, 16 KB) holds only .isr_vector; sectors 1 + 2
 *      (0x08004000..0x0800BFFF, 2 x 16 KB) are the NVM record log and receive NO load section
 *      (region NVM, nothing is placed there); code and constants start at sector 3 (0x0800C000).
 *      tools/check_map.py fails the build if any load address falls into the NVM hole.
 *   2. a NOLOAD .noinit output section after .bss (HardFault record, survives a reset).
 *   3. heap 0 (NFR-005: no allocator), main stack reserve 4 KB (FW_design §7).
 * Origin: Thrust_Stand_HAW/02_FW/ldscript/thrust_f103rb.ld @37c8747 (.noinit pattern).
 * Implements: FW-NVM-001 (NVM sectors reserved), NFR-005, FW-PLT-001
 */
ENTRY(Reset_Handler)

_estack = ORIGIN(RAM) + LENGTH(RAM);

_Min_Heap_Size = 0x0;      /* NFR-005: no heap */
_Min_Stack_Size = 0x1000;  /* 4 KB main stack (painted, stack_free_min in GET_STATUS) */

/* NVM record log (FW_design §5.11): flash sectors 1 and 2 */
__nvm_start = 0x08004000;
__nvm_end   = 0x0800C000;

MEMORY
{
  RAM       (xrw) : ORIGIN = 0x20000000, LENGTH = LD_MAX_DATA_SIZE
  FLASH_VEC (rx)  : ORIGIN = 0x08000000, LENGTH = 16K
  NVM       (r)   : ORIGIN = 0x08004000, LENGTH = 32K
  FLASH     (rx)  : ORIGIN = 0x0800C000, LENGTH = LD_MAX_SIZE - 48K
}

SECTIONS
{
  /* vector table alone in sector 0 */
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } >FLASH_VEC

  .text :
  {
    . = ALIGN(4);
    *(.text)
    *(.text*)
    *(.glue_7)
    *(.glue_7t)
    *(.eh_frame)

    KEEP (*(.init))
    KEEP (*(.fini))

    . = ALIGN(4);
    _etext = .;
  } >FLASH

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata*)
    . = ALIGN(4);
  } >FLASH

  .ARM.extab (READONLY) : {
    . = ALIGN(4);
    *(.ARM.extab* .gnu.linkonce.armextab.*)
    . = ALIGN(4);
  } >FLASH

  .ARM (READONLY) : {
    . = ALIGN(4);
    __exidx_start = .;
    *(.ARM.exidx*)
    __exidx_end = .;
    . = ALIGN(4);
  } >FLASH

  .preinit_array (READONLY) :
  {
    . = ALIGN(4);
    PROVIDE_HIDDEN (__preinit_array_start = .);
    KEEP (*(.preinit_array*))
    PROVIDE_HIDDEN (__preinit_array_end = .);
    . = ALIGN(4);
  } >FLASH

  .init_array (READONLY) :
  {
    . = ALIGN(4);
    PROVIDE_HIDDEN (__init_array_start = .);
    KEEP (*(SORT(.init_array.*)))
    KEEP (*(.init_array*))
    PROVIDE_HIDDEN (__init_array_end = .);
    . = ALIGN(4);
  } >FLASH

  .fini_array (READONLY) :
  {
    . = ALIGN(4);
    PROVIDE_HIDDEN (__fini_array_start = .);
    KEEP (*(SORT(.fini_array.*)))
    KEEP (*(.fini_array*))
    PROVIDE_HIDDEN (__fini_array_end = .);
    . = ALIGN(4);
  } >FLASH

  _sidata = LOADADDR(.data);

  /* .RamFunc (flash erase/program loop; M2: level-0/1 handlers, halt primitive) runs from RAM */
  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data)
    *(.data*)
    . = ALIGN(4);
    __ramfunc_start = .;
    *(.RamFunc)
    *(.RamFunc*)
    __ramfunc_end = .;

    . = ALIGN(4);
    _edata = .;
  } >RAM AT> FLASH

  . = ALIGN(4);
  .bss :
  {
    _sbss = .;
    __bss_start__ = _sbss;
    *(.bss)
    *(.bss*)
    *(COMMON)

    . = ALIGN(4);
    _ebss = .;
    __bss_end__ = _ebss;
  } >RAM

  /* not copied, not zeroed: HardFault record (FW_design §5.1) */
  .noinit (NOLOAD) :
  {
    . = ALIGN(4);
    _snoinit = .;
    KEEP(*(.noinit.fault))
    *(.noinit)
    *(.noinit.*)
    . = ALIGN(4);
    _enoinit = .;
  } >RAM

  ._user_heap_stack :
  {
    . = ALIGN(8);
    PROVIDE ( end = . );
    PROVIDE ( _end = . );
    . = . + _Min_Heap_Size;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
  } >RAM

  /DISCARD/ :
  {
    libc.a ( * )
    libm.a ( * )
    libgcc.a ( * )
  }

  .ARM.attributes 0 : { *(.ARM.attributes) }
}
