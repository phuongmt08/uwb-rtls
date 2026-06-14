"""
==============================================================================
  UWB RTLS Studio — Live Tracking ViewModel
==============================================================================
  File        : viewmodels/live_tracking_viewmodel.py
  Description : ViewModel for the "Live Tracking" tab.
                Handles real-time position data conversion and relays start/stop
                ranging commands to RangingModel.

  MVVM Role   : VIEWMODEL — Presentation logic.

  Thread Model:
    - Main GUI Thread: Processes ranging coordinates and anchor metrics updates
      synchronously on the Main GUI Thread.
  
  Tab này hiển thị (User-Facing):
    ┌─────────────────────────────────────────────────────────┐
    │  LIVE TRACKING TAB                                      │
    ├─────────────────────────────────────────────────────────┤
    │  ┌─ Position Map (2D Canvas) ─────────────────────────┐ │
    │  │                                                     │ │
    │  │    [A1]────────────────────────[A2]                  │ │
    │  │      │                          │                   │ │
    │  │      │       ● (Tag pos)        │                   │ │
    │  │      │      ╱  ╲                │                   │ │
    │  │      │     ╱    ╲               │                   │ │
    │  │    [A3]────────────────────────[A4]                  │ │
    │  │                                                     │ │
    │  │  ← Pan/Zoom enabled, trajectory trail shown →       │ │
    │  └─────────────────────────────────────────────────────┘ │
    │  ┌─ Position Data ────────────────────────────────────┐ │
    │  │  X: x.xx (m)  |  Y: x.xx (m)  |  Z: x.xx (m)         │ │
    │  │  RMS Error: x.xx (m)  |  Update Rate: x.xx Hz        │ │
    │  └────────────────────────────────────────────────────┘ │
    │  ┌─ Anchor Distances ─────────────────────────────────┐ │
    │  │  Anchor 1: x.xx (m) (FP: 500) | Anchor 2: x.xx (m)     │ │
    │  │  Anchor 3: x.xx (m) (FP: 480) | Anchor 4: x.xx (m)     │ │
    │  └────────────────────────────────────────────────────┘ │
    │  [▶ Start Ranging]  [■ Stop Ranging]                    │
    └─────────────────────────────────────────────────────────┘

  Signals:
    - position_updated(x, y, z, rms)
    - anchor_distances_updated(anchors: list)
    - ranging_started()
    - ranging_stopped()
    - stats_updated(stats: dict)

  Protocol Messages:
    - ranging_start_t (16), ranging_stop_t (17)
    - ranging_result_t (18)
    - ranging_status_get_t / _resp_t (19, 20)
    - anchor_layout_get_t / _resp_t (43, 45)
===============================================================================
"""
import logging
from typing import Optional
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from models.ranging_model import RangingModel
from services.protocol_service import ProtocolService
from data.raw_packet_store import shared_raw_packet_store
from utils.app_state import shared_app_state
from models.geofence_model import GeofenceZone
from repository.geofence_repository import GeofenceRepository

log = logging.getLogger(__name__)

LIVE_RENDER_INTERVAL_MS = 50  # 20 Hz UI update; model/session buffers keep every sample.

