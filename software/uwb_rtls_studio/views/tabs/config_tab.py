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
from utils.constants import DEVICE_TYPE_LABELS_SHORT
from views.tabs.anchor_visual_widget import AnchorVisualWidget





class ConfigTab(QWidget):
    def __init__(self, parent=None, is_developer=False):
        super().__init__(parent)
        self._is_developer = is_developer
        self._vm = None

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_dev_widgets()
        self._setup_anchor_table()
        self._setup_view_toggle()
        self._setup_target_selector()
        self._merge_ranging_into_uwb_config()

        
        # Apply initial mode
        self.set_developer_mode(self._is_developer)
        # Align bottom-row widgets to the bottom of their cells so they sit close to the status bar
        self.main_layout.addWidget(self.device_operations_group, 2, 0, 1, 1, Qt.AlignmentFlag.AlignBottom)

        # Align config groupboxes to the top of their cells to prevent empty space inside them
        self.main_layout.addWidget(self.uwb_config_group, 0, 1, 2, 1, Qt.AlignmentFlag.AlignTop)

        # Create a vertical layout for Column 2 to stack fusion_group and pos_calib_group closely
        self.col2_layout = QVBoxLayout()
        self.col2_layout.setContentsMargins(0, 0, 0, 0)
        self.col2_layout.setSpacing(10)
        self.col2_layout.addWidget(self.fusion_group, 1)
        self.col2_layout.addWidget(self.pos_calib_group, 1)

        # Add this vertical layout to the main grid layout in column 2, spanning all 3 rows
        self.main_layout.addLayout(self.col2_layout, 0, 2, 3, 1)

        # Track the active device identity to refine layout read/write behavior
        self._current_role = 1  # Default: Tag
        self._current_device_id = 0
        self._last_anchor_layout = []
        self._scan_devices = []

    def _setup_target_selector(self):
        """Add a compact target picker fed by BLE scan results."""
        self.lbl_target_device = QLabel("Target:")
        self.lbl_target_device.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.target_device_combo = QComboBox()
        self.target_device_combo.setMinimumWidth(190)
        self.target_device_combo.currentIndexChanged.connect(self._on_target_device_changed)
        self.device_ops_layout.insertWidget(0, self.lbl_target_device)
        self.device_ops_layout.insertWidget(1, self.target_device_combo)
        self._refresh_target_devices([])

    def _merge_ranging_into_uwb_config(self):
        """Move ranging controls under the shared UWB Configuration group."""
        self.uwb_config_group.setTitle("UWB Configuration")
        if not self._has_widget("ranging_group"):
            return
        self.uwb_config_form.insertRow(5, self.lbl_rng_period, self.rng_period_spin)
        self.uwb_config_form.insertRow(6, self.lbl_rx_timeout, self.rx_timeout_spin)
        self.ranging_group.setVisible(False)

    def _setup_dev_widgets(self):
        """Collect developer-only widgets for visibility toggling."""
        self._dev_widgets = [
            widget for widget in (
                getattr(self, "lbl_tx_delay", None),
                getattr(self, "tx_delay_spin", None),
                getattr(self, "lbl_rx_delay", None),
                getattr(self, "rx_delay_spin", None),
                getattr(self, "lbl_tx_power", None),
                getattr(self, "tx_power_spin", None),
                getattr(self, "lbl_preamble", None),
                getattr(self, "preamble_spin", None),
                getattr(self, "lbl_preamble_len", None),
                getattr(self, "val_preamble_len", None),
                getattr(self, "lbl_rx_pac", None),
                getattr(self, "val_rx_pac", None),
                getattr(self, "lbl_ns_sfd", None),
                getattr(self, "val_ns_sfd", None),
                getattr(self, "lbl_phr_mode", None),
                getattr(self, "val_phr_mode", None),
                getattr(self, "lbl_smart_tx_power", None),
                getattr(self, "chk_smart_tx_power", None),
                getattr(self, "lbl_pg_delay", None),
                getattr(self, "val_pg_delay", None),
                getattr(self, "uwb_adv_separator", None),
                getattr(self, "fusion_group", None),
                getattr(self, "pos_calib_group", None),
            )
            if widget is not None
        ]

    def _has_widget(self, name: str) -> bool:
        return getattr(self, name, None) is not None

    def _spin_value(self, name: str, default=0):
        widget = getattr(self, name, None)
        if widget is None or not hasattr(widget, "value"):
            return default
        return widget.value()

    def _set_value_if_present(self, name: str, value) -> None:
        widget = getattr(self, name, None)
        if widget is not None and hasattr(widget, "setValue"):
            widget.setValue(value)

    def _set_checked_if_present(self, name: str, checked: bool) -> None:
        widget = getattr(self, name, None)
        if widget is not None and hasattr(widget, "setChecked"):
            widget.setChecked(checked)

    def _setup_anchor_table(self):
        """Configure anchor table with default data."""
        header = self.anchor_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        for i in range(4):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)

        self.btn_add_anchor.clicked.connect(self._add_anchor)
        self.btn_remove_anchor.clicked.connect(self._remove_anchor)
        self.anchor_table.itemChanged.connect(self._on_table_item_changed)
        self._set_anchor_placeholders()

    def _set_anchor_placeholders(self, count: int = 4):
        self.anchor_table.blockSignals(True)
        try:
            self.anchor_table.setRowCount(0)
            for idx in range(1, count + 1):
                row = self.anchor_table.rowCount()
                self.anchor_table.insertRow(row)
                self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{idx}"))
                self.anchor_table.setItem(row, 1, QTableWidgetItem("--"))
                self.anchor_table.setItem(row, 2, QTableWidgetItem("--"))
                self.anchor_table.setItem(row, 3, QTableWidgetItem("--"))
        finally:
            self.anchor_table.blockSignals(False)

    @staticmethod
    def _coord_text(value):
        if value is None:
            return "--"
        try:
            return format_coord(float(value))
        except (TypeError, ValueError):
            return "--"

    def _add_anchor(self):
        self.anchor_table.blockSignals(True)
        try:
            row = self.anchor_table.rowCount()
            self.anchor_table.insertRow(row)
            
            # Find the maximum existing anchor ID to calculate the next ID and prevent duplication
            max_id = -1
            for r in range(row):
                item = self.anchor_table.item(r, 0)
                if item:
                    try:
                        val_str = item.text().strip()
                        if val_str.startswith('A') or val_str.startswith('a'):
                            val = int(val_str[1:])
                        else:
                            val = int(val_str)
                        if val > max_id:
                            max_id = val
                    except ValueError:
                        pass
            next_id = max_id + 1 if max_id >= 0 else row
            
            self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{next_id}"))
            self.anchor_table.setItem(row, 1, QTableWidgetItem("0"))
            self.anchor_table.setItem(row, 2, QTableWidgetItem("0"))
            self.anchor_table.setItem(row, 3, QTableWidgetItem("0"))
        finally:
            self.anchor_table.blockSignals(False)
        self._sync_table_to_shared_state()

    def _remove_anchor(self):
        row = self.anchor_table.rowCount()
        if row > 0:
            self.anchor_table.removeRow(row - 1)
            self._sync_table_to_shared_state()

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled
        for w in self._dev_widgets:
            w.setVisible(enabled)

        # Dynamically adjust sys_group column span and alignment
        if not self._has_widget("sys_group"):
            return
        if enabled:
            # Developer mode: sys_group in row 2, col 1, spanning 1 column
            self.main_layout.addWidget(self.sys_group, 2, 1, 1, 1, Qt.AlignmentFlag.AlignBottom)
        else:
            # User mode: sys_group in row 2, col 1, spanning 2 columns
            self.main_layout.addWidget(self.sys_group, 2, 1, 1, 2, Qt.AlignmentFlag.AlignBottom)

    def set_viewmodel(self, vm):
        self._vm = vm
        
        # Connect signals from viewmodel to UI update slots
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_loaded)
        self._vm.sys_config_updated.connect(self._on_sys_config_loaded)
        self._vm.sys_ranging_cfg_updated.connect(self._on_sys_ranging_cfg_loaded)
        self._vm.sensor_fusion_cfg_updated.connect(self._on_sensor_fusion_cfg_loaded)
        self._vm.pos_calib_cfg_updated.connect(self._on_pos_calib_cfg_loaded)
        if hasattr(self._vm, "scan_devices_updated"):
            self._vm.scan_devices_updated.connect(self._refresh_target_devices)
        
        # Connect UI buttons to viewmodel actions
        self.btn_read_device.clicked.connect(self._read_device_config)
        self.btn_write_device.clicked.connect(self._write_device_config)
        self.btn_write_all.clicked.connect(self._write_all_devices)
        self.btn_device_reset.clicked.connect(self._vm.device_reset)
        self.btn_bootloader.clicked.connect(self._vm.enter_bootloader)

        self._vm.emit_current_state()

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
        self._sync_table_to_shared_state()
        if self.anchor_stack.currentIndex() == 1:
            anchors = self._get_anchors_from_table()
            self.visual_widget.set_anchors(anchors)

    def _sync_table_to_shared_state(self):
        if self._vm:
            anchors = self._get_anchors_from_table()
            self._vm.update_shared_anchor_layout(anchors)

    def _refresh_target_devices(self, devices: list):
        self._scan_devices = [dict(d) for d in devices]
        selected_key = None
        current_data = self.target_device_combo.currentData()
        if isinstance(current_data, dict):
            selected_key = current_data.get("key")

        self.target_device_combo.blockSignals(True)
        try:
            self.target_device_combo.clear()
            if not self._scan_devices:
                fallback = {
                    "key": "manual",
                    "label": "Manual target",
                    "device_id": self._parse_device_id_from_ui(default=1),
                    "role": self._role_from_ui(),
                    "device_type": self._role_from_ui(),
                }
                self.target_device_combo.addItem(fallback["label"], fallback)
            else:
                for idx, dev in enumerate(self._scan_devices):
                    target = self._target_from_scan_device(dev, idx)
                    self.target_device_combo.addItem(target["label"], target)
                    if selected_key and selected_key == target["key"]:
                        self.target_device_combo.setCurrentIndex(self.target_device_combo.count() - 1)
        finally:
            self.target_device_combo.blockSignals(False)
        self._on_target_device_changed(self.target_device_combo.currentIndex())

    def _target_from_scan_device(self, dev: dict, idx: int) -> dict:
        device_type = int(dev.get("device_type") or 0)
        role = device_type if device_type in (1, 2, 3) else 1
        device_id = int(dev.get("device_id") or dev.get("serial_number") or idx + 1)
        type_label = DEVICE_TYPE_LABELS_SHORT.get(device_type, str(device_type))
        if type_label == "-":
            type_label = "DEVICE"
        label = f"{type_label} 0x{device_id:08X}"
        mac = dev.get("mac", "")
        if mac:
            label = f"{label} - {mac}"
        return {
            "key": f"{device_type}:{device_id}:{mac}",
            "label": label,
            "role": role,
            "device_type": device_type,
            "device_id": device_id,
            "mac": mac,
        }

    def _selected_target(self) -> dict:
        data = self.target_device_combo.currentData()
        if isinstance(data, dict):
            return data
        return {
            "role": self._role_from_ui(),
            "device_type": self._role_from_ui(),
            "device_id": self._parse_device_id_from_ui(default=1),
        }

    def _on_target_device_changed(self, index: int):
        target = self._selected_target()
        self._apply_target_to_ui(target)

    def _apply_target_to_ui(self, target: dict):
        role = int(target.get("role") or 1)
        device_id = int(target.get("device_id") or 1)
        role_map = {1: "Tag", 2: "Anchor", 3: "Gateway"}
        self._current_role = role
        self._current_device_id = device_id
        self.val_role.setCurrentText(role_map.get(role, "Tag"))
        self.val_deviceid.setCurrentText(f"0x{device_id:04X}")
        if self._last_anchor_layout:
            self._apply_anchor_layout_to_table()

    def _role_from_ui(self) -> int:
        role_map = {"Tag": 1, "Anchor": 2, "Gateway": 3}
        return role_map.get(self.val_role.currentText(), 1)

    def _parse_device_id_from_ui(self, default=1) -> int:
        dev_id_str = self.val_deviceid.currentText().strip()
        try:
            if dev_id_str.lower().startswith("0x"):
                return int(dev_id_str, 16)
            return int(dev_id_str)
        except ValueError:
            return default

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
            self._apply_target_to_ui(self._selected_target())
            self._vm.read_anchor_layout()
            self._vm.read_ranging_config()
            self._vm.read_sys_config()
            self._vm.read_sensor_fusion_config()
            self._vm.read_pos_calib_config()

    def _write_device_config(self):
        if not self._vm:
            return
        self._apply_target_to_ui(self._selected_target())
        
        # 1. Write Layout
        anchors = self._get_anchors_from_table()
        self._vm.write_anchor_layout(anchors)
        
        # 2. Write Ranging Config
        period = self.rng_period_spin.value()
        timeout = self.rx_timeout_spin.value()
        self._vm.write_ranging_config(period, timeout)

        # 3. Write UWB Config (Sys Config)
        role = self._role_from_ui()
        device_id = self._parse_device_id_from_ui(default=1)

        try:
            uwb_channel = int(self.val_channel.currentText())
        except ValueError:
            uwb_channel = 5

        rate_str = self.val_datarate.currentText()
        rate_map = {"110 kbps": 110, "850 kbps": 850, "6.8 Mbps": 6800}
        uwb_data_rate = rate_map.get(rate_str, 6800)

        prf_str = self.val_prf.currentText()
        prf_map = {"16 MHz": 16, "64 MHz": 64}
        uwb_prf = prf_map.get(prf_str, 64)

        # 6 developer-mode UWB fields
        preamble_len_map = {
            "64 symbols": 0x04,
            "128 symbols": 0x08,
            "256 symbols": 0x18,
            "512 symbols": 0x28,
            "1024 symbols": 0x14,
            "1536 symbols": 0x0C,
            "2048 symbols": 0x24,
            "4096 symbols": 0x34
        }
        preamble_len_text = self.val_preamble_len.currentText() if self._has_widget("val_preamble_len") else "4096 symbols"
        uwb_preamble_len = preamble_len_map.get(preamble_len_text, 0x34)

        pac_map = {"8": 0, "16": 1, "32": 2, "64": 3}
        rx_pac_text = self.val_rx_pac.currentText() if self._has_widget("val_rx_pac") else "8"
        uwb_rx_pac = pac_map.get(rx_pac_text, 0)

        sfd_map = {"Standard": 0, "Non-standard": 1}
        ns_sfd_text = self.val_ns_sfd.currentText() if self._has_widget("val_ns_sfd") else "Standard"
        uwb_ns_sfd = sfd_map.get(ns_sfd_text, 0)

        phr_map = {"Standard": 0, "Extended": 1}
        phr_mode_text = self.val_phr_mode.currentText() if self._has_widget("val_phr_mode") else "Standard"
        uwb_phr_mode = phr_map.get(phr_mode_text, 0)

        smart_tx_power = self.chk_smart_tx_power.isChecked() if self._has_widget("chk_smart_tx_power") else False
        pg_delay = self._spin_value("val_pg_delay", 193)

        self._vm.write_sys_config(
            role=role,
            device_id=device_id,
            uwb_channel=uwb_channel,
            uwb_data_rate=uwb_data_rate,
            uwb_prf=uwb_prf,
            tx_antenna_delay=self.tx_delay_spin.value(),
            rx_antenna_delay=self.rx_delay_spin.value(),
            tx_power=int(self.tx_power_spin.value()),
            uwb_preamble_code=self.preamble_spin.value(),
            ranging_period_ms=self.rng_period_spin.value(),
            rx_timeout_ms=self.rx_timeout_spin.value(),
            uwb_preamble_len=uwb_preamble_len,
            uwb_rx_pac=uwb_rx_pac,
            uwb_ns_sfd=uwb_ns_sfd,
            uwb_phr_mode=uwb_phr_mode,
            smart_tx_power=smart_tx_power,
            pg_delay=pg_delay
        )

        # 4. Write Sensor Fusion Config
        self._vm.write_sensor_fusion_config(
            alpha=self.alpha_spin.value(),
            beta=self.beta_spin.value(),
            kappa=self.kappa_spin.value(),
            q_a=self.q_accel_spin.value(),
            q_g=self.q_gyro_spin.value(),
            r_uwb=self.r_uwb_spin.value(),
            init_p_px=self._spin_value("init_p_px_spin", 1.0),
            init_p_py=self._spin_value("init_p_py_spin", 1.0),
            init_p_vx=self._spin_value("init_p_vx_spin", 0.1),
            init_p_vy=self._spin_value("init_p_vy_spin", 0.1),
            init_p_theta=self._spin_value("init_p_theta_spin", 0.1),
            init_p_bias_ax=self._spin_value("init_p_bias_ax_spin", 0.01),
            init_p_bias_ay=self._spin_value("init_p_bias_ay_spin", 0.01),
            init_p_bias_gz=self._spin_value("init_p_bias_gz_spin", 0.01)
        )

        # 5. Write Position Calibration Config
        if self._has_widget("chk_enable_anchor_calib"):
            self._vm.write_pos_calib_config(
                enable_anchor_auto_calib=self.chk_enable_anchor_calib.isChecked(),
                enable_tag_auto_calib=getattr(self, "chk_enable_tag_calib", self.chk_enable_anchor_calib).isChecked(),
                ref_distance_xy_m=self._spin_value("pos_ref_dist_spin", 2.0),
                tag_height_m=self._spin_value("pos_tag_height_spin", 1.0),
                anchor_height_m=self._spin_value("pos_anchor_height_spin", 2.5),
                calib_anchor_id=self._spin_value("pos_calib_anchor_spin", 1),
                samples=self._spin_value("pos_samples_spin", 10),
                error_threshold_m=self._spin_value("pos_err_thresh_spin", 0.3),
                min_delta_step=self._spin_value("pos_min_delta_spin", 1),
                max_rounds=self._spin_value("pos_max_rounds_spin", 10),
                max_std_m=self._spin_value("pos_max_std_spin", 0.2),
                damping=self._spin_value("pos_damping_spin", 0.1),
                iterations=self._spin_value("pos_iterations_spin", 100)
            )

    def _write_all_devices(self):
        # UI only (Backend defined later)
        import logging
        logging.getLogger(__name__).info("Write All Devices clicked (UI Only - Backend not implemented)")

    def _on_anchor_layout_loaded(self, anchors):
        self._last_anchor_layout = [dict(a) for a in anchors]
        self._apply_anchor_layout_to_table()

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
            
        self.anchor_table.setItem(target_row, 1, QTableWidgetItem(self._coord_text(x_m)))
        self.anchor_table.setItem(target_row, 2, QTableWidgetItem(self._coord_text(y_m)))
        self.anchor_table.setItem(target_row, 3, QTableWidgetItem(self._coord_text(z_m)))
    def _apply_anchor_layout_to_table(self):
        anchors = [dict(a) for a in self._last_anchor_layout]
        self.anchor_table.blockSignals(True)
        try:
            if not anchors:
                self.anchor_table.blockSignals(False)
                self._set_anchor_placeholders()
                self.visual_widget.set_anchors([])
                return
            if self._current_role == 2:  # Anchor device: only expose its own layout item.
                target_anchor = None
                for anchor in anchors:
                    if anchor.get("anchor_id") == self._current_device_id:
                        target_anchor = anchor
                        break
                if not target_anchor and anchors:
                    target_anchor = anchors[0]
                self.anchor_table.setRowCount(0)
                if target_anchor:
                    self._update_single_anchor_in_table(
                        target_anchor.get("anchor_id", self._current_device_id),
                        target_anchor.get("x_m"),
                        target_anchor.get("y_m"),
                        target_anchor.get("z_m"),
                    )
            else:  # Tag/Gateway: render the full anchor layout list.
                self.anchor_table.setRowCount(0)
                for anchor in anchors:
                    row = self.anchor_table.rowCount()
                    self.anchor_table.insertRow(row)
                    self.anchor_table.setItem(row, 0, QTableWidgetItem(f"A{anchor['anchor_id']}"))
                    self.anchor_table.setItem(row, 1, QTableWidgetItem(self._coord_text(anchor.get("x_m"))))
                    self.anchor_table.setItem(row, 2, QTableWidgetItem(self._coord_text(anchor.get("y_m"))))
                    self.anchor_table.setItem(row, 3, QTableWidgetItem(self._coord_text(anchor.get("z_m"))))
        finally:
            self.anchor_table.blockSignals(False)
        current_anchors = self._get_anchors_from_table()
        self.visual_widget.set_anchors(current_anchors)

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

        # Map the 6 developer-mode UWB fields
        preamble_len_rev = {
            0x04: "64 symbols",
            0x08: "128 symbols",
            0x18: "256 symbols",
            0x28: "512 symbols",
            0x14: "1024 symbols",
            0x0C: "1536 symbols",
            0x24: "2048 symbols",
            0x34: "4096 symbols"
        }
        preamble_len_val = cfg.get("uwb_preamble_len", 0x34)
        if self._has_widget("val_preamble_len"):
            self.val_preamble_len.setCurrentText(preamble_len_rev.get(preamble_len_val, "4096 symbols"))

        pac_rev = {0: "8", 1: "16", 2: "32", 3: "64"}
        pac_val = cfg.get("uwb_rx_pac", 0)
        if self._has_widget("val_rx_pac"):
            self.val_rx_pac.setCurrentText(pac_rev.get(pac_val, "8"))

        sfd_rev = {0: "Standard", 1: "Non-standard"}
        sfd_val = cfg.get("uwb_ns_sfd", 0)
        if self._has_widget("val_ns_sfd"):
            self.val_ns_sfd.setCurrentText(sfd_rev.get(sfd_val, "Standard"))

        phr_rev = {0: "Standard", 1: "Extended"}
        phr_val = cfg.get("uwb_phr_mode", 0)
        if self._has_widget("val_phr_mode"):
            self.val_phr_mode.setCurrentText(phr_rev.get(phr_val, "Standard"))
        self._set_checked_if_present("chk_smart_tx_power", cfg.get("smart_tx_power", False))
        self._set_value_if_present("val_pg_delay", cfg.get("pg_delay", 193))
        if self._last_anchor_layout:
            self._apply_anchor_layout_to_table()

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
        self._set_value_if_present("init_p_px_spin", cfg.get("init_p_px", 1.0))
        self._set_value_if_present("init_p_py_spin", cfg.get("init_p_py", 1.0))
        self._set_value_if_present("init_p_vx_spin", cfg.get("init_p_vx", 0.1))
        self._set_value_if_present("init_p_vy_spin", cfg.get("init_p_vy", 0.1))
        self._set_value_if_present("init_p_theta_spin", cfg.get("init_p_theta", 0.1))
        self._set_value_if_present("init_p_bias_ax_spin", cfg.get("init_p_bias_ax", 0.01))
        self._set_value_if_present("init_p_bias_ay_spin", cfg.get("init_p_bias_ay", 0.01))
        self._set_value_if_present("init_p_bias_gz_spin", cfg.get("init_p_bias_gz", 0.01))

    def _on_pos_calib_cfg_loaded(self, cfg):
        self._set_checked_if_present("chk_enable_anchor_calib", cfg.get("enable_anchor_auto_calib", True))
        self._set_checked_if_present("chk_enable_tag_calib", cfg.get("enable_tag_auto_calib", True))
        self._set_value_if_present("pos_ref_dist_spin", cfg.get("ref_distance_xy_m", 2.0))
        self._set_value_if_present("pos_tag_height_spin", cfg.get("tag_height_m", 1.0))
        self._set_value_if_present("pos_anchor_height_spin", cfg.get("anchor_height_m", 2.5))
        self._set_value_if_present("pos_calib_anchor_spin", cfg.get("calib_anchor_id", 1))
        self._set_value_if_present("pos_samples_spin", cfg.get("samples", 10))
        self._set_value_if_present("pos_err_thresh_spin", cfg.get("error_threshold_m", 0.3))
        self._set_value_if_present("pos_min_delta_spin", cfg.get("min_delta_step", 1))
        self._set_value_if_present("pos_max_rounds_spin", cfg.get("max_rounds", 10))
        self._set_value_if_present("pos_max_std_spin", cfg.get("max_std_m", 0.2))
        self._set_value_if_present("pos_damping_spin", cfg.get("damping", 0.1))
        self._set_value_if_present("pos_iterations_spin", cfg.get("iterations", 100))
