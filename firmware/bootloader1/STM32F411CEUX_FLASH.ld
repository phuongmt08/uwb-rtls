/* Bootloader partition:
 * - Code: 0x08000000 .. 0x0800BFFF (48KB, sectors 0-2)
 */
MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 48K
  RAM   (xrw) : ORIGIN = 0x20000000, LENGTH = 96K
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
}

/* startup uses these */
PROVIDE(_sidata = LOADADDR(.data));