class LiveTrackingViewModel(QObject):
    ranging_started = pyqtSignal()
    ranging_stopped = pyqtSignal()
    position_updated = pyqtSignal(float, float, float, float)
    sensor_fusion_updated = pyqtSignal(dict)
    anchor_distances_updated = pyqtSignal(list)
    stats_updated = pyqtSignal(dict)
    anchor_layout_updated = pyqtSignal(list)
    
    # Geofence signals
    geofence_status_updated = pyqtSignal(str, str, float)  # status, zone_name, speed_limit
    geofence_layout_updated = pyqtSignal(list)  # list of GeofenceZones

    def __init__(self, model: RangingModel, protocol_service: ProtocolService, ranging_repo=None, command_bus=None, parent=None):
        super().__init__(parent)
        self.model = model
        self.protocol = protocol_service
        self._ranging_repo = ranging_repo
        self._command_bus = command_bus
        self._pending_position: tuple[float, float, float, float] | None = None
        self._pending_sensor_fusion: dict | None = None
        
        # Instantiate geofence repository
        self.geofence_repo = GeofenceRepository()
        
        # Connect position updates to custom handler to run geofencing checks
        self.model.position_updated.connect(self._on_model_position_updated)
        self.model.sensor_fusion_updated.connect(self._on_model_sensor_fusion_updated)
            
        self.model.anchor_distances_updated.connect(self.anchor_distances_updated.emit)
        self.model.stats_updated.connect(self.stats_updated.emit)
        shared_app_state.anchor_layout_changed.connect(self.anchor_layout_updated.emit)

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(LIVE_RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._flush_pending_live_updates)
        self._render_timer.start()

    def _on_model_position_updated(self, x: float, y: float, z: float, rms: float):
        self._pending_position = (x, y, z, rms)

    def _emit_position_update(self, x: float, y: float, z: float, rms: float):
        self.position_updated.emit(x, y, z, rms)
        status, zone_name, speed_limit = self.geofence_repo.check_position(x, y, z)
        self.geofence_status_updated.emit(status, zone_name, speed_limit)

    def _on_model_sensor_fusion_updated(self, data: dict):
        self._pending_sensor_fusion = data.copy()

    def _emit_sensor_fusion_update(self, data: dict):
        self.sensor_fusion_updated.emit(data)
        x = data.get("ukf_x_m", 0.0)
        y = data.get("ukf_y_m", 0.0)
        z = 0.0
        if self.model._position_history:
            z = self.model._position_history[-1].get("z_m", 0.0)
        status, zone_name, speed_limit = self.geofence_repo.check_position(x, y, z)
        self.geofence_status_updated.emit(status, zone_name, speed_limit)

    def _flush_pending_live_updates(self):
        if self._pending_position is not None:
            x, y, z, rms = self._pending_position
            self._pending_position = None
            self._emit_position_update(x, y, z, rms)

        if self._pending_sensor_fusion is not None:
            data = self._pending_sensor_fusion
            self._pending_sensor_fusion = None
            self._emit_sensor_fusion_update(data)

    def _on_ranging_data_updated(self, data: dict):
        self.position_updated.emit(data["x"], data["y"], data["z"], data["rms"])

    @property
    def current_anchor_layout(self) -> list:
        return shared_app_state.anchor_layout

    def _send_command(self, command_name: str, **kwargs):
        if self._command_bus:
            return self._command_bus.send(command_name, **kwargs)
        return self.model.send_command(command_name, **kwargs)

    def start_ranging(self) -> None:
        # Gọi command tới BE từ ViewModel
        self.model.clear_history()
        self._pending_position = None
        self._pending_sensor_fusion = None
        shared_raw_packet_store.clear()
        self.model.start_ranging()
        self.ranging_started.emit()

    def stop_ranging(self) -> None:
        self.model.stop_ranging()
        self._flush_pending_live_updates()
        self.ranging_stopped.emit()

    # Geofence service methods
    def get_geofence_zones(self) -> list:
        return self.geofence_repo.get_zones()

    def add_geofence_zone(self, zone: GeofenceZone) -> None:
        self.geofence_repo.add_zone(zone)
        self.geofence_layout_updated.emit(self.get_geofence_zones())

    def remove_geofence_zone(self, zone_id: str) -> None:
        if self.geofence_repo.remove_zone(zone_id):
            self.geofence_layout_updated.emit(self.get_geofence_zones())

    def clear_geofence_zones(self) -> None:
        self.geofence_repo.clear()
        self.geofence_layout_updated.emit([])

    def save_geofences(self, file_path: Optional[str] = None) -> bool:
        return self.geofence_repo.save(file_path)

    def load_geofences(self, file_path: Optional[str] = None) -> bool:
        res = self.geofence_repo.load(file_path)
        if res:
            self.geofence_layout_updated.emit(self.get_geofence_zones())
        return res
