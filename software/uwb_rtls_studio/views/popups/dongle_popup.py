"""
UWB RTLS Studio — Dongle Detection Popup (Frontend Only)
Popup 1: Tự động quét tìm USB Dongle NRF52840.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QFont, QPainter, QColor, QPen
import math


class PulseIndicator(QFrame):
    """Animated pulsing circle indicator."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(80, 80)
        self._pulse = 0.0
        self._color = QColor("#22D3EE")
        self._anim = QPropertyAnimation(self, b"pulse")
        self._anim.setDuration(1500)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.start()

    def get_pulse(self): return self._pulse
    def set_pulse(self, v):
        self._pulse = v
        self.update()
    pulse = pyqtProperty(float, get_pulse, set_pulse)

    def set_color(self, color: QColor):
        self._color = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        # Outer pulse ring
        alpha = int(120 * (1 - self._pulse))
        radius = 20 + 18 * self._pulse
        c = QColor(self._color)
        c.setAlpha(alpha)
        p.setPen(QPen(c, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2))
        # Inner solid circle
        p.setPen(Qt.PenStyle.NoPen)
        inner_c = QColor(self._color)
        inner_c.setAlpha(200)
        p.setBrush(inner_c)
        p.drawEllipse(int(cx - 14), int(cy - 14), 28, 28)
        # USB icon text
        p.setPen(QColor("#0F172A"))
        f = QFont("Segoe UI", 12, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "⚡")
        p.end()


class DonglePopup(QDialog):
    """Popup Window 1: Dongle Detection & Connection."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("UWB RTLS Studio — Dongle Detection")
        self.setFixedSize(520, 380)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._build_ui()
        self._start_demo_sequence()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Main card
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 16px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 28, 32, 28)

        # Title
        title = QLabel("🔵 Dongle Detection")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #22D3EE; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Subtitle
        sub = QLabel("Scanning USB ports for NRF52840 Central Dongle...")
        sub.setStyleSheet("color: #94A3B8; font-size: 13px; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        layout.addWidget(sub)
        self._subtitle = sub

        # Pulse indicator
        pulse_row = QHBoxLayout()
        pulse_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pulse = PulseIndicator()
        pulse_row.addWidget(self._pulse)
        layout.addLayout(pulse_row)

        # Status label
        self._status = QLabel("Searching COM ports...")
        self._status.setFont(QFont("Segoe UI", 13))
        self._status.setStyleSheet("color: #F59E0B; background: transparent;")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar { background: #0A0F1E; border: none; border-radius: 3px; }
            QProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0E7490, stop:1 #22D3EE);
                border-radius: 3px;
            }
        """)
        layout.addWidget(self._progress)

        # Port info
        self._port_info = QLabel("")
        self._port_info.setStyleSheet("color: #64748B; font-size: 11px; background: transparent;")
        self._port_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._port_info)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._btn_retry = QPushButton("🔄 Retry")
        self._btn_retry.setFixedHeight(38)
        self._btn_retry.setVisible(False)
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setFixedHeight(38)
        self._btn_cancel.setStyleSheet("""
            QPushButton { background: transparent; color: #94A3B8; border: 1px solid #334155;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { border-color: #EF4444; color: #EF4444; }
        """)
        self._btn_retry.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        btn_row.addWidget(self._btn_retry)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

        self._btn_cancel.clicked.connect(self.reject)
        self._btn_retry.clicked.connect(self._start_demo_sequence)

        outer.addWidget(card)

    def _start_demo_sequence(self):
        """Demo animation sequence simulating dongle detection."""
        self._btn_retry.setVisible(False)
        self._progress.setRange(0, 0)
        self._status.setText("Searching COM ports...")
        self._status.setStyleSheet("color: #F59E0B; background: transparent;")
        self._port_info.setText("")
        self._pulse.set_color(QColor("#22D3EE"))
        self._subtitle.setText("Scanning USB ports for NRF52840 Central Dongle...")

        # Step 1: Found dongle (after 2s)
        QTimer.singleShot(2000, self._demo_found)

    def _demo_found(self):
        self._status.setText("✅ Detected NRF52840 Dongle!")
        self._status.setStyleSheet("color: #10B981; background: transparent;")
        self._port_info.setText("COM5  |  VID: 0x1915  |  PID: 0x520F")
        self._progress.setRange(0, 100)
        self._progress.setValue(50)
        self._pulse.set_color(QColor("#10B981"))
        self._subtitle.setText("Connecting to dongle...")
        QTimer.singleShot(1500, self._demo_connected)

    def _demo_connected(self):
        self._status.setText("✅ Connected! Opening scanner...")
        self._progress.setValue(100)
        self._subtitle.setText("Dongle Central NRF52840 ready")
        self._port_info.setText("COM5  |  FW: v2.1.3  |  SN: 0x12345678")
        # After 1s, would transition to ScanPopup
        QTimer.singleShot(1200, self.accept)

    def mousePressEvent(self, event):
        """Allow dragging frameless window."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
