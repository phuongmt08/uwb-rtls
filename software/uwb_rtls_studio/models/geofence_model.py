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
    object_type: str = "zone"  # "zone" | "room" | "wall" | "object"
    thickness: float = 0.2  # Wall thickness metadata in meters
    shape_kind: str = "polygon"  # "polygon" | "circle" | "footprint" for map objects
    object_subtype: str = "generic"  # "generic" | "stairs"
    object_direction: str = "up"  # "up" | "down" for stairs
    radius_m: float = 0.0
    origin_vertex_idx: Optional[int] = None
    local_frame_yaw_deg: float = 0.0
    wall_mode: str = "free_standing"  # "boundary_outside" | "internal_partition" | "free_standing"
    host_room_id: Optional[str] = None
    label_offset_x: float = 0.0
    label_offset_y: float = 0.0

    @property
    def thickness_m(self) -> float:
        return self.thickness

    @thickness_m.setter
    def thickness_m(self, val: float):
        self.thickness = val

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

    def polygon_area_m2(self) -> float:
        """Return the absolute polygon area in square meters."""
        if len(self.points) < 3:
            return 0.0
        area = 0.0
        for idx, (x1, y1) in enumerate(self.points):
            x2, y2 = self.points[(idx + 1) % len(self.points)]
            area += (x1 * y2) - (x2 * y1)
        return abs(area) * 0.5

    def polygon_perimeter_m(self) -> float:
        """Return the polygon perimeter in meters."""
        if len(self.points) < 2:
            return 0.0
        perimeter = 0.0
        for idx, (x1, y1) in enumerate(self.points):
            x2, y2 = self.points[(idx + 1) % len(self.points)]
            perimeter += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        return perimeter

    def to_dict(self) -> dict:
        height_m = max(0.0, self.max_z - self.min_z) if self.object_type in {"wall", "object"} else 0.0
        object_subtype = self.object_subtype if self.object_subtype == "stairs" else "generic"
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
            "thickness": self.thickness,
            "thickness_m": self.thickness,
            "shape_kind": self.shape_kind if self.object_type in {"wall", "object"} else "polygon",
            "object_subtype": object_subtype if self.object_type == "object" else "generic",
            "object_direction": self.object_direction if self.object_type == "object" else "up",
            "radius_m": self.radius_m if self.object_type == "object" else 0.0,
            "origin_vertex_idx": self.origin_vertex_idx if self.object_type == "room" else None,
            "local_frame_yaw_deg": self.local_frame_yaw_deg if self.object_type == "room" else 0.0,
            "wall_mode": self.wall_mode if self.object_type == "wall" else "free_standing",
            "host_room_id": self.host_room_id if self.object_type == "wall" else None,
            "label_offset_x": self.label_offset_x,
            "label_offset_y": self.label_offset_y,
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

        object_subtype = str(data.get("object_subtype", "generic")).lower()
        if object_subtype not in {"generic", "stairs"}:
            object_subtype = "generic"
        object_direction = str(data.get("object_direction", "up")).lower()
        if object_direction not in {"up", "down"}:
            object_direction = "up"
        shape_kind = str(data.get("shape_kind", "polygon")).lower()
        if shape_kind not in {"polygon", "circle", "footprint"}:
            shape_kind = "polygon"

        thickness_val = float(data.get("thickness_m", data.get("thickness", data.get("wall_thickness_m", 0.2))))
        wall_mode_val = str(data.get("wall_mode", "free_standing"))
        host_room_id_val = data.get("host_room_id") or None

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
            thickness=thickness_val,
            shape_kind=shape_kind,
            object_subtype=object_subtype,
            object_direction=object_direction,
            radius_m=float(data.get("radius_m", 0.0)),
            origin_vertex_idx=(
                int(data["origin_vertex_idx"])
                if data.get("origin_vertex_idx") is not None else None
            ),
            local_frame_yaw_deg=float(data.get("local_frame_yaw_deg", 0.0)),
            wall_mode=wall_mode_val,
            host_room_id=host_room_id_val,
            label_offset_x=float(data.get("label_offset_x", 0.0)),
            label_offset_y=float(data.get("label_offset_y", 0.0)),
        )

    def contains(self, x: float, y: float, z: float) -> bool:
        """Return True when the point (x, y, z) lies inside this zone.

        For rule zones (allowed / forbidden) the Z coordinate is intentionally
        ignored — they represent infinite-height 2D boundaries.  A tag triggers
        the zone alarm regardless of how high it is carried, as long as the XY
        position falls inside the polygon footprint.

        Physical map objects (walls, generic objects) retain Z-range checking so
        that only the actual 3-D volume is considered.
        """
        if self.object_type not in {"zone", "object"}:
            return False

        # Rule zones are purely 2-D: ignore Z entirely.
        if self.object_type == "zone":
            poly = QPolygonF()
            for pt in self.points:
                poly.append(QPointF(pt[0], pt[1]))
            from PyQt6.QtCore import Qt
            return poly.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill)

        # Physical objects: honour the Z range if one is defined.
        if self.min_z != self.max_z:
            if not (self.min_z <= z <= self.max_z):
                return False

        poly = QPolygonF()
        for pt in self.points:
            poly.append(QPointF(pt[0], pt[1]))

        from PyQt6.QtCore import Qt
        return poly.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill)
