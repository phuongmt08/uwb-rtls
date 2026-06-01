"""
UWB RTLS Studio — Config Tab (Frontend Only)
Tab 3: User + Developer config sections.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QScrollArea, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QCheckBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class ConfigTab(QWidget):
    def __init__(self, is_developer=False, parent=None):
        super().__init__(parent)
        self._is_developer = is_developer
        self._build_ui()

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled
        for w in self._dev_widgets:
            w.setVisible(enabled)

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        self._dev_widgets = []

        # ═══ 👤 USER: Anchor/Tag Layout ═══
        anchor_group = QGroupBox("👤 Anchor / Tag Layout")
        anchor_layout = QVBoxLayout(anchor_group)

        self._anchor_table = QTableWidget(4, 4)
        self._anchor_table.setHorizontalHeaderLabels(["Anchor ID", "X (m)", "Y (m)", "Z (m)"])
        self._anchor_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._anchor_table.verticalHeader().setVisible(False)
        self._anchor_table.setFixedHeight(180)
        demo_anchors = [("A0", "0.00", "0.00", "0.00"), ("A1", "5.00", "0.00", "0.00"),
                        ("A2", "0.00", "4.00", "0.00"), ("A3", "5.00", "4.00", "0.00")]
        for r, (aid, x, y, z) in enumerate(demo_anchors):
            self._anchor_table.setItem(r, 0, QTableWidgetItem(aid))
            self._anchor_table.setItem(r, 1, QTableWidgetItem(x))
            self._anchor_table.setItem(r, 2, QTableWidgetItem(y))
            self._anchor_table.setItem(r, 3, QTableWidgetItem(z))
        anchor_layout.addWidget(self._anchor_table)

        anchor_btns = QHBoxLayout()
        btn_read_anchor = QPushButton("📥 Read Layout")
        btn_write_anchor = QPushButton("📤 Write Layout")
        btn_write_anchor.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        anchor_btns.addWidget(btn_read_anchor)
        anchor_btns.addWidget(btn_write_anchor)
        anchor_btns.addStretch()
        anchor_layout.addLayout(anchor_btns)
        layout.addWidget(anchor_group)

        # ═══ 👤 USER: Time Synchronization ═══
        time_group = QGroupBox("👤 Time Synchronization")
        time_grid = QGridLayout(time_group)
        time_grid.setSpacing(10)

        time_grid.addWidget(QLabel("Sync Period (ms):"), 0, 0)
        sync_spin = QSpinBox()
        sync_spin.setRange(100, 60000)
        sync_spin.setValue(1000)
        time_grid.addWidget(sync_spin, 0, 1)

        time_grid.addWidget(QLabel("Clock Offset (μs):"), 1, 0)
        offset_lbl = QLineEdit("0")
        offset_lbl.setReadOnly(True)
        time_grid.addWidget(offset_lbl, 1, 1)

        btn_sync = QPushButton("🔄 Sync Now")
        btn_sync.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        time_grid.addWidget(btn_sync, 2, 0, 1, 2)
        layout.addWidget(time_group)

        # ═══ 👤 USER: UWB Basic Info ═══
        uwb_basic = QGroupBox("👤 UWB Basic Info")
        uwb_grid = QGridLayout(uwb_basic)
        uwb_grid.setSpacing(10)

        fields = [
            ("Channel:", "5"),
            ("Role:", "Tag"),
            ("Data Rate:", "6.8 Mbps"),
            ("PRF:", "64 MHz"),
            ("Device ID:", "0x0001"),
        ]
        for i, (label, value) in enumerate(fields):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLineEdit(value)
            val.setReadOnly(True)
            uwb_grid.addWidget(lbl, i, 0)
            uwb_grid.addWidget(val, i, 1)
        layout.addWidget(uwb_basic)

        # ═══ 🔧 DEVELOPER: Ranging Configuration ═══
        ranging_group = QGroupBox("🔧 Ranging Configuration")
        ranging_grid = QGridLayout(ranging_group)

        ranging_grid.addWidget(QLabel("Ranging Period (ms):"), 0, 0)
        rng_period = QSpinBox()
        rng_period.setRange(10, 10000)
        rng_period.setValue(100)
        ranging_grid.addWidget(rng_period, 0, 1)

        ranging_grid.addWidget(QLabel("RX Timeout (ms):"), 1, 0)
        rx_timeout = QSpinBox()
        rx_timeout.setRange(1, 5000)
        rx_timeout.setValue(70)
        ranging_grid.addWidget(rx_timeout, 1, 1)

        rng_btns = QHBoxLayout()
        rng_btns.addWidget(QPushButton("📥 Read"))
        btn_w = QPushButton("📤 Write")
        btn_w.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        rng_btns.addWidget(btn_w)
        ranging_grid.addLayout(rng_btns, 2, 0, 1, 2)
        layout.addWidget(ranging_group)
        self._dev_widgets.append(ranging_group)

        # ═══ 🔧 DEVELOPER: UWB Advanced ═══
        adv_group = QGroupBox("🔧 UWB Advanced Config")
        adv_grid = QGridLayout(adv_group)
        adv_fields = [
            ("TX Antenna Delay:", QSpinBox(), 16436),
            ("RX Antenna Delay:", QSpinBox(), 16436),
            ("TX Power (dBm):", QDoubleSpinBox(), -14.0),
            ("Preamble Code:", QSpinBox(), 10),
        ]
        for i, (label, widget, default) in enumerate(adv_fields):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            if isinstance(widget, QDoubleSpinBox):
                widget.setRange(-30, 0)
                widget.setValue(default)
            else:
                widget.setRange(0, 65535)
                widget.setValue(int(default))
            adv_grid.addWidget(lbl, i, 0)
            adv_grid.addWidget(widget, i, 1)
        layout.addWidget(adv_group)
        self._dev_widgets.append(adv_group)

        # ═══ 🔧 DEVELOPER: Sensor Fusion ═══
        fusion_group = QGroupBox("🔧 Sensor Fusion (UKF)")
        fusion_grid = QGridLayout(fusion_group)
        ukf_params = [
            ("Alpha:", 0.001), ("Kappa:", 0.0), ("Beta:", 2.0),
            ("Q_accel:", 0.1), ("Q_gyro:", 0.01), ("R_uwb:", 0.05),
        ]
        for i, (label, default) in enumerate(ukf_params):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(-100, 100)
            spin.setValue(default)
            fusion_grid.addWidget(lbl, i // 2, (i % 2) * 2)
            fusion_grid.addWidget(spin, i // 2, (i % 2) * 2 + 1)
        layout.addWidget(fusion_group)
        self._dev_widgets.append(fusion_group)

        # ═══ 🔧 DEVELOPER: System Commands ═══
        sys_group = QGroupBox("🔧 System Commands")
        sys_layout = QHBoxLayout(sys_group)
        btn_device_reset = QPushButton("🔄 Device Reset")
        btn_uwb_reset = QPushButton("📡 UWB Reset")
        btn_factory = QPushButton("⚠ Factory Reset")
        btn_factory.setStyleSheet("""
            QPushButton { background: rgba(239,68,68,0.15); color: #EF4444;
                border: 1px solid #EF4444; border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #EF4444; color: #F8FAFC; }
        """)
        sys_layout.addWidget(btn_device_reset)
        sys_layout.addWidget(btn_uwb_reset)
        sys_layout.addWidget(btn_factory)
        layout.addWidget(sys_group)
        self._dev_widgets.append(sys_group)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # Apply initial mode
        self.set_developer_mode(self._is_developer)
