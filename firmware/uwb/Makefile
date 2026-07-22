# Standalone GNU Arm build

PROJECT := uwb-rtls
BUILD_DIR := build
TARGET := $(BUILD_DIR)/$(PROJECT)
REPO_ROOT := $(abspath $(CURDIR)/../..)

# Toolchain path handling
ifeq ($(strip $(GCC_PATH)),)
$(error GCC_PATH is not set. Please set GCC_PATH to your GNU Arm toolchain bin directory)
endif

# Accept GCC_PATH as either .../bin or toolchain root containing /bin
ifneq (,$(wildcard $(GCC_PATH)/arm-none-eabi-gcc$(if $(filter Windows_NT,$(OS)),.exe,)))
TOOLCHAIN_BIN := $(GCC_PATH)
else ifneq (,$(wildcard $(GCC_PATH)/bin/arm-none-eabi-gcc$(if $(filter Windows_NT,$(OS)),.exe,)))
TOOLCHAIN_BIN := $(GCC_PATH)/bin
else
$(error Cannot find arm-none-eabi-gcc under GCC_PATH='$(GCC_PATH)')
endif

CC := $(TOOLCHAIN_BIN)/arm-none-eabi-gcc
AS := $(TOOLCHAIN_BIN)/arm-none-eabi-gcc
OBJCOPY := $(TOOLCHAIN_BIN)/arm-none-eabi-objcopy
SIZE := $(TOOLCHAIN_BIN)/arm-none-eabi-size
PYTHON ?= python

CPUFLAGS := -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
OPTFLAGS := -O2 -g3
DEFS := -DDEBUG -DUSE_HAL_DRIVER -DSTM32F411xE

FW_GIT_SHA ?= $(shell git -C $(REPO_ROOT) rev-parse --short=16 HEAD 2>/dev/null | tr -d '\n' || echo 00000000)
FW_IMAGE_TIMESTAMP ?= $(shell date +%s 2>/dev/null || echo 0)
# FW_IMAGE_LENGTH and FW_IMAGE_CRC are only known after linking.
# Pass them explicitly (e.g. via a post-link script) to get real values;
# default 0 means the header fields will be 0 in normal development builds.
FW_IMAGE_LENGTH ?= 0
FW_IMAGE_CRC ?= 0

GENERATED_DIR := .
FW_IMAGE_META_HEADER := fw_image_meta.h
FOTA_PATCH_SCRIPT := tools/patch_fota_header.py
FW_IMAGE_META_SCRIPT := tools/generate_fw_image_meta.py

# Version is updated by firmware/uwb/tools/update_version.py before build

.PHONY: all clean rebuild patch_fota_header

# Build firmware
all: $(TARGET).elf $(TARGET).hex $(TARGET).bin patch_fota_header
	@$(SIZE) $(TARGET).elf

patch_fota_header: $(TARGET).hex $(TARGET).bin $(FOTA_PATCH_SCRIPT)
	@$(PYTHON) $(FOTA_PATCH_SCRIPT) --hex $(TARGET).hex --bin $(TARGET).bin

