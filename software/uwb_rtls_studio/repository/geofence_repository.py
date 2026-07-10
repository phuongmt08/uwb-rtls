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
from typing import List, Tuple, Optional, Dict
from models.geofence_model import GeofenceZone

log = logging.getLogger(__name__)


class GeofenceRepository:
    def __init__(self, default_file_path: Optional[str] = None):
        if default_file_path is None:
            # Save inside data/runtime folder
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.default_file_path = os.path.join(base_dir, "data", "runtime", "geofence_map.json")
        else:
            self.default_file_path = default_file_path

        self._zones: Dict[str, GeofenceZone] = {}
        self._anchors: List[dict] = []
        self._meta: Dict[str, object] = {}
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
        path = file_path or self.default_file_path
        if not os.path.exists(path):
            log.warning(f"Geofence map file not found: {path}")
            self._zones = {}
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._zones = {}
            self._anchors = []
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
            log.info(f"Successfully loaded {len(self._zones)} geofences from {path}")
            return True
        except Exception as e:
            log.error(f"Error loading geofence map from {path}: {e}")
            return False

    def save(self, file_path: Optional[str] = None) -> bool:
        path = file_path or self.default_file_path
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)

        try:
            rooms, walls, objects, rule_zones = self._split_zones()
            anchors = [dict(anchor) for anchor in self._anchors]
            meta = dict(self._meta)
            meta.pop("editor_settings", None)
            meta.update({
                "name": meta.get("name", "Virtual_Map_Config"),
                "version": 2,
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
                },
                "rule_zones": rule_zones,
                "map_name": self._meta.get("name", "Virtual_Map_Config"),
                "anchors": anchors,
                "objects": [zone.to_dict() for zone in self._zones.values()],
                "geofences": rule_zones,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
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
