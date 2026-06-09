"""
===============================================================================
  UWB RTLS Studio - Position Canvas Component
===============================================================================
"""
import math
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget


class PositionCanvas(QWidget):
    """Interactive 2D position canvas used by the live tracking tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "error": 0.0}
        self.anchors = [
            {"x": 0.0, "y": 0.0, "label": "A0"},
            {"x": 9.76, "y": 0.0, "label": "A1"},
            {"x": 9.76, "y": 9.76, "label": "A2"},
            {"x": 0.0, "y": 9.76, "label": "A3"},
        ]
        self.history = []
        self.max_history = 30

        self.last_update_time = 0.0
        self.update_interval = 0.05

        self._view_cx = 4.88
        self._view_cy = 4.88
        self._view_range = 14.0
        self._margin = 50

        self._dragging = False
        self._drag_start = None
        self._drag_view_cx = 0.0
        self._drag_view_cy = 0.0
        self._rect_zoom = False
        self._rect_start = None
        self._rect_end = None

        QTimer.singleShot(50, self.auto_fit)

    def update_position(self, position):
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval:
            return

        self.last_update_time = current_time
        self.position = position
        self.history.append((position["x"], position["y"]))
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
        pts_x = [a["x"] for a in self.anchors] + [self.position["x"]]
        pts_y = [a["y"] for a in self.anchors] + [self.position["y"]]
        if not pts_x:
            return

        max_x = max(pts_x)
        max_y = max(pts_y)
        padding = 1.0

        need_x = max_x + 2 * padding
        need_y = max_y + 2 * padding

        margin = self._margin
        right_panel_width = 0
        if hasattr(self, "parent_tab") and getattr(self.parent_tab, "sidebar_expanded", False):
            right_panel_width = self.parent_tab.right_widget.width() or 380

        full_width = max(self.width() - 2 * margin, 1)
        full_height = max(self.height() - 2 * margin, 1)
        visible_width = max(full_width - right_panel_width, 1)

        self._view_range = max(
            need_x * min(full_width, full_height) / visible_width,
            need_y * min(full_width, full_height) / full_height,
            2.0,
        )

        scale = min(full_width, full_height) / self._view_range if self._view_range > 0 else 50
        self._view_cx = -padding + (full_width / scale) / 2.0
        self._view_cy = -padding + (full_height / scale) / 2.0
        self.update()

    def _world_to_screen(self, world_x, world_y):
        margin = self._margin
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        scale = min(width, height) / self._view_range if self._view_range > 0 else 50
        screen_x = margin + (width / 2.0) + (world_x - self._view_cx) * scale
        screen_y = margin + (height / 2.0) - (world_y - self._view_cy) * scale
        return int(screen_x), int(screen_y)

    def _screen_to_world(self, screen_x, screen_y):
        margin = self._margin
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        scale = min(width, height) / self._view_range if self._view_range > 0 else 50
        world_x = self._view_cx + (screen_x - margin - width / 2.0) / scale
        world_y = self._view_cy - (screen_y - margin - height / 2.0) / scale
        return world_x, world_y

    def wheelEvent(self, event):
        factor = 0.85 if event.angleDelta().y() > 0 else 1.18
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        self._view_range *= factor
        self._view_range = max(0.5, min(self._view_range, 200.0))
        world_x_2, world_y_2 = self._screen_to_world(pos.x(), pos.y())
        self._view_cx -= world_x_2 - world_x
        self._view_cy -= world_y_2 - world_y
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
            margin = self._margin
            width = self.width() - 2 * margin
            height = self.height() - 2 * margin
            scale = min(width, height) / self._view_range if self._view_range > 0 else 50
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
                x1 = self._rect_start.x()
                y1 = self._rect_start.y()
                x2 = self._rect_end.x()
                y2 = self._rect_end.y()
                if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
                    w1x, w1y = self._screen_to_world(min(x1, x2), max(y1, y2))
                    w2x, w2y = self._screen_to_world(max(x1, x2), min(y1, y2))
                    self._view_cx = (w1x + w2x) / 2.0
                    self._view_cy = (w1y + w2y) / 2.0
                    self._view_range = max(w2x - w1x, w2y - w1y) * 1.1
            self._rect_start = None
            self._rect_end = None
            self.update()

    def mouseDoubleClickEvent(self, event):
        self.auto_fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._dragging and not self._rect_zoom:
            self.auto_fit()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(30, 41, 59))

        margin = self._margin
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        if width <= 0 or height <= 0:
            return

        to_screen = self._world_to_screen
        view_x1, view_y1 = self._screen_to_world(margin, self.height() - margin)
        view_x2, view_y2 = self._screen_to_world(margin + width, margin)

        painter.setPen(QPen(QColor(51, 65, 85, 80), 1, Qt.PenStyle.DotLine))
        raw_step = (view_x2 - view_x1) / 10.0
        step = max(1.0, round(raw_step))
        if raw_step < 0.5:
            step = 0.5

        grid_x = math.floor(view_x1 / step) * step
        while grid_x <= view_x2:
            screen_x, _ = to_screen(grid_x, 0)
            painter.drawLine(screen_x, margin, screen_x, self.height() - margin)
            grid_x += step

        grid_y = math.floor(view_y1 / step) * step
        while grid_y <= view_y2:
            _, screen_y = to_screen(0, grid_y)
            painter.drawLine(margin, screen_y, margin + width, screen_y)
            grid_y += step

        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(148, 163, 184))
        grid_x = math.floor(view_x1 / step) * step
        while grid_x <= view_x2:
            screen_x, _ = to_screen(grid_x, 0)
            painter.drawText(screen_x - 12, self.height() - margin + 16, f"{grid_x:.0f}m")
            grid_x += step

        grid_y = math.floor(view_y1 / step) * step
        while grid_y <= view_y2:
            _, screen_y = to_screen(0, grid_y)
            painter.drawText(4, screen_y + 4, f"{grid_y:.0f}m")
            grid_y += step

        if len(self.history) > 1:
            painter.setPen(QPen(QColor(96, 165, 250, 120), 2))
            for idx in range(len(self.history) - 1):
                x1, y1 = to_screen(self.history[idx][0], self.history[idx][1])
                x2, y2 = to_screen(self.history[idx + 1][0], self.history[idx + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        pos_x, pos_y = to_screen(self.position["x"], self.position["y"])
        for anchor in self.anchors:
            anchor_x, anchor_y = to_screen(anchor["x"], anchor["y"])
            painter.setPen(QPen(QColor(99, 102, 241, 40), 1, Qt.PenStyle.DashLine))
            painter.drawLine(pos_x, pos_y, anchor_x, anchor_y)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for anchor in self.anchors:
            center_x, center_y = to_screen(anchor["x"], anchor["y"])
            painter.setPen(QPen(QColor(99, 102, 241), 2))
            painter.setBrush(QColor(30, 41, 59))
            painter.drawEllipse(center_x - 10, center_y - 10, 20, 20)
            painter.setBrush(QColor(99, 102, 241))
            painter.drawEllipse(center_x - 4, center_y - 4, 8, 8)

            label = anchor.get("label", anchor.get("id", "?"))
            painter.setPen(QColor(226, 232, 240))
            painter.drawText(center_x + 16, center_y - 10, label)
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(center_x + 16, center_y + 4, f"({anchor['x']:.1f}, {anchor['y']:.1f})")
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

        scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50
        if self.position.get("error", 0) > 0:
            error_radius = int(self.position["error"] * scale_px)
            painter.setPen(QPen(QColor(239, 68, 68, 60), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(239, 68, 68, 20))
            painter.drawEllipse(pos_x - error_radius, pos_y - error_radius, error_radius * 2, error_radius * 2)

        painter.save()
        painter.translate(pos_x, pos_y)
        painter.rotate(-self.position.get("yaw", 0))
        painter.setPen(
            QPen(
                QColor(37, 99, 235),
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        gradient = QLinearGradient(0, -12, 0, 10)
        gradient.setColorAt(0, QColor(96, 165, 250))
        gradient.setColorAt(1, QColor(37, 99, 235))
        painter.setBrush(gradient)
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

        glow_gradient = QRadialGradient(pos_x, pos_y, 18)
        glow_gradient.setColorAt(0, QColor(96, 165, 250, 60))
        glow_gradient.setColorAt(1, QColor(96, 165, 250, 0))
        painter.setBrush(glow_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(pos_x - 18, pos_y - 18, 36, 36)

        coord_text = f"{self.position['x']:.2f}, {self.position['y']:.2f}"
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        text_rect = painter.fontMetrics().boundingRect(coord_text)
        text_rect.translate(pos_x + 15, pos_y + 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 180))
        painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pos_x + 15, pos_y + 15 + text_rect.height() - 4, coord_text)

        if self._rect_zoom and self._rect_start and self._rect_end:
            rect_x = min(self._rect_start.x(), self._rect_end.x())
            rect_y = min(self._rect_start.y(), self._rect_end.y())
            rect_w = abs(self._rect_end.x() - self._rect_start.x())
            rect_h = abs(self._rect_end.y() - self._rect_start.y())
            painter.setPen(QPen(QColor(99, 102, 241), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(99, 102, 241, 30))
            painter.drawRect(int(rect_x), int(rect_y), int(rect_w), int(rect_h))
