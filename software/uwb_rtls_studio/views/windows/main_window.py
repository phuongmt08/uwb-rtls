"""
==============================================================================
  UWB RTLS Studio - Main Window View
==============================================================================
  File        : main_window.py
  Description : MainWindow controller loaded from main_window.ui.
                Coordinates the tab switching, developer modes, and end session flow.

  MVVM Role   : VIEW - MainWindow controller.

  Thread Model:
    - Main GUI Thread: Manages primary user actions, child popup triggers, and
      overall window event dispatching strictly on this thread.
==============================================================================
"""
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QStatusBar, QComboBox, QFrame,
    QApplication, QMessageBox
)
from PyQt6.QtCore import QTimer, QSize
from PyQt6.QtGui import QFont, QIcon, QAction
from PyQt6 import uic

# Tab imports are handled dynamically by uic.loadUi based on <customwidgets> in the .ui file

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'main_window.ui')


class MainWindow(QMainWindow):
    def __init__(
        self,
        live_tracking_vm=None,
        device_info_vm=None,
        config_vm=None,
        calibration_vm=None,
        dongle_vm=None,
        log_vm=None,
        main_vm=None,
        serial_service=None,
        parent=None,
    ):
        super().__init__(parent)
        self._live_tracking_vm = live_tracking_vm
        self._device_info_vm = device_info_vm
        self._config_vm = config_vm
        self._calibration_vm = calibration_vm
        self._dongle_vm = dongle_vm
        self._log_vm = log_vm
        self._main_vm = main_vm
        self._serial_service = serial_service
        self._is_developer = False
        self._session_active = False
        self._session_seconds = 0

        # -- Load UI from .ui file --
        uic.loadUi(UI_FILE, self)

        # -- Setup tabs (replace placeholder) --
        self._setup_tabs()

        # -- Setup status bar --
        self._setup_statusbar()

        # -- Connect signals --
        self._connect_signals()

        # Init session timer but do not start it yet
        self._session_timer = QTimer(self)
        self._session_timer.timeout.connect(self._tick_session)
        self._set_session_button_active(False)

    def _setup_tabs(self):
        """Setup viewmodels for pre-loaded tabs from .ui"""
        # Set up variables to map to UI widgets created by uic.loadUi
        self._tab_device = self.tab_device
        self._tab_tracking = self.tab_tracking
        self._tab_spatial_constraints = self.tab_spatial_constraints
        self._tab_config = self.tab_config
        self._tab_calibration = self.tab_calibration
        self._tab_log = self.tab_log

        if self._device_info_vm:
            self._tab_device.set_viewmodel(self._device_info_vm)
            self._device_info_vm.device_info_updated.connect(self._on_device_changed)

        if self._live_tracking_vm:
            self._tab_tracking.set_viewmodel(self._live_tracking_vm)
            self._tab_spatial_constraints.set_viewmodel(self._live_tracking_vm)

        if self._config_vm:
            self._tab_config.set_viewmodel(self._config_vm)

        if self._calibration_vm:
            self._tab_calibration.set_viewmodel(self._calibration_vm)

        if self._log_vm:
            self._tab_log.set_viewmodel(self._log_vm)

        self._tab_config.set_developer_mode(False)
        self._tab_log.set_developer_mode(False)
        self._tab_tracking.set_developer_mode(False)
        self._tab_spatial_constraints.set_developer_mode(True)

        # Get index for calibration and spatial constraints tabs
        self._calib_tab_index = self.tabs.indexOf(self._tab_calibration)
        self._spatial_tab_index = self.tabs.indexOf(self._tab_spatial_constraints)
        self._tracking_tab_index = self.tabs.indexOf(self._tab_tracking)

        # Hide calibration and spatial constraints tabs in User mode
        self.tabs.setTabVisible(self._calib_tab_index, False)
        self.tabs.setTabVisible(self._spatial_tab_index, False)
        self.tabs.setTabVisible(self._tracking_tab_index, True)

        # Initialize active tab title
        self._on_tab_changed(self.tabs.currentIndex())

    def _setup_statusbar(self):
        """Add status bar widgets - the QStatusBar itself is loaded from .ui."""
        status = self.statusbar

        # Connection status
        self._status_conn = QLabel("\U0001F534 Disconnected")
        self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
        status.addWidget(self._status_conn)

        status.addWidget(self._make_separator())

        # Battery
        self._status_bat = QLabel("\U0001F50B ---")
        self._status_bat.setStyleSheet("color: #94A3B8;")
        status.addWidget(self._status_bat)

        status.addWidget(self._make_separator())

        # Session timer
        self._status_session = QLabel("\u23F2 Session: 00:00:00")
        self._status_session.setStyleSheet("color: #94A3B8;")
        status.addWidget(self._status_session)

        status.addWidget(self._make_separator())

        # RSSI
        self._status_rssi = QLabel("\U0001F4E1 RSSI: ---")
        status.addWidget(self._status_rssi)

        status.addWidget(self._make_separator())

        # RMS
        self._status_rms = QLabel("\U0001F4CA RMS: ---")
        status.addWidget(self._status_rms)

        status.addWidget(self._make_separator())

        # Update rate
        self._status_rate = QLabel("\U0001F504 ---")
        status.addWidget(self._status_rate)

        # Right side: mode indicator
        self._status_mode = QLabel("\U0001F464 User Mode")
        self._status_mode.setStyleSheet("color: #22D3EE; font-weight: bold;")
        status.addPermanentWidget(self._status_mode)

        # Connect signals for status bar updates
        if self._device_info_vm:
            self._device_info_vm.telemetry_updated.connect(self._on_telemetry_status)
            self._device_info_vm.ble_info_updated.connect(self._on_ble_info_status)
        if self._live_tracking_vm:
            self._live_tracking_vm.position_updated.connect(self._on_position_status)

    def _connect_signals(self):
        """Connect UI signals from .ui widgets to backend logic."""
        # mode_combo and btn_end_session are loaded from .ui
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.view().pressed.connect(self._on_mode_item_pressed)
        self.btn_end_session.clicked.connect(self._on_end_session)

        # Connect tab change to update header title
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Connect serial connection lost signal
        if self._serial_service:
            self._serial_service.connection_lost.connect(self._on_dongle_disconnected)

        if self._main_vm:
            self._main_vm.session_save_failed.connect(self._on_session_save_failed)

    # -- Status bar update slots -----------------------------------------------

    def _on_telemetry_status(self, data: dict):
        soc = data.get("bat_soc_percent")
        if soc is None:
            self._status_bat.setText("\U0001F50B --")
            self._status_bat.setStyleSheet("color: #94A3B8;")
            return
        soc = int(soc)
        self._status_bat.setText(f"\U0001F50B {soc}%")
        self._status_bat.setStyleSheet("color: #10B981;" if soc > 20 else "color: #EF4444;")

    def _on_ble_info_status(self, data: dict):
        rssi = data.get("rssi_dbm")
        if rssi is not None:
            self._status_rssi.setText(f"\U0001F4E1 RSSI: {rssi} dBm")

    def _on_position_status(self, x, y, z, rms):
        self._status_rms.setText(f"\U0001F4CA RMS: {rms:.3f} m")

    def _on_device_changed(self, info: dict):
        name = info.get("Device Name")
        status_text = info.get("Status", "Connected")

        if name and name != "Unknown" and name != "-":
            self.device_badge.setText(f"\u25CF {name}")

            if status_text == "Connecting":
                self._status_conn.setText(f"\u23F3 Connecting: {name}")
                self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
                self._status_rate.setText("\U0001F504 ---")

                # Stop session timer while connecting
                self._session_active = False
                self._session_timer.stop()
                self._status_session.setText("\u23F2 Session: 00:00:00")
                self._status_session.setStyleSheet("color: #94A3B8;")
                self._set_session_button_active(False)
            elif status_text == "Disconnecting":
                self._status_conn.setText(f"\U0001F6D1 Disconnecting: {name}")
                self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
                self._status_rate.setText("\U0001F504 ---")
                self._session_active = False
                self._session_timer.stop()
                self._set_session_button_active(False)
            elif status_text == "Disconnected":
                self._status_conn.setText(f"\U0001F534 Disconnected: {name}")
                self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
                self._status_rate.setText("\U0001F504 ---")
                self._session_active = False
                self._session_timer.stop()
                self._set_session_button_active(False)
            else:
                self._status_conn.setText(f"\U0001F7E2 Connected: {name}")
                self._status_conn.setStyleSheet("color: #10B981; font-weight: bold;")
                self._status_rate.setText("\U0001F504 30 FPS")  # Target FPS

                # Start session timer if not active
                if not self._session_active:
                    self._session_active = True
                    self._session_seconds = 0
                    if self._log_vm:
                        self._log_vm.clear_session_logs()
                    if self._main_vm:
                        try:
                            self._main_vm.start_session()
                        except Exception:
                            pass
                    self._session_timer.start(1000)
                    self._set_session_button_active(True)
        else:
            self.device_badge.setText("\u25CF -")
            self._status_conn.setText("\U0001F534 Disconnected")
            self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
            self._status_bat.setText("\U0001F50B ---")
            self._status_bat.setStyleSheet("color: #94A3B8;")
            self._status_rssi.setText("\U0001F4E1 RSSI: ---")
            self._status_rms.setText("\U0001F4CA RMS: ---")
            self._status_rate.setText("\U0001F504 ---")

            # Stop session timer
            self._session_active = False
            self._session_timer.stop()
            self._status_session.setText("\u23F2 Session: 00:00:00")
            self._status_session.setStyleSheet("color: #94A3B8;")
            self._set_session_button_active(False)

    def _make_separator(self):
        sep = QLabel("|")
        sep.setStyleSheet("color: #334155; background: transparent;")
        return sep

    def _on_mode_item_pressed(self, model_index):
        """Apply a popup mode selection on the first click."""
        index = model_index.row()
        if index < 0 or index >= self.mode_combo.count():
            return
        if self.mode_combo.currentIndex() != index:
            self.mode_combo.setCurrentIndex(index)
        else:
            self._on_mode_changed(index)
        QTimer.singleShot(0, self.mode_combo.hidePopup)

    def _on_mode_changed(self, index):
        self._is_developer = (index == 1)

        # Toggle calibration and spatial constraints tabs visibility
        self.tabs.setTabVisible(self._calib_tab_index, self._is_developer)
        self.tabs.setTabVisible(self._spatial_tab_index, self._is_developer)
        self.tabs.setTabVisible(self._tracking_tab_index, not self._is_developer)

        # Update config, log, and tracking tabs
        self._tab_config.set_developer_mode(self._is_developer)
        self._tab_log.set_developer_mode(self._is_developer)
        self._tab_tracking.set_developer_mode(False)
        self._tab_spatial_constraints.set_developer_mode(True)

        # Update status bar and active tab
        if self._is_developer:
            self.tabs.setCurrentWidget(self._tab_spatial_constraints)
            self._status_mode.setText("\U0001F527 Developer Mode")
            self._status_mode.setStyleSheet("color: #F59E0B; font-weight: bold;")
        else:
            self.tabs.setCurrentWidget(self._tab_tracking)
            self._status_mode.setText("\U0001F464 User Mode")
            self._status_mode.setStyleSheet("color: #22D3EE; font-weight: bold;")

    def _on_tab_changed(self, index):
        if index < 0 or not hasattr(self, "active_tab_title"):
            return
        text = self.tabs.tabText(index)
        # Clean leading emoji and space if present (e.g., "📱 Device Info" -> "Device Info")
        cleaned_text = text
        for i, char in enumerate(text):
            if char.isalnum():
                cleaned_text = text[i:]
                break
        self.active_tab_title.setText(cleaned_text)

    def _on_end_session(self):
        if not self._session_active:
            if self._main_vm:
                try:
                    self._main_vm.start_session()
                except Exception as exc:
                    QMessageBox.warning(self, "Session Start Failed", str(exc))
                    return
            self._session_active = True
            self._session_seconds = 0
            if self._log_vm:
                self._log_vm.clear_session_logs()
            self._session_timer.start(1000)
            self._set_session_button_active(True)
            return

        reply = QMessageBox.question(
            self, "End Session",
            "End current session?\n\n"
            "This will:\n"
            "\u2022 Stop active ranging/streaming\n"
            "\u2022 Save session data to repository\n"
            "\u2022 App will remain open\n"
            "\u2022 Dongle stays connected",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._session_active = False
            self._status_session.setText("\u23F2 Session: Ended")
            self._status_session.setStyleSheet("color: #F59E0B;")
            
            # GOI LUU SESSION THUC TE
            if self._main_vm:
                try:
                    self._main_vm.end_session(duration_sec=self._session_seconds)
                except Exception as exc:
                    QMessageBox.warning(self, "Session Save Failed", str(exc))
            else:
                self._save_active_session()
            self._session_seconds = 0
            self._session_timer.stop()
            self._set_session_button_active(False)
            
    def _set_session_button_active(self, active: bool):
        if active:
            self.btn_end_session.setText("\U0001F534 End Session")
            self.btn_end_session.setToolTip("End current session and save active ranging/log runs")
            self.btn_end_session.setStyleSheet(
                "QPushButton { background: rgba(239,68,68,0.12); color: #EF4444; border: 1px solid #EF4444; "
                "border-radius: 8px; font-weight: bold; font-size: 13px; }"
                "QPushButton:hover { background: #EF4444; color: #F8FAFC; }"
            )
        else:
            self.btn_end_session.setText("\u25B6 Start Session")
            self.btn_end_session.setToolTip("Start a new app session")
            self.btn_end_session.setStyleSheet(
                "QPushButton { background: rgba(16,185,129,0.12); color: #10B981; border: 1px solid #10B981; "
                "border-radius: 8px; font-weight: bold; font-size: 13px; }"
                "QPushButton:hover { background: #10B981; color: #F8FAFC; }"
            )

    def _save_active_session(self):
        """Compatibility wrapper. Session persistence is owned by MainViewModel."""
        if self._main_vm:
            return self._main_vm.save_active_session(duration_sec=self._session_seconds)
        return ""

    def _on_session_save_failed(self, message: str):
        QMessageBox.warning(self, "Session Save Failed", message)

    def _safe_shutdown(self):
        # 1. Gửi disconnect dongle (ble_disconnect_t)
        if self._device_info_vm:
            try:
                self._device_info_vm.request_ble_disconnect()
                import time
                time.sleep(0.2)
            except Exception:
                pass

        # 2. Stop all session & save session
        if self._session_active:
            if self._main_vm:
                try:
                    self._main_vm.end_session(duration_sec=self._session_seconds)
                except Exception:
                    pass
            else:
                try:
                    self._save_active_session()
                except Exception:
                    pass
            self._session_active = False
            self._session_timer.stop()

        # 3. Đóng cổng COM
        if self._serial_service:
            try:
                self._serial_service.close()
            except Exception:
                pass

        # 4. EXIT PROCESS
        import sys
        sys.exit(0)

    def _start_session_timer(self):
        # Kept for compatibility if used elsewhere, but managed by _on_device_changed now
        if not self._session_active:
            self._session_active = True
            self._session_seconds = 0
            self._session_timer.start(1000)

    def _tick_session(self):
        if self._session_active:
            self._session_seconds += 1
            h = self._session_seconds // 3600
            m = (self._session_seconds % 3600) // 60
            s = self._session_seconds % 60
            self._status_session.setText(f"\u23F2 Session: {h:02d}:{m:02d}:{s:02d}")
            self._status_session.setStyleSheet("color: #10B981;")

    def _on_dongle_disconnected(self):
        """Handle dongle physical disconnection."""
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("MainWindow: Dongle disconnected!")

        # Show warning notification popup
        QMessageBox.warning(
            self,
            "Dongle Disconnected",
            "Dongle was disconnected! Please check the USB connection.",
            QMessageBox.StandardButton.Ok
        )

        # Pop dongle detect popup
        from views.popups.dongle_popup import DonglePopup
        if self._dongle_vm:
            # Temporarily disconnect to avoid multiple popups
            try:
                self._serial_service.connection_lost.disconnect(self._on_dongle_disconnected)
            except Exception:
                pass

            dongle_popup = DonglePopup(self._dongle_vm, parent=self)
            res = dongle_popup.exec()

            # Reconnect signal after popup closes
            try:
                self._serial_service.connection_lost.connect(self._on_dongle_disconnected)
            except Exception:
                pass

            if res != 1:
                # User cancelled -> Close main window / exit app
                self.close()

    def closeEvent(self, event):
        """Handle app close - confirmation popup and shutdown sequence."""
        msg = "Are you sure you want to exit the application?\n\nThis will:\n- Disconnect device\n- Stop and save active session (if any)\n- Close COM port and release resources."
        if self._session_active:
            msg = "An active session is currently running.\nIf you exit, session data will be saved automatically.\n\nDo you want to save and exit the application?"

        reply = QMessageBox.question(
            self, "Confirm Exit",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.No:
            event.ignore()
            return

        self._safe_shutdown()
        event.accept()
