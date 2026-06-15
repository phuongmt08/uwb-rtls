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
        self.load()

    def get_zones(self) -> List[GeofenceZone]:
        return list(self._zones.values())

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
            geofences_data = data.get("objects", data.get("geofences", []))
            for g_data in geofences_data:
                zone = GeofenceZone.from_dict(g_data)
                self._zones[zone.id] = zone
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
            data = {
                "map_name": "Virtual_Map_Config",
                "objects": [zone.to_dict() for zone in self._zones.values()],
                "geofences": [
                    zone.to_dict()
                    for zone in self._zones.values()
                    if getattr(zone, "object_type", "zone") == "zone"
                ],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info(f"Successfully saved geofences to {path}")
            return True
        except Exception as e:
            log.error(f"Error saving geofences to {path}: {e}")
            return False

    def check_position(self, x: float, y: float, z: float) -> Tuple[str, str, float]:
        """
        Checks a coordinate against all active geofence zones.
        
        Returns:
            Tuple[str, str, float]: (status, zone_name, speed_limit)
                status: "allowed" | "forbidden"
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
                    return "allowed", zone.name, zone.speed_limit
            # Not in any allowed zone -> Forbidden (out of boundary)
            return "forbidden", "Outside Allowed Boundary", 0.0

        # 3. If no allowed zones are specified, then default space is allowed
        # (with standard default speed)
        return "allowed", "Default Space", 1.5
