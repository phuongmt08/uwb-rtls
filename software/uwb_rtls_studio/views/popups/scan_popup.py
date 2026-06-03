"""
===============================================================================
  UWB RTLS Studio — BLE Scan Popup (Real Backend)
===============================================================================
  File        : views/popups/scan_popup.py
  Description : Popup 2 — BLE scan, select device, connect.
                Pure View: UI chỉ hiển thị data từ ScanViewModel.

  MVVM Role   : VIEW — pure UI, NO business logic.
===============================================================================
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QProgressBar, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor


class ScanPopup(QDialog):
    """Popup 2: BLE Scan → Select → Connect.

    Bindings:
        ScanViewModel.scan_started         → indeterminate progress
        ScanViewModel.scan_stopped         → progress 100%
        ScanViewModel.device_list_updated  → refresh table
        ScanViewModel.device_connecting    → disable button
        ScanViewModel.device_connected     → auto accept()
        ScanViewModel.connection_failed    → show error
        ScanViewModel.log_message          → log label
    """

    def __init__(self, viewmodel, parent=None):
        super().__init__(parent)
        self._vm = viewmodel
        self._selected_mac: str = ""

        self.setWindowTitle("UWB RTLS Studio — BLE Scanner")
        self.setMinimumSize(720, 520)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build_ui()
        self._bind_viewmodel()

        # Auto start scan
        QTimer.singleShot(300, self._vm.start_scan)

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

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
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
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "RSSI", "MAC Address", "Serial"])
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

        # Log label
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

        # Button connections
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_connect.clicked.connect(self._on_connect)
        self._btn_rescan.clicked.connect(self._on_rescan)

        outer.addWidget(card)

    def _bind_viewmodel(self):
        """Connect ViewModel signals → View slots."""
        self._vm.scan_started.connect(self._on_scan_started)
        self._vm.scan_stopped.connect(self._on_scan_stopped)
        self._vm.device_list_updated.connect(self._on_device_list)
        self._vm.device_connecting.connect(self._on_connecting)
        self._vm.device_connected.connect(self._on_connected)
        self._vm.connection_failed.connect(self._on_connect_failed)
        self._vm.log_message.connect(self._log.setText)
        self._vm.dongle_disconnected.connect(self._on_dongle_disconnected)

    # ── View Slots ───────────────────────────────────────────────────

    def _on_selection(self, row, col, prev_row, prev_col):
        """User chọn 1 row trong table."""
        if row >= 0 and self._table.item(row, 2):
            self._selected_mac = self._table.item(row, 2).text()
            name = self._table.item(row, 0).text()
            self._btn_connect.setEnabled(True)
            self._log.setText(f"Selected: {name} ({self._selected_mac})")
        else:
            self._selected_mac = ""
            self._btn_connect.setEnabled(False)

    def _on_scan_started(self):
        self._scan_badge.setText("● Scanning...")
        self._scan_badge.setStyleSheet("""
            color: #10B981; background: rgba(16,185,129,0.12);
            border-radius: 10px; padding: 4px 12px; font-weight: bold;
        """)
        self._progress.setRange(0, 0)

    def _on_scan_stopped(self):
        self._scan_badge.setText("● Stopped")
        self._scan_badge.setStyleSheet("""
            color: #94A3B8; background: rgba(148,163,184,0.12);
            border-radius: 10px; padding: 4px 12px; font-weight: bold;
        """)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)

    def _on_device_list(self, devices: list):
        """Refresh toàn bộ table với device list mới."""
        # Lưu lại selected MAC
        prev_selected = self._selected_mac

        self._table.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            items_data = [
                dev.get("name", ""),
                f"{dev.get('rssi', 0)} dBm",
                dev.get("mac", ""),
                dev.get("serial", ""),
            ]
            for col, text in enumerate(items_data):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                # Color RSSI
                if col == 1:
                    rssi = dev.get("rssi", -100)
                    if rssi > -50:
                        item.setForeground(QColor("#10B981"))
                    elif rssi > -70:
                        item.setForeground(QColor("#F59E0B"))
                    else:
                        item.setForeground(QColor("#EF4444"))

                self._table.setItem(row, col, item)

            # Restore selection
            if dev.get("mac") == prev_selected:
                self._table.selectRow(row)

        self._count_label.setText(f"Found: {len(devices)} device(s)")

    def _on_connecting(self, mac: str):
        self._btn_connect.setEnabled(False)
        self._btn_connect.setText("⏳ Connecting...")
        self._btn_rescan.setEnabled(False)

    def _on_connected(self, info: dict):
        self._btn_connect.setText("✅ Connected")
        self._log.setText("✅ Connected! Opening main window...")
        QTimer.singleShot(800, self.accept)

    def _on_connect_failed(self, msg: str):
        self._btn_connect.setEnabled(True)
        self._btn_connect.setText("⚡ Connect")
        self._btn_rescan.setEnabled(True)
        self._log.setText(f"❌ {msg}")

    def _on_connect(self):
        if self._selected_mac:
            self._vm.connect_device(self._selected_mac)

    def _on_rescan(self):
        self._vm.start_scan()

    def _on_cancel(self):
        self._vm.cleanup()
        self.reject()

    def _on_dongle_disconnected(self, msg: str):
        QMessageBox.warning(self, "Disconnected", msg)
        self._vm.cleanup()
        self.done(2)  # Return custom code for disconnect

    # ── Drag support ─────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
