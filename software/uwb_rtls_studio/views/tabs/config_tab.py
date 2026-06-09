"""
==============================================================================
  UWB RTLS Studio — Config Tab View
==============================================================================
  File        : config_tab.py
  Description : View for config configurations (Tab 3), loaded from config_tab.ui.
                Handles form submissions and binds input fields to ConfigViewModel.

  MVVM Role   : VIEW — Pure presentation.

  Thread Model:
    - Main GUI Thread: Renders widgets and listens to user input events strictly
      on this thread.
==============================================================================
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
        self.val_channel.setCurrentText("---")

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

    def set_viewmodel(self, vm):
        self._vm = vm
        
        # Connect signals from viewmodel to UI update slots
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_loaded)
        self._vm.sys_config_updated.connect(self._on_sys_config_loaded)
        self._vm.sys_ranging_cfg_updated.connect(self._on_sys_ranging_cfg_loaded)
        self._vm.sensor_fusion_cfg_updated.connect(self._on_sensor_fusion_cfg_loaded)
        self._vm.pos_calib_cfg_updated.connect(self._on_pos_calib_cfg_loaded)
        
        # Connect UI buttons to viewmodel actions
        self.btn_read_anchor.clicked.connect(self._vm.read_anchor_layout)
        self.btn_rng_read.clicked.connect(self._vm.read_ranging_config)
        self.btn_device_reset.clicked.connect(self._vm.device_reset)
        self.btn_uwb_reset.clicked.connect(self._vm.uwb_reset)
        self.btn_factory.clicked.connect(self._vm.factory_reset)

    def _on_anchor_layout_loaded(self, anchors):
        self.anchor_table.setRowCount(0)
        for a in anchors:
            row = self.anchor_table.rowCount()
            self.anchor_table.insertRow(row)
            self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{a['anchor_id']}"))
            self.anchor_table.setItem(row, 1, QTableWidgetItem(f"{a['x_m']:.2f}"))
            self.anchor_table.setItem(row, 2, QTableWidgetItem(f"{a['y_m']:.2f}"))
            self.anchor_table.setItem(row, 3, QTableWidgetItem(f"{a['z_m']:.2f}"))

    def _on_sys_config_loaded(self, cfg):
        # Map channel
        chan = str(cfg.get("uwb_channel", 5))
        self.val_channel.setCurrentText(chan)
        
        # Map role (1 = Tag, 2 = Anchor, 3 = Gateway)
        role_map = {1: "Tag", 2: "Anchor", 3: "Gateway"}
        role = role_map.get(cfg.get("role", 1), "Tag")
        self.val_role.setCurrentText(role)
        
        # Map data rate (1 = 110kbps, 2 = 850kbps, 3 = 6.8Mbps)
        # Wait, the enum values in protobuf might be different, let's map integers or strings
        rate_val = cfg.get("uwb_data_rate", 3)
        rate_map = {1: "110 kbps", 2: "850 kbps", 3: "6.8 Mbps", 110: "110 kbps", 850: "850 kbps", 6800: "6.8 Mbps"}
        rate = rate_map.get(rate_val, "6.8 Mbps")
        self.val_datarate.setCurrentText(rate)
        
        # Map PRF (1 = 16MHz, 2 = 64MHz)
        prf_val = cfg.get("uwb_prf", 2)
        prf_map = {1: "16 MHz", 2: "64 MHz", 16: "16 MHz", 64: "64 MHz"}
        prf = prf_map.get(prf_val, "64 MHz")
        self.val_prf.setCurrentText(prf)
        
        # Map Device ID
        dev_id = f"0x{cfg.get('device_id', 1):04X}"
        self.val_deviceid.setCurrentText(dev_id)
        
        # Map Advanced delay & power
        self.tx_delay_spin.setValue(cfg.get("tx_antenna_delay", 16436))
        self.rx_delay_spin.setValue(cfg.get("rx_antenna_delay", 16436))
        self.tx_power_spin.setValue(cfg.get("tx_power", 0))
        self.preamble_spin.setValue(cfg.get("uwb_preamble_code", 10))

    def _on_sys_ranging_cfg_loaded(self, cfg):
        self.rng_period_spin.setValue(cfg.get("ranging_period_ms", 100))
        self.rx_timeout_spin.setValue(cfg.get("rx_timeout_ms", 70))

    def _on_sensor_fusion_cfg_loaded(self, cfg):
        self.alpha_spin.setValue(cfg.get("alpha", 0.001))
        self.beta_spin.setValue(cfg.get("beta", 2.0))
        self.kappa_spin.setValue(cfg.get("kappa", 0.0))
        self.q_accel_spin.setValue(cfg.get("q_a", 0.1))
        self.q_gyro_spin.setValue(cfg.get("q_g", 0.01))
        self.r_uwb_spin.setValue(cfg.get("r_uwb", 0.05))

    def _on_pos_calib_cfg_loaded(self, cfg):
        pass
