"""
==============================================================================
  UWB RTLS Studio — Application Entry Point
==============================================================================
  File        : main.py
  Author      : Trung Quan
  Description : Entry point — khởi chạy toàn bộ app.
                Flow: DonglePopup → ScanPopup → MainWindow.
                Tất cả đều dùng real backend (serial + protobuf).

  Wiring (Dependency Injection):
    1. Tạo Services (singleton): SerialService, ProtocolService
    2. Tạo ViewModels: DongleViewModel, ScanViewModel
    3. Tạo Views (popups): DonglePopup(vm), ScanPopup(vm)
    4. Chạy flow: popup1.exec() → popup2.exec() → MainWindow

  Giải thích:
    - Services được tạo 1 lần, share giữa tất cả ViewModels.
    - ViewModels nhận Services qua constructor (dependency injection).
    - Views nhận ViewModel qua constructor (MVVM binding).
    - main.py là "composition root" — nơi duy nhất wire dependencies.
===============================================================================
"""
import sys
import os
import logging

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Add parent directory for common module access
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

# ── Logging setup ────────────────────────────────────────────────────
from utils.logging_config import setup_logging
setup_logging()


def main():
    # High DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    app = QApplication(sys.argv)

    # Apply global font
    font = QFont("Segoe UI", 13)
    app.setFont(font)

    # ═══════════════════════════════════════════════════════════════
    # STEP 1: Create Services (singleton, shared)
    # ═══════════════════════════════════════════════════════════════
    from services.serial_service import SerialService
    from services.protocol_service import ProtocolService

    serial_service = SerialService()
    protocol_service = ProtocolService(serial_service)

    # Initialize global query manager in shared app state
    from utils.app_state import shared_app_state
    shared_app_state.init_query_manager(
        send_packet_fn=lambda cmd, dst, **kwargs: protocol_service.send_command(cmd, dst_addr=dst, **kwargs)
    )
    # Register the SerialService background reader thread in the registry
    if hasattr(serial_service, "_reader_thread") and serial_service._reader_thread:
        shared_app_state.threads.register("SerialReader", serial_service._reader_thread)

    # ═══════════════════════════════════════════════════════════════
    # STEP 2 & 3: Connection Flow
    # ═══════════════════════════════════════════════════════════════
    from models.dongle_model import DongleModel
    from viewmodels.dongle_viewmodel import DongleViewModel
    from views.popups.dongle_popup import DonglePopup
    
    from models.scan_model import ScanModel
    from viewmodels.scan_viewmodel import ScanViewModel
    from views.popups.scan_popup import ScanPopup
    dongle_model = DongleModel(serial_service, protocol_service)
    dongle_vm = DongleViewModel(dongle_model)

    # Macro to bypass the connection flow popups (set to True to go straight to MainWindow)
    BYPASS_POPUPS = 1

    # Vòng lặp cho Connection Flow
    connected_name = ""
    connected_mac = ""

    if not BYPASS_POPUPS:
        while True:
            dongle_popup = DonglePopup(dongle_vm)
            if dongle_popup.exec() != 1:  # 1 = QDialog.DialogCode.Accepted
                sys.exit(0)

            # Dongle ok -> Scan popup
            scan_model = ScanModel(protocol_service, serial_service)
            scan_vm = ScanViewModel(scan_model)
            scan_popup = ScanPopup(scan_vm)

            res = scan_popup.exec()
            if res == 1:
                # Extract connected device info from scan_model before cleanup
                connected_mac = scan_model.connected_mac
                if connected_mac and connected_mac in scan_model._devices:
                    dev = scan_model._devices[connected_mac]
                    connected_name = dev.get("name", "")

                # Disconnect scan_model from protocol to avoid duplicate handlers
                scan_model.cleanup()
                try:
                    protocol_service.packet_received.disconnect(scan_model._on_packet)
                except TypeError:
                    pass
                break
            elif res == 2:
                # Dongle bị mất kết nối khi đang scan -> comeback popup dongle và quét lại từ đầu
                continue
            else:
                sys.exit(0)
    else:
        # Default fallback values for development/testing when popups are bypassed
        connected_name = "Mock Device"
        connected_mac = "00:11:22:33:44:55"

    # ═══════════════════════════════════════════════════════════════
    # STEP 4: Main Window
    # ═══════════════════════════════════════════════════════════════
    from views.windows.main_window import MainWindow
    from models.ranging_model import RangingModel
    from models.device_model import DeviceModel
    from viewmodels.live_tracking_viewmodel import LiveTrackingViewModel
    from viewmodels.device_info_viewmodel import DeviceInfoViewModel

    ranging_model = RangingModel(protocol_service)
    live_tracking_vm = LiveTrackingViewModel(ranging_model, protocol_service)
    
    device_model = DeviceModel(protocol_service)
    device_info_vm = DeviceInfoViewModel(device_model, dongle_model)
    
    from viewmodels.config_viewmodel import ConfigViewModel
    config_vm = ConfigViewModel(device_model, ranging_model)

    # Seed the connected device info so the tab shows it immediately
    if connected_name and connected_mac:
        device_info_vm.set_connected_device(connected_name, connected_mac)

    window = MainWindow(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm,
        config_vm=config_vm,
        dongle_vm=dongle_vm,
        serial_service=serial_service
    )
    window.show()

    exit_code = app.exec()

    # Cleanup
    serial_service.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
