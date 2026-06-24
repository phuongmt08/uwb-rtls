"""
UWB RTLS Studio — Device Info Tab (UI loaded from .ui file)
Tab 1: Hiển thị thông tin device đã connected.

FE: Loaded from views/ui/device_info_tab.ui (editable in Qt Designer)
BE: ViewModel bindings + data updaters (this file)

Layout: Split-screen
  - LEFT column:  Connected Device + BLE Connection + Battery + Temperature + Voltage
  - RIGHT column: Other Advertising Devices (with Connect buttons per row)

Background polling: ViewModel tự động gửi GET commands mỗi 2s (không cần nút Refresh).
"""
import os
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QProgressBar, QFrame, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6 import uic
from utils.constants import DEVICE_TYPE_LABELS_SHORT

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'device_info_tab.ui')


class DeviceInfoTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        self._is_developer_mode = False

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_mappings()
        self._setup_table()

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
        self._ble_values = {
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

    def _setup_table(self):
        """Configure the advertising devices table header sizing."""
        self._adv_table.setColumnWidth(0, 150)  # Device
        self._adv_table.setColumnWidth(1, 150)  # MAC
        self._adv_table.setColumnWidth(2, 70)   # Bat %
        self._adv_table.setColumnWidth(3, 300)  # Time
        self._adv_table.setColumnWidth(4, 90)   # Status
        self._adv_table.setColumnWidth(5, 90)   # Warn
        self._adv_table.setColumnWidth(6, 90)   # Error
        self._adv_table.setColumnWidth(7, 145)   # Action (Connect + Set Time buttons)

    def set_viewmodel(self, vm):
        self._vm = vm
        self._vm.device_info_updated.connect(self._on_device_info)
        self._vm.ble_info_updated.connect(self._on_ble_info)
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
        for k, v in info.items():
            lbl = f"{k}:"
            if lbl in self._dev_values:
                self._dev_values[lbl].setText(str(v))

    def _on_ble_info(self, info: dict):
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

    def _on_telemetry_updated(self, data: dict):
        pct = data.get("bat_soc_percent")
        if pct is None:
            self._bat_pct.setText("--")
            self._bat_bar.setValue(0)
            color = "#94A3B8"
        else:
            pct = int(pct)
            self._bat_pct.setText(f"{pct}%")
            self._bat_bar.setValue(pct)
            color = "#10B981" if pct > 30 else "#EF4444"
        self._bat_pct.setStyleSheet(f"color: {color}; background: transparent;")

        self._bat_info_labels["Voltage:"].setText(data.get("bat_voltage_str", "--"))
        self._bat_info_labels["Remaining:"].setText(data.get("remaining_str", "--"))
        self._bat_info_labels["Charging:"].setText(data.get("charging_str", "--"))

        self._temp_labels["MCU:"].setText(data.get("mcu_temp_str", "--"))
        self._temp_labels["UWB Chip:"].setText(data.get("uwb_temp_str", "--"))
        self._temp_labels["IMU:"].setText(data.get("imu_temp_str", "--"))

        self._volt_labels["VDDA:"].setText(data.get("vdda_str", "--"))
        self._volt_labels["UWB VBAT:"].setText(data.get("uwb_vbat_str", "--"))

        self._sys_labels["HEAP:"].setText(data.get("heap_usage", "--"))
        self._sys_labels["STACK:"].setText(data.get("stack_usage", "--"))
        self._sys_labels["CPU:"].setText(data.get("cpu_usage", "--"))

    def _on_time_sync_updated(self, local_time: str, is_synced: bool, is_syncing: bool):
        self._time_local.setText(local_time)
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
        # Scan status removed by user

        self._adv_table.setRowCount(len(devices))
        for i, dev in enumerate(devices):
            d_type = dev.get("device_type", 0)
            d_type_label = DEVICE_TYPE_LABELS_SHORT.get(d_type, str(d_type))
            d_id = dev.get("device_id")

            if d_id is not None:
                device_str = f"{d_type_label} 0x{d_id:08X}"
            else:
                device_str = dev.get("name", "-")

            self._adv_table.setItem(i, 0, QTableWidgetItem(device_str))
            self._adv_table.setItem(i, 1, QTableWidgetItem(dev["mac"]))

            bat = dev.get("bat_soc_percent")
            self._adv_table.setItem(i, 2, QTableWidgetItem(f"{bat}%" if bat is not None else "-"))

            t_ms = dev.get("local_timestamp_ms")
            if t_ms is not None and t_ms > 0:
                try:
                    t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_ms / 1000.0))
                except Exception:
                    t_str = str(t_ms)
            else:
                t_str = "-"
            self._adv_table.setItem(i, 3, QTableWidgetItem(t_str))

            st_flags = dev.get("status_flags")
            self._adv_table.setItem(i, 4, QTableWidgetItem(f"0x{st_flags:X}" if st_flags is not None else "-"))

            warn_cnt = dev.get("warning_count")
            self._adv_table.setItem(i, 5, QTableWidgetItem(str(warn_cnt) if warn_cnt is not None else "-"))

            err_cnt = dev.get("error_count")
            self._adv_table.setItem(i, 6, QTableWidgetItem(str(err_cnt) if err_cnt is not None else "-"))

            # Load custom row widget from UI Designer file
            row_ui_path = os.path.join(os.path.dirname(__file__), '..', 'ui', 'adv_device_row.ui')
            widget = QWidget()
            uic.loadUi(row_ui_path, widget)
            widget.btn_connect.clicked.connect(lambda checked, m=dev["mac"]: self._vm.connect_device(m))
            
            # Create and style Set Time button
            btn_set_time = QPushButton("Set Time")
            btn_set_time.setMinimumSize(65, 22)
            btn_set_time.setMaximumSize(65, 22)
            btn_set_time.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_set_time.setStyleSheet(
                "QPushButton { background: #2563EB; color: white; border-radius: 4px; font-weight: bold; font-size: 11px; } "
                "QPushButton:hover { background: #3B82F6; } "
                "QPushButton:disabled { background: #334155; color: #94A3B8; }"
            )
            btn_set_time.clicked.connect(lambda checked, dt=d_type, di=d_id: self._vm.send_time_sync_adv(dt, di))
            
            # Disable Set Time if d_id is None
            if d_id is None:
                btn_set_time.setEnabled(False)
                
            widget.layout().addWidget(btn_set_time)
            
            # Disable connect button if we are currently connecting to this device
            if self._vm.model._pending_connect_mac == dev["mac"]:
                widget.btn_connect.setText("Connecting...")
                widget.btn_connect.setEnabled(False)
                btn_set_time.setEnabled(False)
            
            self._adv_table.setCellWidget(i, 7, widget)

        # Dynamic height adjustment so the groupbox scales with the content
        header_height = self._adv_table.horizontalHeader().height()
        if header_height == 0:
            header_height = 60  # fallback
        row_height = 36
        visible_rows = min(len(devices), 10)  # maximum devices show at 10 rows for height
        total_height = header_height + (visible_rows * row_height) + 2  # +2 for borders
        self._adv_table.setFixedHeight(max(total_height, 60))
