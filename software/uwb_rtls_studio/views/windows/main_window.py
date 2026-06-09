"""
==============================================================================
  UWB RTLS Studio — Main Window View
==============================================================================
  File        : main_window.py
  Description : MainWindow controller loaded from main_window.ui.
                Coordinates the tab switching, developer modes, and end session flow.

  MVVM Role   : VIEW — MainWindow controller.

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
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QFont, QIcon, QAction
from PyQt6 import uic

# Tab imports are handled dynamically by uic.loadUi based on <customwidgets> in the .ui file

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'main_window.ui')


class MainWindow(QMainWindow):
    def __init__(self, live_tracking_vm=None, device_info_vm=None, config_vm=None, dongle_vm=None, serial_service=None, parent=None):
        super().__init__(parent)
        self._live_tracking_vm = live_tracking_vm
        self._device_info_vm = device_info_vm
        self._config_vm = config_vm
        self._dongle_vm = dongle_vm
        self._serial_service = serial_service
        self._is_developer = False
        self._session_active = False
        self._session_seconds = 0

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Setup tabs (replace placeholder) ──
        self._setup_tabs()

        # ── Setup status bar ──
        self._setup_statusbar()

        # ── Connect signals ──
        self._connect_signals()

        # Init session timer but do not start it yet
        self._session_timer = QTimer(self)
        self._session_timer.timeout.connect(self._tick_session)

    def _setup_tabs(self):
        """Setup viewmodels for pre-loaded tabs from .ui"""
        # Set up variables to map to UI widgets created by uic.loadUi
        self._tab_device = self.tab_device
        self._tab_tracking = self.tab_tracking
        self._tab_config = self.tab_config
        self._tab_calibration = self.tab_calibration
        self._tab_log = self.tab_log

        if self._device_info_vm:
            self._tab_device.set_viewmodel(self._device_info_vm)
            self._device_info_vm.device_info_updated.connect(self._on_device_changed)

        if self._live_tracking_vm:
            self._tab_tracking.set_viewmodel(self._live_tracking_vm)

        if self._config_vm:
            self._tab_config.set_viewmodel(self._config_vm)

        self._tab_config.set_developer_mode(False)
        self._tab_log.set_developer_mode(False)

        # Get index for calibration tab
        self._calib_tab_index = self.tabs.indexOf(self._tab_calibration)

        # Hide calibration tab in User mode
        self.tabs.setTabVisible(self._calib_tab_index, False)

    def _setup_statusbar(self):
        """Add status bar widgets — the QStatusBar itself is loaded from .ui."""
        status = self.statusbar

        # Connection status
        self._status_conn = QLabel("🔴 Disconnected")
        self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
        status.addWidget(self._status_conn)

        status.addWidget(self._make_separator())

        # Battery
        self._status_bat = QLabel("🔋 ---")
        self._status_bat.setStyleSheet("color: #94A3B8;")
        status.addWidget(self._status_bat)

        status.addWidget(self._make_separator())

        # Session timer
        self._status_session = QLabel("⏱ Session: 00:00:00")
        self._status_session.setStyleSheet("color: #94A3B8;")
        status.addWidget(self._status_session)

        status.addWidget(self._make_separator())

        # RSSI
        self._status_rssi = QLabel("📡 RSSI: ---")
        status.addWidget(self._status_rssi)

        status.addWidget(self._make_separator())

        # RMS
        self._status_rms = QLabel("📊 RMS: ---")
        status.addWidget(self._status_rms)

        status.addWidget(self._make_separator())

        # Update rate
        self._status_rate = QLabel("🔄 ---")
        status.addWidget(self._status_rate)

        # Right side: mode indicator
        self._status_mode = QLabel("👤 User Mode")
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
        self.btn_end_session.clicked.connect(self._on_end_session)

        # Connect serial connection lost signal
        if self._serial_service:
            self._serial_service.connection_lost.connect(self._on_dongle_disconnected)

    # ── Status bar update slots ──────────────────────────────────────

    def _on_telemetry_status(self, data: dict):
        soc = data.get("bat_soc_percent")
        if soc is not None:
            self._status_bat.setText(f"🔋 {soc}%")
            self._status_bat.setStyleSheet("color: #10B981;" if soc > 20 else "color: #EF4444;")

    def _on_ble_info_status(self, data: dict):
        rssi = data.get("rssi_dbm")
        if rssi is not None:
            self._status_rssi.setText(f"📡 RSSI: {rssi} dBm")

    def _on_position_status(self, x, y, z, rms):
        self._status_rms.setText(f"📊 RMS: {rms:.3f} m")

    def _on_device_changed(self, info: dict):
        name = info.get("Device Name")
        status_text = info.get("Status", "Connected")

        if name and name != "Unknown" and name != "-":
            self.device_badge.setText(f"● {name}")

            if status_text == "Connecting":
                self._status_conn.setText(f"⏳ Connecting: {name}")
                self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
                self._status_rate.setText("🔄 ---")

                # Stop session timer while connecting
                self._session_active = False
                self._session_timer.stop()
                self._status_session.setText("⏱ Session: 00:00:00")
                self._status_session.setStyleSheet("color: #94A3B8;")
            elif status_text == "Disconnecting":
                self._status_conn.setText(f"🛑 Disconnecting: {name}")
                self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
                self._status_rate.setText("🔄 ---")
                self._session_active = False
                self._session_timer.stop()
            elif status_text == "Disconnected":
                self._status_conn.setText(f"🔴 Disconnected: {name}")
                self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
                self._status_rate.setText("🔄 ---")
                self._session_active = False
                self._session_timer.stop()
            else:
                self._status_conn.setText(f"🟢 Connected: {name}")
                self._status_conn.setStyleSheet("color: #10B981; font-weight: bold;")
                self._status_rate.setText("🔄 30 FPS")  # Target FPS

                # Start session timer if not active
                if not self._session_active:
                    self._session_active = True
                    self._session_seconds = 0
                    self._session_timer.start(1000)
        else:
            self.device_badge.setText("● -")
            self._status_conn.setText("🔴 Disconnected")
            self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
            self._status_bat.setText("🔋 ---")
            self._status_bat.setStyleSheet("color: #94A3B8;")
            self._status_rssi.setText("📡 RSSI: ---")
            self._status_rms.setText("📊 RMS: ---")
            self._status_rate.setText("🔄 ---")

            # Stop session timer
            self._session_active = False
            self._session_timer.stop()
            self._status_session.setText("⏱ Session: 00:00:00")
            self._status_session.setStyleSheet("color: #94A3B8;")

    def _make_separator(self):
        sep = QLabel("|")
        sep.setStyleSheet("color: #334155; background: transparent;")
        return sep

    def _on_mode_changed(self, index):
        self._is_developer = (index == 1)

        # Toggle calibration tab visibility
        self.tabs.setTabVisible(self._calib_tab_index, self._is_developer)

        # Update config and log tabs
        self._tab_config.set_developer_mode(self._is_developer)
        self._tab_log.set_developer_mode(self._is_developer)

        # Update status bar
        if self._is_developer:
            self._status_mode.setText("🔧 Developer Mode")
            self._status_mode.setStyleSheet("color: #F59E0B; font-weight: bold;")
        else:
            self._status_mode.setText("👤 User Mode")
            self._status_mode.setStyleSheet("color: #22D3EE; font-weight: bold;")

    def _on_end_session(self):
        reply = QMessageBox.question(
            self, "End Session",
            "End current session?\n\n"
            "This will:\n"
            "• Stop active ranging/streaming\n"
            "• Save session data to repository\n"
            "• App will remain open\n"
            "• Dongle stays connected",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._session_active = False
            self._session_seconds = 0
            self._status_session.setText("⏱ Session: Ended")
            self._status_session.setStyleSheet("color: #F59E0B;")
            # Just send end_session command for "End Session" button
            if self._device_info_vm and self._device_info_vm.model._protocol:
                try:
                    self._device_info_vm.model._protocol.send_command("end_session", reason=0)
                except Exception:
                    pass

    def _safe_shutdown(self):
        if self._device_info_vm and self._device_info_vm.model._protocol:
            try:
                self._device_info_vm.model._protocol.send_command("ble_disconnect")
                self._device_info_vm.model._protocol.send_command("end_session", reason=0)
                import time
                time.sleep(0.5)
            except Exception:
                pass

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
            self._status_session.setText(f"⏱ Session: {h:02d}:{m:02d}:{s:02d}")
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
        """Handle app close — would auto end session."""
        if self._session_active:
            reply = QMessageBox.question(
                self, "Close Application",
                "A session is still active.\n"
                "Session data will be saved automatically.\n\n"
                "Close application?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        self._safe_shutdown()
        event.accept()
