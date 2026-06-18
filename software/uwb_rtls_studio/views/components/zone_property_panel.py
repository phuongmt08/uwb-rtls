"""
===============================================================================
  UWB RTLS Studio — Geofencing Floating Property Panel
===============================================================================
  File        : views/components/zone_property_panel.py
  Description : Floating overlay panel for editing geofence zone parameters.
===============================================================================
"""
import math
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QComboBox,
    QPushButton,
    QScrollArea,
    QWidget,
    QFormLayout,
)


class ZonePropertyPanel(QFrame):
    """Floating property panel widget that overlays on PositionCanvas."""

    closed = pyqtSignal()
    # Emitted when a property changes: (property_name, new_value)
    # e.g., ("name", "Room 1"), ("height", 3.0), ("color", "#F8FAFC"), ("speed_limit", 2.0)
    property_changed = pyqtSignal(str, object)
    
    # Emitted when an edge geometry changes: (edge_idx, length, angle_deg)
    edge_changed = pyqtSignal(int, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zone_id = None
        self.zone_type = "room"
        self.object_type = "room"
        self._dragging = False
        self._drag_offset = QPoint()
        self._user_moved = False
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        
        # Modern white look matching studio aesthetics
        self.setObjectName("ZonePropertyPanel")
        self.setStyleSheet("""
            QFrame#ZonePropertyPanel {
                background-color: #F8FAFC;
                border: 2px solid #0EA5E9;
                border-radius: 12px;
            }
            QWidget {
                background: transparent;
            }
            QLabel {
                font-family: 'Segoe UI';
                font-size: 12px;
                color: #1E293B;
                font-weight: bold;
                border: none;
                background: transparent;
            }
            QLabel#lbl_title {
                font-size: 13px;
                color: #0EA5E9;
                font-weight: bold;
            }
            QLabel#lbl_sec_title {
                color: #64748B;
                font-size: 11px;
                font-weight: bold;
                margin-top: 6px;
                border-bottom: 1px solid #E2E8F0;
                padding-bottom: 2px;
            }
            QLineEdit, QDoubleSpinBox, QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                color: #0F172A;
                padding: 4px 6px;
                font-size: 12px;
                font-family: 'Segoe UI';
            }
            QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1.5px solid #0EA5E9;
            }
            QPushButton#btn_close {
                background: transparent;
                color: #94A3B8;
                border: none;
                font-size: 16px;
                font-weight: bold;
                padding: 0px 4px;
            }
            QPushButton#btn_close:hover {
                color: #EF4444;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QWidget#scroll_widget {
                background: transparent;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #CBD5E1;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #94A3B8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                color: #0F172A;
                selection-background-color: #0EA5E9;
                selection-color: #FFFFFF;
                border: 1px solid #CBD5E1;
            }
            QComboBox QAbstractItemView::item {
                color: #0F172A;
                background-color: #FFFFFF;
            }
            QComboBox QAbstractItemView QWidget {
                color: #0F172A;
                background-color: #FFFFFF;
            }
        """)
        
        self.setFixedSize(280, 340)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 10)
        main_layout.setSpacing(6)
        
        # Header Row
        header_layout = QHBoxLayout()
        self.lbl_title = QLabel("Zone Properties", self)
        self.lbl_title.setObjectName("lbl_title")
        header_layout.addWidget(self.lbl_title)
        
        header_layout.addStretch()
        
        self.btn_close = QPushButton("×", self)
        self.btn_close.setObjectName("btn_close")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.closed.emit)
        header_layout.addWidget(self.btn_close)
        
        main_layout.addLayout(header_layout)
        
        # General Form layout
        self.form_widget = QWidget(self)
        self.form_layout = QFormLayout(self.form_widget)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(6)
        
        # Name field
        self.lbl_name = QLabel("Name:", self)
        self.txt_name = QLineEdit(self)
        self.txt_name.editingFinished.connect(self._on_name_changed)
        self.form_layout.addRow(self.lbl_name, self.txt_name)
        
        # Height field
        self.lbl_height = QLabel("Height (m):", self)
        self.sb_height = QDoubleSpinBox(self)
        self.sb_height.setRange(0.1, 20.0)
        self.sb_height.setSingleStep(0.1)
        self.sb_height.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.sb_height.editingFinished.connect(self._on_height_changed)
        self.form_layout.addRow(self.lbl_height, self.sb_height)
        
        # Speed Limit field
        self.lbl_speed = QLabel("Max Speed (m/s):", self)
        self.sb_speed = QDoubleSpinBox(self)
        self.sb_speed.setRange(0.0, 10.0)
        self.sb_speed.setSingleStep(0.1)
        self.sb_speed.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.sb_speed.editingFinished.connect(self._on_speed_changed)
        self.form_layout.addRow(self.lbl_speed, self.sb_speed)
        
        # Color Combo field
        self.lbl_color = QLabel("Theme Color:", self)
        self.cmb_color = QComboBox(self)
        self.cmb_color.currentIndexChanged.connect(self._on_color_changed)
        self.form_layout.addRow(self.lbl_color, self.cmb_color)
        
        main_layout.addWidget(self.form_widget)
        
        # Segment Dimensions Header
        lbl_dimensions = QLabel("Dimensions & Angles", self)
        lbl_dimensions.setObjectName("lbl_sec_title")
        main_layout.addWidget(lbl_dimensions)
        
        # Scroll area for edge dimensions
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scroll_widget")
        self.edges_layout = QVBoxLayout(self.scroll_content)
        self.edges_layout.setContentsMargins(0, 2, 4, 2)
        self.edges_layout.setSpacing(4)
        
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area)
        
        # Block signals temporarily during updates
        self._updating_ui = False

    def reset_user_position(self):
        self._user_moved = False

    def has_user_position(self):
        return self._user_moved

    def load_zone(self, zone):
        self._updating_ui = True
        self.zone_id = zone.id
        self.zone_type = zone.zone_type
        self.object_type = getattr(zone, "object_type", "zone")
        
        self.lbl_title.setText(f"{self.object_type.upper()}: {zone.name}")
        
        # Hide/Show fields based on type
        if self.object_type == "room":
            self.lbl_name.show()
            self.txt_name.show()
            self.txt_name.setText(zone.name)
            self.lbl_speed.hide()
            self.sb_speed.hide()
            self.lbl_height.hide()
            self.sb_height.hide()

            self.cmb_color.blockSignals(True)
            self.cmb_color.clear()
            self.cmb_color.addItem("White", "#F8FAFC")
            self.cmb_color.addItem("White Semi-transparent", "#F8FAFC_semi")
            is_semi = zone.color.endswith("_semi") or getattr(zone, "alpha", 255) < 150
            idx = 1 if is_semi else 0
            self.cmb_color.setCurrentIndex(idx)
            self.cmb_color.blockSignals(False)
        elif self.object_type == "wall":
            self.lbl_name.hide()
            self.txt_name.hide()
            self.lbl_speed.hide()
            self.sb_speed.hide()

            self.cmb_color.blockSignals(True)
            self.cmb_color.clear()
            self.cmb_color.addItem("Black", "#0F172A")
            self.cmb_color.addItem("Dark Gray", "#475569")
            idx = 1 if zone.color == "#475569" else 0
            self.cmb_color.setCurrentIndex(idx)
            self.cmb_color.blockSignals(False)
            self.lbl_height.show()
            self.sb_height.show()
            self.sb_height.setValue(max(0.1, zone.max_z - zone.min_z))
        else:  # allowed / forbidden zones
            self.lbl_name.show()
            self.txt_name.show()
            self.txt_name.setText(zone.name)
            
            self.lbl_height.hide()
            self.sb_height.hide()
            
            if self.zone_type == "allowed":
                self.lbl_speed.show()
                self.sb_speed.show()
                self.sb_speed.setValue(zone.speed_limit)
                
                # Setup bright colors
                self.cmb_color.blockSignals(True)
                self.cmb_color.clear()
                self.cmb_color.addItem("Green", "#22C55E")
                self.cmb_color.addItem("Purple", "#A855F7")
                self.cmb_color.addItem("Yellow", "#EAB308")
                self.cmb_color.addItem("Blue", "#3B82F6")
                
                # Select current color
                color_map = {"#22C55E": 0, "#A855F7": 1, "#EAB308": 2, "#3B82F6": 3}
                self.cmb_color.setCurrentIndex(color_map.get(zone.color, 0))
                self.cmb_color.blockSignals(False)
            else:  # forbidden/ban zone
                self.lbl_speed.hide()
                self.sb_speed.hide()
                
                # Setup colors for forbidden
                self.cmb_color.blockSignals(True)
                self.cmb_color.clear()
                self.cmb_color.addItem("Red", "#EF4444")
                self.cmb_color.addItem("Black", "#0F172A")
                self.cmb_color.addItem("Dark Gray", "#475569")
                
                color_map = {"#EF4444": 0, "#0F172A": 1, "#475569": 2}
                self.cmb_color.setCurrentIndex(color_map.get(zone.color, 0))
                self.cmb_color.blockSignals(False)

        # Clear existing dimension list
        self._clear_edges_layout()
        
        # Build dimensions list for each edge
        n = len(zone.points)
        if n >= 2:
            for i in range(n):
                pt1 = zone.points[i]
                pt2 = zone.points[(i + 1) % n]
                
                length = math.hypot(pt2[0] - pt1[0], pt2[1] - pt1[1])
                angle_rad = math.atan2(pt2[1] - pt1[1], pt2[0] - pt1[0])
                angle_deg = math.degrees(angle_rad) % 360
                
                self._add_edge_row(i, length, angle_deg)
                
        self._updating_ui = False

    def _clear_edges_layout(self):
        while self.edges_layout.count():
            item = self.edges_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_edge_row(self, idx, length, angle):
        row = QWidget()
        row.setStyleSheet("background: transparent; border: none;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        
        lbl = QLabel(f"E{idx+1}:", row)
        lbl.setMinimumWidth(26)
        layout.addWidget(lbl)
        
        # Length spinbox
        sb_len = QDoubleSpinBox(row)
        sb_len.setRange(0.05, 150.0)
        sb_len.setSingleStep(0.1)
        sb_len.setValue(length)
        sb_len.setSuffix("m")
        sb_len.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        sb_len.setProperty("edge_idx", idx)
        sb_len.setProperty("prop_type", "length")
        sb_len.editingFinished.connect(self._on_edge_edited)
        layout.addWidget(sb_len)
        
        # Angle spinbox
        sb_ang = QDoubleSpinBox(row)
        sb_ang.setRange(0.0, 359.9)
        sb_ang.setSingleStep(1.0)
        sb_ang.setValue(angle)
        sb_ang.setSuffix("°")
        sb_ang.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        sb_ang.setProperty("edge_idx", idx)
        sb_ang.setProperty("prop_type", "angle")
        sb_ang.editingFinished.connect(self._on_edge_edited)
        layout.addWidget(sb_ang)
        
        self.edges_layout.addWidget(row)

    def _on_name_changed(self):
        if self._updating_ui:
            return
        name = self.txt_name.text().strip()
        if name:
            self.lbl_title.setText(f"{self.object_type.upper()}: {name}")
            self.property_changed.emit("name", name)

    def _on_height_changed(self):
        if self._updating_ui:
            return
        height = self.sb_height.value()
        self.property_changed.emit("height", height)

    def _on_speed_changed(self):
        if self._updating_ui:
            return
        speed = self.sb_speed.value()
        self.property_changed.emit("speed_limit", speed)

    def _on_color_changed(self, idx):
        if self._updating_ui or idx < 0:
            return
        color = self.cmb_color.itemData(idx) or self.cmb_color.currentText()
        if self.object_type == "room" and idx == 1:
            color = "#F8FAFC_semi"
        self.property_changed.emit("color", color)

    def _on_edge_edited(self):
        if self._updating_ui:
            return
        sender = self.sender()
        if not sender:
            return
            
        edge_idx = sender.property("edge_idx")
        length = None
        angle = None
        
        for i in range(self.edges_layout.count()):
            row_widget = self.edges_layout.itemAt(i).widget()
            if row_widget:
                spinboxes = row_widget.findChildren(QDoubleSpinBox)
                match = False
                row_len_sb = None
                row_ang_sb = None
                for sb in spinboxes:
                    if sb.property("edge_idx") == edge_idx:
                        match = True
                        if sb.property("prop_type") == "length":
                            row_len_sb = sb
                        elif sb.property("prop_type") == "angle":
                            row_ang_sb = sb
                if match and row_len_sb and row_ang_sb:
                    length = row_len_sb.value()
                    angle = row_ang_sb.value()
                    break
                    
        if length is not None and angle is not None:
            self.edge_changed.emit(edge_idx, length, angle)

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.position().y() <= 34
            and not self.btn_close.geometry().contains(event.position().toPoint())
        ):
            self._dragging = True
            self._drag_offset = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            parent = self.parentWidget()
            if not parent:
                return

            new_pos = self.mapToParent(event.position().toPoint() - self._drag_offset)
            max_x = max(0, parent.width() - self.width())
            max_y = max(0, parent.height() - self.height())
            clamped_x = max(0, min(new_pos.x(), max_x))
            clamped_y = max(0, min(new_pos.y(), max_y))
            self.move(clamped_x, clamped_y)
            self._user_moved = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
