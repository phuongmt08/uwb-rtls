"""
==============================================================================
  UWB RTLS Studio — Communication Tab
==============================================================================
  File        : views/tabs/communication_tab.py
  Description : Two-sub-tab Communication panel for Developer Mode.

  Sub-tabs:
    1.  Live Monitor  — Passive observer of ALL packets (TX + RX) flowing
                          through the protocol service.  Nothing is sent here.
    2.  Packet Tester — Manual packet sender / response inspector.
                          Activating "Manual Test Mode" blocks background
                          auto-queries so the user can test
                          individual packets with a real dongle + hardware.

  Thread Model:
    ALL slot callbacks are invoked on the Main GUI Thread because
    protocol_service.packet_sent / packet_received are emitted there
    (see protocol_service.py comment block).  No extra locking is needed.

  Wiring (set by MainWindow._setup_tabs):
    tab_communication.set_protocol_service(protocol_service)

  Key design rules:
    â€¢ NEVER touch/import anything from other tabs.
    â€¢ NEVER create new threads — Qt signal/slot handles cross-thread delivery.
    â€¢ Monitor tables accumulate rows; Tester rows are correlated by seq number.
    â€¢ Manual-test seq tracking uses ONLY _tester_seqs (set of pkt.hdr.seq),
      NOT a boolean flag, to avoid false positives.
==============================================================================
"""
from __future__ import annotations

import os
import time
import json
import logging

