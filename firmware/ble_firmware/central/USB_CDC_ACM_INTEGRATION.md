# USB CDC ACM Integration Guide

## Overview

USB CDC ACM (Communications Device Class - Abstract Control Model) has been successfully integrated into your nRF52840 BLE central project. This integration allows the device to communicate via USB while maintaining full BLE functionality, without conflicts with USB DFU.

## What Was Changed

### 1. **SDK Configuration (sdk_config.h)**
- ✅ Enabled `NRFX_USBD_ENABLED` = 1 (Low-level USB peripheral driver)
- ✅ Enabled `USBD_ENABLED` = 1 (nRF USB wrapper layer)
- ✅ Enabled `APP_USBD_ENABLED` = 1 (Application-level USB library)
- ✅ Enabled `APP_USBD_CDC_ACM_ENABLED` = 1 (CDC ACM class support)
- ✅ Set USB Vendor ID (VID) = 0x1915 (Nordic Semiconductor)
- ✅ Set USB Product ID (PID) = 0x520F (Nordic CDC ACM Demo)

**Note:** These VIDs/PIDs are for development only. For production, obtain your own VID from usb.org.

### 2. **Build Configuration (Makefile)**
Added USB-related source files:
- `app_usbd.c` - USB Device library core
- `app_usbd_cdc_acm.c` - CDC ACM class implementation
- `app_usbd_core.c` - USB core functionality
- `app_usbd_serial_num.c` - Serial number generation
- `app_usbd_string_desc.c` - String descriptor handling
- `nrfx_usbd.c` - Hardware USB driver
- `app_fifo.c` - FIFO buffer for data handling
- `app_uart_fifo.c` - UART/USB bridge support
- `nrf_drv_power.c` - Power management

Added necessary include directories for USB functionality.

### 3. **USB CDC Handler Module**
Two new files were created:
- **usb_cdc_acm.h** - Public API header
- **usb_cdc_acm.c** - Implementation

Key functions provided:
```c
ret_code_t usb_cdc_acm_init(void);           // Initialize USB CDC ACM
ret_code_t usb_cdc_acm_putchar(char c);      // Send single character
ret_code_t usb_cdc_acm_write(uint8_t *data, size_t len);  // Send data block
ret_code_t usb_cdc_acm_getchar(char *c);     // Receive single character
bool usb_cdc_acm_is_connected(void);         // Check connection status
bool usb_cdc_acm_process(void);              // Process USB events
```

### 4. **Main Application Integration**
- ✅ Added USB CDC ACM header include
- ✅ Added USB CDC ACM initialization in main()
- ✅ Added USB event processing in main loop

## Building the Project

### Prerequisites
- Nordic nRF5 SDK 17.1.0
- ARM GNU Embedded Toolchain (recommended version 10.3)
- make build system

### Build Steps

```bash
cd d:\HOC\S\STM32\IDE\Do an tot nghiep\uwb-rtls\firmware\ble_firmware\central\armgcc

# Clean previous builds
make clean

# Build the project
make

# Expected output
# Output directory: _build/
# Executable: _build/nrf52840_xxaa.out
# Hex file: _build/nrf52840_xxaa.hex
```

## USB DFU Compatibility

| Feature | Status | Notes |
|---------|--------|-------|
| USB CDC ACM | ✅ Enabled | Used for serial communication |
| BLE DFU | ✅ Compatible | Firmware update via BLE (if configured) |
| USB DFU | ⚠️ Reserved | Not enabled to avoid conflicts |
| BLE Central | ✅ Active | Full BLE scanning and connection |

**Important:** DFU and CDC ACM use different transport mechanisms:
- **BLE DFU**: Uses BLE GATT service (no conflict)
- **USB CDC ACM**: Uses USB endpoints (CDC ACM)
- Resources are properly isolated to prevent conflicts

## Usage Example

### Serial Communication via USB

