"""
Modern UWB Position Dashboard - PyQt5
Beautiful and professional dashboard for visualizing UWB position data

Requirements:
    pip install PyQt5

Usage:
    python dashboard.py
"""

import sys
import socket
import struct
import math
import os
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QGridLayout, QMessageBox, QFrame, QScrollArea
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer, QPropertyAnimation, QEasingCurve, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QLinearGradient, QRadialGradient, QBrush, QPainterPath, QIcon
import time


class UDPReceiver(QThread):
    """Thread to receive UDP data"""
    position_received = pyqtSignal(dict)

    def __init__(self, port=5005):
        super().__init__()
        self.port = port
        self.running = False
        self.sock = None

    def run(self):
        """Receive UDP data"""
        self.running = True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('0.0.0.0', self.port))
            self.sock.settimeout(1.0)

            while self.running:
                try:
                    data, addr = self.sock.recvfrom(1024)
                    
                    try:
                        # Try parsing as string format: Position(x=..., y=..., vx=..., vy=..., yaw=..., err=..., dists=[...])
                        msg = data.decode('utf-8').strip()
                        if msg.startswith("Position("):
                            # Simple parsing using string split/replace
                            content = msg[msg.find("(")+1 : msg.find(")")]
                            parts = content.split(", dists=")
                            
                            # Parse main fields
                            fields = parts[0].split(", ")
                            pos_data = {}
                            for field in fields:
                                key, val = field.split("=")
                                pos_data[key] = float(val)
                            
                            # Parse dists
                            dists_str = parts[1].strip("[]")
                            dists = [float(d) for d in dists_str.split(",") if d.strip()]
                            
                            position = {
                                'x': pos_data.get('x', 0.0),
                                'y': pos_data.get('y', 0.0),
                                'z': pos_data.get('z', 0.0), # Default to 0 if not present
                                'vx': pos_data.get('vx', 0.0),
                                'vy': pos_data.get('vy', 0.0),
                                'yaw': pos_data.get('yaw', 0.0),
                                'error': pos_data.get('err', 0.0),
                                'err_cnt': int(pos_data.get('err_cnt', 0)),
                                'dists': dists
                            }
                            
                            # Map dists to d1, d2, d3, d4 for compatibility
                            for i, d in enumerate(dists):
                                position[f'd{i+1}'] = d
                                
                            self.position_received.emit(position)
                            continue
                    except Exception as e:
                        # If string parsing fails, try binary format
                        pass

                    # Fallback: Parse UDP data (32 bytes for x, y, z, error, d1, d2, d3, d4)
                    if len(data) == 32:
                        x, y, z, error, d1, d2, d3, d4 = struct.unpack('<ffffffff', data)

                        position = {
                            'x': x,
                            'y': y,
                            'z': z,
                            'error': error,
                            'd1': d1,
                            'd2': d2,
                            'd3': d3,
                            'd4': d4,
                            'vx': 0.0,
                            'vy': 0.0,
                            'yaw': 0.0
                        }

                        self.position_received.emit(position)
                    else:
                        print(f"Warning: Received packet of unknown length ({len(data)} bytes) or format.")
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Parse error: {e}")

        except Exception as e:
            print(f"UDP receiver error: {e}")
        finally:
            if self.sock:
                self.sock.close()

    def stop(self):
        """Stop thread"""
        self.running = False


