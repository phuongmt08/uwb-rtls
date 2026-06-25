"""
==============================================================================
  UWB RTLS Studio — Communication Tab
==============================================================================
  File        : views/tabs/communication_tab.py
  Description : Two-sub-tab Communication panel for Developer Mode.

  Sub-tabs:
    1. 📊 Live Monitor  — Passive observer of ALL packets (TX + RX) flowing
                          through the protocol service.  Nothing is sent here.
    2. 🧪 Packet Tester — Manual packet sender / response inspector.
                          Activating "Manual Test Mode" blocks background
                          auto-queries (except BLE) so the user can test
                          individual packets with a real dongle + hardware.

  Thread Model:
    ALL slot callbacks are invoked on the Main GUI Thread because
    protocol_service.packet_sent / packet_received are emitted there
    (see protocol_service.py comment block).  No extra locking is needed.

  Wiring (set by MainWindow._setup_tabs):
    tab_communication.set_protocol_service(protocol_service)

  Key design rules:
    • NEVER touch/import anything from other tabs.
    • NEVER create new threads — Qt signal/slot handles cross-thread delivery.
    • Monitor tables accumulate rows; Tester rows are correlated by seq number.
    • Manual-test seq tracking uses ONLY _tester_seqs (set of pkt.hdr.seq),
      NOT a boolean flag, to avoid false positives.
==============================================================================
"""
from __future__ import annotations

import os
import time
import json
import logging

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QComboBox, QLineEdit, QSplitter,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QFormLayout, QAbstractItemView, QTextEdit,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, pyqtSlot, QSize, QRectF, QPointF,
    QPropertyAnimation, pyqtProperty,
)
from PyQt6.QtGui import QColor, QFont, QTextCursor, QPainter, QBrush, QPen

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Custom Animated Toggle Switch
# ─────────────────────────────────────────────────────────────────────────────

