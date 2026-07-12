import os
import sys
import math
import tempfile
from PyQt6.QtCore import QCoreApplication

# Add project root to path for imports
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)

from models.geofence_model import GeofenceZone
from repository.geofence_repository import GeofenceRepository


def _ensure_qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def test_geofence_zone_serialization():
    _ensure_qt_app()
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    zone = GeofenceZone(
        id="zone_test",
        name="Test Zone",
        zone_type="forbidden",
        points=points,
        min_z=0.5,
        max_z=2.5,
        speed_limit=0.5,
        color="#FF0000"
    )

    data = zone.to_dict()
    assert data["id"] == "zone_test"
    assert data["name"] == "Test Zone"
    assert data["type"] == "forbidden"
    assert data["object_type"] == "zone"
    assert len(data["points"]) == 4
    assert data["min_z"] == 0.5
    assert data["max_z"] == 2.5
    assert data["speed_limit"] == 0.5
    assert data["color"] == "#FF0000"

    loaded_zone = GeofenceZone.from_dict(data)
    assert loaded_zone.id == zone.id
    assert loaded_zone.name == zone.name
    assert loaded_zone.zone_type == zone.zone_type
    assert loaded_zone.object_type == zone.object_type
    assert loaded_zone.min_z == zone.min_z
    assert loaded_zone.max_z == zone.max_z
    assert loaded_zone.speed_limit == zone.speed_limit
    assert loaded_zone.color == zone.color
    assert len(loaded_zone.points) == 4
    assert loaded_zone.points[1] == (10.0, 0.0)


def test_geofence_zone_contains_math():
    _ensure_qt_app()
    points = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)]
    zone = GeofenceZone(
        id="zone_math",
        name="Math Zone",
        zone_type="forbidden",
        points=points,
        min_z=1.0,
        max_z=2.0,
        speed_limit=1.0,
        color="#FF0000"
    )

    # 1. Point is inside the 2D rule zone
    assert zone.contains(2.5, 2.5, 1.5) is True

    # 2. Point is outside the 2D polygon
    assert zone.contains(6.0, 2.5, 1.5) is False

    # 3. Rule zones are 2.5D, so z coordinates outside [min_z, max_z] return False.
    assert zone.contains(2.5, 2.5, 0.5) is False
    assert zone.contains(2.5, 2.5, 1.5) is True

    # 4. Map objects are geometry only and do not participate in rule checks.
    wall = GeofenceZone(
        id="wall_math",
        name="Wall",
        zone_type="wall",
        points=points,
        min_z=0.0,
        max_z=3.0,
        speed_limit=0.0,
        color="#0F172A",
        object_type="wall",
    )
    assert wall.contains(2.5, 2.5, 1.5) is False


def test_geofence_repository_position_checks():
    _ensure_qt_app()
    # Create a temporary file path for testing repo
    test_json_path = os.path.join(tempfile.gettempdir(), f"uwb_rtls_test_geofences_{os.getpid()}.json")
    if os.path.exists(test_json_path):
        try:
            os.remove(test_json_path)
        except PermissionError:
            pass

    repo = GeofenceRepository(default_file_path=test_json_path)
    
    # Init empty checking
    status, zone_name, limit = repo.check_position(1.0, 1.0, 1.0)
    assert status == "allowed"
    assert zone_name == "Default Space"

    # Add allowed zone
    allowed_points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    allowed_zone = GeofenceZone(
        id="allowed_1",
        name="Allowed Space",
        zone_type="allowed",
        points=allowed_points,
        min_z=0.0,
        max_z=3.0,
        speed_limit=2.0,
        color="#00FF00"
    )
    repo.add_zone(allowed_zone)

    # Add forbidden zone inside
    forbidden_points = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
    forbidden_zone = GeofenceZone(
        id="forbidden_1",
        name="Danger Zone",
        zone_type="forbidden",
        points=forbidden_points,
        min_z=0.0,
        max_z=3.0,
        speed_limit=0.0,
        color="#FF0000"
    )
    repo.add_zone(forbidden_zone)

    # Add a wall map object; it should be persisted but ignored by rule checks.
    wall_zone = GeofenceZone(
        id="wall_1",
        name="North Wall",
        zone_type="wall",
        points=[(20.0, 20.0), (21.0, 20.0), (21.0, 21.0), (20.0, 21.0)],
        min_z=0.0,
        max_z=3.0,
        speed_limit=0.0,
        color="#0F172A",
        object_type="wall",
    )
    repo.add_zone(wall_zone)

    # Check safe position (inside allowed, outside forbidden)
    status, zone_name, limit = repo.check_position(2.0, 2.0, 1.0)
    assert status == "allowed"
    assert zone_name == "Allowed Space"
    assert limit == 2.0

    # Check forbidden position (inside forbidden)
    status, zone_name, limit = repo.check_position(5.0, 5.0, 1.0)
    assert status == "forbidden"
    assert zone_name == "Danger Zone"
    assert limit == 0.0

    # Check out of bounds position (outside allowed)
    status, zone_name, limit = repo.check_position(12.0, 5.0, 1.0)
    assert status == "forbidden"
    assert "Outside Allowed Boundary" in zone_name

    # Check save/load
    assert repo.save() is True
    assert os.path.exists(test_json_path) is True

    # Reload repo from saved json
    new_repo = GeofenceRepository(default_file_path=test_json_path)
    assert len(new_repo.get_zones()) == 3
    status, zone_name, limit = new_repo.check_position(5.0, 5.0, 1.0)
    assert status == "forbidden"
    assert zone_name == "Danger Zone"

    # Cleanup temp file
    if os.path.exists(test_json_path):
        try:
            os.remove(test_json_path)
        except PermissionError:
            pass




