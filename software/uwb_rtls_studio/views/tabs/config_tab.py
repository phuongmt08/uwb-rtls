"""
UWB RTLS Studio — Config Tab (UI loaded from .ui file)
Tab 3: User + Developer config sections.

FE: Loaded from views/ui/config_tab.ui (editable in Qt Designer)
BE: Developer mode toggle + ViewModel bindings (this file)
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QScrollArea, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QCheckBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'config_tab.ui')


class ConfigTab(QWidget):
    def __init__(self, parent=None, is_developer=False):
        super().__init__(parent)
        self._is_developer = is_developer

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_dev_widgets()
        self._setup_anchor_table()
        self._setup_comboboxes()

        # Apply initial mode
        self.set_developer_mode(self._is_developer)

    def _setup_dev_widgets(self):
        """Collect developer-only widgets for visibility toggling."""
        self._dev_widgets = [
            self.adv_group,
            self.fusion_group,
        ]

    def _setup_anchor_table(self):
        """Configure anchor table with default data."""
        self.anchor_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.btn_add_anchor.clicked.connect(self._add_anchor)
        self.btn_remove_anchor.clicked.connect(self._remove_anchor)

    def _add_anchor(self):
        row = self.anchor_table.rowCount()
        self.anchor_table.insertRow(row)
        self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{row}"))
        self.anchor_table.setItem(row, 1, QTableWidgetItem("0.00"))
        self.anchor_table.setItem(row, 2, QTableWidgetItem("0.00"))
        self.anchor_table.setItem(row, 3, QTableWidgetItem("0.00"))

    def _remove_anchor(self):
        row = self.anchor_table.rowCount()
        if row > 0:
            self.anchor_table.removeRow(row - 1)

    def _setup_comboboxes(self):
        """Configure QComboBoxes with default items."""
        self.val_channel.addItems(["1", "2", "3", "4", "5", "7"])
        self.val_channel.setCurrentText("5")

        self.val_role.addItems(["Tag", "Anchor", "Gateway"])
        self.val_role.setCurrentText("Tag")

        self.val_datarate.addItems(["110 kbps", "850 kbps", "6.8 Mbps"])
        self.val_datarate.setCurrentText("6.8 Mbps")

        self.val_prf.addItems(["16 MHz", "64 MHz"])
        self.val_prf.setCurrentText("64 MHz")

        # Allow user to type custom device id or select predefined
        self.val_deviceid.addItems(["0x0001", "0x0002", "0x0003", "0x0004"])
        self.val_deviceid.setEditable(True)
        self.val_deviceid.setCurrentText("0x0001")

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled
        for w in self._dev_widgets:
            w.setVisible(enabled)