# Source discovery
SRC_DIRS := Core/Src Drivers USB_DEVICE app bsp deca middlewares sys
LOCAL_SOURCES := \
	$(wildcard Core/Src/*.c) \
	$(wildcard Drivers/CMSIS/*.c) $(wildcard Drivers/STM32F4xx_HAL_Driver/Src/*.c) $(wildcard Drivers/device_driver/*.c) \
	$(wildcard USB_DEVICE/App/*.c) $(wildcard USB_DEVICE/Target/*.c) \
	$(wildcard app/*.c) $(wildcard bsp/*.c) \
	$(wildcard deca/deca_driver/*.c) $(wildcard deca/platform/*.c) \
	$(wildcard middlewares/*.c) $(wildcard middlewares/ST/STM32_USB_Device_Library/Class/CDC/Src/*.c) $(wildcard middlewares/ST/STM32_USB_Device_Library/Core/Src/*.c) \
	$(wildcard middlewares/arm_libs/MatrixFunctions/*.c) \
	$(wildcard middlewares/Third_Party/FreeRTOS/Source/*.c) \
	$(wildcard middlewares/Third_Party/FreeRTOS/Source/CMSIS_RTOS_V2/*.c) \
	$(wildcard middlewares/Third_Party/FreeRTOS/Source/portable/GCC/ARM_CM4F/*.c) \
	$(wildcard middlewares/Third_Party/FreeRTOS/Source/portable/MemMang/*.c) \
	$(wildcard middlewares/Third_Party/SEGGER/RTT/RTT/*.c) \
	$(wildcard middlewares/Third_Party/SEGGER/RTT/RTT/*.S) \
	$(wildcard middlewares/Third_Party/SEGGER/SystemView/SYSVIEW/*.c) \
	$(wildcard middlewares/Third_Party/SEGGER/SystemView/Sample/FreeRTOSV10/*.c) \
	$(wildcard middlewares/Third_Party/SEGGER/SystemView/Sample/FreeRTOSV10/Config/Cortex-M/*.c) \
	$(wildcard sys/*.c)
COMMON_SOURCES := \
	$(wildcard $(REPO_ROOT)/firmware/common/serial/*.c) \
	$(wildcard $(REPO_ROOT)/firmware/common/network/*.c) \
	$(wildcard $(REPO_ROOT)/firmware/common/ble/*.c) \
	$(wildcard $(REPO_ROOT)/firmware/common/otp/*.c)
PROTOCOL_SOURCES := \
	$(REPO_ROOT)/protocol/protos/protocol.pb.c \
	$(REPO_ROOT)/protocol/nanopb/pb_common.c \
	$(REPO_ROOT)/protocol/nanopb/pb_decode.c \
	$(REPO_ROOT)/protocol/nanopb/pb_encode.c
STARTUP := Core/Startup/startup_stm32f411ceux.s

# Include directories
INCLUDE_DIRS := Core/Inc Drivers/CMSIS/Device/ST/STM32F4xx/Include Drivers/CMSIS/Include Drivers/STM32F4xx_HAL_Driver/Inc Drivers/STM32F4xx_HAL_Driver/Inc/Legacy Drivers/device_driver USB_DEVICE/App USB_DEVICE/Target app bsp deca deca/deca_driver deca/platform middlewares middlewares/arm_libs middlewares/arm_libs/Include middlewares/arm_libs/PrivateInclude middlewares/ST/STM32_USB_Device_Library/Class/CDC/Inc middlewares/ST/STM32_USB_Device_Library/Core/Inc sys middlewares/Third_Party/FreeRTOS/Source/include middlewares/Third_Party/FreeRTOS/Source/CMSIS_RTOS_V2 middlewares/Third_Party/FreeRTOS/Source/portable/GCC/ARM_CM4F $(REPO_ROOT)/protocol $(REPO_ROOT)/protocol/nanopb $(REPO_ROOT)/firmware/common $(REPO_ROOT)/firmware/common/otp $(GENERATED_DIR) \
	middlewares/Third_Party/SEGGER/SystemView/Config \
	middlewares/Third_Party/SEGGER/SystemView/SEGGER \
	middlewares/Third_Party/SEGGER/SystemView/SYSVIEW \
	middlewares/Third_Party/SEGGER/RTT/Config \
	middlewares/Third_Party/SEGGER/RTT/RTT \
	middlewares/Third_Party/SEGGER/SystemView/Sample/FreeRTOSV10
INCLUDES := $(addprefix -I,$(INCLUDE_DIRS))

CFLAGS := $(CPUFLAGS) -std=gnu11 $(OPTFLAGS) $(DEFS) $(INCLUDES) -ffunction-sections -fdata-sections -fstack-usage -Wall --specs=nano.specs
ASFLAGS := $(CPUFLAGS) -x assembler-with-cpp $(DEFS) $(INCLUDES) -g3

LDSCRIPT := STM32F411CEUX_FLASH.ld
LDFLAGS := $(CPUFLAGS) $(OPTFLAGS) -T$(LDSCRIPT) -Wl,-Map=$(TARGET).map,--gc-sections -Wl,--print-memory-usage -static -u _printf_float --specs=nano.specs --specs=nosys.specs -Wl,--start-group -lc -lm -Wl,--end-group

LOCAL_OBJECTS_C := $(patsubst %.c,$(BUILD_DIR)/%.o,$(filter %.c,$(LOCAL_SOURCES)))
LOCAL_OBJECTS_ASM := $(patsubst %.S,$(BUILD_DIR)/%.o,$(filter %.S,$(LOCAL_SOURCES))) $(patsubst %.s,$(BUILD_DIR)/%.o,$(filter %.s,$(LOCAL_SOURCES)))
LOCAL_OBJECTS := $(LOCAL_OBJECTS_C) $(LOCAL_OBJECTS_ASM)
COMMON_OBJECTS := $(patsubst $(REPO_ROOT)/%.c,$(BUILD_DIR)/%.o,$(COMMON_SOURCES))
PROTOCOL_OBJECTS := $(BUILD_DIR)/protocol/protos/protocol.pb.o \
	$(BUILD_DIR)/protocol/nanopb/pb_common.o \
	$(BUILD_DIR)/protocol/nanopb/pb_decode.o \
	$(BUILD_DIR)/protocol/nanopb/pb_encode.o
OBJECTS := $(LOCAL_OBJECTS) $(COMMON_OBJECTS) $(PROTOCOL_OBJECTS)
OBJECTS += $(BUILD_DIR)/$(STARTUP:.s=.o)

# Extract version from source for use in tag-release
VERSION_MAJOR := $(shell grep '#define FW_VERSION_MAJOR' app/version.h | tail -1 | grep -o '[0-9]\+$$')
VERSION_MINOR := $(shell grep '#define FW_VERSION_MINOR' app/version.h | tail -1 | grep -o '[0-9]\+$$')
VERSION_PATCH := $(shell grep '#define FW_VERSION_PATCH' app/version.h | tail -1 | grep -o '[0-9]\+$$')

$(FW_IMAGE_META_HEADER): $(FW_IMAGE_META_SCRIPT)
	@mkdir -p $(GENERATED_DIR)
	@$(PYTHON) $(FW_IMAGE_META_SCRIPT) \
		--output $(FW_IMAGE_META_HEADER) \
		--git-sha $(FW_GIT_SHA) \
		--timestamp $(FW_IMAGE_TIMESTAMP) \
		--image-length $(FW_IMAGE_LENGTH) \
		--image-crc $(FW_IMAGE_CRC)

$(TARGET).elf: $(OBJECTS)
	@mkdir -p $(dir $@)
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@

$(TARGET).hex: $(TARGET).elf
	$(OBJCOPY) -O ihex $< $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary -S $< $@

$(BUILD_DIR)/%.o: %.c $(FW_IMAGE_META_HEADER)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

$(BUILD_DIR)/protocol/%.o: $(REPO_ROOT)/protocol/%.c $(FW_IMAGE_META_HEADER)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

$(BUILD_DIR)/firmware/common/%.o: $(REPO_ROOT)/firmware/common/%.c $(FW_IMAGE_META_HEADER)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

$(BUILD_DIR)/%.o: %.s
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: %.S
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) -c $< -o $@

-include $(OBJECTS:.o=.d)

clean:
	rm -rf $(BUILD_DIR) build_obj

rebuild: clean all


