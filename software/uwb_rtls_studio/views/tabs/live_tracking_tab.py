"""
UWB RTLS Studio — Live Tracking Tab (Frontend Only)
Tab 2: Bản đồ 2D tracking realtime với anchors + tag + trajectory.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QFrame, QSlider, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QFont, QPainter, QColor, QPen, QBrush, QLinearGradient,
    QRadialGradient, QPainterPath
)
import math
import random


class TrackingCanvas(QWidget):
    """Custom 2D map widget with anchors, tag, trajectory, and grid."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 400)
        self._anchors = [
            {"id": "A1", "x": 0.0, "y": 0.0},
            {"id": "A2", "x": 5.0, "y": 0.0},
            {"id": "A3", "x": 0.0, "y": 4.0},
            {"id": "A4", "x": 5.0, "y": 4.0},
        ]
        self._tag_pos = QPointF(2.5, 2.0)
        self._trajectory = []
        self._grid_spacing = 1.0  # meters
        self._world_margin = 0.8  # extra margin in meters
        self._t = 0.0

    def set_tag_position(self, x, y):
        self._trajectory.append(QPointF(self._tag_pos))
        if len(self._trajectory) > 200:
            self._trajectory = self._trajectory[-200:]
        self._tag_pos = QPointF(x, y)
        self.update()

    def _world_to_screen(self, wx, wy):
        """Convert world coordinates (meters) to widget pixels."""
        w, h = self.width(), self.height()
        margin = 60
        x_min = min(a["x"] for a in self._anchors) - self._world_margin
        x_max = max(a["x"] for a in self._anchors) + self._world_margin
        y_min = min(a["y"] for a in self._anchors) - self._world_margin
        y_max = max(a["y"] for a in self._anchors) + self._world_margin
        sx = margin + (wx - x_min) / (x_max - x_min) * (w - 2 * margin)
        sy = h - margin - (wy - y_min) / (y_max - y_min) * (h - 2 * margin)
        return sx, sy

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0, QColor("#0A0F1E"))
        bg_grad.setColorAt(1, QColor("#0F172A"))
        p.fillRect(0, 0, w, h, bg_grad)

        # Grid
        p.setPen(QPen(QColor(51, 65, 85, 40), 1, Qt.PenStyle.DotLine))
        x_min = min(a["x"] for a in self._anchors) - self._world_margin
        x_max = max(a["x"] for a in self._anchors) + self._world_margin
        y_min = min(a["y"] for a in self._anchors) - self._world_margin
        y_max = max(a["y"] for a in self._anchors) + self._world_margin

        gx = math.floor(x_min)
        while gx <= math.ceil(x_max):
            sx, _ = self._world_to_screen(gx, 0)
            p.drawLine(int(sx), 0, int(sx), h)
            gx += self._grid_spacing
        gy = math.floor(y_min)
        while gy <= math.ceil(y_max):
            _, sy = self._world_to_screen(0, gy)
            p.drawLine(0, int(sy), w, int(sy))
            gy += self._grid_spacing

        # Axis labels
        p.setPen(QColor("#475569"))
        p.setFont(QFont("Segoe UI", 9))
        gx = math.ceil(x_min)
        while gx <= math.floor(x_max):
            sx, sy0 = self._world_to_screen(gx, y_min)
            p.drawText(int(sx) - 10, int(sy0) + 16, f"{gx:.0f}m")
            gx += 1
        gy = math.ceil(y_min)
        while gy <= math.floor(y_max):
            sx0, sy = self._world_to_screen(x_min, gy)
            p.drawText(int(sx0) - 5, int(sy) + 4, f"{gy:.0f}m")
            gy += 1

        # Trajectory trail (fading)
        for i in range(1, len(self._trajectory)):
            alpha = int(30 + 150 * i / len(self._trajectory))
            trail_c = QColor(34, 211, 238, alpha)
            p.setPen(QPen(trail_c, 2))
            x1, y1 = self._world_to_screen(self._trajectory[i-1].x(), self._trajectory[i-1].y())
            x2, y2 = self._world_to_screen(self._trajectory[i].x(), self._trajectory[i].y())
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Trail dots
        for i, pt in enumerate(self._trajectory[-30:]):
            alpha = int(50 + 180 * i / 30)
            size = 2 + 3 * i / 30
            sx, sy = self._world_to_screen(pt.x(), pt.y())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(34, 211, 238, alpha))
            p.drawEllipse(QPointF(sx, sy), size, size)

        # Anchors
        for anchor in self._anchors:
            ax, ay = self._world_to_screen(anchor["x"], anchor["y"])

            # Anchor glow
            glow = QRadialGradient(ax, ay, 25)
            glow.setColorAt(0, QColor(245, 158, 11, 60))
            glow.setColorAt(1, QColor(245, 158, 11, 0))
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(ax, ay), 25, 25)

            # Anchor diamond
            p.setBrush(QColor("#F59E0B"))
            p.setPen(QPen(QColor("#FCD34D"), 2))
            path = QPainterPath()
            s = 10
            path.moveTo(ax, ay - s)
            path.lineTo(ax + s, ay)
            path.lineTo(ax, ay + s)
            path.lineTo(ax - s, ay)
            path.closeSubpath()
            p.drawPath(path)

            # Anchor label
            p.setPen(QColor("#FCD34D"))
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            p.drawText(int(ax) - 10, int(ay) - 18, anchor["id"])

        # Tag position
        tx, ty = self._world_to_screen(self._tag_pos.x(), self._tag_pos.y())

        # Tag outer glow
        tag_glow = QRadialGradient(tx, ty, 35)
        tag_glow.setColorAt(0, QColor(34, 211, 238, 80))
        tag_glow.setColorAt(0.5, QColor(34, 211, 238, 20))
        tag_glow.setColorAt(1, QColor(34, 211, 238, 0))
        p.setBrush(tag_glow)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(tx, ty), 35, 35)

        # Tag dot
        p.setBrush(QColor("#22D3EE"))
        p.setPen(QPen(QColor("#67E8F9"), 2))
        p.drawEllipse(QPointF(tx, ty), 8, 8)

        # Tag label
        p.setPen(QColor("#22D3EE"))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.drawText(int(tx) + 14, int(ty) - 8, "TAG")
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor("#94A3B8"))
        p.drawText(int(tx) + 14, int(ty) + 6,
                   f"({self._tag_pos.x():.2f}, {self._tag_pos.y():.2f})")

        p.end()


class LiveTrackingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_ranging = False
        self._build_ui()
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._demo_update)

    def _build_ui(self):
        main = QHBoxLayout(self)
        main.setSpacing(14)
        main.setContentsMargins(12, 12, 12, 12)

        # ═══ LEFT: Canvas (65%) ═══
        left = QVBoxLayout()
        self._canvas = TrackingCanvas()
        self._canvas.setStyleSheet("border: 1px solid #334155; border-radius: 8px;")
        left.addWidget(self._canvas)

        # Controls under canvas
        ctrl_row = QHBoxLayout()
        self._btn_start = QPushButton("▶  Start Ranging")
        self._btn_start.setFixedHeight(40)
        self._btn_start.setStyleSheet("""
            QPushButton { background: #059669; color: #F8FAFC; border: 1px solid #10B981;
                border-radius: 8px; font-weight: bold; font-size: 14px; padding: 0 24px; }
            QPushButton:hover { background: #10B981; }
        """)
        self._btn_stop = QPushButton("■  Stop Ranging")
        self._btn_stop.setFixedHeight(40)
        self._btn_stop.setEnabled(False)
        self._btn_stop.setStyleSheet("""
            QPushButton { background: rgba(239,68,68,0.15); color: #EF4444;
                border: 1px solid #EF4444; border-radius: 8px; font-weight: bold;
                font-size: 14px; padding: 0 24px; }
            QPushButton:hover { background: #EF4444; color: #F8FAFC; }
            QPushButton:disabled { background: #1E293B; color: #475569; border-color: #334155; }
        """)
        self._btn_clear = QPushButton("🗑 Clear Trail")
        self._btn_clear.setFixedHeight(40)

        ctrl_row.addWidget(self._btn_start)
        ctrl_row.addWidget(self._btn_stop)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self._btn_clear)
        left.addLayout(ctrl_row)

        self._btn_start.clicked.connect(self._start_ranging)
        self._btn_stop.clicked.connect(self._stop_ranging)
        self._btn_clear.clicked.connect(lambda: setattr(self._canvas, '_trajectory', []))

        main.addLayout(left, 65)

        # ═══ RIGHT: Telemetry panel (35%) ═══
        right = QVBoxLayout()
        right.setSpacing(12)

        # Position
        pos_group = QGroupBox("📍 Live Position")
        pos_layout = QVBoxLayout(pos_group)
        self._lbl_x = QLabel("X: 2.50 m")
        self._lbl_y = QLabel("Y: 2.00 m")
        self._lbl_z = QLabel("Z: 0.00 m")
        for lbl, color in [(self._lbl_x, "#22D3EE"), (self._lbl_y, "#10B981"), (self._lbl_z, "#F59E0B")]:
            lbl.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {color}; background: transparent;")
            pos_layout.addWidget(lbl)
        right.addWidget(pos_group)

        # Quality
        qual_group = QGroupBox("📊 Quality Metrics")
        qual_grid = QGridLayout(qual_group)
        qual_data = [
            ("RMS Error:", "0.045 m", "#10B981"),
            ("Update Rate:", "10.2 Hz", "#22D3EE"),
            ("Success Rate:", "98.5%", "#10B981"),
            ("Avg RSSI:", "-45 dBm", "#F59E0B"),
        ]
        self._qual_values = {}
        for i, (label, value, color) in enumerate(qual_data):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 14px;")
            qual_grid.addWidget(lbl, i, 0)
            qual_grid.addWidget(val, i, 1)
            self._qual_values[label] = val
        right.addWidget(qual_group)

        # Anchor Distances
        dist_group = QGroupBox("📏 Anchor Distances")
        dist_grid = QGridLayout(dist_group)
        self._dist_values = {}
        anchors = ["A1", "A2", "A3", "A4"]
        for i, aid in enumerate(anchors):
            lbl = QLabel(f"{aid}:")
            lbl.setStyleSheet("color: #F59E0B; font-weight: bold;")
            val = QLabel("— cm")
            val.setStyleSheet("color: #F8FAFC;")
            bar = QProgressBar()
            bar.setRange(0, 500)
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setStyleSheet("""
                QProgressBar { background: #0A0F1E; border: none; border-radius: 4px; }
                QProgressBar::chunk { background: #22D3EE; border-radius: 4px; }
            """)
            dist_grid.addWidget(lbl, i, 0)
            dist_grid.addWidget(val, i, 1)
            dist_grid.addWidget(bar, i, 2)
            self._dist_values[aid] = (val, bar)
        right.addWidget(dist_group)

        right.addStretch()
        main.addLayout(right, 35)

    def _start_ranging(self):
        self._is_ranging = True
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._demo_timer.start(100)  # 10 Hz

    def _stop_ranging(self):
        self._is_ranging = False
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._demo_timer.stop()

    def _demo_update(self):
        """Simulate position streaming with smooth motion."""
        if not hasattr(self, '_demo_t'):
            self._demo_t = 0.0
        self._demo_t += 0.05

        # Lissajous-like pattern
        x = 2.5 + 1.8 * math.sin(self._demo_t * 0.7)
        y = 2.0 + 1.2 * math.cos(self._demo_t * 0.5)
        x += random.gauss(0, 0.02)
        y += random.gauss(0, 0.02)

        self._canvas.set_tag_position(x, y)
        self._lbl_x.setText(f"X: {x:.2f} m")
        self._lbl_y.setText(f"Y: {y:.2f} m")
        self._lbl_z.setText(f"Z: 0.00 m")

        # Update distances
        for aid, anchor in zip(["A1", "A2", "A3", "A4"],
                                [(0,0), (5,0), (0,4), (5,4)]):
            d = math.sqrt((x - anchor[0])**2 + (y - anchor[1])**2)
            d_cm = d * 100
            val, bar = self._dist_values[aid]
            val.setText(f"{d_cm:.1f} cm")
            bar.setValue(min(int(d_cm), 500))

        # Update quality
        rms = abs(random.gauss(0.04, 0.01))
        self._qual_values["RMS Error:"].setText(f"{rms:.3f} m")
