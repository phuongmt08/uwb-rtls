"""
UWB RTLS Studio - Antenna Delay Calibration Tab (host-driven, UI loaded from .ui file)

Replaces the old firmware-driven antenna-delay flow (app_calib_master.c,
calib_start/status/candidate_apply — removed). Place a reference tag at a
known position, pick a target anchor, and this tab drives the closed-loop
calibration entirely from the app via AntennaDelayCalibrationViewModel.

FE: Loaded from views/ui/antenna_delay_calib_tab.ui (editable in Qt Designer)
BE: viewmodels/antenna_delay_calibration_viewmodel.py
"""

import os
from PyQt6.QtWidgets import QWidget, QMessageBox
from PyQt6 import uic

UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'antenna_delay_calib_tab.ui')


def _parse_serial_number(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return 0


class AntennaDelayCalibTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        uic.loadUi(UI_FILE, self)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_save_serial.clicked.connect(self._on_save_serial_clicked)
        self.anchor_id_spin.valueChanged.connect(self._refresh_serial_field)
        self.btn_stop.setEnabled(False)

    def set_viewmodel(self, viewmodel):
        if self._vm is viewmodel:
            return
        self._vm = viewmodel
        self._vm.progress_updated.connect(self._on_progress)
        self._vm.finished.connect(self._on_finished)
        self._vm.operation_failed.connect(self._on_failed)
        self._refresh_serial_field()

    def _refresh_serial_field(self):
        if not self._vm:
            return
        serial = self._vm.serial_number_for(self.anchor_id_spin.value())
        self.anchor_serial_edit.setText(f"0x{serial:08X}" if serial else "")

    def _on_save_serial_clicked(self):
        if not self._vm:
            return
        anchor_id = self.anchor_id_spin.value()
        serial_number = _parse_serial_number(self.anchor_serial_edit.text())
        if serial_number <= 0:
            QMessageBox.warning(self, "Invalid serial number", "Enter a valid non-zero serial number.")
            return
        if not self._vm.save_anchor_serial_number(anchor_id, serial_number):
            QMessageBox.warning(self, "Anchor not found", f"Anchor {anchor_id} is not in the current anchor layout.")
            return
        self.status_label.setText(f"Status: Saved serial_number for anchor {anchor_id}.")

    def _on_start_clicked(self):
        if not self._vm:
            return
        self._vm.error_tolerance_m = self.tolerance_spin.value()
        self._vm.max_iterations = self.max_iter_spin.value()
        ok = self._vm.start(
            anchor_id=self.anchor_id_spin.value(),
            tag_x_m=self.tag_x_spin.value(),
            tag_y_m=self.tag_y_spin.value(),
            tag_z_m=self.tag_z_spin.value(),
        )
        if ok:
            self.results_log.clear()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)

    def _on_stop_clicked(self):
        if self._vm:
            self._vm.stop()

    def _on_progress(self, status: dict):
        text = status.get("custom_status_text", "")
        self.status_label.setText(f"Status: {text}")
        if "iteration" in status:
            self.results_log.append(text)

    def _on_finished(self, result: dict):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        outcome = "CONVERGED" if result.get("converged") else "STOPPED"
        self.results_log.append(
            f"[{outcome}] anchor={result.get('anchor_id')} "
            f"iterations={result.get('iterations')} "
            f"final_delay={result.get('final_delay')} "
            f"best_abs_error_m={result.get('best_abs_error_m')} "
            f"reason={result.get('reason')}"
        )
        self.status_label.setText(f"Status: {outcome} — {result.get('reason', '')}")

    def _on_failed(self, message: str):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_label.setText(f"Status: Error - {message}")
        self.results_log.append(f"[ERROR] {message}")
