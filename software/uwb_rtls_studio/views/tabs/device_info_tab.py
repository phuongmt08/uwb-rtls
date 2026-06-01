"""
UWB RTLS Studio — Device Info Tab (Frontend Only)
Tab 1: Hiển thị thông tin device đã connected.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QProgressBar, QFrame, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


class DeviceInfoTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._start_demo_updates()

    def _build_ui(self):
        main = QHBoxLayout(self)
        main.setSpacing(16)
        main.setContentsMargins(16, 16, 16, 16)

        # ═══ LEFT COLUMN: Device + Dongle Info ═══
        left = QVBoxLayout()
        left.setSpacing(14)

        # Device Info Card
        dev_group = QGroupBox("📱 Connected Device")
        dev_grid = QGridLayout(dev_group)
        dev_grid.setSpacing(10)

        labels = [
            ("Device Name:", "UWB-Tag-001"),
            ("Type:", "TAG"),
            ("Role:", "Tag (Peripheral)"),
            ("MAC Address:", "AA:BB:CC:DD:01:01"),
            ("Serial Number:", "0x00010001"),
            ("Firmware:", "v2.1.3"),
            ("Hardware Rev:", "3"),
            ("UID:", "0xDEADBEEF12345678"),
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
            ("Port:", "COM5"),
            ("VID / PID:", "0x1915 / 0x520F"),
            ("Firmware:", "v2.1.3"),
            ("Serial:", "0x12345678"),
            ("Status:", "Connected"),
        ]
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
        left.addWidget(dongle_group)

        # BLE Connection Params
        ble_group = QGroupBox("📶 BLE Connection")
        ble_grid = QGridLayout(ble_group)
        ble_grid.setSpacing(10)
        ble_labels = [
            ("RSSI:", "-45 dBm"),
            ("Conn Interval:", "30 ms"),
            ("Slave Latency:", "0"),
            ("Sup. Timeout:", "4000 ms"),
            ("PHY:", "2M"),
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
        main.addLayout(left, 1)

        # ═══ RIGHT COLUMN: Telemetry ═══
        right = QVBoxLayout()
        right.setSpacing(14)

        # Battery
        bat_group = QGroupBox("🔋 Battery")
        bat_layout = QVBoxLayout(bat_group)

        self._bat_pct = QLabel("78%")
        self._bat_pct.setFont(QFont("Segoe UI", 36, QFont.Weight.Bold))
        self._bat_pct.setStyleSheet("color: #10B981; background: transparent;")
        self._bat_pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bat_layout.addWidget(self._bat_pct)

        self._bat_bar = QProgressBar()
        self._bat_bar.setRange(0, 100)
        self._bat_bar.setValue(78)
        self._bat_bar.setFixedHeight(12)
        self._bat_bar.setTextVisible(False)
        self._bat_bar.setStyleSheet("""
            QProgressBar { background: #0A0F1E; border: 1px solid #334155; border-radius: 6px; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #059669, stop:1 #10B981); border-radius: 5px; }
        """)
        bat_layout.addWidget(self._bat_bar)

        bat_details = QGridLayout()
        bat_info = [("Voltage:", "3.82V"), ("Remaining:", "~5h 23m"), ("Charging:", "No")]
        for i, (l, v) in enumerate(bat_info):
            lbl = QLabel(l)
            lbl.setStyleSheet("color: #94A3B8; background: transparent;")
            val = QLabel(v)
            val.setStyleSheet("color: #F8FAFC; background: transparent;")
            bat_details.addWidget(lbl, i, 0)
            bat_details.addWidget(val, i, 1)
        bat_layout.addLayout(bat_details)
        right.addWidget(bat_group)

        # Temperature
        temp_group = QGroupBox("🌡 Temperature")
        temp_grid = QGridLayout(temp_group)
        temp_grid.setSpacing(10)
        temp_data = [
            ("MCU:", "32.5 °C", "#10B981"),
            ("UWB Chip:", "34.2 °C", "#10B981"),
            ("IMU:", "31.8 °C", "#10B981"),
        ]
        for i, (label, value, color) in enumerate(temp_data):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 15px;")
            temp_grid.addWidget(lbl, i, 0)
            temp_grid.addWidget(val, i, 1)
        right.addWidget(temp_group)

        # Voltage
        volt_group = QGroupBox("⚡ Voltage")
        volt_grid = QGridLayout(volt_group)
        volt_data = [("VDDA:", "3.30V"), ("UWB VBAT:", "3.28V")]
        for i, (l, v) in enumerate(volt_data):
            lbl = QLabel(l)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(v)
            val.setStyleSheet("color: #F8FAFC;")
            volt_grid.addWidget(lbl, i, 0)
            volt_grid.addWidget(val, i, 1)
        right.addWidget(volt_group)

        # Refresh button
        btn_refresh = QPushButton("🔄 Refresh Telemetry")
        btn_refresh.setFixedHeight(38)
        btn_refresh.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        right.addWidget(btn_refresh)

        right.addStretch()
        main.addLayout(right, 1)

    def _start_demo_updates(self):
        """Simulate periodic telemetry updates."""
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._update_demo)
        self._demo_timer.start(5000)

    def _update_demo(self):
        import random
        bat = random.randint(60, 95)
        self._bat_pct.setText(f"{bat}%")
        self._bat_bar.setValue(bat)
        if bat > 50:
            self._bat_pct.setStyleSheet("color: #10B981; background: transparent;")
        elif bat > 20:
            self._bat_pct.setStyleSheet("color: #F59E0B; background: transparent;")
        else:
            self._bat_pct.setStyleSheet("color: #EF4444; background: transparent;")
