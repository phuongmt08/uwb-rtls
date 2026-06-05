"""
UWB RTLS Studio — Device Info Tab (Frontend Only)
Tab 1: Hiển thị thông tin device đã connected.

Layout: Split-screen
  - LEFT column:  Connected Device + BLE Connection + Battery + Temperature + Voltage
  - RIGHT column: Other Advertising Devices (with Connect buttons per row)

Background polling: ViewModel tự động gửi GET commands mỗi 2s (không cần nút Refresh).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QProgressBar, QFrame, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


class DeviceInfoTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        self._build_ui()

    def set_viewmodel(self, vm):
        self._vm = vm
        self._vm.device_info_updated.connect(self._on_device_info)
        self._vm.ble_info_updated.connect(self._on_ble_info)
        self._vm.telemetry_updated.connect(self._on_telemetry_updated)
        self._vm.advertising_devices_updated.connect(self._on_advertising_devices)

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        container = QWidget()
        container.setStyleSheet("QWidget { background: transparent; }")

        # ═══ MAIN SPLIT: Left + Right ═══
        main_hbox = QHBoxLayout(container)
        main_hbox.setSpacing(16)
        main_hbox.setContentsMargins(16, 16, 16, 16)

        # ═══════════════════════════════════════════════════════════════
        #  LEFT COLUMN: Device Info + BLE + Battery + Temperature + Voltage
        # ═══════════════════════════════════════════════════════════════
        left = QVBoxLayout()
        left.setSpacing(14)

        # ── Connected Device Card ──
        dev_group = QGroupBox("📱 Connected Device")
        dev_grid = QGridLayout(dev_group)
        dev_grid.setSpacing(10)

        labels = [
            ("Device Name:", "-"),
            ("Type:", "-"),
            ("Role:", "-"),
            ("MAC Address:", "-"),
            ("Serial Number:", "-"),
            ("Firmware:", "-"),
            ("Hardware Rev:", "-"),
            ("UID:", "-"),
        ]
        self._dev_values = {}
        for i, (label, value) in enumerate(labels):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet("color: #F8FAFC;")
            if label == "Type:":
                val.setStyleSheet("color: #22D3EE; font-weight: bold;")
            dev_grid.addWidget(lbl, i, 0)
            dev_grid.addWidget(val, i, 1)
            self._dev_values[label] = val
        left.addWidget(dev_group)

        # ── BLE Connection Params ──
        ble_group = QGroupBox("📶 BLE Connection")
        ble_grid = QGridLayout(ble_group)
        ble_grid.setSpacing(10)
        ble_labels = [
            ("RSSI:", "-"),
            ("Conn Interval:", "-"),
            ("Slave Latency:", "-"),
            ("Sup. Timeout:", "-"),
            ("PHY:", "-"),
        ]
        self._ble_values = {}
        for i, (label, value) in enumerate(ble_labels):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet("color: #F8FAFC;")
            ble_grid.addWidget(lbl, i, 0)
            ble_grid.addWidget(val, i, 1)
            self._ble_values[label] = val
        left.addWidget(ble_group)

        # ── Battery ──
        bat_group = QGroupBox("🔋 Battery")
        bat_layout = QVBoxLayout(bat_group)

        self._bat_pct = QLabel("- %")
        self._bat_pct.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        self._bat_pct.setStyleSheet("color: #94A3B8; background: transparent;")
        self._bat_pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bat_layout.addWidget(self._bat_pct)

        self._bat_bar = QProgressBar()
        self._bat_bar.setRange(0, 100)
        self._bat_bar.setValue(0)
        self._bat_bar.setFixedHeight(12)
        self._bat_bar.setTextVisible(False)
        self._bat_bar.setStyleSheet("""
            QProgressBar { background: #0A0F1E; border: 1px solid #334155; border-radius: 6px; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #059669, stop:1 #10B981); border-radius: 5px; }
        """)
        bat_layout.addWidget(self._bat_bar)

        bat_details = QGridLayout()
        self._bat_info_labels = {}
        bat_info = [("Voltage:", "-"), ("Remaining:", "-"), ("Charging:", "-")]
        for i, (l, v) in enumerate(bat_info):
            lbl = QLabel(l)
            lbl.setStyleSheet("color: #94A3B8; background: transparent;")
            val = QLabel(v)
            val.setStyleSheet("color: #F8FAFC; background: transparent;")
            bat_details.addWidget(lbl, i, 0)
            bat_details.addWidget(val, i, 1)
            self._bat_info_labels[l] = val
        bat_layout.addLayout(bat_details)
        left.addWidget(bat_group)

        # ── Temperature ──
        temp_group = QGroupBox("🌡 Temperature")
        temp_grid = QGridLayout(temp_group)
        temp_grid.setSpacing(10)
        self._temp_labels = {}
        temp_data = [
            ("MCU:", "-"),
            ("UWB Chip:", "-"),
            ("IMU:", "-"),
        ]
        for i, (label, value) in enumerate(temp_data):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet("color: #94A3B8; font-weight: bold; font-size: 15px;")
            temp_grid.addWidget(lbl, i, 0)
            temp_grid.addWidget(val, i, 1)
            self._temp_labels[label] = val
        left.addWidget(temp_group)

        # ── Voltage ──
        volt_group = QGroupBox("⚡ Voltage")
        volt_grid = QGridLayout(volt_group)
        self._volt_labels = {}
        volt_data = [("VDDA:", "-"), ("UWB VBAT:", "-")]
        for i, (l, v) in enumerate(volt_data):
            lbl = QLabel(l)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(v)
            val.setStyleSheet("color: #F8FAFC;")
            volt_grid.addWidget(lbl, i, 0)
            volt_grid.addWidget(val, i, 1)
            self._volt_labels[l] = val
        left.addWidget(volt_group)

        # ── System Resources ──
        sys_group = QGroupBox("💻 System Resources")
        sys_grid = QGridLayout(sys_group)
        sys_grid.setSpacing(10)
        sys_data = [("HEAP:", "-"), ("STACK:", "-"), ("CPU:", "-")]
        self._sys_labels = {}
        for i, (l, v) in enumerate(sys_data):
            lbl = QLabel(l)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            val = QLabel(v)
            val.setStyleSheet("color: #F8FAFC;")
            val.setWordWrap(True)
            sys_grid.addWidget(lbl, i, 0)
            sys_grid.addWidget(val, i, 1)
            self._sys_labels[l] = val
        sys_grid.setColumnStretch(1, 1)
        left.addWidget(sys_group)

        left.addStretch(1)
        main_hbox.addLayout(left, 1)

        # ═══════════════════════════════════════════════════════════════
        #  RIGHT COLUMN: Other Advertising Devices
        # ═══════════════════════════════════════════════════════════════
        right = QVBoxLayout()
        right.setSpacing(14)

        adv_group = QGroupBox("📡 Other Advertising Devices")
        adv_layout = QVBoxLayout(adv_group)

        self._adv_table = QTableWidget(0, 3)
        self._adv_table.setHorizontalHeaderLabels(["Name", "MAC", "RSSI"])
        header = self._adv_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._adv_table.verticalHeader().setDefaultSectionSize(44)
        self._adv_table.verticalHeader().setVisible(False)
        self._adv_table.setStyleSheet("background: #0F172A; color: #F8FAFC; gridline-color: #334155;")
        self._adv_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._adv_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._adv_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._adv_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        adv_layout.addWidget(self._adv_table)

        # Scanning status indicator
        self._scan_status = QLabel("⏳ Scanning...")
        self._scan_status.setStyleSheet("color: #94A3B8; font-style: italic; padding: 4px;")
        self._scan_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        adv_layout.addWidget(self._scan_status)

        adv_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        right.addWidget(adv_group)
        right.addStretch(1)

        main_hbox.addLayout(right, 1)

        scroll.setWidget(container)
        base_layout = QVBoxLayout(self)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.addWidget(scroll)

    # ── View Updaters ────────────────────────────────────────────────
    def _on_device_info(self, info: dict):
        for k, v in info.items():
            lbl = f"{k}:"
            if lbl in self._dev_values:
                self._dev_values[lbl].setText(str(v))

    def _on_ble_info(self, info: dict):
        rssi = info.get("rssi_dbm")
        if rssi is not None:
            self._ble_values["RSSI:"].setText(f"{rssi} dBm")

    def _on_telemetry_updated(self, data: dict):
        pct = data.get("bat_soc_percent", 0)
        self._bat_pct.setText(f"{pct}%")
        self._bat_bar.setValue(pct)
        color = "#10B981" if pct > 30 else "#EF4444"
        self._bat_pct.setStyleSheet(f"color: {color}; background: transparent;")

        self._bat_info_labels["Voltage:"].setText(f"{data.get('bat_voltage_mv', 0) / 1000.0:.2f}V")
        self._bat_info_labels["Remaining:"].setText(f"{data.get('remaining_min', 0)} min")
        self._bat_info_labels["Charging:"].setText("Yes" if data.get("is_charging") else "No")

        self._temp_labels["MCU:"].setText(f"{data.get('mcu_temp_c', 0):.1f} °C")
        self._temp_labels["UWB Chip:"].setText(f"{data.get('uwb_temp_c', 0):.1f} °C")
        self._temp_labels["IMU:"].setText(f"{data.get('imu_temp_c', 0):.1f} °C")

        self._volt_labels["VDDA:"].setText(f"{data.get('vdda_mv', 0) / 1000.0:.2f}V")
        self._volt_labels["UWB VBAT:"].setText(f"{data.get('uwb_vbat_mv', 0) / 1000.0:.2f}V")

        self._sys_labels["HEAP:"].setText(data.get("heap_usage", "-"))
        self._sys_labels["STACK:"].setText(data.get("stack_usage", "-"))
        self._sys_labels["CPU:"].setText(data.get("cpu_usage", "-"))

    def _on_advertising_devices(self, devices: list, is_scanning: bool):
        # Update scanning status
        self._scan_status.setText("⏳ Scanning..." if is_scanning else "⏸ Scan stopped")

        self._adv_table.setRowCount(len(devices))
        for i, dev in enumerate(devices):
            self._adv_table.setItem(i, 0, QTableWidgetItem(dev["name"]))
            self._adv_table.setItem(i, 1, QTableWidgetItem(dev["mac"]))
            
            # Combine RSSI text and Connect button in column 2
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(8, 2, 8, 2)
            
            lbl_rssi = QLabel(f"{dev['rssi']} dBm")
            lbl_rssi.setStyleSheet("color: #F8FAFC; background: transparent;")
            layout.addWidget(lbl_rssi)
            
            layout.addStretch()
            
            btn_connect = QPushButton("Connect")
            btn_connect.setFixedSize(80, 24)
            btn_connect.setStyleSheet("""
                QPushButton { background: #059669; color: white; border-radius: 4px;
                    font-weight: bold; font-size: 11px; }
                QPushButton:hover { background: #10B981; }
            """)
            btn_connect.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_connect.clicked.connect(lambda checked, m=dev["mac"]: self._vm.connect_device(m))
            layout.addWidget(btn_connect)
            
            self._adv_table.setCellWidget(i, 2, widget)

        # Dynamic height adjustment so the groupbox scales with the content
        header_height = self._adv_table.horizontalHeader().height()
        if header_height == 0:
            header_height = 30 # fallback
        row_height = 44
        total_height = header_height + (len(devices) * row_height) + 2 # +2 for borders
        self._adv_table.setFixedHeight(max(total_height, 60))
