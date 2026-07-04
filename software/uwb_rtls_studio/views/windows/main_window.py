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
    QApplication, QMessageBox, QProgressBar, QGraphicsOpacityEffect
)
from PyQt6.QtCore import QTimer, QSize, Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QIcon, QAction
from PyQt6 import uic

# Tab imports are handled dynamically by uic.loadUi based on <customwidgets> in the .ui file

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'main_window.ui')


class ToastNotification(QFrame):
    def __init__(self, title: str, message: str, kind: str = "info", parent=None):
        super().__init__(parent)
        self.setObjectName("ble_toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(430)
        self._close_started = False
        accent = {
            "disconnect": "#EF4444",
            "error": "#EF4444",
            "connect_retry": "#F59E0B",
            "success": "#10B981",
        }.get(kind, "#22D3EE")
        self.setStyleSheet(f"""
            QFrame#ble_toast {{
                background: #111827;
                border: 1px solid {accent};
                border-left: 4px solid {accent};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; color: #E5E7EB; }}
            QPushButton {{
                background: transparent;
                color: #94A3B8;
                border: none;
                font-weight: bold;
                padding: 2px 6px;
            }}
            QPushButton:hover {{ color: #FFFFFF; }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self._title = QLabel(title)
        self._title.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF;")
        text_layout.addWidget(self._title)

        self._message = QLabel(message)
        self._message.setWordWrap(True)
        self._message.setStyleSheet("font-size: 12px; color: #CBD5E1;")
        text_layout.addWidget(self._message)
        layout.addLayout(text_layout, 1)

        self._close_btn = QPushButton("x")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.clicked.connect(self.close_animated)
        layout.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignTop)

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._auto_close = QTimer(self)
        self._auto_close.setSingleShot(True)
        self._auto_close.timeout.connect(self.close_animated)

    def show_animated(self, auto_close_ms: int) -> None:
        self._opacity.setOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        if auto_close_ms > 0:
            self._auto_close.start(auto_close_ms)

    def close_animated(self) -> None:
        if self._close_started:
            return
        self._close_started = True
        self._auto_close.stop()
        self._fade.stop()
        self._fade.setStartValue(float(self._opacity.opacity()))
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()

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
        protocol_service=None,
        command_bus=None,
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
        self._protocol_service = protocol_service
        self._command_bus = command_bus
        self._is_developer = False
        self._session_active = False
        self._session_seconds = 0
        self._reconnect_popup = None
        self._conn_progress_target = 0
        self._conn_progress_display = 0
        self._conn_progress_timer = QTimer(self)
        self._conn_progress_timer.setInterval(8)
        self._conn_progress_timer.timeout.connect(self._tick_connection_progress)

        # -- Load UI from .ui file --
        uic.loadUi(UI_FILE, self)

        self._ble_toast = None
        self._setup_header_progress()

        # -- Setup tabs (replace placeholder) --
        self._setup_tabs()

        # -- Setup status bar --
        self._setup_statusbar()

        # -- Connect signals --
        self._connect_signals()

        # Init session timer
        self._session_timer = QTimer(self)
        self._session_timer.timeout.connect(self._tick_session)
        self._set_session_button_active(False)
        self._begin_session(initial=True)

    def _setup_header_progress(self):
        # Create Scan Device button
        self.btn_scan_device = QPushButton("🔍 Scan Device")
        self.btn_scan_device.setObjectName("btn_scan_device")
        self.btn_scan_device.setMinimumSize(QSize(130, 36))
        self.btn_scan_device.setMaximumSize(QSize(130, 36))
        self.btn_scan_device.setStyleSheet(
            "QPushButton { background: rgba(6, 182, 212, 0.12); color: #06B6D4; border: 1px solid #06B6D4; border-radius: 8px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background: #06B6D4; color: #F8FAFC; }"
            "QPushButton:disabled { background: rgba(51, 65, 85, 0.2); color: #64748B; border: 1px solid #475569; }"
        )
        self.btn_scan_device.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan_device.clicked.connect(self._on_scan_device_clicked)

        self._conn_progress_frame = QFrame(self.header_content_frame)
        self._conn_progress_frame.setObjectName("ble_process_frame")
        self._conn_progress_frame.setFixedHeight(36)
        self._conn_progress_frame.setMinimumWidth(360)
        self._conn_progress_frame.setMaximumWidth(470)

        layout = QHBoxLayout(self._conn_progress_frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        self._conn_progress_label = QLabel("BLE: Disconnected")
        self._conn_progress_label.setMinimumWidth(132)
        self._conn_progress_label.setMaximumWidth(170)
        self._conn_progress_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._conn_progress_label)

        self._conn_progress_bar = QProgressBar()
        self._conn_progress_bar.setRange(0, 100)
        self._conn_progress_bar.setValue(0)
        self._conn_progress_bar.setFormat("%p%")
        self._conn_progress_bar.setTextVisible(True)
        self._conn_progress_bar.setFixedHeight(18)
        layout.addWidget(self._conn_progress_bar, 1)

        # Insert button first, then progress status frame
        self.header_layout.insertWidget(2, self.btn_scan_device)
        self.header_layout.insertWidget(3, self._conn_progress_frame)
        self.update_progress_style("IDLE")

    def update_progress_style(self, status: str):
        if status == "SUCCESS":
            border_color = "#10B981"
            chunk_style = "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #10B981, stop:1 #047857);"
        elif status in ("FAILED", "ERROR"):
            border_color = "#EF4444"
            chunk_style = "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #EF4444, stop:1 #B91C1C);"
        elif status == "RETRYING":
            border_color = "#F59E0B"
            chunk_style = "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F59E0B, stop:1 #B45309);"
        elif status == "RUNNING":
            border_color = "#3B82F6"
            chunk_style = "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #06B6D4, stop:1 #10B981);"
        else:  # IDLE/DEFAULT
            border_color = "#334155"
            chunk_style = "background: #1E293B;"

        self._conn_progress_frame.setStyleSheet(f"""
            QFrame#ble_process_frame {{
                background: #0F172A;
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; color: #CBD5E1; font-size: 12px; }}
            QProgressBar {{
                background: #020617;
                border: 1px solid #334155;
                border-radius: 5px;
                color: #E5E7EB;
                font-size: 11px;
                font-weight: bold;
                text-align: center;
            }}
            QProgressBar::chunk {{
                {chunk_style}
                border-radius: 4px;
            }}
        """)

    def _animate_success(self):
        import math
        self._success_anim_step = 0
        if hasattr(self, "_success_timer") and self._success_timer:
            self._success_timer.stop()
        self._success_timer = QTimer(self)
        self._success_timer.setInterval(60)
        self._success_timer.timeout.connect(self._on_success_anim_tick)
        self._success_timer.start()

    def _on_success_anim_tick(self):
        import math
        self._success_anim_step += 1
        if self._success_anim_step > 12:
            self._success_timer.stop()
            self._success_timer = None
            self.update_progress_style("SUCCESS")
            return

        alpha = int(127 + 128 * abs(math.sin(self._success_anim_step * 0.5)))
        border_color = f"rgba(16, 185, 129, {alpha/255.0:.2f})"
        chunk_style = f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(16, 185, 129, {alpha/255.0:.2f}), stop:1 #047857);"

        self._conn_progress_frame.setStyleSheet(f"""
            QFrame#ble_process_frame {{
                background: #0F172A;
                border: 2px solid {border_color};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; color: #E2E8F0; font-size: 12px; font-weight: bold; }}
            QProgressBar {{
                background: #020617;
                border: 1px solid #334155;
                border-radius: 5px;
                color: #FFFFFF;
                font-size: 11px;
                font-weight: bold;
                text-align: center;
            }}
            QProgressBar::chunk {{
                {chunk_style}
                border-radius: 4px;
            }}
        """)

    def _tick_connection_progress(self):
        if self._conn_progress_display < self._conn_progress_target:
            self._conn_progress_display += 1
            self._conn_progress_bar.setValue(self._conn_progress_display)
        elif self._conn_progress_display > self._conn_progress_target:
            self._conn_progress_display -= 1
            self._conn_progress_bar.setValue(self._conn_progress_display)
        else:
            self._conn_progress_timer.stop()

    def _on_connection_progress(self, payload: dict):
        progress = max(0, min(100, int(payload.get("progress", 0) or 0)))
        message = str(payload.get("message") or "BLE process")
        status = str(payload.get("status") or "RUNNING")

        self._conn_progress_label.setText(message)
        self._conn_progress_label.setToolTip(message)
        self._conn_progress_target = progress
        if not self._conn_progress_timer.isActive():
            self._conn_progress_timer.start()

        if progress == 0:
            self._conn_progress_display = 0
            self._conn_progress_bar.setValue(0)
            self._conn_progress_timer.stop()
        else:
            self.update_progress_style(status)

        if hasattr(self, "_success_timer") and self._success_timer:
            self._success_timer.stop()
            self._success_timer = None

        if status == "SUCCESS" or progress >= 100:
            self._animate_success()
        else:
            self.update_progress_style(status)

    def _show_ble_notification(self, payload: dict):
        title = str(payload.get("title") or "BLE notification")
        message = str(payload.get("message") or "")
        reason_hex = str(payload.get("reason_code_hex") or "").strip()
        reason_name = str(payload.get("reason_name") or "").strip()
        reason_text = ""
        if reason_hex and reason_name:
            reason_text = f"{reason_hex} - {reason_name}"
        elif reason_hex:
            reason_text = reason_hex
        elif reason_name:
            reason_text = reason_name

        if reason_text and reason_text not in message:
            message = f"{message}\n{reason_text}" if message else reason_text

        kind = str(payload.get("kind") or "info")
        auto_close_ms = int(payload.get("auto_close_ms", 3000) or 0)

        current = self._ble_toast
        if current is not None:
            current.close_animated()

        toast = ToastNotification(title, message, kind, parent=self.centralwidget)
        self._ble_toast = toast
        toast.destroyed.connect(lambda _=None, t=toast: self._on_toast_destroyed(t))
        self._position_ble_toast(toast)
        toast.show_animated(auto_close_ms)

    def _position_ble_toast(self, toast=None):
        toast = toast or self._ble_toast
        if toast is None:
            return
        parent = toast.parentWidget()
        if parent is None:
            return
        width = min(430, max(320, parent.width() - 48))
        toast.setFixedWidth(width)
        toast.adjustSize()
        x = max(16, parent.width() - toast.width() - 24)
        y = 70
        toast.move(x, y)

    def _on_toast_destroyed(self, toast):
        if self._ble_toast is toast:
            self._ble_toast = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_ble_toast()

    def _setup_tabs(self):
        """Setup viewmodels for pre-loaded tabs from .ui"""
        # Set up variables to map to UI widgets created by uic.loadUi
        self._tab_device = self.tab_device
        self._tab_tracking = self.tab_tracking
        self._tab_spatial_constraints = self.tab_spatial_constraints
        self._tab_config = self.tab_config
        self._tab_calibration = self.tab_calibration
        self._tab_log = self.tab_log
        self._tab_communication = self.tab_communication

        if hasattr(self._tab_communication, "set_protocol_service"):
            self._tab_communication.set_protocol_service(self._protocol_service)

        if self._device_info_vm:
            self._tab_device.set_viewmodel(self._device_info_vm)
            self._device_info_vm.device_info_updated.connect(self._on_device_changed)
            if hasattr(self._device_info_vm, "connection_progress_updated"):
                self._device_info_vm.connection_progress_updated.connect(self._on_connection_progress)
            if hasattr(self._device_info_vm, "ble_notification_requested"):
                self._device_info_vm.ble_notification_requested.connect(self._show_ble_notification)

        if self._live_tracking_vm:
            self._tab_tracking.set_viewmodel(self._live_tracking_vm)
            self._tab_spatial_constraints.set_viewmodel(self._live_tracking_vm)

        if self._config_vm:
            self._tab_config.set_viewmodel(self._config_vm)

        if self._calibration_vm:
            self._tab_calibration.set_viewmodel(self._calibration_vm)

        if self._log_vm:
            self._tab_log.set_viewmodel(self._log_vm)

        if hasattr(self._tab_device, "set_developer_mode"):
            self._tab_device.set_developer_mode(False)
        self._tab_config.set_developer_mode(False)
        self._tab_log.set_developer_mode(False)
        self._tab_tracking.set_developer_mode(False)
        self._tab_spatial_constraints.set_developer_mode(True)
        if hasattr(self._tab_communication, "set_developer_mode"):
            self._tab_communication.set_developer_mode(False)

        # Get index for calibration and spatial constraints tabs
        self._calib_tab_index = self.tabs.indexOf(self._tab_calibration)
        self._spatial_tab_index = self.tabs.indexOf(self._tab_spatial_constraints)
        self._tracking_tab_index = self.tabs.indexOf(self._tab_tracking)
        self._communication_tab_index = self.tabs.indexOf(self._tab_communication)

        # Hide calibration and spatial constraints tabs in User mode
        self.tabs.setTabVisible(self._calib_tab_index, False)
        self.tabs.setTabVisible(self._spatial_tab_index, False)
        self.tabs.setTabVisible(self._tracking_tab_index, True)
        self.tabs.setTabVisible(self._communication_tab_index, False)

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

        # BLE telemetry state from dongle
        self._status_ble = QLabel("BLE: ---")
        self._status_ble.setStyleSheet("color: #94A3B8;")
        status.addWidget(self._status_ble)

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
            self._main_vm.session_ended.connect(self._on_session_ended)

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

        state_label = data.get("display_state") or data.get("state_name")
        state_value = data.get("state")
        if state_label is not None:
            self._status_ble.setText(f"BLE: {state_label}")
            if state_value == 5:
                self._status_ble.setStyleSheet("color: #10B981; font-weight: bold;")
            elif state_value in (2, 3, 4):
                self._status_ble.setStyleSheet("color: #F59E0B; font-weight: bold;")
            elif state_value == 1:
                self._status_ble.setStyleSheet("color: #94A3B8; font-weight: bold;")
            else:
                self._status_ble.setStyleSheet("color: #EF4444; font-weight: bold;")

    def _on_position_status(self, x, y, z, rms):
        self._status_rms.setText(f"\U0001F4CA RMS: {rms:.3f} m")

    def _on_device_changed(self, info: dict):
        status_text = info.get("Status")
        name = info.get("Device Name") or info.get("name") or "-"

        if name and name != "Unknown" and name != "-":
            self.device_badge.setText(f"\u25CF {name}")
        else:
            self.device_badge.setText("\u25CF -")

        if status_text == "Connecting":
            self.btn_scan_device.setEnabled(False)
            self._status_conn.setText(f"\u23F3 Connecting: {name}")
            self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
            self._status_rate.setText("\U0001F504 ---")
            return

        if status_text == "Disconnecting":
            self.btn_scan_device.setEnabled(False)
            self._status_conn.setText(f"\U0001F6D1 Disconnecting: {name}")
            self._status_conn.setStyleSheet("color: #F59E0B; font-weight: bold;")
            self._status_rate.setText("\U0001F504 ---")
            return

        if status_text == "Disconnected":
            self.btn_scan_device.setEnabled(True)
            label_name = name if name and name != "-" else "-"
            self._status_conn.setText(f"\U0001F534 Disconnected: {label_name}")
            self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
            self._status_bat.setText("\U0001F50B ---")
            self._status_bat.setStyleSheet("color: #94A3B8;")
            self._status_rssi.setText("\U0001F4E1 RSSI: ---")
            self._status_rms.setText("\U0001F4CA RMS: ---")
            self._status_rate.setText("\U0001F504 ---")
            return

        if status_text == "Connected":
            self.btn_scan_device.setEnabled(True)
            self._status_conn.setText(f"\U0001F7E2 Connected: {name}")
            self._status_conn.setStyleSheet("color: #10B981; font-weight: bold;")
            self._status_rate.setText("\U0001F504 30 FPS")
            return

        if not info:
            self.btn_scan_device.setEnabled(True)
            self.device_badge.setText("\u25CF -")
            self._status_conn.setText("\U0001F534 Disconnected")
            self._status_conn.setStyleSheet("color: #EF4444; font-weight: bold;")
            self._status_bat.setText("\U0001F50B ---")
            self._status_bat.setStyleSheet("color: #94A3B8;")
            self._status_rssi.setText("\U0001F4E1 RSSI: ---")
            self._status_rms.setText("\U0001F4CA RMS: ---")
            self._status_rate.setText("\U0001F504 ---")

    def _make_separator(self):
        sep = QLabel("|")
        sep.setStyleSheet("color: #334155; background: transparent;")
        return sep

    def _begin_session(self, initial: bool = False):
        if self._session_active:
            return True
        if self._main_vm:
            try:
                self._main_vm.start_session()
            except Exception as exc:
                if not initial:
                    QMessageBox.warning(self, "Session Start Failed", str(exc))
                return False
        self._session_active = True
        self._session_seconds = 0
        if self._log_vm:
            self._log_vm.clear_session_logs()
        self._status_session.setText("\u23F2 Session: 00:00:00")
        self._status_session.setStyleSheet("color: #10B981;")
        self._session_timer.start(1000)
        self._set_session_button_active(True)
        return True

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

        # Toggle calibration, spatial constraints and communication tabs visibility
        self.tabs.setTabVisible(self._calib_tab_index, self._is_developer)
        self.tabs.setTabVisible(self._spatial_tab_index, self._is_developer)
        self.tabs.setTabVisible(self._tracking_tab_index, not self._is_developer)
        self.tabs.setTabVisible(self._communication_tab_index, self._is_developer)

        # Update config, log, device info, tracking and communication tabs
        if hasattr(self._tab_device, "set_developer_mode"):
            self._tab_device.set_developer_mode(self._is_developer)
        self._tab_config.set_developer_mode(self._is_developer)
        self._tab_log.set_developer_mode(self._is_developer)
        self._tab_tracking.set_developer_mode(False)
        self._tab_spatial_constraints.set_developer_mode(True)
        if hasattr(self._tab_communication, "set_developer_mode"):
            self._tab_communication.set_developer_mode(self._is_developer)
            if not self._is_developer and hasattr(self._tab_communication, "disable_manual_test_mode"):
                self._tab_communication.disable_manual_test_mode()

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
            self._begin_session(initial=False)
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
            self._session_timer.stop()
            self._status_session.setText("\u23F2 Session: Ending...")
            self._status_session.setStyleSheet("color: #F59E0B;")
            self.btn_end_session.setEnabled(False)

            if self._main_vm:
                try:
                    self._main_vm.end_session(duration_sec=self._session_seconds)
                except Exception as exc:
                    self.btn_end_session.setEnabled(True)
                    QMessageBox.warning(self, "Session Save Failed", str(exc))
            else:
                self._save_active_session()
                self._on_session_ended("")
            
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

    def _on_scan_device_clicked(self):
        """Manually trigger BLE device scanning."""
        if self._device_info_vm:
            self._device_info_vm.start_ble_scan()
        return ""

    def _on_session_ended(self, _session_id: str):
        self._session_active = False
        self._session_seconds = 0
        self._session_timer.stop()
        self.btn_end_session.setEnabled(True)
        self._status_session.setText("\u23F2 Session: Ended")
        self._status_session.setStyleSheet("color: #F59E0B;")
        self._set_session_button_active(False)

    def _on_session_save_failed(self, message: str):
        self.btn_end_session.setEnabled(True)
        self._status_session.setText("\u23F2 Session: End Failed")
        self._status_session.setStyleSheet("color: #EF4444;")
        self._set_session_button_active(True)
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
        else:
            if self._main_vm:
                try:
                    self._main_vm._clear_live_session_buffers()
                except Exception:
                    pass


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

        if self._reconnect_popup is not None:
            return

        from views.popups.dongle_popup import DonglePopup
        from models.scan_model import ScanModel
        from viewmodels.scan_viewmodel import ScanViewModel
        from views.popups.scan_popup import ScanPopup
        from utils.app_state import shared_app_state

        if not self._dongle_vm:
            return

        try:
            self._serial_service.connection_lost.disconnect(self._on_dongle_disconnected)
        except Exception:
            pass

        try:
            while True:
                self._reconnect_popup = DonglePopup(self._dongle_vm, parent=self)
                dongle_res = self._reconnect_popup.exec()
                self._reconnect_popup = None

                if dongle_res != 1:
                    self.close()
                    return

                shared_app_state.clear_device_session_state()

                scan_model = ScanModel(
                    self._protocol_service,
                    self._serial_service,
                    command_bus=self._command_bus,
                )
                scan_vm = ScanViewModel(scan_model)
                scan_popup = ScanPopup(scan_vm, parent=self)

                scan_res = scan_popup.exec()
                connected_mac = scan_model.connected_mac
                scanned_devices = [dict(dev) for dev in sorted(scan_model._devices.values(), key=lambda d: d.get("order", 0))]
                connected_name = ""
                if connected_mac and connected_mac in scan_model._devices:
                    dev = scan_model._devices[connected_mac]
                    connected_name = dev.get("name", "")

                scan_model.cleanup()
                try:
                    self._protocol_service.packet_received.disconnect(scan_model._on_packet)
                except TypeError:
                    pass

                if scan_res == 1 and connected_mac:
                    if self._device_info_vm:
                        self._device_info_vm.set_connected_device(connected_name, connected_mac, scanned_devices)
                    return

                if scan_res == 2:
                    continue

                self.close()
                return
        finally:
            self._reconnect_popup = None
            try:
                self._serial_service.connection_lost.connect(self._on_dongle_disconnected)
            except Exception:
                pass

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
