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
import math
from typing import Optional
from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from models.ranging_model import RangingModel
from services.protocol_service import ProtocolService
from data.raw_packet_store import shared_raw_packet_store
from utils.app_state import shared_app_state
from models.geofence_model import GeofenceZone
from repository.geofence_repository import GeofenceRepository

log = logging.getLogger(__name__)

LIVE_RENDER_INTERVAL_MS = 16  # ~60 Hz UI flush; model/session buffers keep every sample.
STOP_RANGING_END_DELAY_MS = 3000

class LiveTrackingViewModel(QObject):
    ranging_started = pyqtSignal()
    ranging_stopped = pyqtSignal()
    position_updated = pyqtSignal(float, float, float, float)
    sensor_fusion_updated = pyqtSignal(dict)
    calib_data_updated = pyqtSignal(dict)
    anchor_distances_updated = pyqtSignal(list)
    stats_updated = pyqtSignal(dict)
    anchor_layout_updated = pyqtSignal(list)
    scan_devices_updated = pyqtSignal(list)
    
    # Geofence signals
    geofence_status_updated = pyqtSignal(str, str, float)  # status, zone_name, speed_limit
    geofence_layout_updated = pyqtSignal(list)  # list of GeofenceZones

    def __init__(
        self,
        model: RangingModel,
        protocol_service: ProtocolService,
        ranging_repo=None,
        command_bus=None,
        session_run_manager=None,
        ble_scan_repo=None,
        parent=None,
    ):
        super().__init__(parent)
        self.model = model
        self.protocol = protocol_service
        self._ranging_repo = ranging_repo
        self._command_bus = command_bus
        self._session_run_manager = session_run_manager
        self._ble_scan_repo = ble_scan_repo
        self._pending_position: tuple[float, float, float, float] | None = None
        self._pending_position_meta: dict | None = None
        self._pending_sensor_fusion: dict | None = None
        
        # Instantiate geofence repository
        self.geofence_repo = GeofenceRepository()
        
        # Connect position updates to custom handler to run geofencing checks
        self.model.position_updated.connect(self._on_model_position_updated)
        self.model.sensor_fusion_updated.connect(self._on_model_sensor_fusion_updated)
        self.model.calib_data_updated.connect(self.calib_data_updated.emit)
            
        self.model.anchor_distances_updated.connect(self.anchor_distances_updated.emit)
        self.model.stats_updated.connect(self.stats_updated.emit)
        shared_app_state.anchor_layout_changed.connect(self.anchor_layout_updated.emit)

        if self._ble_scan_repo:
            self._ble_scan_repo.scan_results_updated.connect(self.scan_devices_updated.emit)

        self._render_timer = QTimer(self)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.setInterval(LIVE_RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._flush_pending_live_updates)
        self._render_timer.start()

    def _find_room(self, room_id: str):
        if not room_id:
            return None
        return next(
            (
                zone for zone in self.geofence_repo.get_zones()
                if zone.id == room_id and getattr(zone, "object_type", "zone") == "room"
            ),
            None,
        )

    @staticmethod
    def _room_origin(room):
        points = list(getattr(room, "points", []) or [])
        if not points:
            return 0.0, 0.0
        index = getattr(room, "origin_vertex_idx", None)
        if index is None or not 0 <= int(index) < len(points):
            index = 0
        return float(points[int(index)][0]), float(points[int(index)][1])

    @staticmethod
    def _room_local_to_scene(local_x, local_y, room):
        origin_x, origin_y = LiveTrackingViewModel._room_origin(room)
        theta = math.radians(float(getattr(room, "local_frame_yaw_deg", 0.0)))
        cos_theta, sin_theta = math.cos(theta), math.sin(theta)
        return (
            origin_x + cos_theta * float(local_x) - sin_theta * float(local_y),
            origin_y + sin_theta * float(local_x) + cos_theta * float(local_y),
        )

    def _resolve_room_frame(self, payload: dict, x_key: str, y_key: str, z_value: float):
        room_id = str(payload.get("room_id") or "")
        local_x = payload.get("local_x_m")
        local_y = payload.get("local_y_m")
        if room_id and local_x is not None and local_y is not None:
            room = self._find_room(room_id)
            if room is not None:
                global_x, global_y = self._room_local_to_scene(local_x, local_y, room)
                return {
                    "room_id": room_id,
                    "local_x_m": float(local_x),
                    "local_y_m": float(local_y),
                    "x_m": float(global_x),
                    "y_m": float(global_y),
                    "z_m": float(payload.get("local_z_m", z_value) if payload.get("local_z_m") is not None else z_value),
                    "is_local_frame": True,
                }
        return {
            "room_id": room_id,
            "local_x_m": None,
            "local_y_m": None,
            "x_m": float(payload.get(x_key, 0.0)),
            "y_m": float(payload.get(y_key, 0.0)),
            "z_m": float(z_value),
            "is_local_frame": False,
        }

    def _on_model_position_updated(self, x: float, y: float, z: float, rms: float):
        sample = self.model._position_history[-1].copy() if self.model._position_history else {}
        resolved = self._resolve_room_frame(sample, "x_m", "y_m", z)
        self._pending_position = (resolved["x_m"], resolved["y_m"], resolved["z_m"], rms)
        self._pending_position_meta = resolved

    def _emit_position_update(self, x: float, y: float, z: float, rms: float):
        self.position_updated.emit(x, y, z, rms)
        status, zone_name, speed_limit = self.geofence_repo.check_position(x, y, z, speed=0.0)
        self.geofence_status_updated.emit(status, zone_name, speed_limit)

    def _on_model_sensor_fusion_updated(self, data: dict):
        self._pending_sensor_fusion = data.copy()

    def _emit_sensor_fusion_update(self, data: dict):
        z = 0.0
        if self._pending_position_meta is not None:
            z = float(self._pending_position_meta.get("z_m", 0.0))
        elif self.model._position_history:
            z = self.model._position_history[-1].get("z_m", 0.0)

        resolved = self._resolve_room_frame(data, "ukf_x_m", "ukf_y_m", z)
        emitted = data.copy()
        emitted["ukf_x_m"] = resolved["x_m"]
        emitted["ukf_y_m"] = resolved["y_m"]
        emitted["room_id"] = resolved["room_id"]
        emitted["is_local_frame"] = resolved["is_local_frame"]
        if emitted.get("local_x_m") is None:
            emitted["local_x_m"] = resolved["local_x_m"]
        if emitted.get("local_y_m") is None:
            emitted["local_y_m"] = resolved["local_y_m"]
        if emitted.get("tril_x_m") is not None and emitted.get("tril_y_m") is not None and resolved["is_local_frame"]:
            emitted["tril_x_m"] = resolved["x_m"]
            emitted["tril_y_m"] = resolved["y_m"]

        self.sensor_fusion_updated.emit(emitted)
        x = emitted.get("ukf_x_m", 0.0)
        y = emitted.get("ukf_y_m", 0.0)
        vx = emitted.get("vx_mps", 0.0)
        vy = emitted.get("vy_mps", 0.0)
        speed = math.hypot(vx, vy)
        status, zone_name, speed_limit = self.geofence_repo.check_position(x, y, z, speed)
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

    def start_ranging(self, yaw_deg: int | float = 0, is_ukf_reinit: bool = False) -> None:
        # Gọi command tới BE từ ViewModel
        if self._session_run_manager:
            self._session_run_manager.open_ranging_run()
        self.model.clear_history()
        self._pending_position = None
        self._pending_position_meta = None
        self._pending_sensor_fusion = None
        shared_raw_packet_store.clear()
        self.model.start_ranging(yaw_deg=yaw_deg, is_ukf_reinit=is_ukf_reinit)
        self.ranging_started.emit()

    def stop_ranging(self) -> None:
        self.model.stop_ranging()
        self._flush_pending_live_updates()
        if self._session_run_manager:
            log.info(
                "LiveTrackingViewModel: Waiting %d ms after ranging_stop before end_session.",
                STOP_RANGING_END_DELAY_MS,
            )
            QTimer.singleShot(
                STOP_RANGING_END_DELAY_MS,
                lambda: self._session_run_manager.close_ranging_run(send_end=True),
            )
        self.ranging_stopped.emit()

    # Geofence service methods
    def get_geofence_zones(self) -> list:
        return self.geofence_repo.get_zones()

    def set_geofence_zones(self, zones: list[GeofenceZone]) -> None:
        self.geofence_repo.set_zones(zones)
        self.geofence_layout_updated.emit(self.get_geofence_zones())

    def get_map_anchors(self) -> list:
        return self.geofence_repo.get_anchors()

    def get_active_room_id(self) -> str:
        active_ids = self.get_active_room_ids()
        return active_ids[0] if active_ids else ""

    def set_active_room_id(self, room_id: str) -> None:
        self.set_active_room_ids([room_id] if room_id else [])

    def get_active_room_ids(self) -> list[str]:
        return self.geofence_repo.get_active_room_ids()

    def set_active_room_ids(self, room_ids: list[str]) -> None:
        self.geofence_repo.set_active_room_ids(room_ids)

    def _coerce_int_id(self, value, default: int = 0) -> int:
        if value is None or value == "":
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        try:
            if text.lower().startswith("0x"):
                return int(text, 16)
            if text.lower().startswith("a") and text[1:].isdigit():
                return int(text[1:])
            return int(text)
        except (TypeError, ValueError):
            return default

    def update_anchor_layout_from_map(self, anchors: list) -> None:
        normalized = []
        for idx, anchor in enumerate(anchors):
            anchor_id = self._coerce_int_id(anchor.get("anchor_id"), idx)
            normalized.append({
                "anchor_id": anchor_id,
                "x_m": float(anchor.get("x_m", anchor.get("x", 0.0))),
                "y_m": float(anchor.get("y_m", anchor.get("y", 0.0))),
                "z_m": float(anchor.get("z_m", anchor.get("z", 0.0))),
                "label": anchor.get("label", f"A{anchor_id}"),
                "role": anchor.get("role", "anchor"),
                "device_type": anchor.get("device_type", "uwb_anchor"),
                "device_id": self._coerce_int_id(anchor.get("device_id"), anchor_id),
                "mac": anchor.get("mac", ""),
                "zone_id": anchor.get("zone_id", ""),
                "zone_name": anchor.get("zone_name", ""),
                "zone_ids": list(anchor.get("zone_ids", [])),
                "zone_names": list(anchor.get("zone_names", [])),
                "room_id": anchor.get("room_id", anchor.get("zone_id", "")),
                "local_x_m": float(anchor.get("local_x_m", anchor.get("x_m", anchor.get("x", 0.0)))),
                "local_y_m": float(anchor.get("local_y_m", anchor.get("y_m", anchor.get("y", 0.0)))),
                "placed": bool(anchor.get("placed", True)),
                "is_scanned": bool(anchor.get("is_scanned", anchor.get("scan_seen", False))),
                "sync_state": anchor.get("sync_state", "synced"),
            })
        self.geofence_repo.set_anchors(normalized)
        if hasattr(self.model, "set_anchor_layout"):
            self.model.set_anchor_layout(normalized)
        else:
            shared_app_state.anchor_layout = normalized

    def get_scan_devices(self) -> list:
        if self._ble_scan_repo:
            return self._ble_scan_repo.merged_results()
        return []

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
