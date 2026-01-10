"""
UWB Position Dashboard - PyQt5
Simple and lightweight dashboard for visualizing UWB position data

Requirements:
    pip install PyQt5

Usage:
    python dashboard.py
"""

import sys
import json
import socket
import struct
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QGridLayout, QMessageBox
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QFont
import time


class UDPReceiver(QThread):
    """Thread để nhận UDP data"""
    position_received = pyqtSignal(dict)

    def __init__(self, port=5005):
        super().__init__()
        self.port = port
        self.running = False
        self.sock = None

    def run(self):
        """Nhận dữ liệu UDP"""
        self.running = True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('0.0.0.0', self.port))
            self.sock.settimeout(1.0)

            while self.running:
                try:
                    data, addr = self.sock.recvfrom(1024)

                    # Parse UDP data (12 bytes for x, y, z)
                    if len(data) == 12:
                        x, y, z = struct.unpack('<fff', data)

                        position = {
                            'x': x,
                            'y': y,
                            'z': z
                        }

                        self.position_received.emit(position)

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
        """Dừng thread"""
        self.running = False


class PositionCanvas(QWidget):
    """Widget vẽ vị trí 2D"""
    
    def __init__(self):
        super().__init__()
        self.setMinimumSize(600, 600)
        
        # Data
        self.position = {'x': 0, 'y': 0, 'z': 0, 'error': 0}
        self.anchors = []
        self.history = []
        self.max_history = 50
    
    def update_position(self, position):
        """Cập nhật vị trí"""
        self.position = position
        self.history.append((position['x'], position['y']))
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.update()
    
    def set_anchors(self, anchors):
        """Set danh sách anchors"""
        self.anchors = anchors
        self.update()
    
    def paintEvent(self, event):
        """Vẽ canvas"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Background
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        
        # Calculate bounds
        margin = 50
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        
        # Get all coordinates for auto-scale
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
        
        # Scale
        scale = min(width / range_x, height / range_y) if range_x > 0 and range_y > 0 else 50
        
        def to_canvas(x, y):
            """Convert world coordinates to canvas coordinates"""
            cx = margin + (x - min_x) * scale
            cy = self.height() - margin - (y - min_y) * scale
            return int(cx), int(cy)
        
        # Draw grid
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        for i in range(int(min_x), int(max_x) + 1):
            x1, y1 = to_canvas(i, min_y)
            x2, y2 = to_canvas(i, max_y)
            painter.drawLine(x1, y1, x2, y2)
        
        for i in range(int(min_y), int(max_y) + 1):
            x1, y1 = to_canvas(min_x, i)
            x2, y2 = to_canvas(max_x, i)
            painter.drawLine(x1, y1, x2, y2)
        
        # Draw history trail
        if len(self.history) > 1:
            painter.setPen(QPen(QColor(100, 255, 100, 100), 2))
            for i in range(len(self.history) - 1):
                x1, y1 = to_canvas(self.history[i][0], self.history[i][1])
                x2, y2 = to_canvas(self.history[i+1][0], self.history[i+1][1])
                painter.drawLine(x1, y1, x2, y2)
        
        # Draw anchors
        painter.setFont(QFont('Arial', 10))
        for anchor in self.anchors:
            cx, cy = to_canvas(anchor['x'], anchor['y'])
            
            # Anchor circle
            painter.setPen(QPen(QColor(50, 150, 255), 2))
            painter.setBrush(QColor(50, 150, 255))
            painter.drawEllipse(cx - 8, cy - 8, 16, 16)
            
            # Label
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(cx + 15, cy - 10, anchor['label'])
            painter.drawText(cx + 15, cy + 5, f"({anchor['x']:.1f}, {anchor['y']:.1f})")
        
        # Draw current position
        px, py = to_canvas(self.position['x'], self.position['y'])
        
        # Error circle
        error_radius = int(self.position['error'] * scale)
        painter.setPen(QPen(QColor(255, 100, 100, 100), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(px - error_radius, py - error_radius, 
                          error_radius * 2, error_radius * 2)
        
        # Position marker
        painter.setPen(QPen(QColor(0, 255, 0), 3))
        painter.setBrush(QColor(0, 255, 0))
        painter.drawEllipse(px - 10, py - 10, 20, 20)
        
        # Crosshair
        painter.drawLine(px - 15, py, px + 15, py)
        painter.drawLine(px, py - 15, px, py + 15)
        
        # Draw coordinates
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(px + 20, py - 20, 
                        f"({self.position['x']:.2f}, {self.position['y']:.2f})")


class MainWindow(QMainWindow):
    """Main dashboard window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UWB Position Dashboard")
        self.setGeometry(100, 100, 1200, 800)
        
        # Data
        self.anchors = [
            {'x': 0.0, 'y': 0.0, 'label': 'A0'},
            {'x': 5.0, 'y': 0.0, 'label': 'A1'},
            {'x': 5.0, 'y': 5.0, 'label': 'A2'},
            {'x': 0.0, 'y': 5.0, 'label': 'A3'},
        ]
        
        self.position = {'x': 0, 'y': 0, 'z': 0, 'error': 0, 'timestamp': 0}
        self.frame_count = 0
        self.start_time = time.time()
        
        # UDP receiver
        self.udp_receiver = None
        
        # Setup UI
        self.setup_ui()
        
        # FPS timer
        self.fps_timer = QTimer()
        self.fps_timer.timeout.connect(self.update_stats)
        self.fps_timer.start(1000)  # Update every second
    
    def setup_ui(self):
        """Setup UI components"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)
        
        # Left panel - Canvas
        self.canvas = PositionCanvas()
        self.canvas.set_anchors(self.anchors)
        main_layout.addWidget(self.canvas, stretch=2)
        
        # Right panel - Controls
        right_panel = QVBoxLayout()
        main_layout.addLayout(right_panel, stretch=1)
        
        # Connection group
        conn_group = QGroupBox("Connection")
        conn_layout = QVBoxLayout()
        
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("UDP Port:"))
        self.port_input = QLineEdit("5005")
        self.port_input.setMaximumWidth(100)
        port_layout.addWidget(self.port_input)
        conn_layout.addLayout(port_layout)
        
        self.connect_btn = QPushButton("Start Listening")
        self.connect_btn.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.connect_btn)
        
        self.status_label = QLabel("Status: Disconnected")
        self.status_label.setStyleSheet("color: red;")
        conn_layout.addWidget(self.status_label)
        
        conn_group.setLayout(conn_layout)
        right_panel.addWidget(conn_group)
        
        # Position group
        pos_group = QGroupBox("Current Position")
        pos_layout = QGridLayout()
        
        pos_layout.addWidget(QLabel("X:"), 0, 0)
        self.x_label = QLabel("0.000 m")
        self.x_label.setStyleSheet("font-family: monospace; color: #4ade80;")
        pos_layout.addWidget(self.x_label, 0, 1)
        
        pos_layout.addWidget(QLabel("Y:"), 1, 0)
        self.y_label = QLabel("0.000 m")
        self.y_label.setStyleSheet("font-family: monospace; color: #4ade80;")
        pos_layout.addWidget(self.y_label, 1, 1)
        
        pos_layout.addWidget(QLabel("Z:"), 2, 0)
        self.z_label = QLabel("0.000 m")
        self.z_label.setStyleSheet("font-family: monospace; color: #4ade80;")
        pos_layout.addWidget(self.z_label, 2, 1)
        
        pos_layout.addWidget(QLabel("Error:"), 3, 0)
        self.error_label = QLabel("0.000 m")
        self.error_label.setStyleSheet("font-family: monospace; color: #f59e0b;")
        pos_layout.addWidget(self.error_label, 3, 1)
        
        pos_group.setLayout(pos_layout)
        right_panel.addWidget(pos_group)
        
        # Statistics group
        stats_group = QGroupBox("Statistics")
        stats_layout = QGridLayout()
        
        stats_layout.addWidget(QLabel("Frames:"), 0, 0)
        self.frames_label = QLabel("0")
        stats_layout.addWidget(self.frames_label, 0, 1)
        
        stats_layout.addWidget(QLabel("FPS:"), 1, 0)
        self.fps_label = QLabel("0")
        stats_layout.addWidget(self.fps_label, 1, 1)
        
        stats_layout.addWidget(QLabel("Uptime:"), 2, 0)
        self.uptime_label = QLabel("0s")
        stats_layout.addWidget(self.uptime_label, 2, 1)
        
        stats_group.setLayout(stats_layout)
        right_panel.addWidget(stats_group)
        
        # Anchors group
        anchor_group = QGroupBox("Anchors")
        anchor_layout = QVBoxLayout()
        
        self.anchor_table = QTableWidget()
        self.anchor_table.setColumnCount(3)
        self.anchor_table.setHorizontalHeaderLabels(["Label", "X (m)", "Y (m)"])
        self.anchor_table.setRowCount(len(self.anchors))
        
        for i, anchor in enumerate(self.anchors):
            self.anchor_table.setItem(i, 0, QTableWidgetItem(anchor['label']))
            self.anchor_table.setItem(i, 1, QTableWidgetItem(f"{anchor['x']:.2f}"))
            self.anchor_table.setItem(i, 2, QTableWidgetItem(f"{anchor['y']:.2f}"))
        
        self.anchor_table.cellChanged.connect(self.on_anchor_changed)
        anchor_layout.addWidget(self.anchor_table)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self.add_anchor)
        btn_layout.addWidget(add_btn)
        
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete_anchor)
        btn_layout.addWidget(del_btn)
        
        anchor_layout.addLayout(btn_layout)
        anchor_group.setLayout(anchor_layout)
        right_panel.addWidget(anchor_group)
        
        right_panel.addStretch()
    
    def toggle_connection(self):
        """Start/Stop UDP listening"""
        if self.udp_receiver is None or not self.udp_receiver.isRunning():
            # Start
            try:
                port = int(self.port_input.text())
                self.udp_receiver = UDPReceiver(port)
                self.udp_receiver.position_received.connect(self.on_position_received)
                self.udp_receiver.start()
                
                self.connect_btn.setText("Stop Listening")
                self.status_label.setText(f"Status: Listening on port {port}")
                self.status_label.setStyleSheet("color: green;")
                self.start_time = time.time()
                self.frame_count = 0
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to start: {e}")
        else:
            # Stop
            self.udp_receiver.stop()
            self.udp_receiver.wait()
            
            self.connect_btn.setText("Start Listening")
            self.status_label.setText("Status: Disconnected")
            self.status_label.setStyleSheet("color: red;")
    
    def on_position_received(self, position):
        """Handle received position data"""
        self.position = position
        self.frame_count += 1
        
        # Update labels
        self.x_label.setText(f"{position['x']:.3f} m")
        self.y_label.setText(f"{position['y']:.3f} m")
        self.z_label.setText(f"{position['z']:.3f} m")
        self.error_label.setText(f"{position['error']:.3f} m")
        
        # Update canvas
        self.canvas.update_position(position)
    
    def update_stats(self):
        """Update statistics labels"""
        self.frames_label.setText(str(self.frame_count))
        
        uptime = int(time.time() - self.start_time)
        fps = self.frame_count / uptime if uptime > 0 else 0
        self.fps_label.setText(f"{fps:.1f}")
        self.uptime_label.setText(f"{uptime}s")
    
    def on_anchor_changed(self, row, col):
        """Handle anchor table edit"""
        try:
            if col == 0:  # Label
                self.anchors[row]['label'] = self.anchor_table.item(row, col).text()
            else:  # X, Y
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
        event.accept()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()