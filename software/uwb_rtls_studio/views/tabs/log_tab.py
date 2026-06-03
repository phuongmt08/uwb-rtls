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





class LogTab(QWidget):
    def __init__(self, is_developer=False, parent=None):
        super().__init__(parent)
        self._is_developer = is_developer
        self._build_ui()
        self._log_entry_count = 0

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

        # Placeholder for real data
        pass

        splitter.addWidget(history_widget)
        splitter.setSizes([400, 250])

        main.addWidget(splitter)

        # Apply initial mode
        self.set_developer_mode(self._is_developer)

    def _apply_filter(self, level):
        # In real app, this would filter the log entries
        pass

