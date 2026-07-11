"""
===============================================================================
  UWB RTLS Studio — Dongle Detection Popup (Real Backend)
===============================================================================
  File        : views/popups/dongle_popup.py
  Description : Popup 1 — Auto-detect NRF52840 dongle, connect serial.
                Pure View: chỉ hiển thị UI, logic nằm ở DongleViewModel.

  MVVM Role   : VIEW — pure UI, NO business logic.
===============================================================================
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, QTimer
from PyQt6.QtGui import QFont, QPainter, QColor, QPen


class PulseIndicator(QFrame):
    """Animated pulsing circle — visual feedback khi đang scan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(80, 80)
        self._pulse = 0.0
        self._color = QColor("#22D3EE")
        self._icon = "⚡"
        self._anim = QPropertyAnimation(self, b"pulse")
        self._anim.setDuration(1500)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.start()

    def get_pulse(self):
        return self._pulse

    def set_pulse(self, v):
        self._pulse = v
        self.update()

    pulse = pyqtProperty(float, get_pulse, set_pulse)

    def set_color(self, color: QColor):
        self._color = color
        self.update()

    def set_icon(self, icon: str):
        self._icon = icon
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
        p.drawEllipse(int(cx - radius), int(cy - radius),
                      int(radius * 2), int(radius * 2))

        # Inner solid circle
        p.setPen(Qt.PenStyle.NoPen)
        inner_c = QColor(self._color)
        inner_c.setAlpha(200)
        p.setBrush(inner_c)
        p.drawEllipse(int(cx - 14), int(cy - 14), 28, 28)

        # Icon text
        p.setPen(QColor("#0F172A"))
        f = QFont("Segoe UI", 12, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._icon)
        p.end()


class DonglePopup(QDialog):
    """Popup 1: Dongle Detection & Connection.

    Bindings:
        DongleViewModel.status_changed    → _status label
        DongleViewModel.port_info_changed → _port_info label
        DongleViewModel.dongle_detected   → green pulse
        DongleViewModel.dongle_ready      → auto accept()
        DongleViewModel.dongle_error      → show error + retry
        DongleViewModel.progress_*        → progress bar
    """

    def __init__(self, viewmodel, parent=None):
        super().__init__(parent)
        self._vm = viewmodel
        self.setWindowTitle("UWB RTLS Studio — Dongle Detection")
        self.setFixedSize(520, 380)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build_ui()
        self._bind_viewmodel()

        # Auto start detection
        QTimer.singleShot(300, self._vm.start_detection)

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
        self._subtitle = QLabel("Scanning USB ports for UWB-RTLS Dongle ...")
        self._subtitle.setStyleSheet("color: #94A3B8; font-size: 13px; background: transparent;")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

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

        self._btn_retry = QPushButton("Retry")
        self._btn_retry.setFixedHeight(38)
        self._btn_retry.setVisible(False)
        self._btn_retry.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setFixedHeight(38)
        self._btn_cancel.setStyleSheet("""
            QPushButton { background: transparent; color: #94A3B8; border: 1px solid #334155;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { border-color: #EF4444; color: #EF4444; }
        """)

        btn_row.addWidget(self._btn_retry)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

        # Button connections
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_retry.clicked.connect(self._on_retry)

        outer.addWidget(card)

    def _bind_viewmodel(self):
        """Connect ViewModel signals → View slots."""
        self._vm.status_changed.connect(self._on_status)
        self._vm.port_info_changed.connect(self._port_info.setText)
        self._vm.port_probing_changed.connect(self._on_port_probing)
        self._vm.dongle_detected.connect(self._on_detected)
        self._vm.dongle_ready.connect(self._on_ready)
        self._vm.dongle_error.connect(self._on_error)
        self._vm.progress_indeterminate.connect(
            lambda: self._progress.setRange(0, 0)
        )
        self._vm.progress_value.connect(self._on_progress)

    # ── View Slots ───────────────────────────────────────────────────

    def _on_status(self, text: str):
        self._status.setText(text)
        if "✅" in text:
            self._status.setStyleSheet("color: #10B981; background: transparent;")
        elif "❌" in text or "⚠️" in text:
            self._status.setStyleSheet("color: #EF4444; background: transparent;")
        else:
            self._status.setStyleSheet("color: #F59E0B; background: transparent;")

    def _on_detected(self, port: str):
        self._pulse.set_color(QColor("#10B981"))
        self._pulse.set_icon("✓")
        self._subtitle.setText(f"Dongle found on {port}, verifying...")

    def _on_port_probing(self, port: str):
        self._subtitle.setText(f"Probing {port}...")

    def _on_ready(self, info: dict):
        self._subtitle.setText("Dongle ready! Opening scanner...")
        self._btn_retry.setVisible(False)
        # Auto close sau 800ms
        QTimer.singleShot(800, self.accept)

    def _on_error(self, msg: str):
        self._status.setStyleSheet("color: #EF4444; background: transparent;")
        self._pulse.set_color(QColor("#EF4444"))
        self._pulse.set_icon("✕")
        self._btn_retry.setVisible(True)
        self._subtitle.setText(msg)

    def _on_progress(self, value: int):
        self._progress.setRange(0, 100)
        self._progress.setValue(value)

    def _on_retry(self):
        self._btn_retry.setVisible(False)
        self._pulse.set_color(QColor("#22D3EE"))
        self._pulse.set_icon("⚡")
        self._vm.retry()

    def _on_cancel(self):
        self.reject()

    def reject(self):
        self._vm.cancel()
        super().reject()

    # ── Drag support for frameless window ────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
