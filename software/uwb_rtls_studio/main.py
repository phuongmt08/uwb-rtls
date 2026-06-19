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

    # Apply global stylesheet (dark theme & custom scrollbars)
    from utils.theme import DARK_STYLESHEET
    app.setStyleSheet(DARK_STYLESHEET)

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

    from repository.telemetry_repository import TelemetryRepository
    from repository.ble_scan_repository import BleScanRepository
    from repository.ranging_repository import RangingRepository
    from repository.config_repository import ConfigRepository
    from repository.diagnostics_repository import DiagnosticsRepository
    from repository.log_repository import LogRepository
    from repository.protocol_packet_repository import ProtocolPacketRepository
    from services.command_bus import init_shared_command_bus
    from models.telemetry_model import TelemetryModel

    telemetry_model = TelemetryModel()
    telemetry_repo = TelemetryRepository(telemetry_model=telemetry_model)
    ble_scan_repo = BleScanRepository()
    ranging_repo = RangingRepository()
    config_repo = ConfigRepository()
    diagnostics_repo = DiagnosticsRepository()
    log_repo = LogRepository()
    packet_repo = ProtocolPacketRepository(
        ranging_repo,
        telemetry_repo,
        ble_scan_repo,
        config_repo,
        diagnostics_repo,
        log_repo,
    )
    protocol_service.set_packet_repository(packet_repo)
    command_bus = init_shared_command_bus(protocol_service)

    # Initialize global query manager in shared app state
    from utils.app_state import shared_app_state
    shared_app_state.init_query_manager(
        send_packet_fn=lambda cmd, dst, **kwargs: command_bus.send(cmd, dst_addr=dst, **kwargs)
    )

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

    # Development-only bypass. Default production flow shows dongle/scan popups. Macro ON/OFF popups
    # Set env var UWB_RTLS_BYPASS_POPUPS = 1 to skip straight to main window with mock device.
    BYPASS_POPUPS = os.getenv("UWB_RTLS_BYPASS_POPUPS", "1").strip().lower() in {"1", "true", "yes", "on"}

    # Vòng lặp cho Connection Flow
    connected_name = ""
    connected_mac = ""

    if not BYPASS_POPUPS:
        while True:
            dongle_popup = DonglePopup(dongle_vm)
            if dongle_popup.exec() != 1:  # 1 = QDialog.DialogCode.Accepted
                sys.exit(0)

            # Dongle ok -> Scan popup
            scan_model = ScanModel(protocol_service, serial_service, command_bus=command_bus)
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
    from models.session_model import SessionModel
    from viewmodels.live_tracking_viewmodel import LiveTrackingViewModel
    from viewmodels.device_info_viewmodel import DeviceInfoViewModel
    from repository.session_repository import SessionRepository
    from repository.session_browser import SessionBrowser
    from services.session_run_manager import SessionRunManager
    from models.log_model import LogModel
    from viewmodels.log_viewmodel import LogViewModel
    from viewmodels.config_viewmodel import ConfigViewModel
    from viewmodels.calibration_viewmodel import CalibrationViewModel
    from viewmodels.main_viewmodel import MainViewModel
    
    session_repo = SessionRepository()
    session_browser = SessionBrowser(session_repo)
    log_model = LogModel(log_repository=log_repo, command_bus=command_bus)

    ranging_model = RangingModel(protocol_service, ranging_repo=ranging_repo, command_bus=command_bus)
    device_model = DeviceModel(
        protocol_service,
        telemetry_repo=telemetry_repo,
        ble_scan_repo=ble_scan_repo,
        command_bus=command_bus,
    )
    device_info_vm = DeviceInfoViewModel(
        device_model,
        dongle_model,
        telemetry_repo=telemetry_repo,
        ble_scan_repo=ble_scan_repo,
        telemetry_model=telemetry_model,
    )
    session_model = SessionModel()
    session_run_manager = SessionRunManager(
        session_model,
        session_repo,
        device_info_vm=device_info_vm,
        ranging_model=ranging_model,
        log_model=log_model,
    )
    log_vm = LogViewModel(session_browser, log_model=log_model, session_run_manager=session_run_manager)
    live_tracking_vm = LiveTrackingViewModel(
        ranging_model,
        protocol_service,
        ranging_repo=ranging_repo,
        command_bus=command_bus,
        session_run_manager=session_run_manager,
        ble_scan_repo=ble_scan_repo,
    )
    config_vm = ConfigViewModel(
        device_model,
        ranging_model,
        command_bus=command_bus,
        ble_scan_repo=ble_scan_repo,
    )
    calibration_vm = CalibrationViewModel(device_model)
    main_vm = MainViewModel(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm,
        log_vm=log_vm,
        session_repository=session_repo,
        session_run_manager=session_run_manager,
    )

    # Seed the connected device info so the tab shows it immediately
    if connected_name and connected_mac:
        device_info_vm.set_connected_device(connected_name, connected_mac)

    window = MainWindow(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm,
        config_vm=config_vm,
        calibration_vm=calibration_vm,
        dongle_vm=dongle_vm,
        log_vm=log_vm,
        main_vm=main_vm,
        serial_service=serial_service
    )
    window.showMaximized()

    # Initialize device data after UI is fully ready
    # QTimer.singleShot(0) defers to the next event loop iteration,
    # ensuring all Qt signals are wired before we request telemetry.
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(0, device_info_vm.initialize)

    exit_code = app.exec()

    # Cleanup
    serial_service.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