def test_live_tracking_syncs_loaded_map_anchors_to_canvas_and_layout():
    _ensure_qt_app()
    from views.tabs.live_tracking_tab import LiveTrackingTab

    room = GeofenceZone(
        id="room_1",
        name="Room 1",
        zone_type="room",
        points=[(10.0, 10.0), (14.0, 10.0), (14.0, 14.0), (10.0, 14.0)],
        object_type="room",
    )

    class FakeViewModel:
        def __init__(self):
            self.committed = None

        def get_map_anchors(self):
            return [
                {
                    "anchor_id": 1,
                    "room_id": "room_1",
                    "local_x_m": 1.0,
                    "local_y_m": 2.0,
                    "x_m": 1.0,
                    "y_m": 2.0,
                    "z_m": 2.5,
                    "label": "A1",
                }
            ]

        def get_geofence_zones(self):
            return [room]

        def update_anchor_layout_from_map(self, anchors):
            self.committed = [dict(anchor) for anchor in anchors]

    class FakeCanvas:
        def __init__(self):
            self.anchors = None

        def set_anchors(self, anchors):
            self.anchors = [dict(anchor) for anchor in anchors]

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab._vm = FakeViewModel()
    tab._canvas = FakeCanvas()
    tab._map_3d = FakeCanvas()

    synced = LiveTrackingTab._sync_loaded_map_anchors(tab, update_canvas=True)

    assert len(synced) == 1
    assert tab._vm.committed[0]["room_id"] == "room_1"
    assert math.isclose(tab._vm.committed[0]["x_m"], 1.0)
    assert math.isclose(tab._vm.committed[0]["y_m"], 2.0)
    assert math.isclose(tab._canvas.anchors[0]["x"], 11.0)
    assert math.isclose(tab._canvas.anchors[0]["y"], 12.0)
    assert math.isclose(tab._map_3d.anchors[0]["x"], 11.0)

def test_live_tracking_current_layout_uses_room_scene_coordinates():
    _ensure_qt_app()
    from views.tabs.live_tracking_tab import LiveTrackingTab

    room = GeofenceZone(
        id="room_e1_303",
        name="E1-303",
        zone_type="room",
        points=[(-2.3, -6.0), (6.0, -6.0), (6.0, 2.4), (-2.3, 2.4)],
        object_type="room",
        origin_vertex_idx=0,
    )

    class FakeViewModel:
        def __init__(self):
            self.current_anchor_layout = [
                {
                    "anchor_id": 0,
                    "room_id": "room_e1_303",
                    "local_x_m": 0.7,
                    "local_y_m": 0.0,
                    "x_m": 0.7,
                    "y_m": 0.0,
                    "z_m": 2.5,
                    "label": "A0",
                }
            ]

        def get_geofence_zones(self):
            return [room]

    class FakeCanvas:
        def __init__(self):
            self.anchors = []

        def set_anchors(self, anchors):
            self.anchors = [dict(anchor) for anchor in anchors]

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab._vm = FakeViewModel()
    tab._canvas = FakeCanvas()

    LiveTrackingTab._set_current_layout_on_canvas(tab)

    assert math.isclose(tab._canvas.anchors[0]["x"], -1.6)
    assert math.isclose(tab._canvas.anchors[0]["y"], -6.0)
    assert math.isclose(tab._canvas.anchors[0]["local_x_m"], 0.7)
    assert math.isclose(tab._canvas.anchors[0]["local_y_m"], 0.0)

if __name__ == "__main__":
    print("Running Geofencing tests...")
    try:
        test_geofence_zone_serialization()
        print("[OK] test_geofence_zone_serialization passed!")
        test_geofence_zone_contains_math()
        print("[OK] test_geofence_zone_contains_math passed!")
        test_geofence_repository_position_checks()
        print("[OK] test_geofence_repository_position_checks passed!")
        test_live_tracking_syncs_loaded_map_anchors_to_canvas_and_layout()
        print("[OK] test_live_tracking_syncs_loaded_map_anchors_to_canvas_and_layout passed!")
        test_live_tracking_current_layout_uses_room_scene_coordinates()
        print("[OK] test_live_tracking_current_layout_uses_room_scene_coordinates passed!")
        print("All tests passed successfully!")
    except Exception as e:
        import traceback
        print("[ERROR] Test failed!")
        traceback.print_exc()
        sys.exit(1)
