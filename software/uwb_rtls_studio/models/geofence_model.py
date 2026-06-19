"""
===============================================================================
  UWB RTLS Studio - Geofence Model
===============================================================================
  File        : models/geofence_model.py
  Description : Data model for 2.5D Geofence zones and violation logic.
  MVVM Role   : MODEL - contains geofence data structures.
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
    zone_type: str  # "allowed" | "forbidden" for rule zones; map objects reuse the same field for compatibility
    points: List[Tuple[float, float]]  # Global scene coordinates as (x, y) in meters
    min_z: float = 0.0
    max_z: float = 3.0
    speed_limit: float = 1.0  # Max speed inside this zone (m/s)
    color: str = "#FF0000"  # Hex color string
    object_type: str = "zone"  # "zone" | "room" | "wall" | "object"
    shape_kind: str = "polygon"  # "polygon" | "circle" for generic objects
    radius_m: float = 0.0  # Circle radius in meters when shape_kind == "circle"
    thickness_m: float = 0.1  # Wall thickness in meters.
    # Room-local coordinate frame. Geometry remains in scene coordinates.
    origin_vertex_idx: Optional[int] = None
    local_frame_yaw_deg: float = 0.0
    # Wall behavior: boundary_outside | internal_partition | free_standing.
    wall_mode: str = "free_standing"
    host_room_id: Optional[str] = None

    @property
    def design_kind(self) -> str:
        if self.object_type == "room":
            return "room"
        if self.object_type == "wall":
            return "wall"
        if self.object_type == "object":
            return "object"
        if self.zone_type == "forbidden":
            return "no_go_rule"
        return "speed_rule"

    @property
    def is_rule_zone(self) -> bool:
        return self.object_type == "zone"

    @property
    def is_map_object(self) -> bool:
        return self.object_type in {"room", "wall", "object"}

    def to_dict(self) -> dict:
        height_m = max(0.0, self.max_z - self.min_z) if self.object_type in {"wall", "object"} else 0.0
        return {
            "id": self.id,
            "name": self.name,
            "type": self.zone_type,
            "object_type": self.object_type,
            "shape_kind": self.shape_kind if self.object_type == "object" else "polygon",
            "points": [{"x": p[0], "y": p[1]} for p in self.points],
            "min_z": self.min_z,
            "max_z": self.max_z,
            "height_m": height_m,
            "radius_m": self.radius_m if self.object_type == "object" else 0.0,
            "thickness_m": self.thickness_m if self.object_type == "wall" else 0.0,
            "origin_vertex_idx": self.origin_vertex_idx if self.object_type == "room" else None,
            "local_frame_yaw_deg": self.local_frame_yaw_deg if self.object_type == "room" else 0.0,
            "wall_mode": self.wall_mode if self.object_type == "wall" else "free_standing",
            "host_room_id": self.host_room_id if self.object_type == "wall" else None,
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
            object_type = zone_type if zone_type in {"room", "wall", "object"} else "zone"

        height_m = data.get("height_m")
        min_z = float(data.get("min_z", 0.0))
        max_z = float(data.get("max_z", 3.0))
        if object_type == "room":
            min_z = 0.0
            max_z = 0.0
        elif height_m is not None and object_type in {"wall", "object"}:
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
            shape_kind=str(data.get("shape_kind", "circle" if object_type == "object" and data.get("radius_m", 0.0) else "polygon")),
            radius_m=float(data.get("radius_m", 0.0)),
            thickness_m=float(data.get("thickness_m", data.get("wall_thickness_m", 0.1))),
            origin_vertex_idx=(
                int(data["origin_vertex_idx"])
                if data.get("origin_vertex_idx") is not None else None
            ),
            local_frame_yaw_deg=float(data.get("local_frame_yaw_deg", 0.0)),
            wall_mode=str(data.get("wall_mode", "free_standing")),
            host_room_id=data.get("host_room_id") or None,
        )

    def contains(self, x: float, y: float, z: float) -> bool:
        if self.object_type not in {"zone", "object"}:
            return False

        if self.min_z != self.max_z:
            if not (self.min_z <= z <= self.max_z):
                return False

        poly = QPolygonF()
        for pt in self.points:
            poly.append(QPointF(pt[0], pt[1]))

        from PyQt6.QtCore import Qt
        return poly.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill)
