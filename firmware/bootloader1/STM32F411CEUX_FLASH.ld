/* Bootloader partition:
 * - Code: 0x08000000 .. 0x0800BFFF (48KB, sectors 0-2)
 */
MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 48K
  RAM     (xrw) : ORIGIN = 0x20000000, LENGTH = 92K
  LOG_RAM (xrw) : ORIGIN = 0x20017000, LENGTH = 4K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
  .isr_vector :
  {
    KEEP(*(.isr_vector))
  } > FLASH

  .text :
  {
    *(.text*) *(.rodata*) *(.glue_7) *(.glue_7t)
    KEEP(*(.init)) KEEP(*(.fini))
  } > FLASH

  .ARM.extab   : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
  .ARM.exidx   : { *(.ARM.exidx* .gnu.linkonce.armexidx.*) } > FLASH

  /* Place data in RAM, but store/load image from FLASH */
  .data : 
  {
    _sdata = .;
    *(.data*)
    _edata = .;
  } > RAM AT > FLASH

  .bss (NOLOAD) :
  {
    _sbss = .;
    *(.bss*) *(COMMON)
    _ebss = .;
  } > RAM

  /* Shared boot/app RAM log area. NOLOAD keeps content over software reset.
   * Place variables with __attribute__((section(".shared_log"))). */
  .shared_log (NOLOAD) :
  {
    . = ALIGN(4);
    __shared_log_start__ = .;
    KEEP(*(.shared_log))
    KEEP(*(.shared_log*))
    . = ALIGN(4);
    __shared_log_end__ = .;
  } > LOG_RAM

  /* Symbols used by sysmem.c (_sbrk). */
  _end = .;
}

/* startup uses these */
PROVIDE(_sidata = LOADADDR(.data));
PROVIDE(_Min_Stack_Size = 0x400);
PROVIDE(SHARED_LOG_START = ORIGIN(LOG_RAM));
PROVIDE(SHARED_LOG_SIZE  = LENGTH(LOG_RAM));
