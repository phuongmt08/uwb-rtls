"""
UWB RTLS Studio — Live Tracking Tab
Tab 2: Real-time 2D position tracking matching dashboard.py design.

Layout (mirroring dashboard.py):
  - LEFT:  Canvas header ("Real-time Position Tracking" + OUT OF ZONE warning)
           + ModernPositionCanvas + Start/Stop/Clear controls
  - RIGHT: Scrollable panel with CollapsibleCards:
           • Live Position  (COORDINATES, MOTION, RANGING, QUALITY)
           • Statistics      (Frames, FPS, Uptime)
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtGui import (
    QFont, QPainter, QColor, QPen, QBrush, QLinearGradient,
    QRadialGradient, QPainterPath
)
import time
import math


# ═══════════════════════════════════════════════════════════════════════
#  ModernPositionCanvas — ported from dashboard.py (PyQt5 → PyQt6)
# ═══════════════════════════════════════════════════════════════════════
class ModernPositionCanvas(QWidget):
    """Modern 2D position canvas with zoom/pan/auto-fit.
    Features:
      - Mouse wheel zoom (centered on cursor)
      - Left-click drag to pan
      - Right-click drag rectangle to zoom into area
      - Double-click to auto-fit / reset view
      - Auto-fit when anchor layout changes
    """

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.position = {'x': 0, 'y': 0, 'z': 0, 'yaw': 0, 'error': 0}
        self.anchors = [
            {'x': 0.0, 'y': 0.0, 'label': 'A0'},
            {'x': 9.76, 'y': 0.0, 'label': 'A1'},
            {'x': 9.76, 'y': 9.76, 'label': 'A2'},
            {'x': 0.0, 'y': 9.76, 'label': 'A3'},
        ]
        self.history = []
        self.max_history = 30

        # Throttle updates
        self.last_update_time = 0
        self.update_interval = 0.05

        # ── View transform state ──
        self._view_cx = 4.88    # World center X
        self._view_cy = 4.88    # World center Y
        self._view_range = 14.0 # Visible world range (meters across the smaller axis)
        self._margin = 50       # Pixel margin for axis labels

        # ── Interaction state ──
        self._dragging = False
        self._drag_start = None     # Screen coords at drag start
        self._drag_view_cx = 0.0
        self._drag_view_cy = 0.0
        self._rect_zoom = False     # Right-click rectangle zoom
        self._rect_start = None
        self._rect_end = None

        # Auto-fit on first show
        QTimer.singleShot(50, self.auto_fit)

    # ── Public API ───────────────────────────────────────────────────
    def update_position(self, position):
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval:
            return
        self.last_update_time = current_time
        self.position = position
        self.history.append((position['x'], position['y']))
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.update()

    def set_anchors(self, anchors):
        self.anchors = anchors
        self.auto_fit()

    def clear_trail(self):
        self.history.clear()
        self.update()

    def auto_fit(self):
        """Auto-fit view so origin (0,0) sits at the bottom-left corner with 1m padding."""
        pts_x = [a['x'] for a in self.anchors] + [self.position['x']]
        pts_y = [a['y'] for a in self.anchors] + [self.position['y']]
        if not pts_x:
            return

        max_x = max(pts_x)
        max_y = max(pts_y)
        padding = 1.0  # 1m breathing room

        # Determine how much world space we need to show
        need_x = max_x + 2 * padding  # from -padding to max_x + padding
        need_y = max_y + 2 * padding

        m = self._margin
        w = max(self.width() - 2 * m, 1)
        h = max(self.height() - 2 * m, 1)

        # _view_range maps to min(w,h) pixels, so figure out
        # how much _view_range is needed to fit both axes
        self._view_range = max(need_x * min(w, h) / w,
                               need_y * min(w, h) / h,
                               2.0)

        # Place center so that the left/bottom visible edge = -padding
        scale = min(w, h) / self._view_range
        self._view_cx = -padding + (w / scale) / 2.0
        self._view_cy = -padding + (h / scale) / 2.0
        self.update()

    # ── Coordinate transforms ────────────────────────────────────────
    def _world_to_screen(self, wx, wy):
        m = self._margin
        w = self.width() - 2 * m
        h = self.height() - 2 * m
        half = self._view_range / 2.0
        # Aspect-ratio-correct scale
        scale = min(w, h) / self._view_range if self._view_range > 0 else 50
        sx = m + (w / 2.0) + (wx - self._view_cx) * scale
        sy = m + (h / 2.0) - (wy - self._view_cy) * scale
        return int(sx), int(sy)

    def _screen_to_world(self, sx, sy):
        m = self._margin
        w = self.width() - 2 * m
        h = self.height() - 2 * m
        scale = min(w, h) / self._view_range if self._view_range > 0 else 50
        wx = self._view_cx + (sx - m - w / 2.0) / scale
        wy = self._view_cy - (sy - m - h / 2.0) / scale
        return wx, wy

    # ── Mouse events ─────────────────────────────────────────────────
    def wheelEvent(self, event):
        """Zoom in/out centered on cursor."""
        delta = event.angleDelta().y()
        factor = 0.85 if delta > 0 else 1.18
        # Zoom toward cursor position
        pos = event.position()
        wx, wy = self._screen_to_world(pos.x(), pos.y())
        self._view_range *= factor
        self._view_range = max(0.5, min(self._view_range, 200.0))
        # Adjust center so cursor stays at same world point
        wx2, wy2 = self._screen_to_world(pos.x(), pos.y())
        self._view_cx -= (wx2 - wx)
        self._view_cy -= (wy2 - wy)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = event.position()
            self._drag_view_cx = self._view_cx
            self._drag_view_cy = self._view_cy
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            self._rect_zoom = True
            self._rect_start = event.position()
            self._rect_end = event.position()

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start:
            pos = event.position()
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            m = self._margin
            w = self.width() - 2 * m
            h = self.height() - 2 * m
            scale = min(w, h) / self._view_range if self._view_range > 0 else 50
            self._view_cx = self._drag_view_cx - dx / scale
            self._view_cy = self._drag_view_cy + dy / scale
            self.update()
        elif self._rect_zoom and self._rect_start:
            self._rect_end = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif event.button() == Qt.MouseButton.RightButton and self._rect_zoom:
            self._rect_zoom = False
            if self._rect_start and self._rect_end:
                x1, y1 = self._rect_start.x(), self._rect_start.y()
                x2, y2 = self._rect_end.x(), self._rect_end.y()
                if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
                    w1x, w1y = self._screen_to_world(min(x1, x2), max(y1, y2))
                    w2x, w2y = self._screen_to_world(max(x1, x2), min(y1, y2))
                    self._view_cx = (w1x + w2x) / 2.0
                    self._view_cy = (w1y + w2y) / 2.0
                    self._view_range = max(w2x - w1x, w2y - w1y) * 1.1
            self._rect_start = self._rect_end = None
            self.update()

    def mouseDoubleClickEvent(self, event):
        """Double-click to reset/auto-fit view."""
        self.auto_fit()

    def resizeEvent(self, event):
        """Keep view correct when widget resizes. Re-aligns origin to bottom-left."""
        super().resizeEvent(event)
        
        # When the window resizes, we recalculate the view bounds so that the
        # origin (0,0) remains exactly pinned near the bottom-left corner, 
        # instead of letting the extra width push it into the negatives.
        if not self._dragging and not self._rect_zoom:
            self.auto_fit()
        self.update()

    # ── Paint ────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        painter.fillRect(self.rect(), QColor(30, 41, 59))

        m = self._margin
        w = self.width() - 2 * m
        h = self.height() - 2 * m
        if w <= 0 or h <= 0:
            return

        to = self._world_to_screen  # alias

        # Visible world bounds
        vx1, vy1 = self._screen_to_world(m, self.height() - m)  # bottom-left
        vx2, vy2 = self._screen_to_world(m + w, m)              # top-right

        # ── Grid ──
        painter.setPen(QPen(QColor(51, 65, 85, 80), 1, Qt.PenStyle.DotLine))
        # Choose grid step dynamically
        raw_step = (vx2 - vx1) / 10.0
        step = max(1.0, round(raw_step))
        if raw_step < 0.5:
            step = 0.5

        gx = math.floor(vx1 / step) * step
        while gx <= vx2:
            sx, _ = to(gx, 0)
            painter.drawLine(sx, m, sx, self.height() - m)
            gx += step
        gy = math.floor(vy1 / step) * step
        while gy <= vy2:
            _, sy = to(0, gy)
            painter.drawLine(m, sy, m + w, sy)
            gy += step

        # ── Axis labels ──
        painter.setFont(QFont('Segoe UI', 9))
        painter.setPen(QColor(148, 163, 184))
        gx = math.floor(vx1 / step) * step
        while gx <= vx2:
            sx, _ = to(gx, 0)
            painter.drawText(sx - 12, self.height() - m + 16, f"{gx:.0f}m")
            gx += step
        gy = math.floor(vy1 / step) * step
        while gy <= vy2:
            _, sy = to(0, gy)
            painter.drawText(4, sy + 4, f"{gy:.0f}m")
            gy += step

        # ── History trail ──
        if len(self.history) > 1:
            painter.setPen(QPen(QColor(96, 165, 250, 120), 2))
            for i in range(len(self.history) - 1):
                x1, y1 = to(self.history[i][0], self.history[i][1])
                x2, y2 = to(self.history[i + 1][0], self.history[i + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        # ── Connection lines ──
        px, py = to(self.position['x'], self.position['y'])
        for anchor in self.anchors:
            ax, ay = to(anchor['x'], anchor['y'])
            painter.setPen(QPen(QColor(99, 102, 241, 40), 1, Qt.PenStyle.DashLine))
            painter.drawLine(px, py, ax, ay)

        # ── Anchors ──
        painter.setFont(QFont('Segoe UI', 10, QFont.Weight.Bold))
        for anchor in self.anchors:
            cx, cy = to(anchor['x'], anchor['y'])
            painter.setPen(QPen(QColor(99, 102, 241), 2))
            painter.setBrush(QColor(30, 41, 59))
            painter.drawEllipse(cx - 10, cy - 10, 20, 20)
            painter.setBrush(QColor(99, 102, 241))
            painter.drawEllipse(cx - 4, cy - 4, 8, 8)

            label = anchor.get('label', anchor.get('id', '?'))
            painter.setPen(QColor(226, 232, 240))
            painter.drawText(cx + 16, cy - 10, label)
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(cx + 16, cy + 4, f"({anchor['x']:.1f}, {anchor['y']:.1f})")
            painter.setFont(QFont('Segoe UI', 10, QFont.Weight.Bold))

        # ── Error circle ──
        scale_px = min(w, h) / self._view_range if self._view_range > 0 else 50
        if self.position.get('error', 0) > 0:
            er = int(self.position['error'] * scale_px)
            painter.setPen(QPen(QColor(239, 68, 68, 60), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(239, 68, 68, 20))
            painter.drawEllipse(px - er, py - er, er * 2, er * 2)

        # ── Directional arrow (yaw) ──
        painter.save()
        painter.translate(px, py)
        painter.rotate(-self.position.get('yaw', 0))
        painter.setPen(QPen(QColor(37, 99, 235), 2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        grad = QLinearGradient(0, -12, 0, 10)
        grad.setColorAt(0, QColor(96, 165, 250))
        grad.setColorAt(1, QColor(37, 99, 235))
        painter.setBrush(grad)
        path = QPainterPath()
        path.moveTo(14, 0)
        path.lineTo(-10, -9)
        path.lineTo(-4, 0)
        path.lineTo(-10, 9)
        path.closeSubpath()
        painter.drawPath(path)
        painter.setBrush(QColor(248, 113, 113, 150))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-10, -3, 4, 6)
        painter.restore()

        # ── Glow ──
        glow_grad = QRadialGradient(px, py, 18)
        glow_grad.setColorAt(0, QColor(96, 165, 250, 60))
        glow_grad.setColorAt(1, QColor(96, 165, 250, 0))
        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(px - 18, py - 18, 36, 36)

        # ── Coordinate label ──
        coord_text = f"{self.position['x']:.2f}, {self.position['y']:.2f}"
        painter.setFont(QFont('Segoe UI', 9, QFont.Weight.Bold))
        tr = painter.fontMetrics().boundingRect(coord_text)
        tr.translate(px + 15, py + 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 180))
        painter.drawRoundedRect(tr.adjusted(-4, -2, 4, 2), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(px + 15, py + 15 + tr.height() - 4, coord_text)

        # ── Rectangle zoom overlay ──
        if self._rect_zoom and self._rect_start and self._rect_end:
            rx = min(self._rect_start.x(), self._rect_end.x())
            ry = min(self._rect_start.y(), self._rect_end.y())
            rw = abs(self._rect_end.x() - self._rect_start.x())
            rh = abs(self._rect_end.y() - self._rect_start.y())
            painter.setPen(QPen(QColor(99, 102, 241), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(99, 102, 241, 30))
            painter.drawRect(int(rx), int(ry), int(rw), int(rh))


# ═══════════════════════════════════════════════════════════════════════
#  CollapsibleCard — ported from dashboard.py
# ═══════════════════════════════════════════════════════════════════════
class CollapsibleCard(QFrame):
    """Collapsible card widget matching dashboard.py design."""
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            CollapsibleCard {
                background-color: #1e293b;
                border-radius: 10px;
                border: 1px solid #334155;
            }
        """)

        self.is_collapsed = False

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Title bar with toggle button
        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("""
            color: #f1f5f9;
            font-size: 14px;
            font-weight: bold;
            background-color: transparent;
        """)
        title_layout.addWidget(self.title_label)

        self.toggle_btn = QPushButton("▼")
        self.toggle_btn.setFixedSize(30, 26)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #e2e8f0;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                padding: 0px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #475569;
            }
        """)
        self.toggle_btn.clicked.connect(self.toggle_collapse)
        title_layout.addWidget(self.toggle_btn)

        main_layout.addLayout(title_layout)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #334155; max-height: 2px;")
        main_layout.addWidget(separator)

        # Content area
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_widget.setLayout(self.content_layout)
        main_layout.addWidget(self.content_widget)

        self.setLayout(main_layout)

    def toggle_collapse(self):
        """Toggle collapsed state."""
        self.is_collapsed = not self.is_collapsed
        self.content_widget.setVisible(not self.is_collapsed)
        self.toggle_btn.setText("►" if self.is_collapsed else "▼")


# ═══════════════════════════════════════════════════════════════════════
#  LiveTrackingTab — main tab widget
# ═══════════════════════════════════════════════════════════════════════
class LiveTrackingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        self._frame_count = 0
        self._start_time = time.time()
        self._is_ranging = False
        self._build_ui()

        # FPS / stats timer
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)

    def set_viewmodel(self, vm):
        self._vm = vm
        # Connect signals
        self._vm.ranging_started.connect(self._on_ranging_started)
        self._vm.ranging_stopped.connect(self._on_ranging_stopped)
        self._vm.position_updated.connect(self._on_position_updated)
        self._vm.anchor_distances_updated.connect(self._on_anchor_distances)

    # ── Build UI ─────────────────────────────────────────────────────
    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ═══ LEFT PANEL: Canvas + header + controls ═══
        left_panel = QVBoxLayout()
        left_panel.setSpacing(6)
        left_panel.setContentsMargins(0, 0, 0, 0)

        # Canvas header
        header_layout = QHBoxLayout()

        canvas_header = QLabel("Real-time Position Tracking")
        canvas_header.setStyleSheet("""
            color: #f1f5f9;
            font-size: 16px;
            font-weight: bold;
            padding: 0px 0px 8px 0px;
            background-color: transparent;
        """)
        canvas_header.setFixedHeight(30)
        header_layout.addWidget(canvas_header)

        self._warning_label = QLabel("⚠️ OUT OF ZONE")
        self._warning_label.setStyleSheet("""
            color: white;
            font-size: 14px;
            font-weight: bold;
            background-color: #ef4444;
            padding: 2px 10px;
            border-radius: 4px;
        """)
        self._warning_label.setFixedHeight(25)
        self._warning_label.setVisible(False)

        header_layout.addStretch()
        header_layout.addWidget(self._warning_label)

        left_panel.addLayout(header_layout)

        # Canvas
        self._canvas = ModernPositionCanvas()
        left_panel.addWidget(self._canvas, 1)

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
        left_panel.addLayout(ctrl_row)

        self._btn_start.clicked.connect(self._start_ranging)
        self._btn_stop.clicked.connect(self._stop_ranging)
        self._btn_clear.clicked.connect(self._canvas.clear_trail)

        main_layout.addLayout(left_panel, 1)  # Stretch factor 1

        # ═══ RIGHT PANEL: Scrollable telemetry cards (fixed width) ═══
        right_widget = QWidget()
        right_widget.setFixedWidth(380)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #0f172a;
            }
            QScrollBar:vertical {
                background-color: #0f172a;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #475569;
                border-radius: 5px;
                min-height: 30px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #64748b;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #0f172a;")
        right_panel = QVBoxLayout()
        right_panel.setSpacing(8)
        right_panel.setContentsMargins(0, 0, 0, 0)
        scroll_content.setLayout(right_panel)

        scroll_area.setWidget(scroll_content)

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(scroll_area)
        right_widget.setLayout(right_layout)

        # ── Live Position Card ──
        pos_card = CollapsibleCard("Live Position")
        pos_layout = QGridLayout()
        pos_layout.setSpacing(15)

        groups = [
            ("COORDINATES", [
                ("X:", "x_label", "#60a5fa", "m"),
                ("Y:", "y_label", "#60a5fa", "m"),
                ("Z:", "z_label", "#60a5fa", "m"),
            ]),
            ("MOTION", [
                ("VX:", "vx_label", "#2dd4bf", "m/s"),
                ("VY:", "vy_label", "#2dd4bf", "m/s"),
                ("Yaw:", "yaw_label", "#f472b6", "°"),
            ]),
            ("RANGING", [
                ("D1:", "d1_label", "#a78bfa", "m"),
                ("D2:", "d2_label", "#a78bfa", "m"),
                ("D3:", "d3_label", "#a78bfa", "m"),
                ("D4:", "d4_label", "#a78bfa", "m"),
            ]),
            ("QUALITY", [
                ("Error:", "error_label", "#f59e0b", "m"),
                ("Err Frames:", "err_cnt_label", "#f87171", "packets"),
            ]),
        ]

        current_row = 0
        for group_name, items in groups:
            group_label = QLabel(group_name)
            group_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #64748b; margin-top: 5px;"
                "background-color: transparent;"
            )
            pos_layout.addWidget(group_label, current_row, 0, 1, 2)
            current_row += 1

            for text, attr, color, unit in items:
                lbl = QLabel(text)
                lbl.setStyleSheet("font-size: 13px; color: #94a3b8; background-color: transparent;")
                pos_layout.addWidget(lbl, current_row, 0)

                value_label = QLabel(f"0.000 {unit}")
                value_label.setStyleSheet(
                    f"font-family: 'Consolas', monospace; font-size: 15px;"
                    f"font-weight: bold; color: {color}; background-color: transparent;"
                )
                pos_layout.addWidget(value_label, current_row, 1)
                setattr(self, f"_{attr}", value_label)
                current_row += 1

        pos_card.content_layout.addLayout(pos_layout)
        right_panel.addWidget(pos_card)

        # ── Statistics Card ──
        stats_card = CollapsibleCard("Statistics")
        stats_layout = QGridLayout()
        stats_layout.setSpacing(10)

        stats = [
            ("Frames:", "frames_label"),
            ("FPS:", "fps_label"),
            ("Uptime:", "uptime_label"),
        ]

        for i, (text, attr) in enumerate(stats):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 13px; color: #94a3b8; background-color: transparent;")
            stats_layout.addWidget(lbl, i, 0)

            value_label = QLabel("0")
            value_label.setStyleSheet(
                "font-size: 15px; font-weight: bold; color: #60a5fa;"
                "background-color: transparent;"
            )
            stats_layout.addWidget(value_label, i, 1)
            setattr(self, f"_{attr}", value_label)

        stats_card.content_layout.addLayout(stats_layout)
        right_panel.addWidget(stats_card)

        right_panel.addStretch()

        main_layout.addWidget(right_widget)

    # ── Actions ──────────────────────────────────────────────────────
    def _start_ranging(self):
        if self._vm:
            self._vm.start_ranging()

    def _stop_ranging(self):
        if self._vm:
            self._vm.stop_ranging()

    # ── Slots ────────────────────────────────────────────────────────
    def _on_ranging_started(self):
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._is_ranging = True
        self._frame_count = 0
        self._start_time = time.time()

    def _on_ranging_stopped(self):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._is_ranging = False

    def _on_position_updated(self, x, y, z, rms):
        self._frame_count += 1

        # Update canvas
        position = {
            'x': x, 'y': y, 'z': z,
            'error': rms,
            'yaw': 0,  # Will be updated if available
        }
        self._canvas.update_position(position)

        # Update labels
        self._x_label.setText(f"{x:.3f} m")
        self._y_label.setText(f"{y:.3f} m")
        self._z_label.setText(f"{z:.3f} m")
        self._error_label.setText(f"{rms:.3f} m")

        # Out-of-zone warning
        if self._canvas.anchors:
            anchors = self._canvas.anchors
            min_x = min(a['x'] for a in anchors)
            max_x = max(a['x'] for a in anchors)
            min_y = min(a['y'] for a in anchors)
            max_y = max(a['y'] for a in anchors)
            out_of_zone = not (min_x <= x <= max_x and min_y <= y <= max_y)
            self._warning_label.setVisible(out_of_zone)
        else:
            self._warning_label.setVisible(False)

    def _on_anchor_distances(self, anchors):
        """Update ranging distance labels (D1–D4)."""
        for anchor in anchors:
            aid = anchor.get("id", "")
            # Map A1→D1, A2→D2, etc.
            idx = aid.replace("A", "")
            attr = f"_d{idx}_label"
            if hasattr(self, attr):
                d_m = anchor.get("distance_cm", 0) / 100.0
                getattr(self, attr).setText(f"{d_m:.3f} m")

    def _update_stats(self):
        """Update statistics labels every second."""
        if not self._is_ranging:
            return

        self._frames_label.setText(str(self._frame_count))

        uptime = int(time.time() - self._start_time)
        fps = self._frame_count / uptime if uptime > 0 else 0
        self._fps_label.setText(f"{fps:.1f}")
        self._uptime_label.setText(f"{uptime}s")

    # ── Public API ───────────────────────────────────────────────────
    def set_anchors(self, anchors):
        """Called externally (e.g. from Config tab) to set anchor layout."""
        self._canvas.set_anchors(anchors)
