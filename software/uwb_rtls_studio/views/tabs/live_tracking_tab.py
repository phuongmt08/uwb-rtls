"""
==============================================================================
  UWB RTLS Studio - Live Tracking Tab View
==============================================================================
"""
import os
import time
import uuid

from PyQt6 import uic
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QTimer
from PyQt6.QtWidgets import (
    QLabel,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QFileDialog,
    QFrame,
    QCheckBox,
)

from views.components.position_canvas import PositionCanvas
from models.geofence_model import GeofenceZone
from views.components.geofence_editor import GeofenceEditorWidget


UI_FILE = os.path.join(os.path.dirname(__file__), "..", "ui", "live_tracking_tab.ui")


class LiveTrackingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        self._frame_count = 0
        self._start_time = time.time()
        self._is_ranging = False
        self._last_z = 0.0
        self._last_rms = 0.0
        self._last_stats = {}
        self.sidebar_expanded = True
        self._is_developer_mode = False

        uic.loadUi(UI_FILE, self)
        self._setup_dynamic_metrics()

        self._canvas = self.position_canvas
        self._canvas.parent_tab = self

        self._setup_geofencing_ui()

        self.warning_label.setVisible(False)
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar)
        self.btn_start.clicked.connect(self._start_ranging)
        self.btn_stop.clicked.connect(self._stop_ranging)
        self.btn_clear.clicked.connect(self._canvas.clear_trail)

        self.header_widget.raise_()
        self.right_widget.raise_()
        self.btn_toggle_sidebar.raise_()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_sidebar_geometry()

    def update_sidebar_geometry(self):
        panel_width = 380
        panel_height = self.height() - 20
        target_x = self.width() - panel_width - 10 if self.sidebar_expanded else self.width()

        self.right_widget.setGeometry(target_x, 10, panel_width, panel_height)

        button_width = self.btn_toggle_sidebar.width()
        button_height = self.btn_toggle_sidebar.height()
        self.btn_toggle_sidebar.setGeometry(
            target_x - button_width,
            (self.height() - button_height) // 2,
            button_width,
            button_height,
        )

        header_width = max(
            self.width() - 20 - (panel_width + 10 if self.sidebar_expanded else 0),
            100,
        )
        self.header_widget.setGeometry(10, 10, header_width, 40)

    def _make_metric_label(self, text: str, color: str = "#94A3B8", bold: bool = False) -> QLabel:
        label = QLabel(text, self)
        weight = "bold" if bold else "normal"
        label.setStyleSheet(
            f"font-family: 'Consolas'; font-size: 14px; font-weight: {weight}; "
            f"color: {color}; background-color: transparent;"
        )
        return label

    def _add_metric_row(self, grid, row: int, title: str, value_label: QLabel):
        title_label = QLabel(title, self)
        title_label.setStyleSheet("font-size: 13px; color: #94A3B8; background-color: transparent;")
        grid.addWidget(title_label, row, 0)
        grid.addWidget(value_label, row, 1)

    def _setup_dynamic_metrics(self):
        self.tril_xy_label = self._make_metric_label("0.000, 0.000 m", "#38BDF8", True)
        self.raw_yaw_label = self._make_metric_label("0.000 deg", "#F472B6", True)
        self.fusion_ts_label = self._make_metric_label("0 ms", "#CBD5E1", True)

        self.grp_fusion = QLabel("FUSION", self)
        self.grp_fusion.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #64748B; "
            "margin-top: 5px; background-color: transparent;"
        )
        self.pos_grid.addWidget(self.grp_fusion, 16, 0, 1, 2)
        self._add_metric_row(self.pos_grid, 17, "TRIL XY:", self.tril_xy_label)
        self._add_metric_row(self.pos_grid, 18, "Raw Yaw:", self.raw_yaw_label)
        self._add_metric_row(self.pos_grid, 19, "Fusion TS:", self.fusion_ts_label)

        self.success_label = self._make_metric_label("0", "#10B981", True)
        self.failed_label = self._make_metric_label("0", "#F87171", True)
        self.timeout_label = self._make_metric_label("0", "#F59E0B", True)
        self.period_label = self._make_metric_label("0 ms", "#60A5FA", True)
        self.success_rate_label = self._make_metric_label("0.0%", "#10B981", True)
        self.avg_rssi_label = self._make_metric_label("0 dBm", "#A78BFA", True)
        self.last_range_time_label = self._make_metric_label("0 ms", "#CBD5E1", True)

        self._add_metric_row(self.stats_grid, 3, "Success:", self.success_label)
        self._add_metric_row(self.stats_grid, 4, "Failed:", self.failed_label)
        self._add_metric_row(self.stats_grid, 5, "Timeout:", self.timeout_label)
        self._add_metric_row(self.stats_grid, 6, "Period:", self.period_label)
        self._add_metric_row(self.stats_grid, 7, "Success Rate:", self.success_rate_label)
        self._add_metric_row(self.stats_grid, 8, "Avg RSSI:", self.avg_rssi_label)
        self._add_metric_row(self.stats_grid, 9, "Last Range:", self.last_range_time_label)

    def toggle_sidebar(self):
        self.sidebar_expanded = not self.sidebar_expanded
        self.btn_toggle_sidebar.setText(">" if self.sidebar_expanded else "<")

        panel_width = 380
        panel_height = self.height() - 20
        end_x = self.width() - panel_width - 10 if self.sidebar_expanded else self.width()

        button_width = self.btn_toggle_sidebar.width()
        button_height = self.btn_toggle_sidebar.height()

        self.anim = QPropertyAnimation(self.right_widget, b"geometry")
        self.anim.setDuration(250)
        self.anim.setStartValue(self.right_widget.geometry())
        self.anim.setEndValue(QRect(end_x, 10, panel_width, panel_height))
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.btn_anim = QPropertyAnimation(self.btn_toggle_sidebar, b"geometry")
        self.btn_anim.setDuration(250)
        self.btn_anim.setStartValue(self.btn_toggle_sidebar.geometry())
        self.btn_anim.setEndValue(
            QRect(
                end_x - button_width,
                (self.height() - button_height) // 2,
                button_width,
                button_height,
            )
        )
        self.btn_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        header_width = max(
            self.width() - 20 - (panel_width + 10 if self.sidebar_expanded else 0),
            100,
        )
        self.header_anim = QPropertyAnimation(self.header_widget, b"geometry")
        self.header_anim.setDuration(250)
        self.header_anim.setStartValue(self.header_widget.geometry())
        self.header_anim.setEndValue(QRect(10, 10, header_width, 40))
        self.header_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.anim.start()
        self.btn_anim.start()
        self.header_anim.start()
        self._canvas.auto_fit()

    def set_viewmodel(self, vm):
        self._vm = vm
        self._vm.ranging_started.connect(self._on_ranging_started)
        self._vm.ranging_stopped.connect(self._on_ranging_stopped)
        self._vm.position_updated.connect(self._on_position_updated)
        self._vm.sensor_fusion_updated.connect(self._on_sensor_fusion_updated)
        self._vm.anchor_distances_updated.connect(self._on_anchor_distances)
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_updated)
        self._vm.stats_updated.connect(self._on_stats_updated)
        
        # Connect Geofencing signals
        self._vm.geofence_status_updated.connect(self._on_geofence_status_updated)
        self._vm.geofence_layout_updated.connect(self._canvas.set_geofences)
        
        # Load any existing geofence maps on startup
        self._vm.load_geofences()
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        
        current_layout = getattr(self._vm, "current_anchor_layout", [])
        if current_layout:
            self._on_anchor_layout_updated(current_layout)

    def _on_anchor_layout_updated(self, anchors_list):
        formatted = []
        for anchor in anchors_list:
            formatted.append(
                {
                    "x": anchor["x_m"],
                    "y": anchor["y_m"],
                    "label": f"A{anchor['anchor_id']}",
                }
            )
        self.set_anchors(formatted)

    def _start_ranging(self):
        if self._vm:
            self._vm.start_ranging()

    def _stop_ranging(self):
        if self._vm:
            self._vm.stop_ranging()

    def _on_ranging_started(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._is_ranging = True
        self._frame_count = 0
        self._start_time = time.time()
        self._canvas.clear_trail()

    def _on_ranging_stopped(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._is_ranging = False

    def _on_position_updated(self, x, y, z, rms):
        self._frame_count += 1
        self._last_z = z
        self._last_rms = rms
        self._canvas.update_position(
            {
                "x": x,
                "y": y,
                "z": z,
                "error": rms,
                "yaw": 0,
                "source": "ranging",
            }
        )

        self.x_label.setText(f"{x:.3f} m")
        self.y_label.setText(f"{y:.3f} m")
        self.z_label.setText(f"{z:.3f} m")
        self.error_label.setText(f"{rms:.3f} m")

        if self._canvas.anchors:
            anchors = self._canvas.anchors
            min_x = min(anchor["x"] for anchor in anchors)
            max_x = max(anchor["x"] for anchor in anchors)
            min_y = min(anchor["y"] for anchor in anchors)
            max_y = max(anchor["y"] for anchor in anchors)
            self.warning_label.setVisible(not (min_x <= x <= max_x and min_y <= y <= max_y))
        else:
            self.warning_label.setVisible(False)

    def _on_sensor_fusion_updated(self, data: dict):
        x = float(data.get("ukf_x_m", 0.0))
        y = float(data.get("ukf_y_m", 0.0))
        yaw = float(data.get("ukf_yaw_deg", 0.0))
        vx = float(data.get("vx_mps", 0.0))
        vy = float(data.get("vy_mps", 0.0))
        tril_x = float(data.get("tril_x_m", 0.0))
        tril_y = float(data.get("tril_y_m", 0.0))
        raw_yaw = float(data.get("yaw_deg", 0.0))
        timestamp_ms = int(data.get("timestamp_ms", 0))
        err_count = int(data.get("ranging_error_count", 0))

        self._canvas.update_position(
            {
                "x": x,
                "y": y,
                "z": self._last_z,
                "error": self._last_rms,
                "yaw": yaw,
                "source": "sensor_fusion",
            }
        )

        self.vx_label.setText(f"{vx:.3f} m/s")
        self.vy_label.setText(f"{vy:.3f} m/s")
        self.yaw_label.setText(f"{yaw:.3f} deg")
        self.tril_xy_label.setText(f"{tril_x:.3f}, {tril_y:.3f} m")
        self.raw_yaw_label.setText(f"{raw_yaw:.3f} deg")
        self.fusion_ts_label.setText(f"{timestamp_ms} ms")
        self.err_cnt_label.setText(f"{err_count} packets")

    def _on_anchor_distances(self, anchors):
        for anchor in anchors:
            anchor_id = anchor.get("id", "")
            idx = anchor_id.replace("A", "")
            label_widget = getattr(self, f"d{idx}_label", None)
            if label_widget:
                distance_m = anchor.get("distance_cm", 0) / 100.0
                label_widget.setText(f"{distance_m:.3f} m")

    def _update_stats(self):
        if not self._is_ranging:
            return

        uptime = int(time.time() - self._start_time)
        self.uptime_label.setText(f"{uptime}s")
        self._render_stats()

    def _on_stats_updated(self, stats: dict):
        self._last_stats = stats.copy()
        self._render_stats()

    def _render_stats(self):
        stats = self._last_stats
        total = int(stats.get("total_count", stats.get("ranging_total_count", self._frame_count)))
        success = int(stats.get("success_count", stats.get("ranging_success_count", 0)))
        failed = int(stats.get("failed_count", stats.get("ranging_failed_count", 0)))
        timeout = int(stats.get("timeout_count", stats.get("ranging_timeout_count", 0)))
        rate = float(stats.get("update_rate_hz", 0.0))
        if rate <= 0:
            uptime = max(int(time.time() - self._start_time), 1)
            rate = self._frame_count / uptime

        self.frames_label.setText(str(total))
        self.fps_label.setText(f"{rate:.1f}")
        self.success_label.setText(str(success))
        self.failed_label.setText(str(failed))
        self.timeout_label.setText(str(timeout))
        self.period_label.setText(f"{int(stats.get('ranging_period_ms', 0))} ms")
        self.success_rate_label.setText(f"{float(stats.get('success_rate_percent', 0.0)):.1f}%")
        self.avg_rssi_label.setText(f"{int(stats.get('last_avg_rssi_dbm', 0))} dBm")
        self.last_range_time_label.setText(f"{int(stats.get('last_ranging_time_ms', 0))} ms")

    def set_anchors(self, anchors):
        self._canvas.set_anchors(anchors)

    # --- 2.5D GEOFENCING IMPLEMENTATION ---

    def _setup_user_map_ui(self):
        # Programmatically create the floating User Mode map selector widget
        self.user_map_groupbox = QGroupBox("Bản đồ hoạt động ảo", self)
        self.user_map_groupbox.setStyleSheet("""
            QGroupBox {
                background-color: rgba(15, 23, 42, 0.90);
                color: #38BDF8;
                font-weight: bold;
                font-family: 'Segoe UI';
                font-size: 13px;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 8px;
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background-color: transparent;
            }
            QComboBox {
                background-color: #1E293B;
                border: 1px solid #475569;
                border-radius: 6px;
                color: #F8FAFC;
                padding: 5px;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #38BDF8;
            }
            QComboBox QAbstractItemView {
                background-color: #0F172A;
                color: #F8FAFC;
                selection-background-color: #2563EB;
                border: 1px solid #334155;
            }
            QCheckBox {
                color: #CBD5E1;
                font-weight: bold;
                font-size: 12px;
            }
        """)
        
        layout = QVBoxLayout(self.user_map_groupbox)
        layout.setContentsMargins(10, 15, 10, 10)
        layout.setSpacing(8)
        
        self.cmb_user_map = QComboBox(self)
        self.chk_enable_geofence = QCheckBox("🔓 Bản đồ đang tắt", self)
        
        layout.addWidget(self.cmb_user_map)
        layout.addWidget(self.chk_enable_geofence)
        
        # Connect signals
        self.chk_enable_geofence.toggled.connect(self._on_enable_geofence_toggled)
        self.chk_enable_geofence.setChecked(True)
        self.cmb_user_map.currentIndexChanged.connect(self._on_user_map_changed)
        
        # Populate map list
        self._refresh_map_list()
        
        # Set geometry & parenting
        self.user_map_groupbox.setParent(self)
        self.user_map_groupbox.setVisible(True)
        self.update_sidebar_geometry()

    def _refresh_map_list(self):
        self.cmb_user_map.clear()
        
        # Maps folder path
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        maps_dir = os.path.join(base_dir, "data", "runtime")
        
        if not os.path.exists(maps_dir):
            os.makedirs(maps_dir, exist_ok=True)
            
        # Get all json files
        files = [f for f in os.listdir(maps_dir) if f.endswith(".json")]
        
        # Default filename
        default_file = "geofence_map.json"
        if default_file not in files:
            files.insert(0, default_file)
            
        for f in files:
            full_path = os.path.join(maps_dir, f)
            label = f[:-5] if f.endswith(".json") else f
            self.cmb_user_map.addItem(label, full_path)

    def _on_user_map_changed(self, index):
        if index < 0:
            return
        if self.chk_enable_geofence.isChecked():
            file_path = self.cmb_user_map.itemData(index)
            if self._vm and file_path and os.path.exists(file_path):
                self._vm.load_geofences(file_path)
                self._canvas.set_geofences(self._vm.get_geofence_zones())

    def _setup_geofencing_ui(self):
        # 1. Instantiate the GeofenceEditorWidget and add to the geofence page layout
        self.geofence_editor_widget = GeofenceEditorWidget(self)
        self.geofence_page_layout.addWidget(self.geofence_editor_widget)
        
        # 2. Setup User Map UI
        self._setup_user_map_ui()

        # 3. Initially in User Mode, show sidebar stack index 0 (telemetry)
        self.sidebar_stack.setCurrentIndex(0)
        self.btn_geofence_edit.setVisible(False)
        self.user_map_groupbox.setVisible(False)
        self._canvas._show_scale_bar = False
        self._canvas._show_mouse_coords = False
        self._canvas.is_developer_mode = False

        # Connect button signals
        self.btn_geofence_edit.clicked.connect(self._enter_geofence_editor)
        self.geofence_editor_widget.btn_mode_draw.clicked.connect(lambda: self._set_editor_mode("draw"))
        self.geofence_editor_widget.btn_mode_edit.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        self.geofence_editor_widget.btn_apply_properties.clicked.connect(self._apply_zone_properties)
        self.geofence_editor_widget.btn_delete_zone.clicked.connect(self._delete_selected_zone)
        self.geofence_editor_widget.btn_save_map.clicked.connect(self._save_map)
        self.geofence_editor_widget.btn_clear_map.clicked.connect(self._clear_map)
        self.geofence_editor_widget.btn_exit_editor.clicked.connect(self._exit_geofence_editor)
        self.geofence_editor_widget.btn_view_3d.clicked.connect(self._open_3d_viewer)

        # Connect Canvas interactions
        self._canvas.polygon_completed.connect(self._on_canvas_polygon_completed)
        self._canvas.zone_selected.connect(self._on_canvas_zone_selected)
        self._canvas.zone_modified.connect(self._on_canvas_zone_modified)

    def _enter_geofence_editor(self):
        self._canvas.dim_tracking_view = True
        self.btn_geofence_edit.setVisible(False)
        self.user_map_groupbox.setVisible(False)
        self.sidebar_stack.setCurrentIndex(1) # Switch to Geofence Page

        self._set_editor_mode("draw") # Start in draw mode by default
        if self._vm:
            self._canvas.set_geofences(self._vm.get_geofence_zones())

    def _exit_geofence_editor(self):
        self._canvas.dim_tracking_view = False
        self._canvas.set_edit_mode("navigate")
        self.sidebar_stack.setCurrentIndex(0) # Switch back to Telemetry Page

        if self._is_developer_mode:
            self.btn_geofence_edit.setVisible(True)
            self.user_map_groupbox.setVisible(False)
        else:
            self.btn_geofence_edit.setVisible(False)
            self.user_map_groupbox.setVisible(False)

    def _set_editor_mode(self, mode):
        self.geofence_editor_widget.btn_mode_draw.setChecked(mode == "draw")
        self.geofence_editor_widget.btn_mode_edit.setChecked(mode == "edit_vertices")
        self._canvas.set_edit_mode(mode)

    def _on_canvas_polygon_completed(self, points):
        zone_id = str(uuid.uuid4())[:8]
        zone_name = f"Vùng {len(self._vm.get_geofence_zones()) + 1}"
        
        # Read current type from UI to respect user's pre-selection
        current_type_idx = self.geofence_editor_widget.cmb_zone_type.currentIndex()
        zone_type = "allowed" if current_type_idx == 0 else "forbidden"
        color = "#22C55E" if zone_type == "allowed" else "#EF4444"
        
        new_zone = GeofenceZone(
            id=zone_id,
            name=zone_name,
            zone_type=zone_type,
            points=points,
            min_z=self.geofence_editor_widget.sb_z_min.value(),
            max_z=self.geofence_editor_widget.sb_z_max.value(),
            speed_limit=self.geofence_editor_widget.sb_speed.value(),
            color=color
        )
        if self._vm:
            self._vm.add_geofence_zone(new_zone)
            self._canvas.set_geofences(self._vm.get_geofence_zones())
            self._canvas.set_selected_zone(zone_id)
            self._load_zone_properties_to_ui(new_zone)

    def _load_zone_properties_to_ui(self, zone):
        self.geofence_editor_widget.txt_zone_name.setText(zone.name)
        self.geofence_editor_widget.cmb_zone_type.setCurrentIndex(0 if zone.zone_type == "allowed" else 1)
        self.geofence_editor_widget.sb_z_min.setValue(zone.min_z)
        self.geofence_editor_widget.sb_z_max.setValue(zone.max_z)
        self.geofence_editor_widget.sb_speed.setValue(zone.speed_limit)

    def _apply_zone_properties(self):
        selected_id = self._canvas.selected_zone_id
        if not selected_id:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn một vùng trên bản đồ trước!")
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == selected_id), None)
        if zone:
            zone.name = self.geofence_editor_widget.txt_zone_name.text()
            zone.zone_type = "allowed" if self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0 else "forbidden"
            zone.min_z = self.geofence_editor_widget.sb_z_min.value()
            zone.max_z = self.geofence_editor_widget.sb_z_max.value()
            zone.speed_limit = self.geofence_editor_widget.sb_speed.value()
            zone.color = "#22C55E" if zone.zone_type == "allowed" else "#EF4444"
            if self._vm:
                self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
            self._canvas.update()
            QMessageBox.information(self, "Thông báo", f"Đã cập nhật thông số vùng: {zone.name}")

    def _delete_selected_zone(self):
        selected_id = self._canvas.selected_zone_id
        if not selected_id:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn một vùng trên bản đồ để xóa!")
            return
        self._vm.remove_geofence_zone(selected_id)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(None)
        self.geofence_editor_widget.txt_zone_name.clear()

    def _on_canvas_zone_selected(self, zone_id):
        if not zone_id:
            self.geofence_editor_widget.txt_zone_name.clear()
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            self._load_zone_properties_to_ui(zone)

    def _on_canvas_zone_modified(self, zone_id, points):
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            zone.points = points
            if self._vm:
                self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())

    def _save_map(self):
        if not self._vm:
            return
            
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_dir = os.path.join(base_dir, "data", "runtime")
        os.makedirs(default_dir, exist_ok=True)
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu Bản đồ ảo",
            os.path.join(default_dir, "geofence_map.json"),
            "Bản đồ JSON (*.json)"
        )
        
        if file_path:
            if self._vm.save_geofences(file_path):
                QMessageBox.information(self, "Thành công", f"Đã lưu bản đồ thành công vào:\n{os.path.basename(file_path)}")
                self._refresh_map_list()
            else:
                QMessageBox.warning(self, "Lỗi", "Không thể lưu bản đồ!")

    def _clear_map(self):
        reply = QMessageBox.question(self, "Xác nhận", "Xóa toàn bộ bản đồ ảo?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._vm.clear_geofence_zones()
            self._canvas.set_geofences([])
            self._canvas.set_selected_zone(None)
            self.geofence_editor_widget.txt_zone_name.clear()

    def set_developer_mode(self, is_developer: bool):
        self._is_developer_mode = is_developer
        self._canvas.is_developer_mode = is_developer
        self._canvas._show_scale_bar = is_developer
        self._canvas._show_mouse_coords = is_developer
        if is_developer:
            self.btn_geofence_edit.setVisible(True)
            self.user_map_groupbox.setVisible(False)
        else:
            self.btn_geofence_edit.setVisible(False)
            self.user_map_groupbox.setVisible(False)
            if self.sidebar_stack.currentIndex() == 1:
                self._exit_geofence_editor()

    def _on_enable_geofence_toggled(self, checked):
        if checked:
            self.chk_enable_geofence.setText("🔒 Kích hoạt Bản đồ")
            if self._vm:
                file_path = self.cmb_user_map.currentData()
                if file_path and os.path.exists(file_path):
                    self._vm.load_geofences(file_path)
                else:
                    self._vm.load_geofences()
                self._canvas.set_geofences(self._vm.get_geofence_zones())
        else:
            self.chk_enable_geofence.setText("🔓 Bản đồ đang tắt")
            self._canvas.set_geofences([])

    def _on_geofence_status_updated(self, status: str, zone_name: str, speed_limit: float):
        if not self.chk_enable_geofence.isChecked():
            self.warning_label.setVisible(False)
            return

        if status == "forbidden":
            self.warning_label.setText(f"VI PHẠM VÙNG CẤM: {zone_name}!")
            self.warning_label.setStyleSheet(
                "color: white; font-size: 14px; font-weight: bold; background-color: #EF4444; padding: 2px 10px; border-radius: 4px;"
            )
            self.warning_label.setVisible(True)
        elif status == "allowed" and zone_name != "Default Space":
            self.warning_label.setText(f"Vùng cho phép: {zone_name} (Max Speed: {speed_limit:.1f} m/s)")
            self.warning_label.setStyleSheet(
                "color: white; font-size: 14px; font-weight: bold; background-color: #10B981; padding: 2px 10px; border-radius: 4px;"
            )
            self.warning_label.setVisible(True)
        else:
            self.warning_label.setVisible(False)

    def _open_3d_viewer(self):
        from views.popups.geofence_3d_viewer import Geofence3DViewer
        self._3d_viewer = Geofence3DViewer(self._vm, parent=self)
        self._3d_viewer.show()