from common.commands import CommandCatalog, mapped_destination_for
from common.transport import VvAddress
from common import protocol_pb2 as pb
from utils.runtime_mode import is_test_mode

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QComboBox, QLineEdit, QSplitter,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QFormLayout, QAbstractItemView, QTextEdit, QDialog,
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
      â€¢ Live Monitor  — passively shows every TX/RX packet.
      â€¢ Packet Tester — manually send any command and inspect the response.
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
        self._manual_send_in_flight: bool = False
        self._manual_send_expected_name: str | None = None
        self._manual_send_expected_seq: int | None = None
        self._last_manual_send_sig: tuple | None = None
        self._last_manual_send_at: float = 0.0

        # seq → table row index for correlated display
        self._monitor_seq_to_row: dict[int, int] = {}
        self._tester_seq_to_row: dict[int, int] = {}
        self._decode_popups: list[QDialog] = []

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
        self.sub_tabs.addTab(self.tab_monitor, "Live Monitor")

        # ── Tab 2: Packet Tester
        self.tab_tester = QWidget()
        self._build_tester_tab()
        self.sub_tabs.addTab(self.tab_tester, "Packet Tester")

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
        self.btn_monitor_clear = QPushButton("Clear Monitor", self.tab_monitor)
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
        self.monitor_sent_table.cellClicked.connect(self._on_monitor_sent_cell_clicked)
        self.monitor_received_table.cellClicked.connect(self._on_monitor_received_cell_clicked)

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
        self.tester_src_select.addItem(self._addr_name(int(VvAddress.HOST)), int(VvAddress.HOST))
        hdr.addWidget(self.tester_src_select, 1)

        hdr.addWidget(QLabel("Dst:", self.tester_control_group))
        self.tester_dst_select = QComboBox(self.tester_control_group)
        self.tester_dst_select.addItem(self._addr_name(int(VvAddress.MCU)), int(VvAddress.MCU))
        self.tester_dst_select.addItem(self._addr_name(int(VvAddress.CENTRAL)), int(VvAddress.CENTRAL))
        self.tester_dst_select.addItem(self._addr_name(int(VvAddress.PERIPHERAL)), int(VvAddress.PERIPHERAL))
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

        self.btn_send_packet = QPushButton("Send Packet", self.tester_control_group)
        self.btn_send_packet.setStyleSheet("""
            QPushButton {
                background-color: rgba(34,211,238,0.15); color: #22D3EE;
                border: 1px solid #22D3EE; border-radius: 6px;
                padding: 6px 18px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #22D3EE; color: #0F172A; }
            QPushButton:pressed { background-color: #0891B2; color: #F8FAFC; }
        """)
        self.btn_send_packet.setAutoDefault(False)
        self.btn_send_packet.setDefault(False)
        self.btn_send_packet.clicked.connect(self._send_manual_packet)
        action_row.addWidget(self.btn_send_packet, 0)

        ctrl_layout.addLayout(action_row)
        layout.addWidget(self.tester_control_group, 1)

        # ── Clear tester button
        clear_row = QHBoxLayout()
        clear_row.addStretch()
        self.btn_tester_clear = QPushButton("Clear Tester", self.tab_tester)
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
        self.tester_sent_table.cellClicked.connect(self._on_tester_sent_cell_clicked)
        self.tester_received_table.cellClicked.connect(self._on_tester_received_cell_clicked)

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
        table.setColumnCount(7)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(False)

        if is_sent:
            headers = ["Time", "ID", "Src", "Dst", "Seq", "Command", "Parameters"]
            accent = "#F59E0B"
            sel_bg = "rgba(245,158,11,0.2)"
        else:
            headers = ["Time", "ID", "Src", "Dst", "Seq", "Response", "Decoded Data"]
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

        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 45)
        table.setColumnWidth(2, 105)
        table.setColumnWidth(3, 105)
        table.setColumnWidth(4, 70)
        table.setColumnWidth(5, 160)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

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
            ("time_sync_adv_set",       "time_sync_adv_set"),
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
            ("device_type_get",         "device_type_get"),
            ("device_type_set",         "device_type_set"),
            ("enter_to_bootloader",     "enter_to_bootloader"),
            ("end_session",             "end_session"),
            ("── Log Commands ──────────────────", None),
            ("log_data",                "log_data"),
            ("log_clear",               "log_clear"),
            ("BLE Commands",           None),
            ("ble_status_get",          "ble_status_get"),
            ("ble_conn_params_get",     "ble_conn_params_get"),
            ("ble_conn_params_set",     "ble_conn_params_set"),
            ("ble_scan_start",          "ble_scan_start"),
            ("ble_scan_stop",           "ble_scan_stop"),
            ("ble_connect",             "ble_connect"),
            ("ble_disconnect",          "ble_disconnect"),
            ("ble_adv_config_set",      "ble_adv_config_set"),
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
        self.f_device_type = QComboBox(self.tester_control_group)
        self.f_device_type.addItem("TAG (1)", 1)
        self.f_device_type.addItem("ANCHOR (2)", 2)
        self.f_device_type.addItem("DONGLE (3)", 3)
        add("device_type", "Device type:", self.f_device_type)

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

        # BLE test fields
        self.f_ble_mac = QLineEdit(self.tester_control_group)
        self.f_ble_mac.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        add("mac_address", "BLE MAC:", self.f_ble_mac)

        self.f_ble_reason = QLineEdit(self.tester_control_group)
        self.f_ble_reason.setText("0")
        self.f_ble_reason.setPlaceholderText("Disconnect reason")
        add("reason", "Reason:", self.f_ble_reason)

        self.f_ble_min_interval_ms = QLineEdit(self.tester_control_group)
        self.f_ble_min_interval_ms.setText("15")
        add("min_interval_ms", "Min interval ms:", self.f_ble_min_interval_ms)

        self.f_ble_max_interval_ms = QLineEdit(self.tester_control_group)
        self.f_ble_max_interval_ms.setText("30")
        add("max_interval_ms", "Max interval ms:", self.f_ble_max_interval_ms)

        self.f_ble_slave_latency = QLineEdit(self.tester_control_group)
        self.f_ble_slave_latency.setText("0")
        add("slave_latency", "Slave latency:", self.f_ble_slave_latency)

        self.f_ble_sup_timeout_ms = QLineEdit(self.tester_control_group)
        self.f_ble_sup_timeout_ms.setText("4000")
        add("sup_timeout_ms", "Supervision timeout ms:", self.f_ble_sup_timeout_ms)

        self.f_ble_scan_duration_ms = QLineEdit(self.tester_control_group)
        self.f_ble_scan_duration_ms.setText("5000")
        add("duration_ms", "Scan duration ms:", self.f_ble_scan_duration_ms)

        self.f_ble_scan_interval_ms = QLineEdit(self.tester_control_group)
        self.f_ble_scan_interval_ms.setText("160")
        add("interval_ms", "Scan interval ms:", self.f_ble_scan_interval_ms)

        self.f_ble_scan_window_ms = QLineEdit(self.tester_control_group)
        self.f_ble_scan_window_ms.setText("80")
        add("window_ms", "Scan window ms:", self.f_ble_scan_window_ms)

        self.f_ble_active_scanning = QComboBox(self.tester_control_group)
        self.f_ble_active_scanning.addItem("True", True)
        self.f_ble_active_scanning.addItem("False", False)
        add("active_scanning", "Active scanning:", self.f_ble_active_scanning)

        self.f_ble_adv_enable = QComboBox(self.tester_control_group)
        self.f_ble_adv_enable.addItem("True", True)
        self.f_ble_adv_enable.addItem("False", False)
        add("enable", "Adv enable:", self.f_ble_adv_enable)

        self.f_ble_adv_serial = QLineEdit(self.tester_control_group)
        self.f_ble_adv_serial.setText("0")
        add("serial_number", "Serial number:", self.f_ble_adv_serial)

        self.f_ble_adv_name = QLineEdit(self.tester_control_group)
        self.f_ble_adv_name.setPlaceholderText("Device name")
        add("device_name", "Device name:", self.f_ble_adv_name)
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
        "time_sync_adv_set":   {"device_type", "device_id", "unix_time_ms", "timezone_offset"},
        "device_type_set":     {"device_type"},
        "sys_config_set":      {"role", "device_id", "ranging_period_ms", "rx_timeout_ms", "uwb_channel"},
        "sys_ranging_cfg_set": {"period_ms", "timeout_ms"},
        "ble_connect":         {"mac_address"},
        "ble_disconnect":      {"reason"},
        "ble_conn_params_set": {"min_interval_ms", "max_interval_ms", "slave_latency", "sup_timeout_ms"},
        "ble_scan_start":      {"duration_ms", "interval_ms", "window_ms", "active_scanning"},
        "ble_adv_config_set":  {"enable", "serial_number", "device_name"},
        "log_data":            {"log_type", "log_data_payload"},
        "log_clear":           {"log_type", "log_offset", "log_length"},
    }

    def _update_tester_fields(self) -> None:
        packet_name = self.tester_packet_select.currentData()
        visible = self._VISIBLE_FIELDS.get(packet_name or "", set())
        mapped_dst = mapped_destination_for(packet_name or "")
        if mapped_dst is not None:
            index = self.tester_dst_select.findData(int(mapped_dst))
            if index >= 0:
                self.tester_dst_select.setCurrentIndex(index)
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
            protocol_service.packet_received.connect(
                self._on_packet_received,
                Qt.ConnectionType.UniqueConnection,
            )
            protocol_service.packet_sent.connect(
                self._on_packet_sent,
                Qt.ConnectionType.UniqueConnection,
            )
            log.debug("CommunicationTab: connected to ProtocolService signals.")

    def set_developer_mode(self, enabled: bool) -> None:
        self._is_developer = enabled
        if not enabled:
            self.disable_manual_test_mode()

    def disable_manual_test_mode(self) -> None:
        if hasattr(self, "btn_toggle_manual_test"):
            self.btn_toggle_manual_test.setChecked(False)
        from utils.app_state import shared_app_state
        shared_app_state.manual_test_mode_enabled = False

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
        from utils.app_state import shared_app_state
        shared_app_state.manual_test_mode_enabled = checked
        if shared_command_bus:
            shared_command_bus.manual_test_mode_enabled = checked
            if checked:
                self.tester_status_label.setText(
                    "Test Mode ACTIVE - background traffic blocked"
                )
                self.tester_status_label.setStyleSheet("color: #F59E0B; font-weight: bold;")
            else:
                self.tester_status_label.setText("✓ Test Mode OFF — background queries restored")
                self.tester_status_label.setStyleSheet("color: #10B981;")
            log.info("Manual Test Mode: %s", "ENABLED" if checked else "DISABLED")
        else:
            self.tester_status_label.setText("CommandBus not available")
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
        timestamp = self._event_timestamp_text("tx", pkt)
        hdr = getattr(pkt, "hdr", None)
        addr = getattr(hdr, "addr", None)
        src_addr = int(getattr(addr, "src", 0) or 0)
        dst_addr = int(getattr(addr, "dst", 0) or 0)
        seq = int(getattr(hdr, "seq", 0) or 0)
        src_str = self._addr_name(src_addr)
        dst_str = self._addr_name(dst_addr)
        details = self._format_pkt(param_name, pkt)
        packet_id = self._packet_id_text(param_name, pkt)

        is_current_manual_send = False
        if self._manual_send_active and self._manual_send_expected_seq is None:
            if param_name == self._manual_send_expected_name:
                self._manual_send_expected_seq = seq
                self._tester_seqs.add(seq)
                is_current_manual_send = True
        elif self._manual_send_expected_seq == seq:
            is_current_manual_send = True

        self.monitor_detail_text.setPlainText(
            f"DIRECTION : SENT (TX)\n"
            f"Time      : {timestamp}\n"
            f"Packet    : {param_name}\n"
            f"Seq       : {seq}  (0x{seq:04X})\n"
            f"Src       : {src_str}\n"
            f"Dst       : {dst_str}\n"
            f"\nParameters:\n{details.replace(', ', chr(10))}"
        )

        self._add_sent_row(
            self.monitor_sent_table,
            self.monitor_received_table,
            self._monitor_seq_to_row,
            seq, timestamp, packet_id, src_str, dst_str, param_name, details,
        )

        if is_current_manual_send or seq in self._tester_seqs:
            self._add_sent_row(
                self.tester_sent_table,
                self.tester_received_table,
                self._tester_seq_to_row,
                seq, timestamp, packet_id, src_str, dst_str, param_name, details,
            )

    @pyqtSlot(str, object)
    def _on_packet_received(self, param_name: str, pkt) -> None:
        """Called every time a packet is decoded & received from the device."""
        timestamp = self._event_timestamp_text("rx", pkt)
        hdr = getattr(pkt, "hdr", None)
        addr = getattr(hdr, "addr", None)
        src_addr = int(getattr(addr, "src", 0) or 0)
        dst_addr = int(getattr(addr, "dst", 0) or 0)
        seq = int(getattr(hdr, "seq", 0) or 0)
        src_str = self._addr_name(src_addr)
        dst_str = self._addr_name(dst_addr)
        details = self._format_pkt(param_name, pkt)
        packet_id = self._packet_id_text(param_name, pkt)
        match_seq = int(getattr(getattr(pkt, "ack", None), "ack_seq", seq) or seq) if param_name == "ack" else seq
        tester_match_seq = match_seq
        if param_name != "ack" and self._manual_send_expected_seq is not None and self._manual_send_expected_name:
            try:
                expected_manual_resp = CommandCatalog().expected_response_for(self._manual_send_expected_name)
            except Exception:
                expected_manual_resp = ""
            if expected_manual_resp == param_name:
                tester_match_seq = int(self._manual_send_expected_seq)
        response_detail = (
            f"\nDIRECTION : RECEIVED (RX)\n"
            f"Time      : {timestamp}\n"
            f"Packet    : {param_name}\n"
            f"Seq       : {seq}  (0x{seq:04X})\n"
        )
        if param_name == "ack":
            response_detail += f"Ack Seq   : {match_seq}  (0x{match_seq:04X})\n"
        response_detail += (
            f"Src       : {src_str}\n"
            f"Dst       : {dst_str}\n"
            f"\nDecoded Data:\n{details.replace(', ', chr(10))}"
        )
        self.monitor_detail_text.append(response_detail)


        self._fill_recv_row(
            self.monitor_sent_table,
            self.monitor_received_table,
            self._monitor_seq_to_row,
            match_seq, timestamp, packet_id, src_str, dst_str, param_name, details,
        )

        if tester_match_seq in self._tester_seqs:
            self._fill_recv_row(
                self.tester_sent_table,
                self.tester_received_table,
                self._tester_seq_to_row,
                tester_match_seq, timestamp, packet_id, src_str, dst_str, param_name, details,
            )
            if param_name == "ack":
                self.tester_status_label.setText(f"ACK received - seq={tester_match_seq}")
                self.tester_status_label.setStyleSheet("color: #22D3EE;")
            else:
                self._tester_seqs.discard(tester_match_seq)
                if seq != tester_match_seq:
                    self._tester_seqs.discard(seq)
                if self._manual_send_expected_seq == tester_match_seq:
                    self._manual_send_expected_seq = None
                    self._manual_send_expected_name = None
                self.tester_status_label.setText(f"Response received - seq={tester_match_seq}")
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
        packet_id: str,
        src: str,
        dst: str,
        cmd: str,
        details: str,
    ) -> None:
        """Insert a new correlated row into sent+recv tables (TX side)."""
        follow_tail = self._should_follow_table_tail(sent_table, recv_table)
        row = sent_table.rowCount()
        sent_table.insertRow(row)
        recv_table.insertRow(row)

        # Sent side
        self._set_row(sent_table, row, [timestamp, packet_id, src, dst, str(seq), cmd, details])

        # Received side — placeholders (will be filled when response arrives)
        for col in range(7):
            item = QTableWidgetItem("")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            recv_table.setItem(row, col, item)

        seq_to_row[seq] = row
        self._scroll_tables_if_following(sent_table, recv_table, follow_tail)

    def _fill_recv_row(
        self,
        sent_table: QTableWidget,
        recv_table: QTableWidget,
        seq_to_row: dict,
        seq: int,
        timestamp: str,
        packet_id: str,
        src: str,
        dst: str,
        resp: str,
        details: str,
    ) -> None:
        """Fill the RX side of an existing correlated row, or add a new unsolicited row."""
        follow_tail = self._should_follow_table_tail(sent_table, recv_table)
        row = seq_to_row.get(seq)

        if row is not None:
            # Only correlate payload responses back to the TX row when the command on that row
            # actually expects this response type. MCU-originated packets can use an independent
            # seq counter, so matching by seq alone can overwrite the wrong row.
            sent_cmd = sent_table.item(row, 5).text().strip() if sent_table.item(row, 5) else ""
            expected_resp = ""
            if resp != "ack" and sent_cmd:
                try:
                    expected_resp = CommandCatalog().expected_response_for(sent_cmd)
                except Exception:
                    expected_resp = ""
                if expected_resp and expected_resp != resp:
                    log.warning(
                        "Monitor seq collision: sent_cmd=%s expected_resp=%s got_resp=%s seq=%s src=%s dst=%s",
                        sent_cmd,
                        expected_resp,
                        resp,
                        seq,
                        src,
                        dst,
                    )
                    row = None
        if row is not None:
            # Update existing correlated row (matching Sent entry)
            existing_resp = recv_table.item(row, 5).text().strip() if recv_table.item(row, 5) else ""
            existing_details = recv_table.item(row, 6).data(Qt.ItemDataRole.UserRole) if recv_table.item(row, 6) else ""
            if resp == "ack" and existing_resp and existing_resp != "ack" and existing_details:
                return
            recv_table.setItem(row, 0, self._make_item(timestamp))
            recv_table.setItem(row, 1, self._make_item(packet_id))
            recv_table.setItem(row, 2, self._make_item(src))
            recv_table.setItem(row, 3, self._make_item(dst))
            recv_table.setItem(row, 4, self._make_item(str(seq)))
            recv_table.setItem(row, 5, self._make_item(resp))
            data_item = self._make_item(details)
            data_item.setData(Qt.ItemDataRole.UserRole, details)
            data_item.setToolTip("Click to view full decoded fields")
            data_item.setForeground(QColor("#10B981"))  # green = valid data received
            recv_table.setItem(row, 6, data_item)
            self._scroll_tables_if_following(sent_table, recv_table, follow_tail)
        else:
            # Unsolicited / pushed-by-device packet — no matching sent entry
            row = sent_table.rowCount()
            sent_table.insertRow(row)
            recv_table.insertRow(row)
            for col in range(7):
                sent_table.setItem(row, col, self._make_item(""))
            recv_table.setItem(row, 0, self._make_item(timestamp))
            recv_table.setItem(row, 1, self._make_item(packet_id))
            recv_table.setItem(row, 2, self._make_item(src))
            recv_table.setItem(row, 3, self._make_item(dst))
            recv_table.setItem(row, 4, self._make_item(str(seq)))
            recv_table.setItem(row, 5, self._make_item(resp))
            data_item = self._make_item(details)
            data_item.setData(Qt.ItemDataRole.UserRole, details)
            data_item.setToolTip("Click to view full decoded fields")
            data_item.setForeground(QColor("#10B981"))
            recv_table.setItem(row, 6, data_item)
            self._scroll_tables_if_following(sent_table, recv_table, follow_tail)

    @staticmethod
    def _is_table_at_tail(table: QTableWidget, threshold: int = 2) -> bool:
        scrollbar = table.verticalScrollBar()
        return scrollbar.value() >= max(0, scrollbar.maximum() - threshold)

    def _should_follow_table_tail(self, sent_table: QTableWidget, recv_table: QTableWidget) -> bool:
        return self._is_table_at_tail(sent_table) and self._is_table_at_tail(recv_table)

    @staticmethod
    def _scroll_tables_if_following(sent_table: QTableWidget, recv_table: QTableWidget, follow_tail: bool) -> None:
        if follow_tail:
            sent_table.scrollToBottom()
            recv_table.scrollToBottom()

    @staticmethod
    def _packet_id_text(param_name: str, pkt=None) -> str:
        descriptor = getattr(pkt, "DESCRIPTOR", pb.packet_t.DESCRIPTOR)
        field = descriptor.fields_by_name.get(param_name or "")
        return str(field.number) if field is not None else "-"
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

    def _on_monitor_received_cell_clicked(self, row: int, column: int) -> None:
        self._show_decoded_popup(self.monitor_received_table, row, column, "Received Packet Fields")

    def _on_monitor_sent_cell_clicked(self, row: int, column: int) -> None:
        self._show_decoded_popup(self.monitor_sent_table, row, column, "Sent Packet Parameters")

    def _on_tester_received_cell_clicked(self, row: int, column: int) -> None:
        self._show_decoded_popup(self.tester_received_table, row, column, "Test Packet Fields")

    def _on_tester_sent_cell_clicked(self, row: int, column: int) -> None:
        self._show_decoded_popup(self.tester_sent_table, row, column, "Test Sent Packet Parameters")

    def _show_decoded_popup(self, table: QTableWidget, row: int, column: int, title: str) -> None:
        if column != 6 or row < 0:
            return
        item = table.item(row, column)
        if item is None:
            return
        details = item.data(Qt.ItemDataRole.UserRole) or item.text().strip()
        if not details:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)

        text = QTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(details.replace(", ", "\n"))
        text.setStyleSheet("""
            QTextEdit {
                background-color: #05080E; color: #22D3EE;
                border: 1px solid #334155;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px; border-radius: 4px; padding: 6px;
            }
        """)
        layout.addWidget(text)

        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        self._decode_popups.append(dialog)
        try:
            dialog.exec()
        finally:
            if dialog in self._decode_popups:
                self._decode_popups.remove(dialog)

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
        self._manual_send_expected_name = None
        self._manual_send_expected_seq = None
        self.tester_status_label.setText("Ready")
        self.tester_status_label.setStyleSheet("color: #94A3B8; font-style: italic;")

    def _finish_manual_send_dispatch(self) -> None:
        self._manual_send_in_flight = False
        if hasattr(self, "btn_send_packet"):
            self.btn_send_packet.setEnabled(True)

    # ─────────────────────────────────────────────────────────────────────────
    #  Manual packet sender
    # ─────────────────────────────────────────────────────────────────────────

    def _send_manual_packet(self) -> None:
        if self._manual_send_in_flight:
            self.tester_status_label.setText("Warning: previous send still being dispatched")
            self.tester_status_label.setStyleSheet("color: #F59E0B;")
            return

        packet_name = self.tester_packet_select.currentData()
        if not packet_name:
            self.tester_status_label.setText("Warning: please select a valid packet")
            self.tester_status_label.setStyleSheet("color: #F59E0B;")
            return

        dst_addr = int(self.tester_dst_select.currentData())
        src_addr = int(self.tester_src_select.currentData())

        try:
            params = self._collect_params(packet_name)
        except ValueError as exc:
            self.tester_status_label.setText(f"Warning: param error: {exc}")
            self.tester_status_label.setStyleSheet("color: #EF4444;")
            return

        send_signature = (
            packet_name,
            src_addr,
            dst_addr,
            json.dumps(params, sort_keys=True, default=str),
        )
        now = time.monotonic()
        if self._last_manual_send_sig == send_signature and (now - self._last_manual_send_at) < 0.25:
            self.tester_status_label.setText("Warning: duplicate click ignored")
            self.tester_status_label.setStyleSheet("color: #F59E0B;")
            return

        self._manual_send_in_flight = True
        self.btn_send_packet.setEnabled(False)
        self._manual_send_expected_name = packet_name
        self._manual_send_expected_seq = None

        try:
            if not self._protocol_service:
                self._last_manual_send_sig = send_signature
                self._last_manual_send_at = now
                if not is_test_mode():
                    self.tester_status_label.setText("Warning: no hardware protocol service")
                    self.tester_status_label.setStyleSheet("color: #EF4444;")
                    self._manual_send_in_flight = False
                    self.btn_send_packet.setEnabled(True)
                    return

                self.tester_status_label.setText("Warning: simulation mode (no dongle connected)")
                self.tester_status_label.setStyleSheet("color: #F59E0B;")
                self._simulate_exchange(packet_name, dst_addr, params)
                return

            from services.command_bus import shared_command_bus  # local import
            if not shared_command_bus:
                self.tester_status_label.setText("Warning: CommandBus not initialized")
                self.tester_status_label.setStyleSheet("color: #EF4444;")
                self._manual_send_expected_name = None
                return

            self._manual_send_active = True
            try:
                pkt = shared_command_bus.send(
                    packet_name,
                    dst_addr=dst_addr,
                    src_addr=src_addr,
                    manual_bypass=True,
                    command_params=params,
                )
            finally:
                self._manual_send_active = False

            if pkt is not None:
                seq = getattr(getattr(pkt, "hdr", None), "seq", None)
                if self._manual_send_expected_seq is None and seq is not None:
                    self._manual_send_expected_seq = int(seq)
                    self._tester_seqs.add(int(seq))
                self._last_manual_send_sig = send_signature
                self._last_manual_send_at = now
                self.tester_status_label.setText(f"Sent {packet_name}  seq={seq}")
                self.tester_status_label.setStyleSheet("color: #22D3EE;")
                log.info("Manual packet sent: %s  seq=%s  dst=0x%02X", packet_name, seq, dst_addr)
            else:
                self._manual_send_expected_name = None
                self._manual_send_expected_seq = None
                self.tester_status_label.setText("Warning: send failed (check connection / command flag)")
                self.tester_status_label.setStyleSheet("color: #EF4444;")
        finally:
            QTimer.singleShot(150, self._finish_manual_send_dispatch)

    # Manual packet parameter collection
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_params(self, packet_name: str) -> dict:
        params: dict = {}

        if packet_name == "time_sync_set":
            txt = self.f_unix_ms.text().strip()
            params["unix_time_ms"] = int(txt, 0) if txt else int(time.time() * 1000)
            params["timezone_offset"] = self._parse_int(
                self.f_timezone.text().strip() or "420", "timezone_offset"
            )

        elif packet_name == "time_sync_adv_set":
            txt = self.f_unix_ms.text().strip()
            params["device_type"] = int(self.f_device_type.currentData())
            params["device_id"] = self._parse_int(self.f_device_id.text().strip() or "1", "device_id")
            params["unix_time_ms"] = int(txt, 0) if txt else int(time.time() * 1000)
            params["timezone_offset"] = self._parse_int(
                self.f_timezone.text().strip() or "420", "timezone_offset"
            )

        elif packet_name == "device_type_set":
            params["device_type"] = int(self.f_device_type.currentData())
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

        elif packet_name == "ble_connect":
            params["mac_address"] = self._parse_mac_bytes(self.f_ble_mac.text())

        elif packet_name == "ble_disconnect":
            params["reason"] = self._parse_int(self.f_ble_reason.text().strip() or "0", "reason")

        elif packet_name == "ble_conn_params_set":
            params["min_interval_ms"] = self._parse_int(self.f_ble_min_interval_ms.text().strip() or "15", "min_interval_ms")
            params["max_interval_ms"] = self._parse_int(self.f_ble_max_interval_ms.text().strip() or "30", "max_interval_ms")
            params["slave_latency"] = self._parse_int(self.f_ble_slave_latency.text().strip() or "0", "slave_latency")
            params["sup_timeout_ms"] = self._parse_int(self.f_ble_sup_timeout_ms.text().strip() or "4000", "sup_timeout_ms")

        elif packet_name == "ble_scan_start":
            params["duration_ms"] = self._parse_int(self.f_ble_scan_duration_ms.text().strip() or "5000", "duration_ms")
            params["interval_ms"] = self._parse_int(self.f_ble_scan_interval_ms.text().strip() or "160", "interval_ms")
            params["window_ms"] = self._parse_int(self.f_ble_scan_window_ms.text().strip() or "80", "window_ms")
            params["active_scanning"] = bool(self.f_ble_active_scanning.currentData())

        elif packet_name == "ble_adv_config_set":
            params["enable"] = bool(self.f_ble_adv_enable.currentData())
            params["serial_number"] = self._parse_int(self.f_ble_adv_serial.text().strip() or "0", "serial_number")
            params["device_name"] = self.f_ble_adv_name.text().strip()

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

    @staticmethod
    def _parse_mac_bytes(text: str) -> bytes:
        raw = (text or "").strip()
        normalized = raw.replace(":", "").replace("-", "").replace(" ", "")
        if len(normalized) != 12:
            raise ValueError("mac_address must have exactly 6 bytes")
        try:
            return bytes.fromhex(normalized)
        except ValueError as exc:
            raise ValueError("mac_address must be valid hex") from exc
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
            self._monitor_seq_to_row, seq, timestamp, self._packet_id_text(packet_name), self._addr_name(int(VvAddress.HOST)), dst_str, packet_name, param_str,
        )
        self._add_sent_row(
            self.tester_sent_table, self.tester_received_table,
            self._tester_seq_to_row, seq, timestamp, self._packet_id_text(packet_name), self._addr_name(int(VvAddress.HOST)), dst_str, packet_name, param_str,
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
                self._monitor_seq_to_row, seq, resp_ts, self._packet_id_text(resp_name), dst_str, self._addr_name(int(VvAddress.HOST)), resp_name, resp_data,
            )
            self._fill_recv_row(
                self.tester_sent_table, self.tester_received_table,
                self._tester_seq_to_row, seq, resp_ts, self._packet_id_text(resp_name), dst_str, self._addr_name(int(VvAddress.HOST)), resp_name, resp_data,
            )
            self._tester_seqs.discard(seq)
            self.tester_status_label.setText(f"✓ [SIM] Response received — seq={seq}")
            self.tester_status_label.setStyleSheet("color: #10B981;")

        QTimer.singleShot(120, _respond)

    # ─────────────────────────────────────────────────────────────────────────
    #  Utility helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_wallclock(timestamp_s: float) -> str:
        base = time.localtime(timestamp_s)
        millis = int((timestamp_s % 1) * 1000)
        return time.strftime("%H:%M:%S", base) + f".{millis:03d}"

    def _event_timestamp_text(self, direction: str, pkt) -> str:
        event_time = None
        if self._protocol_service and hasattr(self._protocol_service, "packet_event_time"):
            try:
                event_time = self._protocol_service.packet_event_time(direction, pkt)
            except Exception:
                event_time = None
        if event_time is None:
            event_time = time.time()
        return self._format_wallclock(float(event_time))

    @staticmethod
    def _addr_name(addr: int) -> str:
        labels = {
            int(VvAddress.NONE): f"NONE (0x{int(VvAddress.NONE):02X})",
            int(VvAddress.MCU): f"MCU (0x{int(VvAddress.MCU):02X})",
            int(VvAddress.VEHICLE): f"VEHICLE (0x{int(VvAddress.VEHICLE):02X})",
            int(VvAddress.CENTRAL): f"CENTRAL (0x{int(VvAddress.CENTRAL):02X})",
            int(VvAddress.PERIPHERAL): f"PERIPHERAL (0x{int(VvAddress.PERIPHERAL):02X})",
            int(VvAddress.HOST): f"HOST (0x{int(VvAddress.HOST):02X})",
            int(VvAddress.DEBUG): f"DEBUG (0x{int(VvAddress.DEBUG):02X})",
            int(VvAddress.BCAST): f"BCAST (0x{int(VvAddress.BCAST):02X})",
        }
        return labels.get(int(addr or 0), f"UNKNOWN (0x{int(addr or 0):02X})")

    @staticmethod
    def _format_pkt(param_name: str, pkt) -> str:
        """Extract human-readable field summary from a protobuf packet_t."""
        def _scalar_to_text(value) -> str:
            if isinstance(value, bytes):
                return "0x" + value.hex().upper()
            if isinstance(value, float):
                return f"{value:.4f}"
            return str(value)

        def _is_repeated_field(fd) -> bool:
            label = getattr(fd, "label", None)
            if label is not None:
                repeated_label = getattr(type(fd), "LABEL_REPEATED", 3)
                return label == repeated_label
            return bool(getattr(fd, "is_repeated", False))

        def _is_message_field(fd) -> bool:
            field_type = getattr(fd, "type", None)
            message_type = getattr(type(fd), "TYPE_MESSAGE", 11)
            return field_type == message_type

        def _flatten_fields(value, prefix: str = "") -> list[str]:
            if hasattr(value, "ListFields"):
                parts: list[str] = []
                for fd, subval in value.ListFields():
                    field_prefix = f"{prefix}{fd.name}"
                    if _is_repeated_field(fd):
                        if _is_message_field(fd):
                            for idx, entry in enumerate(subval):
                                parts.extend(_flatten_fields(entry, f"{field_prefix}[{idx}]."))
                        else:
                            parts.append(f"{field_prefix}: [{', '.join(_scalar_to_text(v) for v in subval)}]")
                    elif _is_message_field(fd):
                        nested = _flatten_fields(subval, f"{field_prefix}.")
                        parts.extend(nested or [f"{field_prefix}: {{}}"])
                    else:
                        parts.append(f"{field_prefix}: {_scalar_to_text(subval)}")
                return parts
            return [f"{prefix.rstrip('.')}: {_scalar_to_text(value)}"] if prefix else [_scalar_to_text(value)]

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
            parts = _flatten_fields(payload)
            return ", ".join(parts) if parts else "no fields"

        return str(payload)
