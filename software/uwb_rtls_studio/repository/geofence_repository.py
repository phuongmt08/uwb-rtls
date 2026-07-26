"""
===============================================================================
  UWB RTLS Studio — Geofence Repository
===============================================================================
  File        : repository/geofence_repository.py
  Description : Data repository for loading/saving geofence zones from JSON.
  MVVM Role   : REPOSITORY — handles persistence and querying.
===============================================================================
"""
import os
import json
import logging
import math
from typing import List, Tuple, Optional, Dict
from models.geofence_model import GeofenceZone
from models.ground_truth_model import GroundTruthTrack

log = logging.getLogger(__name__)


class GeofenceRepository:
    def __init__(
        self,
        default_file_path: Optional[str] = None,
        *,
        autoload: bool = True,
    ):
        if default_file_path is None:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.default_file_path = os.path.join(app_dir, "data", "runtime", "geofence_map.json")
        else:
            self.default_file_path = default_file_path

        self._zones: Dict[str, GeofenceZone] = {}
        self._anchors: List[dict] = []
        self._ground_truths: Dict[str, GroundTruthTrack] = {}
        self._meta: Dict[str, object] = {}
        self._active_file_path = self.default_file_path
        if autoload:
            self.load()

    def get_zones(self) -> List[GeofenceZone]:
        return list(self._zones.values())

    def set_zones(self, zones: List[GeofenceZone]) -> None:
        self._zones = {zone.id: zone for zone in zones or []}

    def get_anchors(self) -> List[dict]:
        return [dict(anchor) for anchor in self._anchors]

    def set_anchors(self, anchors: List[dict]) -> None:
        self._anchors = [
            self._normalize_anchor(anchor, idx)
            for idx, anchor in enumerate(anchors or [])
        ]

    def get_ground_truths(self) -> List[GroundTruthTrack]:
        return list(self._ground_truths.values())

    def set_ground_truths(self, tracks: List[GroundTruthTrack]) -> None:
        self._ground_truths = {track.id: track for track in tracks or []}

    def add_ground_truth(self, track: GroundTruthTrack) -> None:
        self._ground_truths[track.id] = track

    def remove_ground_truth(self, track_id: str) -> bool:
        return self._ground_truths.pop(str(track_id), None) is not None

    def active_file_path(self) -> str:
        return self._active_file_path
    def get_active_room_ids(self) -> List[str]:
        active_ids = self._meta.get("active_room_ids")
        if isinstance(active_ids, list):
            return [str(room_id) for room_id in active_ids if room_id][:4]
        legacy_id = str(self._meta.get("active_room_id") or "")
        return [legacy_id] if legacy_id else []

    def set_active_room_ids(self, room_ids: List[str]) -> None:
        unique_ids = []
        for room_id in room_ids or []:
            normalized = str(room_id or "")
            if normalized and normalized not in unique_ids:
                unique_ids.append(normalized)
        self._meta.pop("active_room_id", None)
        if unique_ids:
            self._meta["active_room_ids"] = unique_ids[:4]
        else:
            self._meta.pop("active_room_ids", None)

    def get_active_room_id(self) -> str:
        active_ids = self.get_active_room_ids()
        return active_ids[0] if active_ids else ""

    def set_active_room_id(self, room_id: str) -> None:
        self.set_active_room_ids([room_id] if room_id else [])

    def _apply_configured_global_frame(self) -> None:
        """Make the configured room's local axes the in-memory global map axes."""
        room_id = str(self._meta.get("global_frame_room_id") or "")
        if not room_id:
            return

        reference_room = self._zones.get(room_id)
        if (
            reference_room is None
            or getattr(reference_room, "object_type", "zone") != "room"
            or not reference_room.points
        ):
            log.warning("Ignoring invalid meta.global_frame_room_id: %s", room_id)
            return

        origin_idx = getattr(reference_room, "origin_vertex_idx", None)
        if origin_idx is None or not 0 <= int(origin_idx) < len(reference_room.points):
            origin_idx = 0
        origin_x, origin_y = reference_room.points[int(origin_idx)]
        yaw_deg = float(getattr(reference_room, "local_frame_yaw_deg", 0.0))
        cos_theta = math.cos(math.radians(yaw_deg))
        sin_theta = math.sin(math.radians(yaw_deg))

        def transform_point(x, y):
            dx, dy = float(x) - origin_x, float(y) - origin_y
            return (
                cos_theta * dx + sin_theta * dy,
                -sin_theta * dx + cos_theta * dy,
            )

        def transform_vector(x, y):
            return (
                cos_theta * float(x) + sin_theta * float(y),
                -sin_theta * float(x) + cos_theta * float(y),
            )

        for zone in self._zones.values():
            zone.points = [transform_point(x, y) for x, y in zone.points]
            zone.label_offset_x, zone.label_offset_y = transform_vector(
                zone.label_offset_x, zone.label_offset_y
            )
            if getattr(zone, "object_type", "zone") == "room":
                zone.local_frame_yaw_deg = float(zone.local_frame_yaw_deg) - yaw_deg

        # Anchors owned by a room are stored in that room's local frame, so they
        # must stay unchanged. Free anchors use map-global coordinates.
        for anchor in self._anchors:
            if anchor.get("room_id") or anchor.get("zone_id"):
                continue
            anchor["x_m"], anchor["y_m"] = transform_point(anchor["x_m"], anchor["y_m"])
            anchor["local_x_m"], anchor["local_y_m"] = transform_point(
                anchor["local_x_m"], anchor["local_y_m"]
            )

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

    def _normalize_anchor(self, anchor: dict, idx: int = 0) -> dict:
        anchor_id = self._coerce_int_id(anchor.get("anchor_id", anchor.get("id", idx)), idx)
        label = str(anchor.get("label") or anchor.get("name") or f"A{anchor_id}")
        return {
            "anchor_id": anchor_id,
            "label": label,
            "role": anchor.get("role", "anchor"),
            "device_type": anchor.get("device_type", "uwb_anchor"),
            "device_id": self._coerce_int_id(anchor.get("device_id"), anchor_id),
            # Hardware serial_number (bsp_util_get_serial_number XOR of the STM32
            # UID) — distinct from anchor_id/device_id, which are just this
            # anchor's slot/position in the layout. Needed to target this
            # physical anchor with antenna_delay_bcast_set. 0 = not yet known.
            "serial_number": self._coerce_int_id(anchor.get("serial_number"), 0),
            # Antenna-delay calibration has no read-back path (bcast set is
            # fire-and-forget), so the app remembers the last combined delay
            # it applied to this anchor as the next session's starting point.
            # 32374 = firmware ANCHOR_DEFAULT_TX/RX_ANT_DLY (16187+16187).
            "last_combined_delay": int(anchor.get("last_combined_delay", 32374) or 32374),
            "mac": anchor.get("mac", ""),
            "zone_id": anchor.get("zone_id", ""),
            "zone_name": anchor.get("zone_name", ""),
            "zone_ids": list(anchor.get("zone_ids", [])),
            "zone_names": list(anchor.get("zone_names", [])),
            "room_id": anchor.get("room_id", anchor.get("zone_id", "")),
            "local_x_m": float(anchor.get("local_x_m", anchor.get("x_m", anchor.get("x", 0.0)))),
            "local_y_m": float(anchor.get("local_y_m", anchor.get("y_m", anchor.get("y", 0.0)))),
            "x_m": float(anchor.get("x_m", anchor.get("x", 0.0))),
            "y_m": float(anchor.get("y_m", anchor.get("y", 0.0))),
            "z_m": float(anchor.get("z_m", anchor.get("z", 0.0))),
            "placed": bool(anchor.get("placed", True)),
            "is_scanned": bool(anchor.get("is_scanned", anchor.get("scan_seen", False))),
            "sync_state": anchor.get("sync_state", "draft"),
        }

    def _load_zone_list(self, items: list) -> None:
        for g_data in items or []:
            zone = GeofenceZone.from_dict(g_data)
            self._zones[zone.id] = zone

    def _split_zones(self) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        rooms = []
        walls = []
        objects = []
        rule_zones = []
        for zone in self._zones.values():
            data = zone.to_dict()
            object_type = getattr(zone, "object_type", "zone")
            if object_type == "room":
                rooms.append(data)
            elif object_type == "wall":
                walls.append(data)
            elif object_type == "object":
                objects.append(data)
            elif object_type == "zone":
                rule_zones.append(data)
        return rooms, walls, objects, rule_zones

    def add_zone(self, zone: GeofenceZone) -> None:
        self._zones[zone.id] = zone
        log.info(f"Added geofence zone: {zone.name} ({zone.zone_type})")

    def remove_zone(self, zone_id: str) -> bool:
        if zone_id in self._zones:
            removed = self._zones.pop(zone_id)
            log.info(f"Removed geofence zone: {removed.name}")
            return True
        return False

    def clear(self) -> None:
        self._zones.clear()
        self._anchors.clear()
        self._meta.pop("active_room_id", None)
        self._meta.pop("active_room_ids", None)

    def load(self, file_path: Optional[str] = None) -> bool:
        path = file_path or self._active_file_path or self.default_file_path
        if not os.path.exists(path):
            log.warning(f"Geofence map file not found: {path}")
            self._zones = {}
            self._ground_truths = {}
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._zones = {}
            self._anchors = []
            self._ground_truths = {}
            self._meta = dict(data.get("meta", {}))
            map_objects = data.get("map_objects", {})

            if isinstance(map_objects, dict):
                self._load_zone_list(map_objects.get("rooms", []))
                self._load_zone_list(map_objects.get("walls", []))
                self._load_zone_list(map_objects.get("objects", []))
                anchor_items = map_objects.get("anchors", data.get("anchors", []))
            else:
                anchor_items = data.get("anchors", [])

            self._load_zone_list(data.get("rule_zones", []))

            has_structured_map = bool(map_objects) or bool(data.get("rule_zones"))
            legacy_objects = data.get("objects", [])
            if legacy_objects and not has_structured_map:
                self._load_zone_list(legacy_objects)
            elif data.get("geofences") and not has_structured_map:
                self._load_zone_list(data.get("geofences", []))

            self._anchors = [
                self._normalize_anchor(anchor, idx)
                for idx, anchor in enumerate(anchor_items or [])
            ]
            self._apply_configured_global_frame()
            ground_truth_items = data.get("ground_truths", [])
            if isinstance(map_objects, dict) and not ground_truth_items:
                ground_truth_items = map_objects.get("ground_truths", [])
            for item in ground_truth_items or []:
                track = GroundTruthTrack.from_dict(item)
                if len(track.points) >= 2:
                    self._ground_truths[track.id] = track
            self._active_file_path = path
            log.info(f"Successfully loaded {len(self._zones)} geofences from {path}")
            return True
        except Exception as e:
            log.error(f"Error loading geofence map from {path}: {e}")
            return False

    def save(self, file_path: Optional[str] = None) -> bool:
        path = file_path or self._active_file_path or self.default_file_path
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)

        try:
            rooms, walls, objects, rule_zones = self._split_zones()
            anchors = [dict(anchor) for anchor in self._anchors]
            ground_truths = [track.to_dict() for track in self._ground_truths.values()]
            meta = dict(self._meta)
            meta.pop("editor_settings", None)
            meta.update({
                "name": meta.get("name", "Virtual_Map_Config"),
                "version": 3,
                "schema": "uwb_rtls_geofence_map",
            })
            data = {
                "meta": meta,
                "map_objects": {
                    "rooms": rooms,
                    "walls": walls,
                    "objects": objects,
                    "anchors": anchors,
                    "gateways": [],
                    "ground_truths": ground_truths,
                },
                "rule_zones": rule_zones,
                "ground_truths": ground_truths,
                "map_name": self._meta.get("name", "Virtual_Map_Config"),
                "anchors": anchors,
                "objects": [zone.to_dict() for zone in self._zones.values()],
                "geofences": rule_zones,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._active_file_path = path
            log.info(f"Successfully saved geofences to {path}")
            return True
        except Exception as e:
            log.error(f"Error saving geofences to {path}: {e}")
            return False

    def check_position(self, x: float, y: float, z: float, speed: float = 0.0) -> Tuple[str, str, float]:
        """
        Checks a coordinate against all active geofence zones.
        
        Returns:
            Tuple[str, str, float]: (status, zone_name, speed_limit)
                status: "allowed" | "forbidden" | "overspeed"
                zone_name: name of the zone trigger
                speed_limit: recommended speed limit (m/s)
        """
        zones = [
            zone
            for zone in self.get_zones()
            if getattr(zone, "object_type", "zone") == "zone"
        ]
        if not zones:
            return "allowed", "Default Space", 1.5  # No geofences configured

        # 1. Check for Forbidden Zones first (critical)
        for zone in zones:
            if zone.zone_type == "forbidden" and zone.contains(x, y, z):
                return "forbidden", zone.name, 0.0

        # 2. Check Allowed Zones
        allowed_zones = [z for z in zones if z.zone_type == "allowed"]
        if allowed_zones:
            # If allowed zones exist, the tag MUST be inside at least one allowed zone
            for zone in allowed_zones:
                if zone.contains(x, y, z):
                    if zone.speed_limit > 0.0 and speed > zone.speed_limit:
                        return "overspeed", zone.name, zone.speed_limit
                    return "allowed", zone.name, zone.speed_limit
            # Not in any allowed zone -> Forbidden (out of boundary)
            return "forbidden", "Outside Allowed Boundary", 0.0

        # 3. If no allowed zones are specified, then default space is allowed
        # (with standard default speed)
        return "allowed", "Default Space", 1.5
