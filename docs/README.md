# UWB-RTLS Documentation Index

[GitHub Repository](https://github.com/phuongmt08/uwb-rtls)

This directory contains the comprehensive technical documentation for the **UWB-RTLS** embedded indoor positioning system, covering firmware architecture, RF ranging protocols, positioning mathematics, hardware specifications, and desktop tools.

---

## 1. Documentation Map

| Section | Document | Description |
| --- | --- | --- |
| **Setup & Build** | **[Getting Started Guide](getting_started.md)** | Step-by-step toolchain setup, `GCC_PATH` configuration, and build instructions |
| **Firmware** | **[Firmware Architecture](firmware/architecture.md)** | Embedded layers, FreeRTOS runtime tasks, role selection, and source map |
| **Firmware** | **[DS-TWR Ranging Protocol](firmware/ranging_protocol.md)** | Timestamp mechanics, TDMA frame timing budget, and failure handling |
| **Firmware** | **[Positioning Algorithms](firmware/positioning_algorithms.md)** | Mahalanobis prefiltering, Huber weighting, WGDOP selection, and 8-State UKF |
| **Hardware** | **[Hardware Specifications](hardware/schematics_and_specs.md)** | System components, MCU/UWB/IMU/BLE pinout mappings, and power architecture |
| **Hardware** | **[Antenna Delay Calibration](hardware/antenna_calibration.md)** | Calibration theory, baseline measurement methodology, and tuning formula |
| **Software** | **[RTLS Studio & Tools](software/rtls_studio_and_tools.md)** | PyQt6 MVVM architecture, live 2D tracking, session export, and USB DFU Programmer |
| **Deployment** | **[Deployment & Commissioning](deployment.md)** | Anchor placement guidelines, GDOP optimization, and commissioning checklist |
| **Assets** | **[Asset Catalog](assets/README.md)** | Extracted thesis diagrams, architecture figures, and vector illustrations |

---

## 2. Thesis Benchmark & Performance Summary

The underlying academic thesis presents a modular indoor positioning system evaluated in a 6-Anchor classroom deployment:

| Metric | Measured Value | Operational Condition |
| --- | :---: | --- |
| **Mean Absolute Error (MAE)** | **$0.085\text{ m}$** ($8.5\text{ cm}$) | In-zone dynamic trajectory |
| **Root Mean Square Error (RMSE)** | **$0.109\text{ m}$** ($10.9\text{ cm}$) | 6-Anchor TDMA constellation |
| **95th-Percentile Error** | **$0.215\text{ m}$** ($21.5\text{ cm}$) | Under typical indoor multipath conditions |
| **Nominal Update Rate** | **$10\text{ Hz}$** ($100\text{ ms}$ frame) | Complete 6-Anchor ranging & UKF cycle |

---

## 3. Project Team & Credits

Developed at the **Faculty of Mechanical Engineering**, Ho Chi Minh City University of Technology and Engineering (**HCMUTE**).

- **Phuong Mai** — Lead Firmware & System Architecture
- **Dong Son** — Project Co-Developer
- **Trung Quan** — Project Co-Developer
