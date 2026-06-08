"""
UWB RTLS Studio — Log & Session History Tab (UI loaded from .ui file)
Tab 5: Live log viewer + Session history browser.

FE: Loaded from views/ui/log_tab.ui (editable in Qt Designer)
BE: Log filtering + session management (this file)
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QTextEdit, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QFrame
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'log_tab.ui')


class LogTab(QWidget):
    def __init__(self, parent=None, is_developer=False):
        super().__init__(parent)
        self._is_developer = is_developer
        self._log_entry_count = 0

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_dev_widgets()
        self._connect_signals()

        # Apply initial mode
        self.set_developer_mode(self._is_developer)

    def _setup_dev_widgets(self):
        """Collect developer-only widgets for visibility toggling."""
        self._dev_widgets = [
            self.lbl_source,
            self.filter_source,
            self.btn_clear,
        ]

    def _connect_signals(self):
        """Connect UI signals."""
        self.filter_level.currentTextChanged.connect(self._apply_filter)
        self.btn_clear.clicked.connect(self.log_text.clear)

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled
        for w in self._dev_widgets:
            w.setVisible(enabled)

    def _apply_filter(self, level):
        # In real app, this would filter the log entries
        pass
