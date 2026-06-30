"""
===============================================================================
  UWB RTLS Studio — Dongle ViewModel
===============================================================================
  File        : viewmodels/dongle_viewmodel.py
  Description : Lớp ViewModel (Strict MVVM).
                Chỉ chứa Presentation Logic: Đổi dữ liệu từ DongleModel thành text/màu.
                Chuyển tiếp (forward) các hành động từ View xuống Model.
===============================================================================
"""
from __future__ import annotations
import logging
from PyQt6.QtCore import QObject, pyqtSignal

from models.dongle_model import DongleModel
from services.dongle_detect_service import DongleInfo

log = logging.getLogger(__name__)

class DongleViewModel(QObject):
    # Signals cho View
    status_changed = pyqtSignal(str)
    port_info_changed = pyqtSignal(str)
    port_probing_changed = pyqtSignal(str)
    dongle_detected = pyqtSignal(str)
    dongle_ready = pyqtSignal(dict)
    dongle_error = pyqtSignal(str)
    progress_indeterminate = pyqtSignal()
    progress_value = pyqtSignal(int)

    def __init__(self, model: DongleModel, parent=None):
        super().__init__(parent)
        self.model = model
        
        # Lắng nghe tín hiệu từ Model, format lại thành giao diện
        self.model.ports_scanned.connect(self._on_ports_scanned)
        self.model.port_probing.connect(self._on_port_probing)
        self.model.dongle_found.connect(self._on_dongle_found)
        self.model.dongle_verified.connect(self._on_dongle_verified)
        self.model.search_timeout.connect(self._on_timeout)
        self.model.error_occurred.connect(self._on_error)

    # ── Action từ View ───────────────────────────────────────────────
    def start_detection(self) -> None:
        self.status_changed.emit("Searching COM ports...")
        self.progress_indeterminate.emit()
        self.model.start_detection()

    def retry(self) -> None:
        self.start_detection()

    def cancel(self) -> None:
        self.model.stop_detection()

    # ── Presentation Logic (Model -> View) ───────────────────────────
    def _on_ports_scanned(self, count: int) -> None:
        self.port_info_changed.emit(f"Found {count} COM port(s) to probe")

    def _on_port_probing(self, port: str) -> None:
        self.port_probing_changed.emit(port)
        self.status_changed.emit(f"Probing {port}...")

    def _on_dongle_found(self, info: DongleInfo) -> None:
        self.dongle_detected.emit(info.port)
        self.status_changed.emit(f"✅ Detected dongle on {info.port}")
        self.port_info_changed.emit(f"{info.port}  |  VID: 0x{info.vid:04X}  |  PID: 0x{info.pid:04X}")
        self.progress_value.emit(50)
        self.status_changed.emit("Verifying dongle identity...")

    def _on_dongle_verified(self, info_dict: dict) -> None:
        if info_dict.get("verified"):
            self.status_changed.emit("✅ Dongle verified!")
        else:
            self.status_changed.emit("⚠️ Connected (unverified)")
        self.progress_value.emit(100)
        self.dongle_ready.emit(info_dict)

    def _on_timeout(self) -> None:
        self.status_changed.emit("❌ No dongle found")
        self.dongle_error.emit("Could not find NRF52840 dongle.\nPlease check USB connection and try again.")

    def _on_error(self, msg: str) -> None:
        self.status_changed.emit("❌ Error opening serial")
        self.dongle_error.emit(msg)
