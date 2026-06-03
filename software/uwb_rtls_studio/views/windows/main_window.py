"""
UWB RTLS Studio — Main Window (Frontend Only)
Cửa sổ chính với Tab bar, Status bar, End Session button, User/Dev toggle.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QStatusBar, QComboBox, QFrame,
    QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QFont, QIcon, QAction

from views.tabs.device_info_tab import DeviceInfoTab
from views.tabs.live_tracking_tab import LiveTrackingTab
from views.tabs.config_tab import ConfigTab
from views.tabs.calibration_tab import CalibrationTab
from views.tabs.log_tab import LogTab


class MainWindow(QMainWindow):
    def __init__(self, live_tracking_vm=None, device_info_vm=None, parent=None):
        super().__init__(parent)
        self._live_tracking_vm = live_tracking_vm
        self._device_info_vm = device_info_vm
        self.setWindowTitle("🔵 UWB RTLS Studio v0.1.0")
        self.setMinimumSize(1280, 820)
        self._is_developer = False
        self._session_active = False
        self._session_seconds = 0
        self._build_ui()
        self._build_statusbar()
        
        # Init session timer but do not start it yet
        self._session_timer = QTimer(self)
        self._session_timer.timeout.connect(self._tick_session)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ═══ TITLE BAR ═══
        title_bar = QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet("""
            QFrame { background: #1E293B; border-bottom: 1px solid #334155; }
        """)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(20, 0, 20, 0)

        # App title
        app_title = QLabel("🔵 UWB RTLS Studio")
        app_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        app_title.setStyleSheet("color: #22D3EE; background: transparent;")
        tb_layout.addWidget(app_title)

        # Connected device badge
        self._device_badge = QLabel("● -")
        self._device_badge.setStyleSheet("""
            color: #10B981; background: rgba(16,185,129,0.1);
            border: 1px solid rgba(16,185,129,0.3); border-radius: 12px;
            padding: 4px 14px; font-weight: bold;
        """)
        tb_layout.addWidget(self._device_badge)

        tb_layout.addStretch()

        # Mode toggle
        mode_label = QLabel("Mode:")
        mode_label.setStyleSheet("color: #94A3B8; background: transparent; font-weight: bold;")
        tb_layout.addWidget(mode_label)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["👤 User", "🔧 Developer"])
        self._mode_combo.setFixedWidth(150)
        self._mode_combo.setStyleSheet("""
            QComboBox { background: #0A0F1E; color: #22D3EE; border: 1px solid #334155;
                border-radius: 8px; padding: 6px 12px; font-weight: bold; }
            QComboBox:hover { border-color: #22D3EE; }
            QComboBox QAbstractItemView { background: #1E293B; color: #F8FAFC;
                border: 1px solid #334155; selection-background-color: #0E7490; }
        """)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        tb_layout.addWidget(self._mode_combo)

        # End Session button
        self._btn_end_session = QPushButton("🔴 End Session")
        self._btn_end_session.setFixedSize(140, 36)
        self._btn_end_session.setStyleSheet("""
            QPushButton { background: rgba(239,68,68,0.12); color: #EF4444;
                border: 1px solid #EF4444; border-radius: 8px; font-weight: bold;
                font-size: 13px; }
            QPushButton:hover { background: #EF4444; color: #F8FAFC; }
        """)
        self._btn_end_session.clicked.connect(self._on_end_session)
        tb_layout.addWidget(self._btn_end_session)

        main_layout.addWidget(title_bar)

        # ═══ TAB WIDGET ═══
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: #0F172A; }
        """)

        # Create tabs
        self._tab_device = DeviceInfoTab()
        if self._device_info_vm:
            self._tab_device.set_viewmodel(self._device_info_vm)
            self._device_info_vm.device_info_updated.connect(self._on_device_changed)
            
        self._tab_tracking = LiveTrackingTab()
        if self._live_tracking_vm:
            self._tab_tracking.set_viewmodel(self._live_tracking_vm)
            
        self._tab_config = ConfigTab(is_developer=False)
        self._tab_calibration = CalibrationTab()
        self._tab_log = LogTab(is_developer=False)

        self._tabs.addTab(self._tab_device, "📱 Device Info")
        self._tabs.addTab(self._tab_tracking, "📍 Live Tracking")
        self._tabs.addTab(self._tab_config, "⚙ Config")
        self._calib_tab_index = self._tabs.addTab(self._tab_calibration, "🔧 Calibration")
        self._tabs.addTab(self._tab_log, "📋 Log & History")

        # Hide calibration tab in User mode
        self._tabs.setTabVisible(self._calib_tab_index, False)

        main_layout.addWidget(self._tabs)

    def _build_statusbar(self):
        status = QStatusBar()
        status.setStyleSheet("""
            QStatusBar { background: #1E293B; color: #94A3B8; border-top: 1px solid #334155;
                font-size: 12px; padding: 4px 12px; }
            QStatusBar QLabel { background: transparent; }
        """)

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

        self.setStatusBar(status)
        
        # Connect signals for status bar updates
        if self._device_info_vm:
            # We already connect device_info_updated to _on_device_changed in _build_ui, 
            # but let's make sure it handles statusbar too.
            self._device_info_vm.telemetry_updated.connect(self._on_telemetry_status)
            self._device_info_vm.ble_info_updated.connect(self._on_ble_info_status)
        if self._live_tracking_vm:
            self._live_tracking_vm.position_updated.connect(self._on_position_status)

    # Removed _on_device_connected_status, logic moved to _on_device_changed
            
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
            self._device_badge.setText(f"● {name}")
            
            if status_text == "Connecting":
                self._status_conn.setText(f"⏳ Connecting: {name}")
                self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
                self._status_rate.setText("🔄 ---")
                
                # Stop session timer while connecting
                self._session_active = False
                self._session_timer.stop()
                self._status_session.setText("⏱ Session: 00:00:00")
                self._status_session.setStyleSheet("color: #94A3B8;")
            else:
                self._status_conn.setText(f"🟢 Connected: {name}")
                self._status_conn.setStyleSheet("color: #10B981; font-weight: bold;")
                self._status_rate.setText("🔄 30 FPS") # Target FPS
                
                # Start session timer if not active
                if not self._session_active:
                    self._session_active = True
                    self._session_seconds = 0
                    self._session_timer.start(1000)
        else:
            self._device_badge.setText("● -")
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
        self._tabs.setTabVisible(self._calib_tab_index, self._is_developer)

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
            if self._device_info_vm and self._device_info_vm.protocol:
                try:
                    self._device_info_vm.protocol.send_command("end_session", reason=0)
                except Exception:
                    pass

    def _safe_shutdown(self):
        if self._device_info_vm and self._device_info_vm.protocol:
            try:
                self._device_info_vm.protocol.send_command("ble_disconnect")
                self._device_info_vm.protocol.send_command("end_session", reason=0)
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
