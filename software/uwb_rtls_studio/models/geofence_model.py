"""
===============================================================================
  UWB RTLS Studio — Geofence Model
===============================================================================
  File        : models/geofence_model.py
  Description : Data model for 2.5D Geofence zones and violation logic.
  MVVM Role   : MODEL — contains geofence data structures.
===============================================================================
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPolygonF


@dataclass
class GeofenceZone:
    id: str
    name: str
    zone_type: str  # "allowed" | "forbidden" for rule zones; "room" | "wall" for map objects
    points: List[Tuple[float, float]]  # List of (x, y) coordinates in meters
    min_z: float = 0.0
    max_z: float = 3.0
    speed_limit: float = 1.0  # Max speed inside this zone (m/s)
    color: str = "#FF0000"  # Hex color string
    object_type: str = "zone"  # "zone" | "room" | "wall"

    def to_dict(self) -> dict:
        height_m = max(0.0, self.max_z - self.min_z) if self.object_type == "wall" else 0.0
        return {
            "id": self.id,
            "name": self.name,
            "type": self.zone_type,
            "object_type": self.object_type,
            "points": [{"x": p[0], "y": p[1]} for p in self.points],
            "min_z": self.min_z,
            "max_z": self.max_z,
            "height_m": height_m,
            "speed_limit": self.speed_limit,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GeofenceZone":
        points_list = []
        for p in data.get("points", []):
            points_list.append((float(p["x"]), float(p["y"])))
        object_type = data.get("object_type")
        zone_type = data.get("type", data.get("zone_type", "forbidden"))
        if object_type is None:
            object_type = zone_type if zone_type in {"room", "wall"} else "zone"

        height_m = data.get("height_m")
        min_z = float(data.get("min_z", 0.0))
        max_z = float(data.get("max_z", 3.0))
        if object_type == "room":
            min_z = 0.0
            max_z = 0.0
        elif height_m is not None and object_type == "wall":
            max_z = min_z + float(height_m)

        return cls(
            id=data["id"],
            name=data["name"],
            zone_type=zone_type,
            points=points_list,
            min_z=min_z,
            max_z=max_z,
            speed_limit=float(data.get("speed_limit", 1.0)),
            color=data.get("color", "#FF0000"),
            object_type=object_type,
        )

    def contains(self, x: float, y: float, z: float) -> bool:
        if self.object_type != "zone":
            return False

        if self.min_z != self.max_z:
            if not (self.min_z <= z <= self.max_z):
                return False

        poly = QPolygonF()
        for pt in self.points:
            poly.append(QPointF(pt[0], pt[1]))

        from PyQt6.QtCore import Qt
        return poly.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill)
