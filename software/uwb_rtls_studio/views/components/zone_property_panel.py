"""
===============================================================================
  UWB RTLS Studio — Geofencing Floating Property Panel
===============================================================================
  File        : views/components/zone_property_panel.py
  Description : Floating overlay panel for editing geofence zone parameters.
===============================================================================
"""
import math
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QColor
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
    QColorDialog,
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
        self._height_refresh_pending = False
        
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
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        else:
            self.setFixedSize(280, 340)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 3, 10, 3)
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
        self.form_layout.setSpacing(4)
        
        # Name field
        self.lbl_name = QLabel("Name:", self)
        self.txt_name = QLineEdit(self)
        self.txt_name.editingFinished.connect(self._on_name_changed)
        self.form_layout.addRow(self.lbl_name, self.txt_name)
        
        # Basic properties are always visible.
        self.lbl_area = QLabel("Area (m²):", self)
        self.val_area = QLabel("---", self)
        self.lbl_area.setMinimumHeight(22)
        self.val_area.setMinimumHeight(22)
        self.val_area.setStyleSheet(
            "color: #38BDF8; font-size: 13px; font-weight: bold; padding: 2px 0;"
        )
        self.form_layout.addRow(self.lbl_area, self.val_area)

        self.lbl_perimeter = QLabel("Perimeter (m):", self)
        self.val_perimeter = QLabel("---", self)
        self.lbl_perimeter.setMinimumHeight(22)
        self.val_perimeter.setMinimumHeight(22)
        self.val_perimeter.setStyleSheet(
            "color: #38BDF8; font-size: 13px; font-weight: bold; padding: 2px 0;"
        )
        self.form_layout.addRow(self.lbl_perimeter, self.val_perimeter)

        self.lbl_color = QLabel("Theme Color:", self)
        self.cmb_color = QComboBox(self)
        self.cmb_color.currentIndexChanged.connect(self._on_color_changed)
        self.form_layout.addRow(self.lbl_color, self.cmb_color)
        self.btn_choose_color = QPushButton("Choose color...", self)
        self.btn_choose_color.clicked.connect(self._choose_custom_color)
        self.form_layout.addRow(QLabel("Custom:", self), self.btn_choose_color)

        self.lbl_object_shape = QLabel("Shape:", self)
        self.cmb_object_shape = QComboBox(self)
        self.cmb_object_shape.addItem("Polygon", "polygon")
        self.cmb_object_shape.addItem("Circle", "circle")
        self.cmb_object_shape.currentIndexChanged.connect(self._on_object_shape_changed)
        self.form_layout.addRow(self.lbl_object_shape, self.cmb_object_shape)

        self.lbl_object_kind = QLabel("Object kind:", self)
        self.cmb_object_kind = QComboBox(self)
        self.cmb_object_kind.addItem("Generic", "generic")
        self.cmb_object_kind.addItem("Stairs", "stairs")
        self.cmb_object_kind.currentIndexChanged.connect(self._on_object_kind_changed)
        self.form_layout.addRow(self.lbl_object_kind, self.cmb_object_kind)
        self.lbl_object_direction = QLabel("Direction:", self)
        self.cmb_object_direction = QComboBox(self)
        self.cmb_object_direction.addItem("Up", "up")
        self.cmb_object_direction.addItem("Down", "down")
        self.cmb_object_direction.currentIndexChanged.connect(self._on_object_direction_changed)
        self.form_layout.addRow(self.lbl_object_direction, self.cmb_object_direction)

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

        self.lbl_wall_mode = QLabel("Wall behavior:", self)
        self.cmb_wall_mode = QComboBox(self)
        self.cmb_wall_mode.addItem("Boundary outside room", "boundary_outside")
        self.cmb_wall_mode.addItem("Internal partition", "internal_partition")
        self.cmb_wall_mode.addItem("Free-standing", "free_standing")
        self.cmb_wall_mode.currentIndexChanged.connect(self._on_wall_mode_changed)
        self.form_layout.addRow(self.lbl_wall_mode, self.cmb_wall_mode)

        self.lbl_wall_host_room = QLabel("Host room:", self)
        self.cmb_wall_host_room = QComboBox(self)
        self.cmb_wall_host_room.currentIndexChanged.connect(self._on_wall_host_room_changed)
        self.form_layout.addRow(self.lbl_wall_host_room, self.cmb_wall_host_room)

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
        is_object = self.object_type == "object"
        is_rule_zone = self.object_type == "zone" and completed
        self.lbl_height.setVisible((is_wall or is_object) and completed)
        self.sb_height.setVisible((is_wall or is_object) and completed)
        self.lbl_thickness.setVisible(is_wall)
        self.sb_thickness.setVisible(is_wall)
        self.lbl_wall_mode.setVisible(is_wall)
        self.cmb_wall_mode.setVisible(is_wall)
        self.lbl_wall_host_room.setVisible(is_wall)
        self.cmb_wall_host_room.setVisible(is_wall)
        self.lbl_object_shape.setVisible(is_object)
        self.cmb_object_shape.setVisible(is_object)
        self.lbl_object_kind.setVisible(is_object)
        self.cmb_object_kind.setVisible(is_object)
        is_stairs = is_object and self.cmb_object_kind.currentData() == "stairs"
        self.lbl_object_direction.setVisible(is_stairs)
        self.cmb_object_direction.setVisible(is_stairs)
        self.lbl_speed.setVisible(is_rule_zone and self.zone_type == "allowed")
        self.sb_speed.setVisible(is_rule_zone and self.zone_type == "allowed")
        self.lbl_dimensions.setVisible(completed)
        self.scroll_area.setVisible(completed)

    def load_draft(self, object_type):
        """Show common fields while waiting for a room/wall to be completed."""
        if object_type not in {"room", "wall", "object"}:
            return
        self._updating_ui = True
        self.zone_id = None
        self.zone_type = object_type
        self.object_type = object_type
        if object_type == "object":
            self.cmb_object_shape.setCurrentIndex(0)
            self.cmb_object_kind.setCurrentIndex(0)
            self.cmb_object_direction.setCurrentIndex(0)
            self.sb_height.setValue(1.0)
        self.lbl_wall_mode.hide()
        self.cmb_wall_mode.hide()
        self.lbl_wall_host_room.hide()
        self.cmb_wall_host_room.hide()
        self.lbl_title.setText(f"{object_type.upper()} PROPERTIES")
        self.txt_name.clear()
        self.txt_name.setPlaceholderText("---")
        self.val_area.setText("---")
        self.val_perimeter.setText("---")
        self.cmb_color.blockSignals(True)
        self.cmb_color.clear()
        colors = (
            [("White", "#F8FAFC"), ("Light Gray", "#E2E8F0"), ("Blue", "#3B82F6"), ("Green", "#22C55E"), ("Purple", "#A855F7")]
            if object_type == "room"
            else [("Orange", "#F59E0B"), ("Amber", "#D97706"), ("Blue", "#3B82F6"), ("Green", "#22C55E"), ("Gray", "#64748B")]
            if object_type == "object"
            else [("Black", "#0F172A"), ("Dark Gray", "#475569"), ("Gray", "#64748B"), ("Red", "#EF4444"), ("Blue", "#3B82F6")]
        )
        for name, value in colors:
            self.cmb_color.addItem(name, value)
        self.cmb_color.blockSignals(False)
        self._update_color_button(self.cmb_color.currentData() or "#64748B")
        self._clear_edges_layout()
        self._set_completed_fields_visible(False)
        self._adjust_dimensions_area_height(0)
        self._adjust_panel_height(False, 0)
        self._updating_ui = False
        self.schedule_height_refresh()

    def reset_user_position(self):
        self._user_moved = False

    def has_user_position(self):
        return self._user_moved

    def load_zone(self, zone):
        self._updating_ui = True
        self.zone_id = zone.id
        self.zone_type = zone.zone_type
        self.object_type = getattr(zone, "object_type", "zone")
        self.val_area.setText(f"{zone.polygon_area_m2():.3f}")
        self.val_perimeter.setText(f"{zone.polygon_perimeter_m():.3f}")
        
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
            self.lbl_wall_mode.show()
            self.cmb_wall_mode.show()
            self.lbl_wall_host_room.show()
            self.cmb_wall_host_room.show()

            self.cmb_wall_mode.blockSignals(True)
            mode_idx = self.cmb_wall_mode.findData(getattr(zone, "wall_mode", "free_standing"))
            self.cmb_wall_mode.setCurrentIndex(max(mode_idx, 0))
            self.cmb_wall_mode.blockSignals(False)

            self.cmb_wall_host_room.blockSignals(True)
            host_idx = self.cmb_wall_host_room.findData(getattr(zone, "host_room_id", ""))
            self.cmb_wall_host_room.setCurrentIndex(max(host_idx, 0))
            self.cmb_wall_host_room.blockSignals(False)
        elif self.object_type == "object":
            self.lbl_name.show()
            self.txt_name.show()
            self.txt_name.setText(zone.name)
            self.lbl_speed.hide()
            self.sb_speed.hide()
            self.lbl_thickness.hide()
            self.sb_thickness.hide()
            self.lbl_height.show()
            self.sb_height.show()
            self.sb_height.setValue(max(0.1, zone.max_z - zone.min_z))
            self.cmb_color.blockSignals(True)
            self.cmb_color.clear()
            for name, value in (("Orange", "#F59E0B"), ("Amber", "#D97706"), ("Blue", "#3B82F6"), ("Green", "#22C55E"), ("Gray", "#64748B")):
                self.cmb_color.addItem(name, value)
            current_color = zone.color if QColor(zone.color).isValid() else "#F59E0B"
            color_idx = self.cmb_color.findData(current_color)
            if color_idx < 0:
                self.cmb_color.addItem(f"Custom {current_color}", current_color)
                color_idx = self.cmb_color.count() - 1
            self.cmb_color.setCurrentIndex(color_idx)
            self.cmb_color.blockSignals(False)
            shape_idx = self.cmb_object_shape.findData(getattr(zone, "shape_kind", "polygon"))
            self.cmb_object_shape.setCurrentIndex(max(shape_idx, 0))
            kind_idx = self.cmb_object_kind.findData(getattr(zone, "object_subtype", "generic"))
            self.cmb_object_kind.setCurrentIndex(max(kind_idx, 0))
            direction_idx = self.cmb_object_direction.findData(getattr(zone, "object_direction", "up"))
            self.cmb_object_direction.setCurrentIndex(max(direction_idx, 0))
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

        self._update_color_button(zone.color)

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
        self.schedule_height_refresh()

    def schedule_height_refresh(self):
        """Re-measure after Qt finishes the first-show layout pass."""
        if not self._embedded:
            return
        QTimer.singleShot(0, self._refresh_height_after_layout)
        QTimer.singleShot(80, self._refresh_height_after_layout)

    def _refresh_height_after_layout(self):
        if not self._embedded:
            return
        self.form_widget.updateGeometry()
        self.form_layout.invalidate()
        self.form_layout.activate()
        self._adjust_panel_height(self._shape_completed, self.edges_layout.count())
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().invalidate()
            parent.layout().activate()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_height_refresh()

    def _adjust_panel_height(self, completed, edge_count):
        if not self._embedded:
            return
        layout = self.layout()
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        if layout is not None:
            layout.invalidate()
            layout.activate()
            target_height = max(110, layout.sizeHint().height() + 4)
        else:
            target_height = max(110, self.sizeHint().height() + 4)
        self.setMinimumHeight(target_height)
        self.setMaximumHeight(target_height)
        self.updateGeometry()

    def _adjust_dimensions_area_height(self, edge_count):
        if not self._shape_completed or edge_count <= 0:
            self.scroll_area.setMinimumHeight(0)
            self.scroll_area.setMaximumHeight(0)
            return
        visible_rows = min(max(edge_count, 3), 6)
        target_height = 8 + (visible_rows * 34)
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

    def _update_color_button(self, color):
        value = QColor(str(color).replace("_semi", ""))
        if not value.isValid():
            value = QColor("#64748B")
        self.btn_choose_color.setToolTip(f"Current color: {value.name().upper()}")
        red, green, blue = value.red(), value.green(), value.blue()
        hover_red = min(255, int(red * 1.12) + 8)
        hover_green = min(255, int(green * 1.12) + 8)
        hover_blue = min(255, int(blue * 1.12) + 8)
        text_color = "#0F172A" if value.lightness() > 155 else "#F8FAFC"
        self.btn_choose_color.setStyleSheet(
            f"QPushButton {{ background: rgba({red}, {green}, {blue}, 190); color: {text_color}; "
            f"border: 1px solid rgba({red}, {green}, {blue}, 245); border-radius: 5px; font-weight: bold; }} "
            f"QPushButton:hover {{ background: rgba({hover_red}, {hover_green}, {hover_blue}, 220); "
            f"border-color: rgb({hover_red}, {hover_green}, {hover_blue}); }}"
        )

    def _choose_custom_color(self):
        current = self.cmb_color.currentData() or "#64748B"
        color = QColorDialog.getColor(QColor(str(current).replace("_semi", "")), self, "Select display color")
        if not color.isValid():
            return
        value = color.name().upper()
        self.cmb_color.blockSignals(True)
        idx = self.cmb_color.findData(value)
        if idx < 0:
            self.cmb_color.addItem(f"Custom {value}", value)
            idx = self.cmb_color.count() - 1
        self.cmb_color.setCurrentIndex(idx)
        self.cmb_color.blockSignals(False)
        self._update_color_button(value)
        if not self._updating_ui:
            self.property_changed.emit("color", value)

    def _on_color_changed(self, idx):
        if self._updating_ui or idx < 0:
            return
        color = self.cmb_color.itemData(idx) or self.cmb_color.currentText()
        self._update_color_button(color)
        self.property_changed.emit("color", color)

    def _on_object_shape_changed(self, _idx):
        if not self._updating_ui:
            self.property_changed.emit("shape_kind", self.cmb_object_shape.currentData() or "polygon")

    def _on_object_kind_changed(self, _idx):
        self._set_completed_fields_visible(self._shape_completed)
        if not self._updating_ui:
            self.property_changed.emit("object_subtype", self.cmb_object_kind.currentData() or "generic")

    def _on_object_direction_changed(self, _idx):
        if not self._updating_ui:
            self.property_changed.emit("object_direction", self.cmb_object_direction.currentData() or "up")

    def _on_wall_mode_changed(self, _idx):
        if not self._updating_ui:
            self.property_changed.emit("wall_mode", self.cmb_wall_mode.currentData() or "free_standing")

    def _on_wall_host_room_changed(self, _idx):
        if not self._updating_ui:
            self.property_changed.emit("host_room_id", self.cmb_wall_host_room.currentData() or None)

    def set_rooms_list(self, rooms):
        """Populate the host room combo box with available rooms."""
        self.cmb_wall_host_room.blockSignals(True)
        self.cmb_wall_host_room.clear()
        self.cmb_wall_host_room.addItem("None", "")
        for room in rooms or []:
            self.cmb_wall_host_room.addItem(room.name, room.id)
        self.cmb_wall_host_room.blockSignals(False)

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
