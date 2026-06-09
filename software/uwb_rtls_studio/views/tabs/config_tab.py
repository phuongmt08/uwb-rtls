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
import math
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QScrollArea, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QCheckBox,
    QStackedWidget
)
from PyQt6.QtCore import Qt
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'config_tab.ui')

from utils.helpers import format_coord
from views.tabs.anchor_visual_widget import AnchorVisualWidget





class ConfigTab(QWidget):
    def __init__(self, parent=None, is_developer=False):
        super().__init__(parent)
        self._is_developer = is_developer

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_dev_widgets()
        self._setup_anchor_table()
        self._setup_view_toggle()

        # Apply initial mode
        self.set_developer_mode(self._is_developer)

        # Track the active device identity to refine layout read/write behavior
        self._current_role = 1  # Default: Tag
        self._current_device_id = 0

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
        self.anchor_table.itemChanged.connect(self._on_table_item_changed)

    def _add_anchor(self):
        row = self.anchor_table.rowCount()
        self.anchor_table.insertRow(row)
        self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{row}"))
        self.anchor_table.setItem(row, 1, QTableWidgetItem("0"))
        self.anchor_table.setItem(row, 2, QTableWidgetItem("0"))
        self.anchor_table.setItem(row, 3, QTableWidgetItem("0"))

    def _remove_anchor(self):
        row = self.anchor_table.rowCount()
        if row > 0:
            self.anchor_table.removeRow(row - 1)

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
        self.btn_read_device.clicked.connect(self._read_device_config)
        self.btn_write_device.clicked.connect(self._write_device_config)
        self.btn_write_all.clicked.connect(self._write_all_devices)
        self.btn_device_reset.clicked.connect(self._vm.device_reset)
        self.btn_bootloader.clicked.connect(self._vm.enter_bootloader)

    def _setup_view_toggle(self):
        self.btn_view_table.clicked.connect(self._show_table_view)
        self.btn_view_visual.clicked.connect(self._show_visual_view)
        self.anchor_stack.setCurrentIndex(0)
        self._update_segmented_style()

    def _show_table_view(self):
        self.anchor_stack.setCurrentIndex(0)
        self._update_segmented_style()

    def _show_visual_view(self):
        anchors = self._get_anchors_from_table()
        self.visual_widget.set_anchors(anchors)
        self.anchor_stack.setCurrentIndex(1)
        self._update_segmented_style()

    def _update_segmented_style(self):
        idx = self.anchor_stack.currentIndex()
        self.btn_view_table.setProperty("active", idx == 0)
        self.btn_view_visual.setProperty("active", idx == 1)
        self.btn_view_table.style().unpolish(self.btn_view_table)
        self.btn_view_table.style().polish(self.btn_view_table)
        self.btn_view_visual.style().unpolish(self.btn_view_visual)
        self.btn_view_visual.style().polish(self.btn_view_visual)

    def _on_table_item_changed(self, item):
        if self.anchor_stack.currentIndex() == 1:
            anchors = self._get_anchors_from_table()
            self.visual_widget.set_anchors(anchors)

    def _get_anchors_from_table(self):
        anchors = []
        for row in range(self.anchor_table.rowCount()):
            id_item = self.anchor_table.item(row, 0)
            x_item = self.anchor_table.item(row, 1)
            y_item = self.anchor_table.item(row, 2)
            z_item = self.anchor_table.item(row, 3)
            
            if not id_item or not x_item or not y_item or not z_item:
                continue
                
            try:
                anchor_id_str = id_item.text().strip()
                if anchor_id_str.startswith('A') or anchor_id_str.startswith('a'):
                    anchor_id = int(anchor_id_str[1:])
                else:
                    anchor_id = int(anchor_id_str)
                    
                x_m = float(x_item.text().strip())
                y_m = float(y_item.text().strip())
                z_m = float(z_item.text().strip())
                
                anchors.append({
                    "anchor_id": anchor_id,
                    "x_m": x_m,
                    "y_m": y_m,
                    "z_m": z_m
                })
            except ValueError:
                pass
        return anchors

    def _read_device_config(self):
        if self._vm:
            self._vm.read_anchor_layout()
            self._vm.read_ranging_config()

    def _write_device_config(self):
        if not self._vm:
            return
        anchors = self._get_anchors_from_table()
        self._vm.write_anchor_layout(anchors)
        period = self.rng_period_spin.value()
        timeout = self.rx_timeout_spin.value()
        self._vm.write_ranging_config(period, timeout)

    def _write_all_devices(self):
        # UI only (Backend defined later)
        import logging
        logging.getLogger(__name__).info("Write All Devices clicked (UI Only - Backend not implemented)")

    def _on_anchor_layout_loaded(self, anchors):
        # Temporarily block itemChanged signal to prevent layout refresh loops during load
        self.anchor_table.blockSignals(True)
        if self._current_role == 2:  # Anchor
            # Only update the row matching our current connected Anchor device ID
            target_anchor = None
            for a in anchors:
                if a.get("anchor_id") == self._current_device_id:
                    target_anchor = a
                    break
            # Fallback to first anchor if none matches but list is non-empty
            if not target_anchor and anchors:
                target_anchor = anchors[0]
            
            if target_anchor:
                self._update_single_anchor_in_table(
                    target_anchor.get("anchor_id", self._current_device_id),
                    target_anchor.get("x_m", 0.0),
                    target_anchor.get("y_m", 0.0),
                    target_anchor.get("z_m", 0.0)
                )
        else:  # Tag or Gateway (role 1 or 3): has full room layout, update all rows
            self.anchor_table.setRowCount(0)
            for a in anchors:
                row = self.anchor_table.rowCount()
                self.anchor_table.insertRow(row)
                self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{a['anchor_id']}"))
                self.anchor_table.setItem(row, 1, QTableWidgetItem(format_coord(a['x_m'])))
                self.anchor_table.setItem(row, 2, QTableWidgetItem(format_coord(a['y_m'])))
                self.anchor_table.setItem(row, 3, QTableWidgetItem(format_coord(a['z_m'])))
        self.anchor_table.blockSignals(False)
        
        # Update the visual widget representation with parsed coordinates
        current_anchors = self._get_anchors_from_table()
        self.visual_widget.set_anchors(current_anchors)

    def _update_single_anchor_in_table(self, anchor_id, x_m, y_m, z_m):
        target_row = -1
        for row in range(self.anchor_table.rowCount()):
            item = self.anchor_table.item(row, 0)
            if item:
                text = item.text().strip()
                if text == f"A{anchor_id}" or text == f"a{anchor_id}" or text == str(anchor_id):
                    target_row = row
                    break
        
        if target_row == -1:
            target_row = self.anchor_table.rowCount()
            self.anchor_table.insertRow(target_row)
            self.anchor_table.setItem(target_row, 0, QTableWidgetItem(f"A{anchor_id}"))
            
        self.anchor_table.setItem(target_row, 1, QTableWidgetItem(format_coord(x_m)))
        self.anchor_table.setItem(target_row, 2, QTableWidgetItem(format_coord(y_m)))
        self.anchor_table.setItem(target_row, 3, QTableWidgetItem(format_coord(z_m)))

    def _on_sys_config_loaded(self, cfg):
        # Save active device role and ID
        self._current_role = cfg.get("role", 1)
        self._current_device_id = cfg.get("device_id", 0)

        # Map channel
        chan = str(cfg.get("uwb_channel", 5))
        self.val_channel.setCurrentText(chan)
        
        # Map role (1 = Tag, 2 = Anchor, 3 = Gateway)
        role_map = {1: "Tag", 2: "Anchor", 3: "Gateway"}
        role = role_map.get(self._current_role, "Tag")
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
