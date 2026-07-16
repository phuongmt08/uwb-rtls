"""Ground-truth path model shared by geofencing and live tracking."""

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class GroundTruthTrack:
    id: str
    name: str
    points: List[Tuple[float, float]]
    color: str = "#FB7185"
    line_width: float = 2.0
    coordinate_frame: str = "world"
    metadata: dict = field(default_factory=dict)

    @property
    def segments(self) -> list[list[object]]:
        return [
            [x1, y1, x2, y2, False]
            for (x1, y1), (x2, y2) in zip(self.points, self.points[1:])
        ]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "points": [{"x": x, "y": y} for x, y in self.points],
            "x": [point[0] for point in self.points],
            "y": [point[1] for point in self.points],
            "segments": self.segments,
            "color": self.color,
            "line_width": self.line_width,
            "coordinate_frame": self.coordinate_frame,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroundTruthTrack":
        points = []
        for point in data.get("points", []):
            if isinstance(point, dict):
                points.append((float(point["x"]), float(point["y"])))
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                points.append((float(point[0]), float(point[1])))

        if not points:
            for segment in data.get("segments", []):
                if not isinstance(segment, (list, tuple)) or len(segment) < 4:
                    continue
                start = (float(segment[0]), float(segment[1]))
                end = (float(segment[2]), float(segment[3]))
                if not points or points[-1] != start:
                    points.append(start)
                points.append(end)

        if not points:
            xs = data.get("x", [])
            ys = data.get("y", [])
            points = [
                (float(x), float(y))
                for x, y in zip(xs, ys)
                if x is not None and y is not None
            ]

        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            points=points,
            color=str(data.get("color", "#FB7185")),
            line_width=max(1.0, float(data.get("line_width", 2.0))),
            coordinate_frame=str(data.get("coordinate_frame", "world")),
            metadata=dict(data.get("metadata", {})),
        )