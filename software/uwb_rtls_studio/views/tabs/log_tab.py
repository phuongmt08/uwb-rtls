"""
UWB RTLS Studio — Log & Session History Tab (Frontend Only)
Tab 5: Live log viewer + Session history browser.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QTextEdit, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QFrame
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
import random
from datetime import datetime, timedelta


DEMO_LOG_MESSAGES = [
    ("INFO",     "DEVICE",   "Ranging result received: x=2.45, y=1.89, z=0.00"),
    ("INFO",     "DEVICE",   "Anchor A1 distance: 234.5 cm, FP_AMP=512"),
    ("WARN",     "DEVICE",   "Anchor A3 response timeout (>70ms)"),
    ("INFO",     "DEVICE",   "Battery SoC: 78%, voltage: 3.82V"),
    ("ERROR",    "DEVICE",   "UWB CRC error on channel 5, retrying..."),
    ("DEBUG",    "DEVICE",   "TWR exchange: poll_tx=0x1A2B, resp_rx=0x3C4D"),
    ("INFO",     "APP",      "Position update rate: 10.2 Hz"),
    ("DEBUG",    "PROTOCOL", "TX [tag=16] ranging_start_t → peripheral"),
    ("DEBUG",    "PROTOCOL", "RX [tag=18] ranging_result_t ← peripheral"),
    ("INFO",     "DEVICE",   "IMU accel: x=0.02g, y=-0.01g, z=1.00g"),
    ("WARN",     "DEVICE",   "RSSI dropped below -70 dBm for Anchor A2"),
    ("INFO",     "DEVICE",   "Clock sync offset: +12 μs"),
]


class LogTab(QWidget):
    def __init__(self, is_developer=False, parent=None):
        super().__init__(parent)
        self._is_developer = is_developer
        self._build_ui()
        self._start_demo_log()

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled
        for w in self._dev_widgets:
            w.setVisible(enabled)

    def _build_ui(self):
        self._dev_widgets = []

        main = QVBoxLayout(self)
        main.setSpacing(0)
        main.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ═══ TOP: Live Log ═══
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setSpacing(8)
        log_layout.setContentsMargins(12, 12, 12, 6)

        # Log header
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("📋 Live Log")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: #22D3EE;")
        header.addWidget(title)

        header.addStretch()

        # Filter controls
        self._filter_level = QComboBox()
        self._filter_level.addItems(["ALL", "INFO", "WARN", "ERROR", "DEBUG"])
        self._filter_level.setFixedWidth(100)
        self._filter_level.currentTextChanged.connect(self._apply_filter)
        header.addWidget(QLabel("Level:"))
        header.addWidget(self._filter_level)

        self._filter_source = QComboBox()
        self._filter_source.addItems(["ALL", "DEVICE", "APP", "PROTOCOL"])
        self._filter_source.setFixedWidth(110)
        dev_filter_label = QLabel("Source:")
        header.addWidget(dev_filter_label)
        header.addWidget(self._filter_source)
        self._dev_widgets.extend([dev_filter_label, self._filter_source])

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Search logs...")
        self._search.setFixedWidth(200)
        header.addWidget(self._search)

        log_layout.addLayout(header)

        # Log text area
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Cascadia Code", 11))
        self._log_text.setStyleSheet("""
            QTextEdit {
                background-color: #020617; color: #10B981;
                border: 1px solid #334155; border-radius: 8px; padding: 8px;
            }
        """)
        log_layout.addWidget(self._log_text)

        # Log buttons
        log_btns = QHBoxLayout()
        log_btns.setSpacing(8)

        self._log_count = QLabel("0 entries")
        self._log_count.setStyleSheet("color: #64748B;")
        log_btns.addWidget(self._log_count)
        log_btns.addStretch()

        btn_export = QPushButton("📥 Export CSV")
        log_btns.addWidget(btn_export)

        btn_clear = QPushButton("🗑 Clear")
        btn_clear.setStyleSheet("""
            QPushButton { background: rgba(239,68,68,0.1); color: #EF4444;
                border: 1px solid #EF4444; border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #EF4444; color: #F8FAFC; }
        """)
        log_btns.addWidget(btn_clear)
        self._dev_widgets.append(btn_clear)

        btn_clear.clicked.connect(self._log_text.clear)
        log_layout.addLayout(log_btns)

        splitter.addWidget(log_widget)

        # ═══ BOTTOM: Session History Browser ═══
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setSpacing(8)
        history_layout.setContentsMargins(12, 6, 12, 12)

        hist_header = QHBoxLayout()
        hist_title = QLabel("📂 Session History")
        hist_title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        hist_title.setStyleSheet("color: #22D3EE;")
        hist_header.addWidget(hist_title)
        hist_header.addStretch()

        self._hist_filter = QComboBox()
        self._hist_filter.addItems(["All Types", "Ranging", "Streaming", "Log"])
        self._hist_filter.setFixedWidth(120)
        hist_header.addWidget(QLabel("Type:"))
        hist_header.addWidget(self._hist_filter)
        history_layout.addLayout(hist_header)

        # Session table
        self._session_table = QTableWidget(0, 7)
        self._session_table.setHorizontalHeaderLabels([
            "Session ID", "Type", "Start Time", "Duration",
            "Packets", "Errors", "Actions"
        ])
        self._session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._session_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._session_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._session_table.setAlternatingRowColors(True)
        self._session_table.verticalHeader().setVisible(False)
        history_layout.addWidget(self._session_table)

        # Populate demo sessions
        self._populate_demo_sessions()

        splitter.addWidget(history_widget)
        splitter.setSizes([400, 250])

        main.addWidget(splitter)

        # Apply initial mode
        self.set_developer_mode(self._is_developer)

    def _apply_filter(self, level):
        # In real app, this would filter the log entries
        pass

    def _populate_demo_sessions(self):
        demo_sessions = [
            ("SES_20260530_123000", "Ranging", "2026-05-30 12:30:00", "15m 23s", "9,231", "3"),
            ("SES_20260530_110500", "Streaming", "2026-05-30 11:05:00", "8m 45s", "5,120", "0"),
            ("SES_20260529_160000", "Ranging", "2026-05-29 16:00:00", "42m 10s", "25,340", "12"),
            ("SES_20260529_091500", "Log", "2026-05-29 09:15:00", "2m 30s", "1,024", "0"),
            ("SES_20260528_143000", "Ranging", "2026-05-28 14:30:00", "1h 05m", "38,912", "8"),
        ]
        for session in demo_sessions:
            row = self._session_table.rowCount()
            self._session_table.insertRow(row)
            for col, text in enumerate(session):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 1:  # Type
                    colors = {"Ranging": "#22D3EE", "Streaming": "#10B981", "Log": "#F59E0B"}
                    item.setForeground(QColor(colors.get(text, "#F8FAFC")))
                if col == 5 and text != "0":  # Errors
                    item.setForeground(QColor("#EF4444"))
                self._session_table.setItem(row, col, item)

            # Action button placeholder
            btn_open = QPushButton("📂 Open")
            btn_open.setFixedHeight(28)
            btn_open.setStyleSheet("""
                QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                    border-radius: 4px; font-size: 11px; font-weight: bold; }
                QPushButton:hover { background: #22D3EE; color: #0F172A; }
            """)
            self._session_table.setCellWidget(row, 6, btn_open)

    def _start_demo_log(self):
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._add_demo_log)
        self._demo_timer.start(1500)
        self._log_entry_count = 0

    def _add_demo_log(self):
        msg = random.choice(DEMO_LOG_MESSAGES)
        level, source, text = msg

        # Filter by mode
        if not self._is_developer and source in ("APP", "PROTOCOL"):
            return
        if not self._is_developer and level == "DEBUG":
            return

        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # Color format
        colors = {
            "INFO": "#10B981", "WARN": "#F59E0B",
            "ERROR": "#EF4444", "DEBUG": "#94A3B8"
        }
        color = colors.get(level, "#F8FAFC")

        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#475569"))
        cursor.insertText(f"[{now}] ", fmt)

        fmt.setForeground(QColor(color))
        fmt.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(f"[{level:5s}] ", fmt)

        fmt.setForeground(QColor("#64748B"))
        fmt.setFontWeight(QFont.Weight.Normal)
        cursor.insertText(f"[{source:8s}] ", fmt)

        fmt.setForeground(QColor("#F8FAFC") if level != "DEBUG" else QColor("#94A3B8"))
        cursor.insertText(f"{text}\n", fmt)

        self._log_text.setTextCursor(cursor)
        self._log_text.ensureCursorVisible()

        self._log_entry_count += 1
        self._log_count.setText(f"{self._log_entry_count} entries")