class ModernPositionCanvas(QWidget):
    """Modern widget for 2D position visualization with beautiful effects"""
    
    def __init__(self):
        super().__init__()
        self.setMinimumSize(700, 550)
        
        self.position = {'x': 0, 'y': 0, 'z': 0}
        self.anchors = []
        self.history = []
        self.max_history = 30
        
        # Throttle updates
        self.last_update_time = 0
        self.update_interval = 0.05  # 50ms minimum between updates
    
    def update_position(self, position):
        """Update position with throttling"""
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
        """Set list of anchors"""
        self.anchors = anchors
        self.update()
    
    def paintEvent(self, event):
        """Draw canvas with modern design"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # Solid background
        painter.fillRect(self.rect(), QColor(30, 41, 59))
        
        # Calculate bounds
        margin = 60
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        
        all_x = [a['x'] for a in self.anchors] + [self.position['x']]
        all_y = [a['y'] for a in self.anchors] + [self.position['y']]
        
        if self.history:
            all_x.extend([h[0] for h in self.history])
            all_y.extend([h[1] for h in self.history])
        
        min_x = min(all_x) - 1 if all_x else -5
        max_x = max(all_x) + 1 if all_x else 5
        min_y = min(all_y) - 1 if all_y else -5
        max_y = max(all_y) + 1 if all_y else 5
        
        range_x = max_x - min_x
        range_y = max_y - min_y
        
        scale = min(width / range_x, height / range_y) if range_x > 0 and range_y > 0 else 50
        
        def to_canvas(x, y):
            cx = margin + (x - min_x) * scale
            cy = self.height() - margin - (y - min_y) * scale
            return int(cx), int(cy)
        
        # Draw modern grid
        painter.setPen(QPen(QColor(51, 65, 85, 80), 1, Qt.DotLine))
        for i in range(int(min_x), int(max_x) + 1):
            x1, y1 = to_canvas(i, min_y)
            x2, y2 = to_canvas(i, max_y)
            painter.drawLine(x1, y1, x2, y2)
        
        for i in range(int(min_y), int(max_y) + 1):
            x1, y1 = to_canvas(min_x, i)
            x2, y2 = to_canvas(max_x, i)
            painter.drawLine(x1, y1, x2, y2)
        
        # Draw axis labels
        painter.setFont(QFont('Segoe UI', 9))
        painter.setPen(QColor(148, 163, 184))
        for i in range(int(min_x), int(max_x) + 1):
            x, y = to_canvas(i, min_y)
            painter.drawText(x - 10, self.height() - margin + 20, f"{i}m")
        for i in range(int(min_y), int(max_y) + 1):
            x, y = to_canvas(min_x, i)
            painter.drawText(margin - 35, y + 5, f"{i}m")
        
        # Draw history trail - simplified
        if len(self.history) > 1:
            painter.setPen(QPen(QColor(96, 165, 250, 120), 2))
            for i in range(len(self.history) - 1):
                x1, y1 = to_canvas(self.history[i][0], self.history[i][1])
                x2, y2 = to_canvas(self.history[i+1][0], self.history[i+1][1])
                painter.drawLine(x1, y1, x2, y2)
        
        # Draw connection lines from tag to anchors (faded)
        px, py = to_canvas(self.position['x'], self.position['y'])
        for anchor in self.anchors:
            ax, ay = to_canvas(anchor['x'], anchor['y'])
            painter.setPen(QPen(QColor(99, 102, 241, 40), 1, Qt.DashLine))
            painter.drawLine(px, py, ax, ay)
        
        # Draw anchors with modern style
        painter.setFont(QFont('Segoe UI', 10, QFont.Bold))
        for anchor in self.anchors:
            cx, cy = to_canvas(anchor['x'], anchor['y'])
            
            # Anchor circle (Smaller size)
            painter.setPen(QPen(QColor(99, 102, 241), 2))
            painter.setBrush(QColor(30, 41, 59))
            painter.drawEllipse(cx - 10, cy - 10, 20, 20)
            
            # Inner dot
            painter.setBrush(QColor(99, 102, 241))
            painter.drawEllipse(cx - 4, cy - 4, 8, 8)
            
            # Label with background
            label_rect = painter.fontMetrics().boundingRect(anchor['label'])
            label_rect.moveCenter(painter.fontMetrics().boundingRect(anchor['label']).center())
            label_rect.translate(cx + 25, cy - 20)
            
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(30, 41, 59, 200))
            painter.drawRoundedRect(label_rect.adjusted(-4, -2, 4, 2), 4, 4)
            
            painter.setPen(QColor(226, 232, 240))
            painter.drawText(cx + 22, cy - 12, anchor['label'])
            
            # Coordinates
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(cx + 22, cy + 2, f"({anchor['x']:.1f}, {anchor['y']:.1f})")
            painter.setFont(QFont('Segoe UI', 10, QFont.Bold))
        
        # Error circle
        if self.position.get('error', 0) > 0:
            error_radius = int(self.position['error'] * scale)
            painter.setPen(QPen(QColor(239, 68, 68, 60), 2, Qt.DashLine))
            painter.setBrush(QColor(239, 68, 68, 20))
            painter.drawEllipse(int(px - error_radius), int(py - error_radius), 
                              int(error_radius * 2), int(error_radius * 2))
        
        # Position marker (Directional Arrow for Yaw)
        painter.save()
        painter.translate(px, py)
        # Assuming yaw is in degrees, positive is CCW
        # UWB systems often have different yaw conventions.
        painter.rotate(-self.position.get('yaw', 0)) 
        
        # Draw a sleek directional shape
        painter.setPen(QPen(QColor(37, 99, 235), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        
        # Body Gradient
        grad = QLinearGradient(0, -12, 0, 10)
        grad.setColorAt(0, QColor(96, 165, 250))
        grad.setColorAt(1, QColor(37, 99, 235))
        painter.setBrush(grad)
        
        # Create a "ship" or "arrow" shape - Smaller
        path = QPainterPath()
        path.moveTo(14, 0)     # Nose (Right)
        path.lineTo(-10, -9)   # Top wing
        path.lineTo(-4, 0)     # Center tail
        path.lineTo(-10, 9)    # Bottom wing
        path.closeSubpath()
        painter.drawPath(path)
        
        # Engine glow
        painter.setBrush(QColor(248, 113, 113, 150))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(-10, -3, 4, 6)
        
        painter.restore()
        
        # Glow effect around the position - Smaller
        glow_grad = QRadialGradient(px, py, 18)
        glow_grad.setColorAt(0, QColor(96, 165, 250, 60))
        glow_grad.setColorAt(1, QColor(96, 165, 250, 0))
        painter.setBrush(glow_grad)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(px - 18, py - 18, 36, 36)
        
        # Coordinates label (Small floating label)
        coord_text = f"{self.position['x']:.2f}, {self.position['y']:.2f}"
        painter.setFont(QFont('Segoe UI', 9, QFont.Bold))
        text_rect = painter.fontMetrics().boundingRect(coord_text)
        text_rect.translate(px + 15, py + 15)
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 23, 42, 180))
        painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
        
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(px + 15, py + 15 + text_rect.height() - 4, coord_text)


class CollapsibleCard(QFrame):
    """Collapsible card widget"""
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
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
        separator.setFrameShape(QFrame.HLine)
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
        """Toggle collapsed state"""
        self.is_collapsed = not self.is_collapsed
        self.content_widget.setVisible(not self.is_collapsed)
        self.toggle_btn.setText("►" if self.is_collapsed else "▼")


class ModernCard(QFrame):
    """Modern card widget"""
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            ModernCard {
                background-color: #1e293b;
                border-radius: 10px;
                border: 1px solid #334155;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet("""
                color: #f1f5f9;
                font-size: 14px;
                font-weight: bold;
                padding-bottom: 8px;
                border-bottom: 2px solid #334155;
            """)
            layout.addWidget(title_label)
        
        self.content_layout = QVBoxLayout()
        layout.addLayout(self.content_layout)
        
        self.setLayout(layout)


class MainWindow(QMainWindow):
    """Modern main dashboard window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UWB Position Dashboard - Modern UI")
        self.setGeometry(20, 20, 1320, 650)
        
        self.anchors = [
            {'x': 0.0, 'y': 0.0, 'label': 'A0'},
            {'x': 5.0, 'y': 0.0, 'label': 'A1'},
            {'x': 5.0, 'y': 5.0, 'label': 'A2'},
            {'x': 0.0, 'y': 5.0, 'label': 'A3'},
        ]
        
        self.position = {'x': 0, 'y': 0, 'z': 0, 'error': 0}
        self.frame_count = 0
        self.start_time = time.time()
        self.udp_receiver = None
        self.is_listening = False
        self.record_file = None
        
        self.setup_ui()
        
        self.fps_timer = QTimer()
        self.fps_timer.timeout.connect(self.update_stats)
        self.fps_timer.start(1000)
    
    def setup_ui(self):
        """Setup modern UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Dark theme
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f172a;
            }
            QLabel {
                color: #e2e8f0;
                font-family: 'Segoe UI', Arial;
            }
            QLineEdit {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 2px solid #334155;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #6366f1;
            }
            QPushButton {
                background-color: #6366f1;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4f46e5;
            }
            QPushButton:pressed {
                background-color: #4338ca;
            }
            QTableWidget {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 6px;
                gridline-color: #334155;
            }
            QTableWidget::item {
                padding: 5px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #6366f1;
            }
            QHeaderView::section {
                background-color: #1e293b;
                color: #e2e8f0;
                padding: 8px;
                border: none;
                font-weight: bold;
            }
            QTableCornerButton::section {
                background-color: #1e293b;
                border: none;
            }
            QTableWidget QLineEdit {
                background-color: #334155;
                color: #f1f5f9;
                border: none;
                border-radius: 0px;
                padding: 0px 5px;
            }
        """)
        
        main_layout = QHBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        central_widget.setLayout(main_layout)
        
        # Left panel with canvas and header
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
        
        self.warning_label = QLabel("⚠️ OUT OF ZONE")
        self.warning_label.setStyleSheet("""
            color: white; 
            font-size: 14px; 
            font-weight: bold; 
            background-color: #ef4444; 
            padding: 2px 10px; 
            border-radius: 4px;
        """)
        self.warning_label.setFixedHeight(25)
        self.warning_label.setVisible(False)
        
        header_layout.addStretch()
        header_layout.addWidget(self.warning_label)
        
        left_panel.addLayout(header_layout)
        
        # Canvas
        self.canvas = ModernPositionCanvas()
        self.canvas.set_anchors(self.anchors)
        left_panel.addWidget(self.canvas, 1)  # Stretch factor 1 to let canvas occupy remaining space
        
        main_layout.addLayout(left_panel, 1)  # Stretch factor 1 for left panel
        
        # Right panel with fixed width and scrollbar
        right_widget = QWidget()
        right_widget.setFixedWidth(380)
        
        # Scroll area for right panel
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
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
        
        main_layout.addWidget(right_widget)
        
        # Connection card
        conn_card = CollapsibleCard("Connection")
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("UDP Port:"))
        self.port_input = QLineEdit("5005")
        self.port_input.setMaximumWidth(100)
        port_layout.addWidget(self.port_input)
        conn_card.content_layout.addLayout(port_layout)
        
        self.connect_btn = QPushButton("Start Listening")
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4f46e5;
            }
            QPushButton:pressed {
                background-color: #4338ca;
            }
        """)
        self.connect_btn.clicked.connect(self.toggle_connection)
        conn_card.content_layout.addWidget(self.connect_btn)
        
        self.status_label = QLabel("● Disconnected")
        self.status_label.setStyleSheet("color: #ef4444; font-size: 14px; font-weight: bold;")
        conn_card.content_layout.addWidget(self.status_label)
        
        # Add Recording button
        self.record_btn = QPushButton("Start Recording")
        self.record_btn.setCheckable(True)
        self.record_btn.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:checked {
                background-color: #ef4444;
            }
        """)
        self.record_btn.clicked.connect(self.toggle_recording)
        conn_card.content_layout.addWidget(self.record_btn)
        
        right_panel.addWidget(conn_card)
        
        pos_card = CollapsibleCard("Live Position")
        pos_layout = QGridLayout()
        pos_layout.setSpacing(15)
        
        # New grouped layout
        groups = [
            ("COORDINATES", [
                ("X:", "x_label", "#60a5fa", "m"),
                ("Y:", "y_label", "#60a5fa", "m"),
                ("Z:", "z_label", "#60a5fa", "m")
            ]),
            ("MOTION", [
                ("VX:", "vx_label", "#2dd4bf", "m/s"),
                ("VY:", "vy_label", "#2dd4bf", "m/s"),
                ("Yaw:", "yaw_label", "#f472b6", "°")
            ]),
            ("RANGING", [
                ("D1:", "d1_label", "#a78bfa", "m"),
                ("D2:", "d2_label", "#a78bfa", "m"),
                ("D3:", "d3_label", "#a78bfa", "m"),
                ("D4:", "d4_label", "#a78bfa", "m")
            ]),
            ("QUALITY", [
                ("Error:", "error_label", "#f59e0b", "m"),
                ("Err Frames:", "err_cnt_label", "#f87171", "packets")
            ])
        ]
        
        current_row = 0
        for group_name, items in groups:
            group_label = QLabel(group_name)
            group_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #64748b; margin-top: 5px;")
            pos_layout.addWidget(group_label, current_row, 0, 1, 2)
            current_row += 1
            
            for i, (text, attr, color, unit) in enumerate(items):
                lbl = QLabel(text)
                lbl.setStyleSheet("font-size: 13px; color: #94a3b8;")
                pos_layout.addWidget(lbl, current_row, 0)
                
                value_label = QLabel(f"0.000 {unit}")
                value_label.setStyleSheet(f"font-family: 'Consolas', monospace; font-size: 15px; font-weight: bold; color: {color};")
                pos_layout.addWidget(value_label, current_row, 1)
                setattr(self, attr, value_label)
                current_row += 1
        
        pos_card.content_layout.addLayout(pos_layout)
        right_panel.addWidget(pos_card)
        
        # Stats card
        stats_card = CollapsibleCard("Statistics")
        stats_layout = QGridLayout()
        stats_layout.setSpacing(10)
        
        stats = [
            ("Frames:", "frames_label"),
            ("FPS:", "fps_label"),
            ("Uptime:", "uptime_label")
        ]
        
        for i, (text, attr) in enumerate(stats):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 13px; color: #94a3b8;")
            stats_layout.addWidget(lbl, i, 0)
            
            value_label = QLabel("0")
            value_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #60a5fa;")
            stats_layout.addWidget(value_label, i, 1)
            setattr(self, attr, value_label)
        
        stats_card.content_layout.addLayout(stats_layout)
        right_panel.addWidget(stats_card)
        
        # Anchors card
        anchor_card = CollapsibleCard("Anchor Points")
        
        self.anchor_table = QTableWidget()
        self.anchor_table.setColumnCount(3)
        self.anchor_table.setHorizontalHeaderLabels(["Label", "X (m)", "Y (m)"])
        self.anchor_table.setRowCount(len(self.anchors))
        self.anchor_table.setMinimumHeight(220)
        self.anchor_table.horizontalHeader().setStretchLastSection(True)
        self.anchor_table.verticalHeader().setDefaultSectionSize(36)
        self.anchor_table.setColumnWidth(0, 70)
        self.anchor_table.setColumnWidth(1, 110)
        # Column 2 (Y) will auto-stretch to fill remaining space
        
        for i, anchor in enumerate(self.anchors):
            self.anchor_table.setItem(i, 0, QTableWidgetItem(anchor['label']))
            self.anchor_table.setItem(i, 1, QTableWidgetItem(f"{anchor['x']:.2f}"))
            self.anchor_table.setItem(i, 2, QTableWidgetItem(f"{anchor['y']:.2f}"))
        
        self.anchor_table.cellChanged.connect(self.on_anchor_changed)
        anchor_card.content_layout.addWidget(self.anchor_table)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("➕ Add")
        add_btn.clicked.connect(self.add_anchor)
        add_btn.setStyleSheet("background-color: #3b82f6;")
        btn_layout.addWidget(add_btn)
        
        del_btn = QPushButton("🗑️ Delete")
        del_btn.clicked.connect(self.delete_anchor)
        del_btn.setStyleSheet("background-color: #ef4444;")
        btn_layout.addWidget(del_btn)
        
        anchor_card.content_layout.addLayout(btn_layout)
        right_panel.addWidget(anchor_card)
        
        right_panel.addStretch()
    
    def toggle_connection(self):
        """Start/Stop UDP listening"""
        if self.udp_receiver is None or not self.udp_receiver.isRunning():
            try:
                port = int(self.port_input.text())
                self.udp_receiver = UDPReceiver(port)
                self.udp_receiver.position_received.connect(self.on_position_received)
                self.udp_receiver.start()
                
                self.connect_btn.setText("Stop Listening")
                self.connect_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ef4444;
                        color: white;
                        border: none;
                        border-radius: 6px;
                        padding: 10px 20px;
                        font-size: 13px;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background-color: #dc2626;
                    }
                    QPushButton:pressed {
                        background-color: #b91c1c;
                    }
                """)
                self.status_label.setText(f"● Connected (Port {port})")
                self.status_label.setStyleSheet("color: #60a5fa; font-size: 14px; font-weight: bold;")
                self.start_time = time.time()
                self.frame_count = 0
                self.is_listening = True
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to start: {e}")
        else:
            self.udp_receiver.stop()
            self.udp_receiver.wait()
            
            self.connect_btn.setText("Start Listening")
            self.connect_btn.setStyleSheet("""
                QPushButton {
                    background-color: #6366f1;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 10px 20px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #4f46e5;
                }
                QPushButton:pressed {
                    background-color: #4338ca;
                }
            """)
            self.status_label.setText("● Disconnected")
            self.status_label.setStyleSheet("color: #ef4444; font-size: 14px; font-weight: bold;")
            self.is_listening = False
    
    def toggle_recording(self, checked):
        """Toggle data recording"""
        if checked:
            # Get the directory containing dashboard.py
            current_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Create path to logs_data folder inside software directory
            log_dir = os.path.join(current_dir, "logs_data")
            
            # Auto-create directory if it doesn't exist
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
                
            filename = os.path.join(log_dir, datetime.now().strftime("uwb_data_%Y%m%d_%H%M%S.csv"))
            try:
                self.record_file = open(filename, "a", encoding="utf-8")
                
                # Create header: Time, X, Y, Z, VX, VY, Yaw, Error, ErrCnt, D1, D2,...
                header = "Time(s), X(m), Y(m), Z(m), VX(m/s), VY(m/s), Yaw(deg), Error(m), ErrCnt"
                for i in range(len(self.anchors)):
                    header += f", D{i+1}(m)"
                self.record_file.write(header + "\n")
                
                self.record_btn.setText("Stop Recording")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not create file: {e}")
                self.record_btn.setChecked(False)
        else:
            if self.record_file:
                self.record_file.close()
                self.record_file = None
            self.record_btn.setText("Start Recording")

    def on_position_received(self, position):
        """Handle received position data"""
        self.position = position
        self.frame_count += 1
        
        self.x_label.setText(f"{position['x']:.3f} m")
        self.y_label.setText(f"{position['y']:.3f} m")
        self.z_label.setText(f"{position['z']:.3f} m")
        
        self.vx_label.setText(f"{position.get('vx', 0):.3f} m/s")
        self.vy_label.setText(f"{position.get('vy', 0):.3f} m/s")
        self.yaw_label.setText(f"{position.get('yaw', 0):.1f} °")
        
        self.d1_label.setText(f"{position.get('d1', 0):.3f} m")
        self.d2_label.setText(f"{position.get('d2', 0):.3f} m")
        self.d3_label.setText(f"{position.get('d3', 0):.3f} m")
        self.d4_label.setText(f"{position.get('d4', 0):.3f} m")
        self.error_label.setText(f"{position.get('error', 0):.3f} m")
        self.err_cnt_label.setText(f"{position.get('err_cnt', 0)}")
        
        if self.anchors:
            min_x = min(a['x'] for a in self.anchors)
            max_x = max(a['x'] for a in self.anchors)
            min_y = min(a['y'] for a in self.anchors)
            max_y = max(a['y'] for a in self.anchors)
            
            # If tag is outside the rectangle formed by anchors
            if not (min_x <= position['x'] <= max_x and min_y <= position['y'] <= max_y):
                self.warning_label.setVisible(True)
            else:
                self.warning_label.setVisible(False)
        else:
            self.warning_label.setVisible(False)
            
        if hasattr(self, 'record_btn') and self.record_btn.isChecked() and self.record_file:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            error_val = position.get('error', 0.0)
            vx = position.get('vx', 0.0)
            vy = position.get('vy', 0.0)
            yaw = position.get('yaw', 0.0)
            err_cnt = position.get('err_cnt', 0)
            log_str = f"{timestamp}, {position['x']:.3f}, {position['y']:.3f}, {position['z']:.3f}, {vx:.3f}, {vy:.3f}, {yaw:.1f}, {error_val:.3f}, {err_cnt}"
            
            for i, a in enumerate(self.anchors):
                # Prefer distances from received package if available
                dist_key = f'd{i+1}'
                if dist_key in position:
                    dist = position[dist_key]
                else:
                    # Fallback for old package (x, y, z only): calculate manually (Euclidean 3D, anchor z=0)
                    dx = position['x'] - a['x']
                    dy = position['y'] - a['y']
                    dz = position['z'] - 0.0
                    dist = math.sqrt(dx**2 + dy**2 + dz**2)
                log_str += f", {dist:.3f}"
                
            self.record_file.write(log_str + "\n")
            self.record_file.flush()
            
        self.canvas.update_position(position)
    
    def update_stats(self):
        """Update statistics"""
        if not self.is_listening:
            return
            
        self.frames_label.setText(str(self.frame_count))
        
        uptime = int(time.time() - self.start_time)
        fps = self.frame_count / uptime if uptime > 0 else 0
        self.fps_label.setText(f"{fps:.1f}")
        self.uptime_label.setText(f"{uptime}s")
    
    def on_anchor_changed(self, row, col):
        """Handle anchor edit"""
        try:
            if col == 0:
                self.anchors[row]['label'] = self.anchor_table.item(row, col).text()
            else:
                coord = ['x', 'y'][col - 1]
                value = float(self.anchor_table.item(row, col).text())
                self.anchors[row][coord] = value
            
            self.canvas.set_anchors(self.anchors)
        except (ValueError, IndexError):
            pass
    
    def add_anchor(self):
        """Add new anchor"""
        new_id = len(self.anchors)
        self.anchors.append({'x': 0.0, 'y': 0.0, 'label': f'A{new_id}'})
        
        row = self.anchor_table.rowCount()
        self.anchor_table.insertRow(row)
        self.anchor_table.setItem(row, 0, QTableWidgetItem(f'A{new_id}'))
        self.anchor_table.setItem(row, 1, QTableWidgetItem('0.00'))
        self.anchor_table.setItem(row, 2, QTableWidgetItem('0.00'))
        
        self.canvas.set_anchors(self.anchors)
    
    def delete_anchor(self):
        """Delete selected anchor"""
        current_row = self.anchor_table.currentRow()
        if current_row >= 0:
            self.anchor_table.removeRow(current_row)
            self.anchors.pop(current_row)
            self.canvas.set_anchors(self.anchors)
    
    def closeEvent(self, event):
        """Clean up on close"""
        if self.udp_receiver and self.udp_receiver.isRunning():
            self.udp_receiver.stop()
            self.udp_receiver.wait()
            
        if hasattr(self, 'record_file') and self.record_file:
            self.record_file.close()
            
        event.accept()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()