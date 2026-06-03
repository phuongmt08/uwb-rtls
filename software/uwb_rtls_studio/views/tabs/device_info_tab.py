"""
UWB RTLS Studio — Device Info Tab (Frontend Only)
Tab 1: Hiển thị thông tin device đã connected.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QProgressBar, QFrame, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea
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
        self._vm.dongle_info_updated.connect(self._on_dongle_info)
        self._vm.ble_info_updated.connect(self._on_ble_info)
        self._vm.telemetry_updated.connect(self._on_telemetry_updated)
        self._vm.advertising_devices_updated.connect(self._on_advertising_devices)
        
        self._btn_refresh.clicked.connect(self._vm.refresh_telemetry)

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        container = QWidget()
        container.setStyleSheet("QWidget { background: transparent; }")
        main = QVBoxLayout(container)
        main.setSpacing(16)
        main.setContentsMargins(16, 16, 16, 16)

        top_hbox = QHBoxLayout()
        top_hbox.setSpacing(16)

        # ═══ LEFT COLUMN: Device + Dongle Info ═══
        left = QVBoxLayout()
        left.setSpacing(14)

        # Device Info Card
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

        # Dongle Info Card
        dongle_group = QGroupBox("🔌 USB Dongle (Central)")
        d_grid = QGridLayout(dongle_group)
        d_grid.setSpacing(10)
        dongle_labels = [
            ("Port:", "-"),
            ("VID / PID:", "-"),
            ("Firmware:", "-"),
            ("Serial:", "-"),
            ("Status:", "-"),
        ]
        self._dongle_values = {}
        for i, (label, value) in enumerate(dongle_labels):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            if label == "Status:":
                val.setStyleSheet("color: #10B981; font-weight: bold;")
            else:
                val.setStyleSheet("color: #F8FAFC;")
            d_grid.addWidget(lbl, i, 0)
            d_grid.addWidget(val, i, 1)
            self._dongle_values[label] = val
        left.addWidget(dongle_group)

        # BLE Connection Params
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
        for i, (label, value) in enumerate(ble_labels):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet("color: #F8FAFC;")
            ble_grid.addWidget(lbl, i, 0)
            ble_grid.addWidget(val, i, 1)
        left.addWidget(ble_group)

        left.addStretch()
        top_hbox.addLayout(left, 1)

        # ═══ RIGHT COLUMN: Telemetry ═══
        right = QVBoxLayout()
        right.setSpacing(14)

        # Battery
        bat_group = QGroupBox("🔋 Battery")
        bat_layout = QVBoxLayout(bat_group)

        self._bat_pct = QLabel("- %")
        self._bat_pct.setFont(QFont("Segoe UI", 36, QFont.Weight.Bold))
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
        right.addWidget(bat_group)

        # Temperature
        temp_group = QGroupBox("🌡 Temperature")
        temp_grid = QGridLayout(temp_group)
        temp_grid.setSpacing(10)
        self._temp_labels = {}
        temp_data = [
            ("MCU:", "-", "#94A3B8"),
            ("UWB Chip:", "-", "#94A3B8"),
            ("IMU:", "-", "#94A3B8"),
        ]
        for i, (label, value, color) in enumerate(temp_data):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 15px;")
            temp_grid.addWidget(lbl, i, 0)
            temp_grid.addWidget(val, i, 1)
            self._temp_labels[label] = val
        right.addWidget(temp_group)

        # Voltage
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
        right.addWidget(volt_group)

        # Refresh button
        self._btn_refresh = QPushButton("🔄 Refresh Telemetry")
        self._btn_refresh.setFixedHeight(38)
        self._btn_refresh.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        right.addWidget(self._btn_refresh)

        right.addStretch()
        top_hbox.addLayout(right, 1)

        main.addLayout(top_hbox)

        # ═══ BOTTOM: Other Advertising Devices ═══
        adv_group = QGroupBox("📡 Other Advertising Devices ")
        adv_layout = QVBoxLayout(adv_group)

        self._adv_table = QTableWidget(0, 4)
        self._adv_table.setHorizontalHeaderLabels(["Name", "MAC", "RSSI", "Action"])
        self._adv_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._adv_table.verticalHeader().setDefaultSectionSize(40)
        self._adv_table.verticalHeader().setVisible(False)
        self._adv_table.setStyleSheet("background: #0F172A; color: #F8FAFC; gridline-color: #334155;")
        self._adv_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._adv_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._adv_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._adv_table.setFixedHeight(60) # Initial empty height
        adv_layout.addWidget(self._adv_table)
        
        main.addWidget(adv_group)
        main.addStretch(1)
        
        scroll.setWidget(container)
        base_layout = QVBoxLayout(self)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.addWidget(scroll)

    # --- View Updaters ---
    def _on_device_info(self, info: dict):
        for k, v in info.items():
            lbl = f"{k}:"
            if lbl in self._dev_values:
                self._dev_values[lbl].setText(str(v))

    def _on_dongle_info(self, info: dict):
        for k, v in info.items():
            lbl = f"{k}:"
            if lbl in self._dongle_values:
                self._dongle_values[lbl].setText(str(v))

    def _on_ble_info(self, info: dict):
        pass

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

    def _on_advertising_devices(self, devices: list, is_scanning: bool):
        self._adv_table.setRowCount(len(devices))
        from PyQt6.QtWidgets import QWidget, QHBoxLayout
        from PyQt6.QtCore import Qt
        for i, dev in enumerate(devices):
            self._adv_table.setItem(i, 0, QTableWidgetItem(dev["name"]))
            self._adv_table.setItem(i, 1, QTableWidgetItem(dev["mac"]))
            self._adv_table.setItem(i, 2, QTableWidgetItem(f"{dev['rssi']} dBm"))
            
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(8, 2, 8, 2)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            btn_connect = QPushButton("Connect")
            btn_connect.setFixedSize(100, 24)
            btn_connect.setStyleSheet("""
                QPushButton { background: #059669; color: white; border-radius: 4px; font-weight: bold; font-size: 11px; }
                QPushButton:hover { background: #10B981; }
            """)
            btn_connect.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_connect.clicked.connect(lambda checked, m=dev["mac"]: self._vm.connect_device(m))
            
            layout.addWidget(btn_connect)
            self._adv_table.setCellWidget(i, 3, widget)

        # Dynamic height adjustment
        header_height = self._adv_table.horizontalHeader().height()
        if header_height == 0:
            header_height = 30 # fallback
        row_height = 40
        total_height = header_height + (len(devices) * row_height) + 2 # +2 for borders
        self._adv_table.setFixedHeight(max(total_height, 60))
