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
    zone_type: str  # "allowed" | "forbidden"
    points: List[Tuple[float, float]]  # List of (x, y) coordinates in meters
    min_z: float = 0.0
    max_z: float = 3.0
    speed_limit: float = 1.0  # Max speed inside this zone (m/s)
    color: str = "#FF0000"  # Hex color string

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.zone_type,
            "points": [{"x": p[0], "y": p[1]} for p in self.points],
            "min_z": self.min_z,
            "max_z": self.max_z,
            "speed_limit": self.speed_limit,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GeofenceZone":
        points_list = []
        for p in data.get("points", []):
            points_list.append((float(p["x"]), float(p["y"])))
        return cls(
            id=data["id"],
            name=data["name"],
            zone_type=data.get("type", data.get("zone_type", "forbidden")),
            points=points_list,
            min_z=float(data.get("min_z", 0.0)),
            max_z=float(data.get("max_z", 3.0)),
            speed_limit=float(data.get("speed_limit", 1.0)),
            color=data.get("color", "#FF0000"),
        )

    def contains(self, x: float, y: float, z: float) -> bool:
        # 1. Check height (Z) bounds
        if not (self.min_z <= z <= self.max_z):
            return False

        # 2. Check 2D Polygon bounds using QPolygonF
        poly = QPolygonF()
        for pt in self.points:
            poly.append(QPointF(pt[0], pt[1]))

        # OddEvenFill is standard Ray-Casting point-in-polygon test
        from PyQt6.QtCore import Qt
        return poly.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill)
