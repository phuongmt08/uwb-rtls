"""
UWB RTLS Studio — BLE Scan Popup (Frontend Only)
Popup 2: Quét BLE devices và cho user select/connect.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

# Demo data
DEMO_DEVICES = [
    {"name": "UWB-Tag-001",     "type": "TAG",    "rssi": -42, "mac": "AA:BB:CC:DD:01:01", "serial": "0x00010001"},
    {"name": "UWB-Tag-002",     "type": "TAG",    "rssi": -58, "mac": "AA:BB:CC:DD:01:02", "serial": "0x00010002"},
    {"name": "UWB-Anchor-A1",   "type": "ANCHOR", "rssi": -35, "mac": "AA:BB:CC:DD:02:01", "serial": "0x00020001"},
    {"name": "UWB-Anchor-A2",   "type": "ANCHOR", "rssi": -40, "mac": "AA:BB:CC:DD:02:02", "serial": "0x00020002"},
    {"name": "UWB-Anchor-A3",   "type": "ANCHOR", "rssi": -52, "mac": "AA:BB:CC:DD:02:03", "serial": "0x00020003"},
    {"name": "UWB-Anchor-A4",   "type": "ANCHOR", "rssi": -48, "mac": "AA:BB:CC:DD:02:04", "serial": "0x00020004"},
]


class ScanPopup(QDialog):
    """Popup Window 2: BLE Scan → Select → Connect."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("UWB RTLS Studio — BLE Scanner")
        self.setMinimumSize(720, 520)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._selected_row = -1
        self._build_ui()
        self._start_scan_demo()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet("""
            QFrame { background-color: #1E293B; border: 1px solid #334155; border-radius: 16px; }
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 24, 28, 24)

        # Header
        header = QHBoxLayout()
        title = QLabel("📡 BLE Device Scanner")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #22D3EE; background: transparent;")
        header.addWidget(title)
        header.addStretch()
        self._scan_badge = QLabel("● Scanning...")
        self._scan_badge.setStyleSheet("""
            color: #10B981; background: rgba(16,185,129,0.12);
            border-radius: 10px; padding: 4px 12px; font-weight: bold;
        """)
        header.addWidget(self._scan_badge)
        layout.addLayout(header)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar { background: #0A0F1E; border: none; border-radius: 2px; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #0E7490, stop:1 #22D3EE); border-radius: 2px; }
        """)
        layout.addWidget(self._progress)

        # Device count
        self._count_label = QLabel("Found: 0 devices")
        self._count_label.setStyleSheet("color: #94A3B8; font-size: 12px; background: transparent;")
        layout.addWidget(self._count_label)

        # Device table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Name", "Type", "RSSI", "MAC Address", "Serial"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #0A0F1E; color: #F8FAFC;
                border: 1px solid #334155; border-radius: 8px;
                gridline-color: #1E293B; font-size: 13px;
            }
            QTableWidget::item { padding: 8px; }
            QTableWidget::item:selected { background: rgba(34,211,238,0.15); color: #22D3EE; }
            QHeaderView::section {
                background: #1E293B; color: #22D3EE; border: 1px solid #334155;
                padding: 10px; font-weight: bold;
            }
        """)
        self._table.currentCellChanged.connect(self._on_selection)
        layout.addWidget(self._table)

        # Log area
        self._log = QLabel("")
        self._log.setStyleSheet("color: #64748B; font-size: 11px; background: transparent;")
        self._log.setWordWrap(True)
        layout.addWidget(self._log)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._btn_rescan = QPushButton("🔄 Rescan")
        self._btn_rescan.setFixedHeight(40)
        self._btn_rescan.setStyleSheet("""
            QPushButton { background: #1E293B; color: #94A3B8; border: 1px solid #334155;
                border-radius: 8px; font-weight: bold; padding: 0 20px; }
            QPushButton:hover { border-color: #22D3EE; color: #22D3EE; }
        """)

        self._btn_connect = QPushButton("⚡ Connect")
        self._btn_connect.setFixedHeight(40)
        self._btn_connect.setEnabled(False)
        self._btn_connect.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 8px; font-weight: bold; padding: 0 28px; font-size: 14px; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
            QPushButton:disabled { background: #1E293B; color: #475569; border-color: #334155; }
        """)

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setFixedHeight(40)
        self._btn_cancel.setStyleSheet("""
            QPushButton { background: transparent; color: #94A3B8; border: 1px solid #334155;
                border-radius: 8px; font-weight: bold; padding: 0 20px; }
            QPushButton:hover { border-color: #EF4444; color: #EF4444; }
        """)

        btn_row.addWidget(self._btn_rescan)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_connect)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

        self._btn_cancel.clicked.connect(self.reject)
        self._btn_connect.clicked.connect(self._demo_connect)
        self._btn_rescan.clicked.connect(self._start_scan_demo)

        outer.addWidget(card)

    def _on_selection(self, row, col, prev_row, prev_col):
        self._selected_row = row
        self._btn_connect.setEnabled(row >= 0)
        if row >= 0:
            name = self._table.item(row, 0).text()
            self._log.setText(f"Selected: {name}")

    def _start_scan_demo(self):
        """Simulate BLE scan with progressive device discovery."""
        self._table.setRowCount(0)
        self._scan_badge.setText("● Scanning...")
        self._scan_badge.setStyleSheet("""
            color: #10B981; background: rgba(16,185,129,0.12);
            border-radius: 10px; padding: 4px 12px; font-weight: bold;
        """)
        self._progress.setRange(0, 0)
        self._count_label.setText("Found: 0 devices")
        self._log.setText("Sending ble_scan_start (tag=51)...")
        self._btn_connect.setEnabled(False)

        # Add devices one by one with delay
        for i, dev in enumerate(DEMO_DEVICES):
            QTimer.singleShot(600 + i * 400, lambda d=dev: self._add_device(d))
        QTimer.singleShot(600 + len(DEMO_DEVICES) * 400 + 500, self._scan_complete)

    def _add_device(self, dev):
        row = self._table.rowCount()
        self._table.insertRow(row)

        items = [
            dev["name"], dev["type"], f"{dev['rssi']} dBm",
            dev["mac"], dev["serial"]
        ]
        for col, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            # Color code by type
            if col == 1:
                if dev["type"] == "TAG":
                    item.setForeground(QColor("#22D3EE"))
                else:
                    item.setForeground(QColor("#F59E0B"))
            # Color code RSSI
            if col == 2:
                rssi = dev["rssi"]
                if rssi > -50:
                    item.setForeground(QColor("#10B981"))
                elif rssi > -60:
                    item.setForeground(QColor("#F59E0B"))
                else:
                    item.setForeground(QColor("#EF4444"))
            self._table.setItem(row, col, item)

        self._count_label.setText(f"Found: {row + 1} devices")

    def _scan_complete(self):
        self._scan_badge.setText("● Scan Complete")
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._log.setText("Scan complete. Select a TAG device and click Connect.")

    def _demo_connect(self):
        if self._selected_row < 0:
            return
        name = self._table.item(self._selected_row, 0).text()
        self._log.setText(f"Connecting to {name}...")
        self._btn_connect.setEnabled(False)
        self._btn_connect.setText("Connecting...")
        QTimer.singleShot(1500, lambda: self._demo_connected(name))

    def _demo_connected(self, name):
        self._log.setText(f"✅ Connected to {name}!")
        self._btn_connect.setText("✅ Connected")
        QTimer.singleShot(800, self.accept)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
