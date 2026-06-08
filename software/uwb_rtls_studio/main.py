"""
===============================================================================
  UWB RTLS Studio — Application Entry Point
===============================================================================
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

from utils.theme import DARK_STYLESHEET

# ── Logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    # High DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    app = QApplication(sys.argv)

    # Apply global font
    font = QFont("Segoe UI", 13)
    app.setFont(font)

    # Apply dark theme
    app.setStyleSheet(DARK_STYLESHEET)

    # ═══════════════════════════════════════════════════════════════
    # STEP 1: Create Services (singleton, shared)
    # ═══════════════════════════════════════════════════════════════
    from services.serial_service import SerialService
    from services.protocol_service import ProtocolService

    serial_service = SerialService()
    protocol_service = ProtocolService(serial_service)

    # ═══════════════════════════════════════════════════════════════
    # STEP 2 & 3: Connection Flow
    # ═══════════════════════════════════════════════════════════════
    from models.dongle_model import DongleModel
    from viewmodels.dongle_viewmodel import DongleViewModel
    from views.popups.dongle_popup import DonglePopup
    
    from models.scan_model import ScanModel
    from viewmodels.scan_viewmodel import ScanViewModel
    from views.popups.scan_popup import ScanPopup

    # === BYPASS POPUPS FOR UI TESTING ===
    dongle_model = DongleModel(serial_service, protocol_service)
    scan_model = ScanModel(protocol_service, serial_service)
    # ====================================

    # ═══════════════════════════════════════════════════════════════
    # STEP 4: Main Window
    # ═══════════════════════════════════════════════════════════════
    from views.windows.main_window import MainWindow
    from models.ranging_model import RangingModel
    from viewmodels.live_tracking_viewmodel import LiveTrackingViewModel
    from viewmodels.device_info_viewmodel import DeviceInfoViewModel

    # Extract connected device info from scan_model before cleanup
    connected_name = ""
    connected_mac = ""
    if scan_model._devices:
        # scan_popup is bypassed, just pick the first device if any
        sel_mac = list(scan_model._devices.keys())[0] if scan_model._devices else ''
        if sel_mac and sel_mac in scan_model._devices:
            dev = scan_model._devices[sel_mac]
            connected_name = dev.get("name", "")
            connected_mac = dev.get("mac", sel_mac)

    # Disconnect scan_model from protocol to avoid duplicate handlers
    scan_model.cleanup()
    try:
        protocol_service.packet_received.disconnect(scan_model._on_packet)
    except TypeError:
        pass

    ranging_model = RangingModel(protocol_service)
    live_tracking_vm = LiveTrackingViewModel(ranging_model, protocol_service)
    device_info_vm = DeviceInfoViewModel(protocol_service, dongle_model)

    # Seed the connected device info so the tab shows it immediately
    if connected_name and connected_mac:
        device_info_vm.set_connected_device(connected_name, connected_mac)

    window = MainWindow(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm
    )
    window.show()

    exit_code = app.exec()

    # Cleanup
    serial_service.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