class ToggleSwitch(QCheckBox):
    """Premium animated slide switch styled for dark theme."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._offset = 3.0
        self._anim = QPropertyAnimation(self, b"offset")
        self._anim.setDuration(120)
        self.toggled.connect(self._on_toggled)

    @pyqtProperty(float)
    def offset(self) -> float:
        return self._offset

    @offset.setter
    def offset(self, val: float) -> None:
        self._offset = val
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(23.0 if checked else 3.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._anim.stop()
        self._offset = 23.0 if checked else 3.0
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(280, 28)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track
        track_rect = QRectF(0, 4, 44, 20)
        track_color = QColor("#10B981") if self.isChecked() else QColor("#334155")
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(track_rect, 10, 10)

        # Thumb / knob
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(QPointF(self._offset + 7, 14), 7, 7)

        # Label
        p.setPen(QColor("#22D3EE"))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.drawText(52, 19, self.text())


# ─────────────────────────────────────────────────────────────────────────────
#  CommunicationTab
# ─────────────────────────────────────────────────────────────────────────────

class CommunicationTab(QWidget):
    """
    Developer-mode tab with two sub-tabs:
      • Live Monitor  — passively shows every TX/RX packet.
      • Packet Tester — manually send any command and inspect the response.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._protocol_service = None
        self._is_developer = False

        # seq numbers issued by _send_manual_packet (used to tag tester rows)
        self._tester_seqs: set[int] = set()

        # Flag: True while _send_manual_packet is in the call stack
        # (needed because packet_sent is a Direct Connection on same thread
        #  → _on_packet_sent fires BEFORE _send_manual_packet returns pkt)
        self._manual_send_active: bool = False

        # seq → table row index for correlated display
        self._monitor_seq_to_row: dict[int, int] = {}
        self._tester_seq_to_row: dict[int, int] = {}

        # Load UI skeleton from .ui file (provides main_layout QVBoxLayout)
        _ui_path = os.path.join(os.path.dirname(__file__), "..", "ui", "communication_tab.ui")
        uic.loadUi(_ui_path, self)

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    #  UI Construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        if hasattr(self, "main_layout") and self.main_layout:
            self.main_layout.setContentsMargins(10, 10, 10, 10)
            self.main_layout.setSpacing(8)

        # Sub-tab container
        self.sub_tabs = QTabWidget(self)
        self.sub_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #334155; border-radius: 6px; background: #0F172A; }
            QTabBar::tab { background: #1E293B; color: #94A3B8; border: 1px solid #334155;
                           border-bottom: none; border-top-left-radius: 6px;
                           border-top-right-radius: 6px; padding: 8px 16px;
                           margin-right: 4px; font-weight: bold; }
            QTabBar::tab:selected { background: #0F172A; color: #22D3EE;
                                    border-bottom: 2px solid #22D3EE; }
            QTabBar::tab:hover:!selected { background: #334155; color: #F8FAFC; }
        """)
        self.sub_tabs.currentChanged.connect(self._on_sub_tab_changed)

        # ── Tab 1: Live Monitor
        self.tab_monitor = QWidget()
        self._build_monitor_tab()
        self.sub_tabs.addTab(self.tab_monitor, "📊 Live Monitor")

        # ── Tab 2: Packet Tester
        self.tab_tester = QWidget()
        self._build_tester_tab()
        self.sub_tabs.addTab(self.tab_tester, "🧪 Packet Tester")

        self.main_layout.addWidget(self.sub_tabs)

    # ── Live Monitor ──────────────────────────────────────────────────────────

    def _build_monitor_tab(self) -> None:
        layout = QVBoxLayout(self.tab_monitor)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Black box: current-packet info
        self.monitor_detail_group = QGroupBox("Current Transmission", self.tab_monitor)
        self.monitor_detail_group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #334155; border-radius: 8px;
                background-color: #0B0F19; margin-top: 12px;
                font-weight: bold; color: #22D3EE;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        """)
        detail_vlayout = QVBoxLayout(self.monitor_detail_group)
        detail_vlayout.setContentsMargins(8, 14, 8, 8)

        self.monitor_detail_text = QTextEdit(self.monitor_detail_group)
        self.monitor_detail_text.setReadOnly(True)
        self.monitor_detail_text.setPlaceholderText("Waiting for communication traffic…")
        self.monitor_detail_text.setStyleSheet("""
            QTextEdit {
                background-color: #05080E; color: #22D3EE;
                border: 1px solid #1E293B;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px; border-radius: 4px; padding: 6px;
            }
        """)
        detail_vlayout.addWidget(self.monitor_detail_text)
        layout.addWidget(self.monitor_detail_group, 1)

        # ── Clear button row
        clear_row = QHBoxLayout()
        clear_row.addStretch()
        self.btn_monitor_clear = QPushButton("🗑 Clear Monitor", self.tab_monitor)
        self.btn_monitor_clear.setStyleSheet(self._secondary_btn_style())
        self.btn_monitor_clear.clicked.connect(self._clear_monitor)
        clear_row.addWidget(self.btn_monitor_clear)
        layout.addLayout(clear_row)

        # ── Correlated tables (sent | received)
        self.monitor_splitter = QSplitter(Qt.Orientation.Horizontal, self.tab_monitor)
        layout.addWidget(self.monitor_splitter, 2)

        # Yellow: Sent
        self.monitor_sent_group = QGroupBox("Message Sent", self.monitor_splitter)
        self.monitor_sent_group.setStyleSheet(self._group_style("#F59E0B"))
        sent_layout = QVBoxLayout(self.monitor_sent_group)
        sent_layout.setContentsMargins(6, 12, 6, 6)
        self.monitor_sent_table = QTableWidget(self.monitor_sent_group)
        self._configure_table(self.monitor_sent_table, is_sent=True)
        sent_layout.addWidget(self.monitor_sent_table)

        # Red: Received
        self.monitor_received_group = QGroupBox("Message Received", self.monitor_splitter)
        self.monitor_received_group.setStyleSheet(self._group_style("#EF4444"))
        recv_layout = QVBoxLayout(self.monitor_received_group)
        recv_layout.setContentsMargins(6, 12, 6, 6)
        self.monitor_received_table = QTableWidget(self.monitor_received_group)
        self._configure_table(self.monitor_received_table, is_sent=False)
        self.monitor_received_table.verticalHeader().setVisible(False)
        recv_layout.addWidget(self.monitor_received_table)

        self.monitor_splitter.setSizes([600, 600])

        # Sync scroll & selection
        self.monitor_sent_table.verticalScrollBar().valueChanged.connect(
            self.monitor_received_table.verticalScrollBar().setValue
        )
        self.monitor_received_table.verticalScrollBar().valueChanged.connect(
            self.monitor_sent_table.verticalScrollBar().setValue
        )
        self.monitor_sent_table.itemSelectionChanged.connect(self._sync_monitor_sel_sent)
        self.monitor_received_table.itemSelectionChanged.connect(self._sync_monitor_sel_recv)

    # ── Packet Tester ─────────────────────────────────────────────────────────

    def _build_tester_tab(self) -> None:
        layout = QVBoxLayout(self.tab_tester)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Black box: Host Packet Sender controls
        self.tester_control_group = QGroupBox("Host Packet Sender", self.tab_tester)
        self.tester_control_group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #334155; border-radius: 8px;
                background-color: #0B0F19; margin-top: 12px;
                font-weight: bold; color: #22D3EE;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        """)
        ctrl_layout = QVBoxLayout(self.tester_control_group)
        ctrl_layout.setContentsMargins(10, 15, 10, 10)
        ctrl_layout.setSpacing(6)

        # Row 1: Packet | Src | Dst
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(QLabel("Packet:", self.tester_control_group))
        self.tester_packet_select = QComboBox(self.tester_control_group)
        self._populate_packet_list()
        self.tester_packet_select.currentIndexChanged.connect(self._update_tester_fields)
        hdr.addWidget(self.tester_packet_select, 3)

        hdr.addWidget(QLabel("Src:", self.tester_control_group))
        self.tester_src_select = QComboBox(self.tester_control_group)
        self.tester_src_select.addItem("HOST (0x02)", 2)
        hdr.addWidget(self.tester_src_select, 1)

        hdr.addWidget(QLabel("Dst:", self.tester_control_group))
        self.tester_dst_select = QComboBox(self.tester_control_group)
        self.tester_dst_select.addItem("MCU (0x01)", 1)
        self.tester_dst_select.addItem("CENTRAL (0x03)", 3)
        self.tester_dst_select.addItem("PERIPHERAL (0x04)", 4)
        hdr.addWidget(self.tester_dst_select, 1)
        ctrl_layout.addLayout(hdr)

        # Row 2: Dynamic parameter form
        self.tester_form = QFormLayout()
        self.tester_form.setSpacing(6)
        ctrl_layout.addLayout(self.tester_form)
        self._tester_param_widgets: dict[str, tuple] = {}
        self._build_tester_param_fields()

        # Row 3: Toggle + Status + Send button
        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self.btn_toggle_manual_test = ToggleSwitch("Enable Manual Test Mode", self.tester_control_group)
        self.btn_toggle_manual_test.toggled.connect(self._on_manual_test_toggled)
        action_row.addWidget(self.btn_toggle_manual_test, 1)

        self.tester_status_label = QLabel("Ready", self.tester_control_group)
        self.tester_status_label.setStyleSheet("color: #94A3B8; font-style: italic;")
        action_row.addWidget(self.tester_status_label, 2)

        self.btn_send_packet = QPushButton("▶ Send Packet", self.tester_control_group)
        self.btn_send_packet.setStyleSheet("""
            QPushButton {
                background-color: rgba(34,211,238,0.15); color: #22D3EE;
                border: 1px solid #22D3EE; border-radius: 6px;
                padding: 6px 18px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #22D3EE; color: #0F172A; }
            QPushButton:pressed { background-color: #0891B2; color: #F8FAFC; }
        """)
        self.btn_send_packet.clicked.connect(self._send_manual_packet)
        action_row.addWidget(self.btn_send_packet, 0)

        ctrl_layout.addLayout(action_row)
        layout.addWidget(self.tester_control_group, 1)

        # ── Clear tester button
        clear_row = QHBoxLayout()
        clear_row.addStretch()
        self.btn_tester_clear = QPushButton("🗑 Clear Tester", self.tab_tester)
        self.btn_tester_clear.setStyleSheet(self._secondary_btn_style())
        self.btn_tester_clear.clicked.connect(self._clear_tester)
        clear_row.addWidget(self.btn_tester_clear)
        layout.addLayout(clear_row)

        # ── Correlated tester tables
        self.tester_splitter = QSplitter(Qt.Orientation.Horizontal, self.tab_tester)
        layout.addWidget(self.tester_splitter, 2)

        self.tester_sent_group = QGroupBox("Test Message Sent", self.tester_splitter)
        self.tester_sent_group.setStyleSheet(self._group_style("#F59E0B"))
        ts_layout = QVBoxLayout(self.tester_sent_group)
        ts_layout.setContentsMargins(6, 12, 6, 6)
        self.tester_sent_table = QTableWidget(self.tester_sent_group)
        self._configure_table(self.tester_sent_table, is_sent=True)
        ts_layout.addWidget(self.tester_sent_table)

        self.tester_recv_group = QGroupBox("Test Message Received", self.tester_splitter)
        self.tester_recv_group.setStyleSheet(self._group_style("#EF4444"))
        tr_layout = QVBoxLayout(self.tester_recv_group)
        tr_layout.setContentsMargins(6, 12, 6, 6)
        self.tester_received_table = QTableWidget(self.tester_recv_group)
        self._configure_table(self.tester_received_table, is_sent=False)
        self.tester_received_table.verticalHeader().setVisible(False)
        tr_layout.addWidget(self.tester_received_table)

        self.tester_splitter.setSizes([600, 600])

        self.tester_sent_table.verticalScrollBar().valueChanged.connect(
            self.tester_received_table.verticalScrollBar().setValue
        )
        self.tester_received_table.verticalScrollBar().valueChanged.connect(
            self.tester_sent_table.verticalScrollBar().setValue
        )
        self.tester_sent_table.itemSelectionChanged.connect(self._sync_tester_sel_sent)
        self.tester_received_table.itemSelectionChanged.connect(self._sync_tester_sel_recv)

        # Trigger initial field visibility
        self._update_tester_fields()

    # ─────────────────────────────────────────────────────────────────────────
    #  Style helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _group_style(color: str) -> str:
        return f"""
            QGroupBox {{
                border: 2px solid {color}; border-radius: 8px;
                margin-top: 12px; font-weight: bold; color: {color};
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
        """

    @staticmethod
    def _secondary_btn_style() -> str:
        return """
            QPushButton {
                background-color: rgba(100,116,139,0.15); color: #64748B;
                border: 1px solid #334155; border-radius: 6px;
                padding: 4px 14px; font-size: 12px;
            }
            QPushButton:hover { background-color: #334155; color: #94A3B8; }
        """

    # ─────────────────────────────────────────────────────────────────────────
    #  Table configuration
    # ─────────────────────────────────────────────────────────────────────────

    def _configure_table(self, table: QTableWidget, *, is_sent: bool) -> None:
        table.setColumnCount(5)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(False)

        if is_sent:
            headers = ["Time", "Dst", "Seq", "Command", "Parameters"]
            accent = "#F59E0B"
            sel_bg = "rgba(245,158,11,0.2)"
        else:
            headers = ["Time", "Src", "Seq", "Response", "Decoded Data"]
            accent = "#EF4444"
            sel_bg = "rgba(239,68,68,0.2)"

        table.setHorizontalHeaderLabels(headers)
        table.setStyleSheet(f"""
            QTableWidget {{
                background-color: #0B0F19; color: #E2E8F0;
                gridline-color: #1E293B; border: 1px solid {accent};
                font-size: 12px;
            }}
            QHeaderView::section {{
                background-color: #1E293B; color: {accent};
                border: 1px solid #334155; font-weight: bold; padding: 4px;
            }}
            QTableWidget::item:selected {{ background-color: {sel_bg}; color: #FFFFFF; }}
        """)

        # Column widths
        # 0 Time  | 1 Dst/Src | 2 Seq | 3 Command/Response | 4 Details (stretch)
        table.setColumnWidth(0, 190)
        table.setColumnWidth(1, 100)
        table.setColumnWidth(2, 70)
        table.setColumnWidth(3, 160)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

    # ─────────────────────────────────────────────────────────────────────────
    #  Packet list & tester parameter fields
    # ─────────────────────────────────────────────────────────────────────────

    def _populate_packet_list(self) -> None:
        commands = [
            ("── GET Commands ──────────────────", None),
            ("device_information_get",  "device_information_get"),
            ("battery_info_get",        "battery_info_get"),
            ("time_sync_get",           "time_sync_get"),
            ("sys_config_get",          "sys_config_get"),
            ("sys_ranging_cfg_get",     "sys_ranging_cfg_get"),
            ("sensor_fusion_cfg_get",   "sensor_fusion_cfg_get"),
            ("pos_calib_cfg_get",       "pos_calib_cfg_get"),
            ("anchor_layout_get",       "anchor_layout_get"),
            ("ranging_status_get",      "ranging_status_get"),
            ("calib_status_get",        "calib_status_get"),
            ("rtos_resource_get",       "rtos_resource_get"),
            ("rtos_task_stats_get",     "rtos_task_stats_get"),
            ("── SET Commands ──────────────────", None),
            ("time_sync_set",           "time_sync_set"),
            ("sys_config_set",          "sys_config_set"),
            ("sys_ranging_cfg_set",     "sys_ranging_cfg_set"),
            ("sensor_fusion_cfg_set",   "sensor_fusion_cfg_set"),
            ("pos_calib_cfg_set",       "pos_calib_cfg_set"),
            ("anchor_layout_set",       "anchor_layout_set"),
            ("host_transport_set",      "host_transport_set"),
            ("── Control Commands ──────────────", None),
            ("ranging_start",           "ranging_start"),
            ("ranging_stop",            "ranging_stop"),
            ("imu_reset",               "imu_reset"),
            ("imu_calib_start",         "imu_calib_start"),
            ("device_reset",            "device_reset"),
            ("uwb_reset",               "uwb_reset"),
            ("factory_config_reset",    "factory_config_reset"),
            ("enter_to_bootloader",     "enter_to_bootloader"),
            ("end_session",             "end_session"),
            ("── Log Commands ──────────────────", None),
            ("log_data",                "log_data"),
            ("log_clear",               "log_clear"),
            ("── Special ───────────────────────", None),
            ("none (keep-alive ping)",  "none"),
        ]
        for label, value in commands:
            self.tester_packet_select.addItem(label, value)
            if value is None:
                # Section headers are not selectable
                idx = self.tester_packet_select.count() - 1
                self.tester_packet_select.model().item(idx).setEnabled(False)
                self.tester_packet_select.setItemData(
                    idx, QColor("#475569"), Qt.ItemDataRole.ForegroundRole
                )

    def _build_tester_param_fields(self) -> None:
        """Build all possible parameter widgets; visibility is toggled per-command."""

        def add(key: str, label: str, widget) -> None:
            lbl = QLabel(label, self.tester_control_group)
            self.tester_form.addRow(lbl, widget)
            self._tester_param_widgets[key] = (lbl, widget)

        # time_sync_set
        self.f_unix_ms = QLineEdit(self.tester_control_group)
        self.f_unix_ms.setPlaceholderText("blank = current host time (ms)")
        add("unix_time_ms", "Unix time ms:", self.f_unix_ms)

        self.f_timezone = QLineEdit(self.tester_control_group)
        self.f_timezone.setText("420")
        self.f_timezone.setPlaceholderText("Timezone offset in minutes (+420 = UTC+7)")
        add("timezone_offset", "Timezone offset:", self.f_timezone)

        # sys_config_set
        self.f_role = QComboBox(self.tester_control_group)
        self.f_role.addItem("ANCHOR (2)", 2)
        self.f_role.addItem("TAG (1)", 1)
        add("role", "Device role:", self.f_role)

        self.f_device_id = QLineEdit(self.tester_control_group)
        self.f_device_id.setText("1")
        self.f_device_id.setPlaceholderText("Integer device ID (1–254)")
        add("device_id", "Device ID:", self.f_device_id)

        self.f_ranging_period_ms = QLineEdit(self.tester_control_group)
        self.f_ranging_period_ms.setText("300")
        self.f_ranging_period_ms.setPlaceholderText("Ranging period in ms")
        add("ranging_period_ms", "Ranging period ms:", self.f_ranging_period_ms)

        self.f_rx_timeout_ms = QLineEdit(self.tester_control_group)
        self.f_rx_timeout_ms.setText("120")
        self.f_rx_timeout_ms.setPlaceholderText("RX timeout in ms")
        add("rx_timeout_ms", "RX timeout ms:", self.f_rx_timeout_ms)

        self.f_uwb_channel = QLineEdit(self.tester_control_group)
        self.f_uwb_channel.setText("5")
        self.f_uwb_channel.setPlaceholderText("UWB channel number (5 or 9)")
        add("uwb_channel", "UWB channel:", self.f_uwb_channel)

        # sys_ranging_cfg_set
        self.f_period_ms = QLineEdit(self.tester_control_group)
        self.f_period_ms.setText("300")
        self.f_period_ms.setPlaceholderText("Measurement period in ms")
        add("period_ms", "Period ms:", self.f_period_ms)

        self.f_timeout_ms = QLineEdit(self.tester_control_group)
        self.f_timeout_ms.setText("120")
        self.f_timeout_ms.setPlaceholderText("Response timeout in ms")
        add("timeout_ms", "Timeout ms:", self.f_timeout_ms)

        # log_data / log_clear
        self.f_log_type = QComboBox(self.tester_control_group)
        self.f_log_type.addItem("DEVICE_LOG (1)", 1)
        self.f_log_type.addItem("UNSPECIFIED (0)", 0)
        add("log_type", "Log type:", self.f_log_type)

        self.f_log_data = QLineEdit(self.tester_control_group)
        self.f_log_data.setPlaceholderText("hex: 01 02 AB  or  text:hello")
        add("log_data_payload", "Data:", self.f_log_data)

        self.f_log_offset = QLineEdit(self.tester_control_group)
        self.f_log_offset.setText("0")
        add("log_offset", "Offset:", self.f_log_offset)

        self.f_log_length = QLineEdit(self.tester_control_group)
        self.f_log_length.setText("0")
        add("log_length", "Length:", self.f_log_length)

        # Generic JSON fallback
        self.f_extra_json = QLineEdit(self.tester_control_group)
        self.f_extra_json.setPlaceholderText('Extra JSON params — e.g. {"role": 1}')
        add("extra_args_json", "Extra JSON:", self.f_extra_json)

    # Mapping: command_name → set of field keys to show
    _VISIBLE_FIELDS: dict[str, set[str]] = {
        "time_sync_set":       {"unix_time_ms", "timezone_offset"},
        "sys_config_set":      {"role", "device_id", "ranging_period_ms", "rx_timeout_ms", "uwb_channel"},
        "sys_ranging_cfg_set": {"period_ms", "timeout_ms"},
        "log_data":            {"log_type", "log_data_payload"},
        "log_clear":           {"log_type", "log_offset", "log_length"},
    }

    def _update_tester_fields(self) -> None:
        packet_name = self.tester_packet_select.currentData()
        visible = self._VISIBLE_FIELDS.get(packet_name or "", set())
        if not visible and packet_name and packet_name != "none":
            visible = {"extra_args_json"}
        for key, (lbl, widget) in self._tester_param_widgets.items():
            show = key in visible
            lbl.setVisible(show)
            widget.setVisible(show)

    # ─────────────────────────────────────────────────────────────────────────
    #  Public API (called by MainWindow)
    # ─────────────────────────────────────────────────────────────────────────

    def set_protocol_service(self, protocol_service) -> None:
        """Wire up protocol_service signals.  Safe to call multiple times."""
        if self._protocol_service:
            try:
                self._protocol_service.packet_received.disconnect(self._on_packet_received)
                self._protocol_service.packet_sent.disconnect(self._on_packet_sent)
            except Exception:
                pass

        self._protocol_service = protocol_service

        if protocol_service:
            protocol_service.packet_received.connect(self._on_packet_received)
            protocol_service.packet_sent.connect(self._on_packet_sent)
            log.debug("CommunicationTab: connected to ProtocolService signals.")

    def set_developer_mode(self, enabled: bool) -> None:
        self._is_developer = enabled
        if not enabled:
            self.disable_manual_test_mode()

    def disable_manual_test_mode(self) -> None:
        if hasattr(self, "btn_toggle_manual_test"):
            self.btn_toggle_manual_test.setChecked(False)

    # ─────────────────────────────────────────────────────────────────────────
    #  Sub-tab switch
    # ─────────────────────────────────────────────────────────────────────────

    def _on_sub_tab_changed(self, index: int) -> None:
        # Toggle state is preserved across sub-tab switches as requested by user
        pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Manual Test Mode toggle
    # ─────────────────────────────────────────────────────────────────────────

    def _on_manual_test_toggled(self, checked: bool) -> None:
        from services.command_bus import shared_command_bus  # local import (avoid circular)
        if shared_command_bus:
            shared_command_bus.manual_test_mode_enabled = checked
            if checked:
                self.tester_status_label.setText(
                    "⚠ Test Mode ACTIVE — background queries blocked (BLE allowed)"
                )
                self.tester_status_label.setStyleSheet("color: #F59E0B; font-weight: bold;")
            else:
                self.tester_status_label.setText("✓ Test Mode OFF — background queries restored")
                self.tester_status_label.setStyleSheet("color: #10B981;")
            log.info("Manual Test Mode: %s", "ENABLED" if checked else "DISABLED")
        else:
            self.tester_status_label.setText("⚠ CommandBus not available")
            self.tester_status_label.setStyleSheet("color: #EF4444;")

    # ─────────────────────────────────────────────────────────────────────────
    #  RX/TX slot handlers  (Main GUI Thread — no locking needed)
    # ─────────────────────────────────────────────────────────────────────────

    @pyqtSlot(str, object)
    def _on_packet_sent(self, param_name: str, pkt) -> None:
        """Called every time a packet is sent via ProtocolService.send_packet().

        NOTE: Because packet_sent uses a Direct Connection on the same thread,
        this slot fires while _send_manual_packet is still on the call stack
        (BEFORE pkt is returned).  We use _manual_send_active flag so the seq
        is immediately tagged as a tester seq when the flag is True.
        """
        timestamp = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        hdr = getattr(pkt, "hdr", None)
        addr = getattr(hdr, "addr", None)
        dst_addr = getattr(addr, "dst", 0)
        seq      = getattr(hdr, "seq", 0)
        dst_str  = self._addr_name(dst_addr)
        details  = self._format_pkt(param_name, pkt)

        # Tag seq immediately if we are inside a manual send
        if self._manual_send_active:
            self._tester_seqs.add(seq)

        # ── Update black box (Current Transmission)
        self.monitor_detail_text.setPlainText(
            f"DIRECTION : SENT (TX)\n"
            f"Time      : {timestamp}\n"
            f"Packet    : {param_name}\n"
            f"Seq       : {seq}  (0x{seq:04X})\n"
            f"Src       : HOST (0x02)\n"
            f"Dst       : {dst_str}\n"
            f"\nParameters:\n{details.replace(', ', chr(10))}"
        )

        # ── Live Monitor: always show every sent packet
        self._add_sent_row(
            self.monitor_sent_table,
            self.monitor_received_table,
            self._monitor_seq_to_row,
            seq, timestamp, dst_str, param_name, details,
        )

        # ── Packet Tester: only show packets triggered by _send_manual_packet
        if seq in self._tester_seqs:
            self._add_sent_row(
                self.tester_sent_table,
                self.tester_received_table,
                self._tester_seq_to_row,
                seq, timestamp, dst_str, param_name, details,
            )

    @pyqtSlot(str, object)
    def _on_packet_received(self, param_name: str, pkt) -> None:
        """Called every time a packet is decoded & received from the device."""
        timestamp = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        hdr = getattr(pkt, "hdr", None)
        addr = getattr(hdr, "addr", None)
        src_addr = getattr(addr, "src", 0)
        seq      = getattr(hdr, "seq", 0)
        src_str  = self._addr_name(src_addr)
        details  = self._format_pkt(param_name, pkt)

        # ── Update black box with the latest received response
        self.monitor_detail_text.append(
            f"\nDIRECTION : RECEIVED (RX)\n"
            f"Time      : {timestamp}\n"
            f"Packet    : {param_name}\n"
            f"Seq       : {seq}  (0x{seq:04X})\n"
            f"Src       : {src_str}\n"
            f"Dst       : HOST (0x02)\n"
            f"\nDecoded Data:\n{details.replace(', ', chr(10))}"
        )
        # Keep detail box from growing unboundedly (≈ last 200 lines)
        doc = self.monitor_detail_text.document()
        if doc.blockCount() > 250:
            # Trim: keep only the last 200 lines to avoid O(n) cursor loop
            full_text = self.monitor_detail_text.toPlainText()
            lines = full_text.split("\n")
            if len(lines) > 200:
                trimmed = "\n".join(lines[-200:])
                self.monitor_detail_text.setPlainText(trimmed)
                # Scroll to bottom after trim
                self.monitor_detail_text.moveCursor(QTextCursor.MoveOperation.End)

        # ── Live Monitor: fill in the matching Received column
        self._fill_recv_row(
            self.monitor_sent_table,
            self.monitor_received_table,
            self._monitor_seq_to_row,
            seq, timestamp, src_str, param_name, details,
        )

        # ── Packet Tester: fill correlated row IF this seq was a manual send
        if seq in self._tester_seqs:
            self._fill_recv_row(
                self.tester_sent_table,
                self.tester_received_table,
                self._tester_seq_to_row,
                seq, timestamp, src_str, param_name, details,
            )
            self._tester_seqs.discard(seq)
            self.tester_status_label.setText(f"✓ Response received — seq={seq}")
            self.tester_status_label.setStyleSheet("color: #10B981;")

    # ─────────────────────────────────────────────────────────────────────────
    #  Correlated table row helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _add_sent_row(
        self,
        sent_table: QTableWidget,
        recv_table: QTableWidget,
        seq_to_row: dict,
        seq: int,
        timestamp: str,
        dst: str,
        cmd: str,
        details: str,
    ) -> None:
        """Insert a new correlated row into sent+recv tables (TX side)."""
        row = sent_table.rowCount()
        sent_table.insertRow(row)
        recv_table.insertRow(row)

        # Sent side
        self._set_row(sent_table, row, [timestamp, dst, str(seq), cmd, details])

        # Received side — placeholders (will be filled when response arrives)
        for col in range(5):
            item = QTableWidgetItem("")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            recv_table.setItem(row, col, item)

        seq_to_row[seq] = row
        sent_table.scrollToBottom()
        recv_table.scrollToBottom()

    def _fill_recv_row(
        self,
        sent_table: QTableWidget,
        recv_table: QTableWidget,
        seq_to_row: dict,
        seq: int,
        timestamp: str,
        src: str,
        resp: str,
        details: str,
    ) -> None:
        """Fill the RX side of an existing correlated row, or add a new unsolicited row."""
        row = seq_to_row.get(seq)

        if row is not None:
            # Update existing correlated row (matching Sent entry)
            recv_table.setItem(row, 0, self._make_item(timestamp))
            recv_table.setItem(row, 1, self._make_item(src))
            recv_table.setItem(row, 2, self._make_item(str(seq)))
            recv_table.setItem(row, 3, self._make_item(resp))
            data_item = self._make_item(details)
            data_item.setForeground(QColor("#10B981"))  # green = valid data received
            recv_table.setItem(row, 4, data_item)
            recv_table.scrollToBottom()
        else:
            # Unsolicited / pushed-by-device packet — no matching sent entry
            row = sent_table.rowCount()
            sent_table.insertRow(row)
            recv_table.insertRow(row)
            for col in range(5):
                sent_table.setItem(row, col, self._make_item(""))
            recv_table.setItem(row, 0, self._make_item(timestamp))
            recv_table.setItem(row, 1, self._make_item(src))
            recv_table.setItem(row, 2, self._make_item(str(seq)))
            recv_table.setItem(row, 3, self._make_item(resp))
            data_item = self._make_item(details)
            data_item.setForeground(QColor("#10B981"))
            recv_table.setItem(row, 4, data_item)
            sent_table.scrollToBottom()
            recv_table.scrollToBottom()

    @staticmethod
    def _set_row(table: QTableWidget, row: int, values: list[str]) -> None:
        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, col, item)

    @staticmethod
    def _make_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    # ─────────────────────────────────────────────────────────────────────────
    #  Selection synchronization
    # ─────────────────────────────────────────────────────────────────────────

    def _sync_monitor_sel_sent(self) -> None:
        row = self.monitor_sent_table.currentRow()
        if row >= 0:
            self.monitor_received_table.blockSignals(True)
            self.monitor_received_table.setCurrentCell(row, 0)
            self.monitor_received_table.blockSignals(False)

    def _sync_monitor_sel_recv(self) -> None:
        row = self.monitor_received_table.currentRow()
        if row >= 0:
            self.monitor_sent_table.blockSignals(True)
            self.monitor_sent_table.setCurrentCell(row, 0)
            self.monitor_sent_table.blockSignals(False)

    def _sync_tester_sel_sent(self) -> None:
        row = self.tester_sent_table.currentRow()
        if row >= 0:
            self.tester_received_table.blockSignals(True)
            self.tester_received_table.setCurrentCell(row, 0)
            self.tester_received_table.blockSignals(False)

    def _sync_tester_sel_recv(self) -> None:
        row = self.tester_received_table.currentRow()
        if row >= 0:
            self.tester_sent_table.blockSignals(True)
            self.tester_sent_table.setCurrentCell(row, 0)
            self.tester_sent_table.blockSignals(False)

    # ─────────────────────────────────────────────────────────────────────────
    #  Clear actions
    # ─────────────────────────────────────────────────────────────────────────

    def _clear_monitor(self) -> None:
        self.monitor_sent_table.setRowCount(0)
        self.monitor_received_table.setRowCount(0)
        self._monitor_seq_to_row.clear()
        self.monitor_detail_text.clear()

    def _clear_tester(self) -> None:
        self.tester_sent_table.setRowCount(0)
        self.tester_received_table.setRowCount(0)
        self._tester_seq_to_row.clear()
        self._tester_seqs.clear()
        self.tester_status_label.setText("Ready")
        self.tester_status_label.setStyleSheet("color: #94A3B8; font-style: italic;")

    # ─────────────────────────────────────────────────────────────────────────
    #  Manual packet sender
    # ─────────────────────────────────────────────────────────────────────────

    def _send_manual_packet(self) -> None:
        packet_name = self.tester_packet_select.currentData()
        if not packet_name:
            self.tester_status_label.setText("⚠ Please select a valid packet")
            self.tester_status_label.setStyleSheet("color: #F59E0B;")
            return

        dst_addr = int(self.tester_dst_select.currentData())
        src_addr = int(self.tester_src_select.currentData())

        # Collect parameters
        try:
            params = self._collect_params(packet_name)
        except ValueError as exc:
            self.tester_status_label.setText(f"⚠ Param error: {exc}")
            self.tester_status_label.setStyleSheet("color: #EF4444;")
            return

        # No dongle → simulation mode
        if not self._protocol_service:
            self.tester_status_label.setText("⚠ Simulation Mode (no dongle connected)")
            self.tester_status_label.setStyleSheet("color: #F59E0B;")
            self._simulate_exchange(packet_name, dst_addr, params)
            return

        # Real hardware path via CommandBus
        from services.command_bus import shared_command_bus  # local import
        if not shared_command_bus:
            self.tester_status_label.setText("⚠ CommandBus not initialized")
            self.tester_status_label.setStyleSheet("color: #EF4444;")
            return

        # ── IMPORTANT: Set flag BEFORE calling send().
        # packet_sent is a Direct Connection → _on_packet_sent fires synchronously
        # INSIDE send_command() BEFORE pkt is returned here.  The flag lets
        # _on_packet_sent know to tag the seq as a tester packet immediately.
        self._manual_send_active = True
        try:
            pkt = shared_command_bus.send(
                packet_name,
                dst_addr=dst_addr,
                src_addr=src_addr,
                manual_bypass=True,
                **params,
            )
        finally:
            self._manual_send_active = False

        if pkt is not None:
            seq = getattr(getattr(pkt, "hdr", None), "seq", None)
            # seq was already added to _tester_seqs inside _on_packet_sent
            self.tester_status_label.setText(f"✈ Sent {packet_name}  seq={seq}")
            self.tester_status_label.setStyleSheet("color: #22D3EE;")
            log.info("Manual packet sent: %s  seq=%s  dst=0x%02X", packet_name, seq, dst_addr)
        else:
            self.tester_status_label.setText("⚠ Send failed (check connection / command flag)")
            self.tester_status_label.setStyleSheet("color: #EF4444;")

    # ─────────────────────────────────────────────────────────────────────────
    #  Parameter collection
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_params(self, packet_name: str) -> dict:
        params: dict = {}

        if packet_name == "time_sync_set":
            txt = self.f_unix_ms.text().strip()
            params["unix_time_ms"] = int(txt, 0) if txt else int(time.time() * 1000)
            params["timezone_offset"] = self._parse_int(
                self.f_timezone.text().strip() or "420", "timezone_offset"
            )

        elif packet_name == "sys_config_set":
            params["role"]               = int(self.f_role.currentData())
            params["device_id"]          = self._parse_int(self.f_device_id.text().strip() or "1", "device_id")
            params["ranging_period_ms"]  = self._parse_int(self.f_ranging_period_ms.text().strip() or "300", "ranging_period_ms")
            params["rx_timeout_ms"]      = self._parse_int(self.f_rx_timeout_ms.text().strip() or "120", "rx_timeout_ms")
            params["uwb_channel"]        = self._parse_int(self.f_uwb_channel.text().strip() or "5", "uwb_channel")

        elif packet_name == "sys_ranging_cfg_set":
            params["period_ms"]  = self._parse_int(self.f_period_ms.text().strip() or "300", "period_ms")
            params["timeout_ms"] = self._parse_int(self.f_timeout_ms.text().strip() or "120", "timeout_ms")

        elif packet_name == "log_data":
            params["log_type"] = int(self.f_log_type.currentData())
            params["data"]     = self._parse_bytes(self.f_log_data.text())

        elif packet_name == "log_clear":
            params["log_type"] = int(self.f_log_type.currentData())
            params["offset"]   = self._parse_int(self.f_log_offset.text().strip() or "0", "offset")
            params["length"]   = self._parse_int(self.f_log_length.text().strip() or "0", "length")

        else:
            # Generic JSON path
            txt = self.f_extra_json.text().strip()
            if txt:
                try:
                    params.update(json.loads(txt))
                except Exception as exc:
                    raise ValueError(f"JSON parse error: {exc}") from exc

        return params

    @staticmethod
    def _parse_int(text: str, field: str) -> int:
        try:
            v = int(text, 0)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc
        if v < 0:
            raise ValueError(f"{field} must be >= 0")
        return v

    @staticmethod
    def _parse_bytes(text: str) -> bytes:
        raw = (text or "").strip()
        if not raw:
            return b""
        if raw.lower().startswith("text:"):
            return raw[5:].encode()
        normalized = raw.replace(" ", "").replace(",", "").replace("_", "")
        try:
            return bytes.fromhex(normalized)
        except ValueError:
            return raw.encode()

    # ─────────────────────────────────────────────────────────────────────────
    #  Simulation (no dongle)
    # ─────────────────────────────────────────────────────────────────────────

    _MOCK_SEQ: int = 200  # class-level counter shared by all instances

    def _simulate_exchange(self, packet_name: str, dst_addr: int, params: dict) -> None:
        seq = CommunicationTab._MOCK_SEQ
        CommunicationTab._MOCK_SEQ += 1

        timestamp = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        dst_str = self._addr_name(dst_addr)
        param_str = ", ".join(f"{k}: {v}" for k, v in params.items()) or "no params"

        self._tester_seqs.add(seq)

        # Add TX rows
        self._add_sent_row(
            self.monitor_sent_table, self.monitor_received_table,
            self._monitor_seq_to_row, seq, timestamp, dst_str, packet_name, param_str,
        )
        self._add_sent_row(
            self.tester_sent_table, self.tester_received_table,
            self._tester_seq_to_row, seq, timestamp, dst_str, packet_name, param_str,
        )

        # Simulate response after 120 ms
        def _respond():
            resp_name = packet_name.replace("_get", "_resp").replace("_set", "_resp")
            if resp_name == packet_name:
                resp_name = packet_name + "_resp"

            mock_data_map = {
                "battery": "bat_soc_percent: 88%, bat_voltage_mv: 3950, mcu_temp_c: 29.5, is_charging: False",
                "device_information": "device_type: ANCHOR, role: ANCHOR, fw_version: v1.2.4, uid: 4E554231",
                "time_sync": "unix_time_ms: 1719324545000, timezone_offset: 420",
                "sys_config": "role: ANCHOR, device_id: 1, ranging_period_ms: 300, uwb_channel: 5",
                "ranging": "status: RUNNING, success_count: 42, total_count: 50",
            }
            resp_data = next(
                (v for k, v in mock_data_map.items() if k in packet_name),
                "status: SUCCESS, code: 0",
            )
            resp_ts = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"

            self._fill_recv_row(
                self.monitor_sent_table, self.monitor_received_table,
                self._monitor_seq_to_row, seq, resp_ts, dst_str, resp_name, resp_data,
            )
            self._fill_recv_row(
                self.tester_sent_table, self.tester_received_table,
                self._tester_seq_to_row, seq, resp_ts, dst_str, resp_name, resp_data,
            )
            self._tester_seqs.discard(seq)
            self.tester_status_label.setText(f"✓ [SIM] Response received — seq={seq}")
            self.tester_status_label.setStyleSheet("color: #10B981;")

        QTimer.singleShot(120, _respond)

    # ─────────────────────────────────────────────────────────────────────────
    #  Utility helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _addr_name(addr: int) -> str:
        return {1: "MCU (0x01)", 2: "HOST (0x02)", 3: "CENTRAL (0x03)", 4: "PERIPHERAL (0x04)"}.get(
            addr, f"UNKNOWN (0x{addr:02X})"
        )

    @staticmethod
    def _format_pkt(param_name: str, pkt) -> str:
        """Extract human-readable field summary from a protobuf packet_t."""
        if pkt is None:
            return ""
        payload = None
        if hasattr(pkt, "WhichOneof"):
            one = pkt.WhichOneof("params")
            if one:
                payload = getattr(pkt, one, None)
        if payload is None:
            payload = pkt

        if hasattr(payload, "ListFields"):
            parts = []
            for fd, val in payload.ListFields():
                if isinstance(val, bytes):
                    val_s = "0x" + val.hex().upper()
                elif isinstance(val, float):
                    val_s = f"{val:.4f}"
                else:
                    val_s = str(val)
                parts.append(f"{fd.name}: {val_s}")
            return ", ".join(parts) if parts else "no fields"

        return str(payload)
