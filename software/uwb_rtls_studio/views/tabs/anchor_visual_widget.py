import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush

class AnchorVisualWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.anchors = []
        self._margin = 35
        self.setStyleSheet("background-color: #0F172A; border-radius: 8px;")

    def set_anchors(self, anchors):
        self.anchors = anchors
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background fill
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        m = self._margin
        w = self.width() - 2 * m
        h = self.height() - 2 * m
        if w <= 0 or h <= 0:
            return

        # Calculate bounding box of anchors
        if not self.anchors:
            min_x, max_x = -1.0, 5.0
            min_y, max_y = -1.0, 5.0
        else:
            xs = [a.get('x_m', 0.0) for a in self.anchors]
            ys = [a.get('y_m', 0.0) for a in self.anchors]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            
            # Make sure we have some span
            if max_x - min_x < 2.0:
                min_x -= 1.0
                max_x += 1.0
            if max_y - min_y < 2.0:
                min_y -= 1.0
                max_y += 1.0

        # Add padding (e.g. 1.0m padding on all sides)
        padding = 1.0
        min_x -= padding
        max_x += padding
        min_y -= padding
        max_y += padding

        span_x = max_x - min_x
        span_y = max_y - min_y
        
        # Auto-scale to fit bounds keeping aspect ratio square-ish
        scale = min(w / span_x, h / span_y)
        
        # Coordinates of the center of world space
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0

        def to_screen(wx, wy):
            sx = m + (w / 2.0) + (wx - cx) * scale
            sy = m + (h / 2.0) - (wy - cy) * scale
            return int(sx), int(sy)

        # Draw grid lines (every 1 meter)
        painter.setFont(QFont('Segoe UI', 8))
        painter.setPen(QPen(QColor(51, 65, 85, 90), 1, Qt.PenStyle.DashLine))

        grid_start_x = math.floor(min_x)
        grid_end_x = math.ceil(max_x)
        grid_start_y = math.floor(min_y)
        grid_end_y = math.ceil(max_y)

        # Draw vertical grid lines and X labels
        for gx in range(grid_start_x, grid_end_x + 1):
            sx, _ = to_screen(gx, 0)
            painter.drawLine(sx, m, sx, self.height() - m)
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(sx - 12, self.height() - m + 14, f"{gx}m")
            painter.setPen(QPen(QColor(51, 65, 85, 90), 1, Qt.PenStyle.DashLine))

        # Draw horizontal grid lines and Y labels
        for gy in range(grid_start_y, grid_end_y + 1):
            _, sy = to_screen(0, gy)
            painter.drawLine(m, sy, self.width() - m, sy)
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(m - 28, sy + 4, f"{gy}m")
            painter.setPen(QPen(QColor(51, 65, 85, 90), 1, Qt.PenStyle.DashLine))

        # Draw primary axes (X = 0, Y = 0)
        axis_pen = QPen(QColor(14, 116, 144, 180), 2)
        painter.setPen(axis_pen)
        
        if min_y <= 0 <= max_y:
            _, sy_orig = to_screen(0, 0)
            painter.drawLine(m, sy_orig, self.width() - m, sy_orig)
        if min_x <= 0 <= max_x:
            sx_orig, _ = to_screen(0, 0)
            painter.drawLine(sx_orig, m, sx_orig, self.height() - m)

        # Draw Anchors
        for a in self.anchors:
            ax = a.get('x_m', 0.0)
            ay = a.get('y_m', 0.0)
            az = a.get('z_m', 0.0)
            aid = a.get('anchor_id', 0)
            
            sx, sy = to_screen(ax, ay)
            
            # Draw anchor circle node outline
            r = 12
            painter.setBrush(QBrush(QColor(15, 23, 42)))
            painter.setPen(QPen(QColor(34, 211, 238), 2))
            painter.drawEllipse(sx - r, sy - r, r * 2, r * 2)

            # Node fill
            painter.setBrush(QBrush(QColor(34, 211, 238)))
            painter.drawEllipse(sx - (r - 3), sy - (r - 3), (r - 3) * 2, (r - 3) * 2)
            
            # Node ID text inside
            painter.setPen(QColor(15, 23, 42))
            painter.setFont(QFont('Segoe UI', 8, QFont.Weight.Bold))
            id_str = f"{aid}"
            painter.drawText(sx - 5 if aid < 10 else sx - 9, sy + 4, id_str)

            # Label on the right
            painter.setPen(QColor(248, 250, 252))
            painter.setFont(QFont('Segoe UI', 9))
            lbl = f"A{aid} (z: {az:.2f}m)"
            painter.drawText(sx + r + 6, sy + 4, lbl)