```c
// After initialization, device appears as COM port on Windows
// or /dev/ttyACM0 on Linux/Mac

// Send data
uint8_t data[] = "Hello from nRF52840\r\n";
ret_code_t ret = usb_cdc_acm_write(data, sizeof(data)-1);

// Check if connected
if (usb_cdc_acm_is_connected())
{
    NRF_LOG_INFO("USB is connected!");
}

// Single character I/O
usb_cdc_acm_putchar('A');
```

### Using with Log Backend

The nRF Log system can be configured to use USB CDC ACM as a backend. By default, it uses UART. To route logs to USB:

1. Modify sdk_config.h:
   - Set `NRF_LOG_BACKEND_UART_ENABLED` = 0
   - Add USB CDC ACM backend handler

2. Call `usb_cdc_acm_process()` in main loop (already done)

## Endpoint Configuration

| Endpoint | Direction | Purpose |
|----------|-----------|---------|
| EP IN 1 | Device → Host | CDC ACM Data TX |
| EP OUT 1 | Host → Device | CDC ACM Data RX |
| EP IN 2 | Device → Host | CDC ACM Control (Notifications) |

These endpoints are configured in `usb_cdc_acm.c`.

## Testing on Windows/Linux/Mac

### Windows
1. Connect device via USB
2. Device appears as "nRF52 CDC ACM Demo" in Device Manager
3. Assign a COM port if needed
4. Use PuTTY, Tera Term, or Windows Terminal to open the COM port

### Linux
```bash
# Identify device
ls /dev/ttyACM*

# Use minicom or screen
screen /dev/ttyACM0 115200
# or
minicom -D /dev/ttyACM0 -b 115200
```

### macOS
```bash
# Identify device
ls /dev/tty.usbmodem*

# Use screen
screen /dev/tty.usbmodem14201 115200
```

## NRF5_SDK_17.1.0 Compatibility

✅ All modifications follow Nordic Semiconductor nRF5 SDK 17.1.0 standards:
- Uses recommended configuration structure
- Compatible with S140 SoftDevice
- Proper memory management for USB buffers
- Respects reserved interrupt priorities for SoftDevice

## Troubleshooting

### Issue: "USB not detected"
- **Solution:** Ensure device powers on. Check USB cable. Verify sdk_config.h settings.

### Issue: "Compilation errors related to USB"
- **Solution:** Verify Makefile includes all USB source files. Check SDK_ROOT path is correct.

### Issue: "BLE not working after adding USB"
- **Solution:** Check that `usb_cdc_acm_process()` is not blocking. It should return quickly for non-blocking operation.

### Issue: "No data received over USB"
- **Solution:** 
  1. Verify host recognizes the device
  2. Check port is opened at correct baud rate (USB CDC is virtual, any rate works)
  3. Ensure device has USB power

## Memory Usage

Expected RAM increase: ~2KB for USB buffers
Expected Flash increase: ~15-20KB for USB library code

Monitor during compilation with:
```bash
make
# Look for "text data bss dec hex filename" in build output
```

## Future Enhancements

You can extend this implementation with:
1. **RX buffer processing** - Currently uses single-byte reads; implement ring buffer for higher throughput
2. **Flow control** - Add RTS/CTS support
3. **Multiple interfaces** - Add HID or MSC alongside CDC ACM
4. **Dual endpoint** - Use more endpoints for parallel transfers

## Support & References

- **nRF5 SDK Documentation**: `../nRF5_SDK_17.1.0_ddde560/documentation/`
- **Example Reference**: `../nRF5_SDK_17.1.0_ddde560/examples/peripheral/usbd_cdc_acm/`
- **Nordic DevZone**: https://devzone.nordicsemi.com

## Notes

- ✅ No conflicts with existing DFU functionality
- ✅ BLE central functionality preserved
- ✅ USB power detection implemented
- ✅ Proper error handling with error codes
- ✅ Logging integration for debugging

---
**Date Created:** 2025-03-27  
**SDK Version:** nRF5 SDK 17.1.0  
**MCU Target:** nRF52840
