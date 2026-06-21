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
    QSizePolicy,
)


class ZonePropertyPanel(QFrame):
    """Floating property panel widget that overlays on PositionCanvas."""

    closed = pyqtSignal()
    # Emitted when a property changes: (property_name, new_value)
    # e.g., ("name", "Room 1"), ("height", 3.0), ("color", "#F8FAFC"), ("speed_limit", 2.0)
    property_changed = pyqtSignal(str, object)
    
    # Emitted when an edge geometry changes: (edge_idx, length, angle_deg)
    edge_changed = pyqtSignal(int, float, float)

    def __init__(self, parent=None, embedded=False):
        super().__init__(parent)
        self._embedded = embedded
        self.zone_id = None
        self.zone_type = "room"
        self.object_type = "room"
        self._shape_completed = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._user_moved = False
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        
        # Match the geofencing editor surface so the embedded panel feels native.
        self.setObjectName("ZonePropertyPanel")
        self.setStyleSheet("""
            QFrame#ZonePropertyPanel {
                background-color: #283246;
                border: 1px solid #3B4965;
                border-radius: 12px;
            }
            QWidget {
                background: transparent;
            }
            QLabel {
                font-family: 'Segoe UI';
                font-size: 12px;
                color: #E2E8F0;
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
                color: #94A3B8;
                font-size: 11px;
                font-weight: bold;
                margin-top: 6px;
                border-bottom: 1px solid #3B4965;
                padding-bottom: 2px;
            }
            QLineEdit, QDoubleSpinBox, QComboBox {
                background-color: #1F2937;
                border: 1px solid #475569;
                border-radius: 6px;
                color: #F8FAFC;
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
                background-color: #475569;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #64748B;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QComboBox QAbstractItemView {
                background-color: #1F2937;
                color: #F8FAFC;
                selection-background-color: #0EA5E9;
                selection-color: #FFFFFF;
                border: 1px solid #475569;
            }
            QComboBox QAbstractItemView::item {
                color: #F8FAFC;
                background-color: #1F2937;
            }
            QComboBox QAbstractItemView QWidget {
                color: #F8FAFC;
                background-color: #1F2937;
            }
        """)
        
        if embedded:
            self.setMinimumHeight(0)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            self.setFixedSize(280, 340)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 6, 10, 8)
        main_layout.setSpacing(4)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Header Row
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
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
        
        # Basic properties are always visible.
        self.lbl_area = QLabel("Area (m2):", self)
        self.val_area = QLabel("---", self)
        self.val_area.setStyleSheet("color: #38BDF8; font-family: 'Consolas'; font-weight: bold;")
        self.form_layout.addRow(self.lbl_area, self.val_area)

        self.lbl_color = QLabel("Theme Color:", self)
        self.cmb_color = QComboBox(self)
        self.cmb_color.currentIndexChanged.connect(self._on_color_changed)
        self.form_layout.addRow(self.lbl_color, self.cmb_color)

        # These fields are revealed after the shape is completed.
        self.lbl_height = QLabel("Height (m):", self)
        self.sb_height = QDoubleSpinBox(self)
        self.sb_height.setRange(0.1, 20.0)
        self.sb_height.setSingleStep(0.1)
        self.sb_height.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.sb_height.editingFinished.connect(self._on_height_changed)
        self.form_layout.addRow(self.lbl_height, self.sb_height)

        self.lbl_thickness = QLabel("Thickness (m):", self)
        self.sb_thickness = QDoubleSpinBox(self)
        self.sb_thickness.setRange(0.01, 10.0)
        self.sb_thickness.setSingleStep(0.05)
        self.sb_thickness.setDecimals(3)
        self.sb_thickness.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.sb_thickness.editingFinished.connect(self._on_thickness_changed)
        self.form_layout.addRow(self.lbl_thickness, self.sb_thickness)

        self.lbl_speed = QLabel("Max Speed (m/s):", self)
        self.sb_speed = QDoubleSpinBox(self)
        self.sb_speed.setRange(0.0, 10.0)
        self.sb_speed.setSingleStep(0.1)
        self.sb_speed.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.sb_speed.editingFinished.connect(self._on_speed_changed)
        self.form_layout.addRow(self.lbl_speed, self.sb_speed)

        if self._embedded:
            self.btn_close.hide()
            self.btn_close.setFixedSize(0, 0)
        
        main_layout.addWidget(self.form_widget)
        
        # Segment Dimensions Header
        self.lbl_dimensions = QLabel("Dimensions & Angles", self)
        self.lbl_dimensions.setObjectName("lbl_sec_title")
        main_layout.addWidget(self.lbl_dimensions)
        
        # Scroll area for edge dimensions
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.scroll_area.setMinimumHeight(120)
        self.scroll_area.setMaximumHeight(260)
        
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scroll_widget")
        self.edges_layout = QVBoxLayout(self.scroll_content)
        self.edges_layout.setContentsMargins(0, 2, 4, 2)
        self.edges_layout.setSpacing(4)
        
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area)
        
        # Block signals temporarily during updates
        self._updating_ui = False
        self.load_draft("room")

    def _set_completed_fields_visible(self, completed):
        self._shape_completed = bool(completed)
        is_wall = self.object_type == "wall" and completed
        is_rule_zone = self.object_type == "zone" and completed
        self.lbl_height.setVisible(is_wall)
        self.sb_height.setVisible(is_wall)
        self.lbl_thickness.setVisible(is_wall)
        self.sb_thickness.setVisible(is_wall)
        self.lbl_speed.setVisible(is_rule_zone and self.zone_type == "allowed")
        self.sb_speed.setVisible(is_rule_zone and self.zone_type == "allowed")
        self.lbl_dimensions.setVisible(completed)
        self.scroll_area.setVisible(completed)

    def load_draft(self, object_type):
        """Show common fields while waiting for a room/wall to be completed."""
        if object_type not in {"room", "wall"}:
            return
        self._updating_ui = True
        self.zone_id = None
        self.zone_type = object_type
        self.object_type = object_type
        self.lbl_title.setText(f"{object_type.upper()} PROPERTIES")
        self.txt_name.clear()
        self.txt_name.setPlaceholderText("---")
        self.val_area.setText("---")
        self.cmb_color.blockSignals(True)
        self.cmb_color.clear()
        colors = (
            [("White", "#F8FAFC"), ("Light Gray", "#E2E8F0"), ("Blue", "#3B82F6"), ("Green", "#22C55E"), ("Purple", "#A855F7")]
            if object_type == "room"
            else [("Black", "#0F172A"), ("Dark Gray", "#475569"), ("Gray", "#64748B"), ("Red", "#EF4444"), ("Blue", "#3B82F6")]
        )
        for name, value in colors:
            self.cmb_color.addItem(name, value)
        self.cmb_color.blockSignals(False)
        self._clear_edges_layout()
        self._set_completed_fields_visible(False)
        self._adjust_dimensions_area_height(0)
        self._adjust_panel_height(False, 0)
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
        area = 0.0
        for idx, (x1, y1) in enumerate(zone.points):
            x2, y2 = zone.points[(idx + 1) % len(zone.points)]
            area += x1 * y2 - x2 * y1
        self.val_area.setText(f"{abs(area) * 0.5:.3f}")
        
        self.lbl_title.setText(f"{self.object_type.upper()}: {zone.name}")
        self.txt_name.setPlaceholderText("---")
        
        # Hide/Show fields based on type
        if self.object_type == "room":
            self.lbl_name.show()
            self.txt_name.show()
            self.txt_name.setText(zone.name)
            self.lbl_speed.hide()
            self.sb_speed.hide()
            self.lbl_height.hide()
            self.sb_height.hide()
            self.lbl_thickness.hide()
            self.sb_thickness.hide()

            self.cmb_color.blockSignals(True)
            self.cmb_color.clear()
            self.cmb_color.addItem("White", "#F8FAFC")
            self.cmb_color.addItem("Light Gray", "#E2E8F0")
            self.cmb_color.addItem("Blue", "#3B82F6")
            self.cmb_color.addItem("Green", "#22C55E")
            self.cmb_color.addItem("Purple", "#A855F7")
            is_semi = zone.color.endswith("_semi") or getattr(zone, "alpha", 255) < 150
            color_map = {"#F8FAFC": 0, "#E2E8F0": 1, "#3B82F6": 2, "#22C55E": 3, "#A855F7": 4}
            idx = color_map.get(zone.color.replace("_semi", ""), 0)
            self.cmb_color.setCurrentIndex(idx)
            self.cmb_color.blockSignals(False)
        elif self.object_type == "wall":
            self.lbl_name.show()
            self.txt_name.show()
            self.txt_name.setText(zone.name)
            self.lbl_speed.hide()
            self.sb_speed.hide()

            self.cmb_color.blockSignals(True)
            self.cmb_color.clear()
            self.cmb_color.addItem("Black", "#0F172A")
            self.cmb_color.addItem("Dark Gray", "#475569")
            self.cmb_color.addItem("Gray", "#64748B")
            self.cmb_color.addItem("Red", "#EF4444")
            self.cmb_color.addItem("Blue", "#3B82F6")
            color_map = {"#0F172A": 0, "#475569": 1, "#64748B": 2, "#EF4444": 3, "#3B82F6": 4}
            idx = color_map.get(zone.color, 0)
            self.cmb_color.setCurrentIndex(idx)
            self.cmb_color.blockSignals(False)
            self.lbl_height.show()
            self.sb_height.show()
            self.sb_height.setValue(max(0.1, zone.max_z - zone.min_z))
            self.lbl_thickness.show()
            self.sb_thickness.show()
            self.sb_thickness.setValue(max(0.01, float(getattr(zone, "thickness", 0.2))))
        else:  # allowed / forbidden zones
            self.lbl_name.show()
            self.txt_name.show()
            self.txt_name.setText(zone.name)
            
            self.lbl_height.hide()
            self.sb_height.hide()
            self.lbl_thickness.hide()
            self.sb_thickness.hide()
            
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

        self._set_completed_fields_visible(n >= 3)
        self._adjust_dimensions_area_height(n)
        self._adjust_panel_height(n >= 3, n)
        self._updating_ui = False

    def _adjust_panel_height(self, completed, edge_count):
        if not self._embedded:
            return
        layout = self.layout()
        if layout is not None:
            layout.activate()
        base_height = self.sizeHint().height()
        if not completed:
            target_height = max(160, min(base_height + 4, 210))
        else:
            target_height = max(220, min(base_height + 8, 420))
        self.setMinimumHeight(target_height)
        self.setMaximumHeight(target_height)

    def _adjust_dimensions_area_height(self, edge_count):
        if not self._shape_completed or edge_count <= 0:
            self.scroll_area.setMinimumHeight(0)
            self.scroll_area.setMaximumHeight(0)
            return
        visible_rows = min(max(edge_count, 3), 6)
        target_height = 12 + (visible_rows * 40)
        self.scroll_area.setMinimumHeight(target_height)
        self.scroll_area.setMaximumHeight(target_height)

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
        
        lbl = QLabel(f"D{idx+1}:", row)
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

    def _on_thickness_changed(self):
        if self._updating_ui:
            return
        self.property_changed.emit("thickness", self.sb_thickness.value())

    def _on_speed_changed(self):
        if self._updating_ui:
            return
        speed = self.sb_speed.value()
        self.property_changed.emit("speed_limit", speed)

    def _on_color_changed(self, idx):
        if self._updating_ui or idx < 0:
            return
        color = self.cmb_color.itemData(idx) or self.cmb_color.currentText()
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
