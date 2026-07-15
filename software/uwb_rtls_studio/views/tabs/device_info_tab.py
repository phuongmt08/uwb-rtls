"""
UWB RTLS Studio - Device Info Tab (UI loaded from .ui file)
Tab 1: Shows connected device information.

FE: Loaded from views/ui/device_info_tab.ui (editable in Qt Designer)
BE: ViewModel bindings + data updaters (this file)

Layout: Split-screen
  - LEFT column: Connected Device + BLE Connection + Battery + Temperature + Voltage
  - RIGHT column: Other Advertising Devices (with Connect buttons per row)

Background polling: ViewModel sends GET commands every 2s (no Refresh button needed).
"""
import os
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QProgressBar, QFrame, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'device_info_tab.ui')
MAX_ADV_VISIBLE_ROWS = 6


class DeviceInfoTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        self._is_developer_mode = False
        # Load UI from .ui file
        uic.loadUi(UI_FILE, self)
        # The page itself must stay fixed; only the advertising table may scroll.
        self._remove_page_scroll_area()
        # Post-load setup
        self._setup_mappings()
        self._setup_table()
        self._reset_display_fields()

    def _remove_page_scroll_area(self):
        content = self.scroll_area.takeWidget()
        if content is None:
            return
        self.base_layout.removeWidget(self.scroll_area)
        content.setParent(self)
        content.setMinimumSize(0, 0)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.left_primary.setContentsMargins(0, 0, 0, 0)
        self.base_layout.addWidget(content)
        self.scroll_area.hide()
        self.scroll_area.deleteLater()

    def _setup_mappings(self):
        """Map label keys to widget references for data updates."""
        # Device Info value labels — map from key to widget objectName
        self._dev_values = {
            "Device Name:": self.val_dev_name,
            "Type:": self.val_dev_type,
            "Role:": self.val_dev_role,
            "MAC Address:": self.val_dev_mac,
            "Serial Number:": self.val_dev_serial,
            "Firmware:": self.val_dev_firmware,
            "Hardware Rev:": self.val_dev_hwrev,
            "UID:": self.val_dev_uid,
        }

        # BLE info value labels
        self.lbl_ble_state = QLabel("Dongle BLE:")
        self.lbl_ble_state.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.val_ble_state = QLabel("-")
        self.val_ble_state.setStyleSheet("color: #F8FAFC;")
        self.ble_grid.addWidget(self.lbl_ble_state, 5, 0)
        self.ble_grid.addWidget(self.val_ble_state, 5, 1)

        self.lbl_device_link = QLabel("Device Link:")
        self.lbl_device_link.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.val_device_link = QLabel("-")
        self.val_device_link.setStyleSheet("color: #F8FAFC;")
        self.ble_grid.addWidget(self.lbl_device_link, 6, 0)
        self.ble_grid.addWidget(self.val_device_link, 6, 1)

        self.lbl_link_health = QLabel("Link Health:")
        self.lbl_link_health.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.val_link_health = QLabel("-")
        self.val_link_health.setStyleSheet("color: #F8FAFC;")
        self.ble_grid.addWidget(self.lbl_link_health, 7, 0)
        self.ble_grid.addWidget(self.val_link_health, 7, 1)

        self._ble_values = {
            "Dongle BLE:": self.val_ble_state,
            "Device Link:": self.val_device_link,
            "Link Health:": self.val_link_health,
            "RSSI:": self.val_ble_rssi,
            "Conn Interval:": self.val_ble_interval,
            "Slave Latency:": self.val_ble_latency,
            "Sup. Timeout:": self.val_ble_timeout,
            "PHY:": self.val_ble_phy,
        }

        # Battery info labels
        self._bat_info_labels = {
            "Voltage:": self.val_bat_voltage,
            "Remaining:": self.val_bat_remaining,
            "Charging:": self.val_bat_charging,
        }

        # Temperature labels
        self._temp_labels = {
            "MCU:": self.val_temp_mcu,
            "UWB Chip:": self.val_temp_uwb,
            "IMU:": self.val_temp_imu,
        }

        # Voltage labels
        self._volt_labels = {
            "VDDA:": self.val_volt_vdda,
            "UWB VBAT:": self.val_volt_uwb,
        }

        # System resource labels
        self._sys_labels = {
            "HEAP:": self.val_sys_heap,
            "STACK:": self.val_sys_stack,
            "CPU:": self.val_sys_cpu,
        }

        # Battery widgets
        self._bat_pct = self.bat_pct
        self._bat_bar = self.bat_bar

        # Time labels
        self._time_local = self.val_time_local
        self._time_status = self.val_time_sync_status

        # Scan status removed by user

        # Advertising table
        self._adv_table = self.adv_table

        # Add Refresh button
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addStretch()
        
        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.setObjectName("btn_refresh")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setStyleSheet(
            "QPushButton { background: rgba(34, 211, 238, 0.1); color: #22D3EE; border: 1px solid #334155; border-radius: 6px; font-weight: bold; padding: 6px 16px; font-size: 12px; }"
            "QPushButton:hover { border-color: #22D3EE; background: rgba(34, 211, 238, 0.2); }"
            "QPushButton:disabled { background: #1E293B; color: #475569; border-color: #334155; }"
        )
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        bottom_layout.addWidget(self.btn_refresh)
        
        self.adv_layout.addLayout(bottom_layout)

    def _reset_display_fields(self):
        """Show no device data until a parsed response reaches the View."""
        for group in (
            self._dev_values,
            self._ble_values,
            self._bat_info_labels,
            self._temp_labels,
            self._volt_labels,
            self._sys_labels,
        ):
            for label in group.values():
                label.setText("-")
                label.setToolTip("")
        self.val_ble_state.setStyleSheet("color: #F8FAFC;")
        self._bat_pct.setText("-")
        self._bat_bar.setValue(0)
        self._time_local.setText("-")
        self._time_status.setText("-")
        self._adv_table.setRowCount(0)

    def _setup_table(self):
        """Configure the advertising devices table header sizing."""
        self._adv_table.setColumnCount(9)
        self._adv_table.setHorizontalHeaderLabels([
            "Device", "MAC", "Bat %", "Time", "Serial", "Status", "Warn", "Error", "Action"
        ])
        self._adv_table.setColumnWidth(0, 120)  # Device
        self._adv_table.setColumnWidth(1, 140)  # MAC
        self._adv_table.setColumnWidth(2, 70)   # Bat %
        self._adv_table.setColumnWidth(3, 150)  # Time
        self._adv_table.setColumnWidth(4, 150)  # Serial
        self._adv_table.setColumnWidth(5, 90)   # Status
        self._adv_table.setColumnWidth(6, 90)   # Warn
        self._adv_table.setColumnWidth(7, 90)   # Error
        self._adv_table.setColumnWidth(8, 145)   # Action (Connect + Set Time buttons)
        self._adv_table.verticalHeader().setDefaultSectionSize(36)
        self._adv_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._adv_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._update_adv_table_height(0)

    def _update_adv_table_height(self, row_count: int):
        header_height = max(
            self._adv_table.horizontalHeader().height(),
            self._adv_table.horizontalHeader().sizeHint().height(),
            36,
        )
        if row_count <= 0:
            total_height = 60
        else:
            visible_rows = min(int(row_count), MAX_ADV_VISIBLE_ROWS)
            row_height = max(self._adv_table.verticalHeader().defaultSectionSize(), 36)
            total_height = header_height + (visible_rows * row_height) + (self._adv_table.frameWidth() * 2)
        self._adv_table.setFixedHeight(total_height)

    def set_viewmodel(self, vm):
        self._vm = vm
        self._vm.device_info_updated.connect(self._on_device_info)
        self._vm.ble_info_updated.connect(self._on_ble_info)
        self._vm.link_health_updated.connect(self._on_link_health)
        self._vm.telemetry_updated.connect(self._on_telemetry_updated)
        self._vm.advertising_devices_updated.connect(self._on_advertising_devices)
        if hasattr(self._vm, 'time_sync_updated'):
            self._vm.time_sync_updated.connect(self._on_time_sync_updated)
        if hasattr(self._vm, 'set_developer_mode'):
            self._vm.set_developer_mode(self._is_developer_mode)

    # ── View Updaters ────────────────────────────────────────────────
    def set_developer_mode(self, enabled: bool):
        self._is_developer_mode = bool(enabled)
        if self._vm and hasattr(self._vm, "set_developer_mode"):
            self._vm.set_developer_mode(self._is_developer_mode)

    def _on_device_info(self, info: dict):
        if "Status" in info:
            if info["Status"] in ("Disconnected", "Connecting", "Connected"):
                ble_snapshot = {
                    key: (label.text(), label.toolTip(), label.styleSheet())
                    for key, label in self._ble_values.items()
                }
                self._reset_display_fields()
                for key, (text, tooltip, style) in ble_snapshot.items():
                    label = self._ble_values.get(key)
                    if label is not None:
                        label.setText(text)
                        label.setToolTip(tooltip)
                        label.setStyleSheet(style)
            return

        for k, v in info.items():
            lbl = f"{k}:"
            if lbl in self._dev_values:
                self._dev_values[lbl].setText(str(v) if v not in (None, "") else "-")

    def _on_ble_info(self, info: dict):
        display_state = info.get("display_state")
        if display_state is not None:
            self._ble_values["Dongle BLE:"].setText(str(display_state).replace("BLE_STATE_", ""))
            try:
                state_value = int(info.get("state", -1) if info.get("state") is not None else -1)
            except (TypeError, ValueError):
                state_value = -1
            raw_state_label = str(info.get("state_name") or display_state or "").upper()
            if state_value == 5 or raw_state_label in ("BLE_STATE_CONNECTED", "CONNECTED"):
                color = "#10B981"
            elif state_value in (2, 3, 4) or any(name in raw_state_label for name in ("SCANNING", "ADVERTISING", "CONNECTING")):
                color = "#F59E0B"
            elif state_value in (0, 1) or any(name in raw_state_label for name in ("UNSPECIFIED", "IDLE")):
                color = "#94A3B8"
            else:
                color = "#EF4444"
            self._ble_values["Dongle BLE:"].setStyleSheet(f"color: {color}; font-weight: bold;")
            reason_hex = info.get("disconnect_reason_hex")
            reason_name = info.get("disconnect_reason_name")
            raw_state = info.get("state_name")
            tooltip = f"Raw BLE state: {raw_state or display_state}"
            if reason_hex and reason_name:
                tooltip += f" | Reason: {reason_hex} - {reason_name}"
            self._ble_values["Dongle BLE:"].setToolTip(tooltip)

        rssi = info.get("rssi_dbm")
        if rssi is not None:
            self._ble_values["RSSI:"].setText(f"{rssi} dBm")
        
        conn_interval = info.get("conn_interval")
        if conn_interval is not None:
            self._ble_values["Conn Interval:"].setText(conn_interval)
            
        slave_latency = info.get("slave_latency")
        if slave_latency is not None:
            self._ble_values["Slave Latency:"].setText(str(slave_latency))
            
        sup_timeout = info.get("supervision_timeout")
        if sup_timeout is not None:
            self._ble_values["Sup. Timeout:"].setText(f"{sup_timeout} ms")

        phy = info.get("phy")
        if phy is not None:
            self._ble_values["PHY:"].setText(str(phy))

    def _on_link_health(self, info: dict):
        connection_status = str(info.get("connection_status") or "-")
        health = str(info.get("health") or "-").upper()
        self._ble_values["Device Link:"].setText(connection_status)
        self._ble_values["Link Health:"].setText(health)
        if health == "OK":
            color = "#10B981"
        elif health in {"WARNING", "CONNECTING"}:
            color = "#F59E0B"
        elif health == "LOST":
            color = "#EF4444"
        else:
            color = "#94A3B8"
        self._ble_values["Link Health:"].setStyleSheet(f"color: {color}; font-weight: bold;")
        age_s = info.get("last_device_rx_age_s")
        age_text = "No device RX yet" if age_s is None else f"Last device RX: {float(age_s):.1f}s ago"
        scan_text = "active" if info.get("scan_active") else "inactive"
        self._ble_values["Link Health:"].setToolTip(f"{age_text} | Dongle scan: {scan_text}")

    def _on_telemetry_updated(self, data: dict):
        pct = data.get("bat_soc_percent")
        if pct is None:
            self._bat_pct.setText("-")
            self._bat_bar.setValue(0)
            color = "#94A3B8"
        else:
            pct = int(pct)
            self._bat_pct.setText(f"{pct}%")
            self._bat_bar.setValue(pct)
            color = "#10B981" if pct > 30 else "#EF4444"
        self._bat_pct.setStyleSheet(f"color: {color}; background: transparent;")

        self._bat_info_labels["Voltage:"].setText(data.get("bat_voltage_str", "-"))
        self._bat_info_labels["Remaining:"].setText(data.get("remaining_str", "-"))
        self._bat_info_labels["Charging:"].setText(data.get("charging_str", "-"))

        self._temp_labels["MCU:"].setText(data.get("mcu_temp_str", "-"))
        self._temp_labels["UWB Chip:"].setText(data.get("uwb_temp_str", "-"))
        self._temp_labels["IMU:"].setText(data.get("imu_temp_str", "-"))

        self._volt_labels["VDDA:"].setText(data.get("vdda_str", "-"))
        self._volt_labels["UWB VBAT:"].setText(data.get("uwb_vbat_str", "-"))

        self._sys_labels["HEAP:"].setText(data.get("heap_usage", "-"))
        self._sys_labels["STACK:"].setText(data.get("stack_usage", "-"))
        self._sys_labels["CPU:"].setText(data.get("cpu_usage", "-"))

    def _on_time_sync_updated(self, local_time: str, is_synced: bool, is_syncing: bool):
        self._time_local.setText(local_time or "-")
        if is_syncing:
            self._time_status.setText("Syncing time...")
            self._time_status.setStyleSheet("color: #EAB308; background: transparent; font-size: 12px; font-weight: bold;")
        elif is_synced:
            self._time_status.setText("Sync Status: OK")
            self._time_status.setStyleSheet("color: #10B981; background: transparent; font-size: 12px; font-weight: bold;")
        else:
            self._time_status.setText("Warning: Out of Sync")
            self._time_status.setStyleSheet("color: #EF4444; background: transparent; font-size: 12px; font-weight: bold;")

    def _on_advertising_devices(self, devices: list, is_scanning: bool):
        # Update Refresh button state
        if hasattr(self, "btn_refresh"):
            if is_scanning:
                self.btn_refresh.setEnabled(False)
                self.btn_refresh.setText("⏳ Scanning...")
            else:
                self.btn_refresh.setEnabled(True)
                self.btn_refresh.setText("🔄 Refresh")

        # Scan status removed by user

        # Compare existing rows to see if we can reuse the table structure and widgets
        rebuild_needed = True
        if self._adv_table.rowCount() == len(devices):
            # Check if all MACs match in the same order
            match = True
            for i, dev in enumerate(devices):
                mac_item = self._adv_table.item(i, 1)
                if not mac_item or mac_item.text() != dev["mac"]:
                    match = False
                    break
            if match:
                rebuild_needed = False

        if rebuild_needed:
            self._adv_table.setRowCount(len(devices))

        for i, dev in enumerate(devices):
            d_type = dev.get("device_type", 0)
            d_id = dev.get("device_id")
            device_name = str(dev.get("name") or "").strip()
            device_str = device_name or "-"

            bat = dev.get("bat_soc_percent")
            bat_str = f"{bat}%" if bat is not None else "-"

            t_ms = int(dev.get("local_timestamp_ms") or 0)
            if t_ms <= 0:
                t_s = int(dev.get("local_timestamp_s") or 0)
                if t_s > 0:
                    t_ms = t_s * 1000
            if t_ms > 0:
                try:
                    t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_ms / 1000.0))
                except Exception:
                    t_str = str(t_ms)
            else:
                t_str = "-"

            st_flags = dev.get("status_flags")
            st_flags_str = f"0x{st_flags:X}" if st_flags is not None else "-"

            warn_cnt = dev.get("warning_count")
            warn_cnt_str = str(warn_cnt) if warn_cnt is not None else "-"

            err_cnt = dev.get("error_count")
            err_cnt_str = str(err_cnt) if err_cnt is not None else "-"

            # Serial number formatting
            sn_val = dev.get("serial_number", 0) or 0
            sn_str = "-"
            if sn_val:
                sn_str = f"0x{sn_val:08X}"
            else:
                raw_sn = dev.get("serial")
                if raw_sn and str(raw_sn).strip():
                    sn_str = str(raw_sn).strip()

            if rebuild_needed:
                self._adv_table.setItem(i, 0, QTableWidgetItem(device_str))
                self._adv_table.setItem(i, 1, QTableWidgetItem(dev["mac"]))
                self._adv_table.setItem(i, 2, QTableWidgetItem(bat_str))
                self._adv_table.setItem(i, 3, QTableWidgetItem(t_str))
                self._adv_table.setItem(i, 4, QTableWidgetItem(sn_str))
                self._adv_table.setItem(i, 5, QTableWidgetItem(st_flags_str))
                self._adv_table.setItem(i, 6, QTableWidgetItem(warn_cnt_str))
                self._adv_table.setItem(i, 7, QTableWidgetItem(err_cnt_str))

                # Load custom row widget from UI Designer file
                row_ui_path = os.path.join(os.path.dirname(__file__), '..', 'ui', 'adv_device_row.ui')
                widget = QWidget()
                uic.loadUi(row_ui_path, widget)
                widget.setProperty("mac", dev["mac"])

                btn_set_time = QPushButton("Set Time")
                btn_set_time.setObjectName("btn_set_time")
                btn_set_time.setMinimumSize(65, 22)
                btn_set_time.setMaximumSize(65, 22)
                btn_set_time.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_set_time.setStyleSheet(
                    "QPushButton { background: #2563EB; color: white; border-radius: 4px; font-weight: bold; font-size: 11px; } "
                    "QPushButton:hover { background: #3B82F6; } "
                    "QPushButton:disabled { background: #334155; color: #94A3B8; }"
                )
                widget.layout().addWidget(btn_set_time)
                self._adv_table.setCellWidget(i, 8, widget)
            else:
                self._set_table_item_text(i, 0, device_str)
                self._set_table_item_text(i, 2, bat_str)
                self._set_table_item_text(i, 3, t_str)
                self._set_table_item_text(i, 4, sn_str)
                self._set_table_item_text(i, 5, st_flags_str)
                self._set_table_item_text(i, 6, warn_cnt_str)
                self._set_table_item_text(i, 7, err_cnt_str)
                widget = self._adv_table.cellWidget(i, 8)

            if widget:
                # Retrieve child buttons
                btn_connect = widget.btn_connect
                btn_set_time = widget.findChild(QPushButton, "btn_set_time")

                model = self._vm.model
                row_mac = dev["mac"]
                is_connected_row = model.connected_mac == row_mac and model.connection_status == "Connected"
                is_connecting_row = model.connected_mac == row_mac and model.connection_status == "Connecting"
                is_disconnecting_row = model.connected_mac == row_mac and model.connection_status == "Disconnecting"
                is_pending_row = model.pending_connect_mac == row_mac and model.connection_status in ("Connecting", "Disconnecting")

                # Determine current state string
                if is_connected_row:
                    state_str = "connected"
                elif is_connecting_row or is_pending_row:
                    state_str = "connecting"
                elif is_disconnecting_row:
                    state_str = "disconnecting"
                else:
                    state_str = "disconnected"

                # Check if state has changed or rebuild occurred to update signals and styling
                prev_state = btn_connect.property("state")
                if prev_state != state_str or rebuild_needed:
                    btn_connect.setProperty("state", state_str)
                    
                    try:
                        btn_connect.clicked.disconnect()
                    except TypeError:
                        pass

                    if state_str == "connected":
                        btn_connect.setText("Disconnect")
                        btn_connect.setEnabled(True)
                        btn_connect.setStyleSheet(
                            "QPushButton { background: #DC2626; color: white; border-radius: 4px; font-weight: bold; font-size: 11px; } "
                            "QPushButton:hover { background: #EF4444; } "
                            "QPushButton:disabled { background: #334155; color: #94A3B8; }"
                        )
                        btn_connect.clicked.connect(lambda checked=False: self._vm.disconnect_device())
                    elif state_str == "connecting":
                        btn_connect.setText("Connecting...")
                        btn_connect.setEnabled(False)
                        btn_connect.setStyleSheet(
                            "QPushButton { background: #334155; color: #94A3B8; border-radius: 4px; font-weight: bold; font-size: 11px; }"
                        )
                    elif state_str == "disconnecting":
                        btn_connect.setText("Disconnecting...")
                        btn_connect.setEnabled(False)
                        btn_connect.setStyleSheet(
                            "QPushButton { background: #334155; color: #94A3B8; border-radius: 4px; font-weight: bold; font-size: 11px; }"
                        )
                    else: # disconnected
                        btn_connect.setText("Connect")
                        btn_connect.setEnabled(True)
                        btn_connect.setStyleSheet(
                            "QPushButton { background: #059669; color: white; border-radius: 4px; font-weight: bold; font-size: 11px; } "
                            "QPushButton:hover { background: #10B981; } "
                            "QPushButton:disabled { background: #334155; color: #94A3B8; }"
                        )
                        btn_connect.clicked.connect(lambda checked, m=row_mac: self._vm.connect_device(m))

                # Update Set Time button connections only on change or rebuild
                if btn_set_time:
                    prev_did = btn_set_time.property("d_id")
                    if prev_did != d_id or rebuild_needed:
                        btn_set_time.setProperty("d_id", d_id)
                        try:
                            btn_set_time.clicked.disconnect()
                        except TypeError:
                            pass
                        btn_set_time.clicked.connect(lambda checked, dt=d_type, di=d_id: self._vm.send_time_sync_adv(dt, di))
                    
                    btn_set_time.setEnabled(d_id is not None and state_str not in ("connecting", "disconnecting"))

        self._update_adv_table_height(len(devices))

    def _set_table_item_text(self, row: int, col: int, text: str):
        item = self._adv_table.item(row, col)
        if item:
            item.setText(text)
        else:
            self._adv_table.setItem(row, col, QTableWidgetItem(text))

    def _on_refresh_clicked(self):
        if self._vm and hasattr(self._vm, "refresh_advertising_devices"):
            self._vm.refresh_advertising_devices()
