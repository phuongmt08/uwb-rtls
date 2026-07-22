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
from models.ground_truth_model import GroundTruthTrack
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


def test_ground_truth_track_simulation_compatible_serialization():
    track = GroundTruthTrack(
        id="gt_square",
        name="Square",
        points=[(1.0, 1.0), (3.0, 1.0), (3.0, 4.0)],
    )

    data = track.to_dict()
    assert data["x"] == [1.0, 3.0, 3.0]
    assert data["y"] == [1.0, 1.0, 4.0]
    assert data["segments"] == [
        [1.0, 1.0, 3.0, 1.0, False],
        [3.0, 1.0, 3.0, 4.0, False],
    ]

    restored = GroundTruthTrack.from_dict(data)
    assert restored.id == track.id
    assert restored.name == track.name
    assert restored.points == track.points

    simulation_style = {
        "id": "sim_segments",
        "name": "Simulation Segments",
        "x": [0.0, 1.0, None, 9.0, 10.0],
        "y": [0.0, 0.0, None, 9.0, 9.0],
        "segments": [
            [0.0, 0.0, 1.0, 0.0, False],
            [1.0, 0.0, 1.0, 1.0, False],
        ],
    }
    restored_sim = GroundTruthTrack.from_dict(simulation_style)
    assert restored_sim.points == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]


def test_live_tracking_ground_truth_import_helpers_accept_json_and_graphml():
    _ensure_qt_app()
    from views.tabs.live_tracking_tab import LiveTrackingTab

    class FakeViewModel:
        def get_ground_truths(self):
            return []

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab._vm = FakeViewModel()

    tracks = LiveTrackingTab._ground_truth_tracks_from_payload(
        tab,
        {
            "ground_truths": [
                {
                    "id": "route_a",
                    "name": "Route A",
                    "points": [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.0}],
                }
            ]
        },
        "friend_ground_truth.json",
    )
    assert len(tracks) == 1
    assert tracks[0].name == "Route A"
    assert tracks[0].points[-1] == (2.0, 0.0)

    graphml_path = os.path.join(tempfile.gettempdir(), f"uwb_gt_{os.getpid()}.xml")
    graphml = '''<?xml version="1.0" encoding="UTF-8"?>
<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
  <key id="d0" for="node" attr.name="x" attr.type="double"/>
  <key id="d1" for="node" attr.name="y" attr.type="double"/>
  <graph edgedefault="undirected">
    <node id="n0"><data key="d0">1.0</data><data key="d1">1.5</data></node>
    <node id="n1"><data key="d0">2.0</data><data key="d1">1.5</data></node>
    <edge source="n0" target="n1"/>
  </graph>
</graphml>'''
    with open(graphml_path, "w", encoding="utf-8") as f:
        f.write(graphml)
    try:
        graph_tracks = LiveTrackingTab._parse_graphml_ground_truth(tab, graphml_path)
    finally:
        if os.path.exists(graphml_path):
            os.remove(graphml_path)
    assert len(graph_tracks) == 1
    assert graph_tracks[0].points == [(1.0, 1.5), (2.0, 1.5)]


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
    repo.add_ground_truth(
        GroundTruthTrack(
            id="gt_test",
            name="Test Route",
            points=[(1.0, 1.0), (2.0, 1.0), (2.0, 3.0)],
        )
    )

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
    assert len(new_repo.get_ground_truths()) == 1
    assert new_repo.get_ground_truths()[0].points[-1] == (2.0, 3.0)
    status, zone_name, limit = new_repo.check_position(5.0, 5.0, 1.0)
    assert status == "forbidden"
    assert zone_name == "Danger Zone"
    new_repo.clear()
    assert len(new_repo.get_ground_truths()) == 1

    # Cleanup temp file
    if os.path.exists(test_json_path):
        try:
            os.remove(test_json_path)
        except PermissionError:
            pass





def test_e1_3_map_json_loads_all_map_objects_and_zero_based_anchors():
    _ensure_qt_app()
    map_path = os.path.abspath(os.path.join(CURRENT_DIR, "..", "data", "maps", "E1-3.json"))
    repo = GeofenceRepository(map_path)

    zones = repo.get_zones()
    anchors = repo.get_anchors()
    object_types = {getattr(zone, "object_type", "zone") for zone in zones}
    anchor_ids = {anchor["anchor_id"] for anchor in anchors}

    assert len(zones) >= 20
    assert {"room", "wall", "object"}.issubset(object_types)
    assert 0 in anchor_ids
    assert max(anchor_ids) >= 6


def test_live_tracking_anchor_telemetry_rows_follow_real_anchor_ids():
    _ensure_qt_app()
    from views.tabs.live_tracking_tab import LiveTrackingTab

    class FakeCanvas:
        anchors = [
            {"anchor_id": 0, "label": "A0"},
            {"anchor_id": 1, "label": "A1"},
            {"anchor_id": 2, "label": "A2"},
            {"anchor_id": 3, "label": "A3"},
            {"anchor_id": 4, "label": "A4"},
            {"anchor_id": 5, "label": "A5"},
        ]

    class FakeLabel:
        def __init__(self):
            self.text = None

        def setText(self, value):
            self.text = value

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab._canvas = FakeCanvas()
    tab._anchor_telemetry_cache = {}
    for row in range(1, 7):
        setattr(tab, f"lbl_d{row}", FakeLabel())
        setattr(tab, f"d{row}_label", FakeLabel())

    LiveTrackingTab._show_anchor_telemetry(
        tab,
        [
            {"anchor_id": 0, "distance_mm": 1234, "weight": 75},
            {"anchor_id": 2, "distance_mm": 3456, "weight": 50},
            {"anchor_id": 5, "distance_mm": 6789, "weight": 25},
        ],
    )

    assert tab.lbl_d1.text == "A0:"
    assert tab.d1_label.text == "1.234 m  |  W: 0.75"
    assert tab.lbl_d3.text == "A2:"
    assert tab.d3_label.text == "3.456 m  |  W: 0.50"
    assert tab.lbl_d6.text == "A5:"
    assert tab.d6_label.text == "6.789 m  |  W: 0.25"


def test_ranging_anchor_parser_preserves_anchor_zero_without_breaking_legacy_mask():
    from models.ranging_model import RangingModel

    class Anchor:
        def __init__(self, anchor_id, distance_mm):
            self.anchor_id = anchor_id
            self.distance_mm = distance_mm
            self.fp_amp = 0

    class Response:
        def __init__(self, anchors):
            self.anchors = anchors

    zero_based = Response([Anchor(0, 1000), Anchor(1, 1100), Anchor(2, 1200), Anchor(3, 1300)])
    anchors, mask, distances = RangingModel._parse_anchor_distances(zero_based)
    assert [anchor["anchor_id"] for anchor in anchors] == [0, 1, 2, 3]
    assert mask == 0b1111
    assert distances[0] == 1000
    assert distances[3] == 1300

    one_based = Response([Anchor(1, 1000), Anchor(2, 1100), Anchor(3, 1200), Anchor(4, 1300)])
    _, mask, distances = RangingModel._parse_anchor_distances(one_based)
    assert mask == 0b1111
    assert distances[1] == 1000
    assert distances[4] == 1300

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


def test_live_tracking_mode_switch_keeps_loaded_map_as_visual_source():
    _ensure_qt_app()
    from views.tabs.live_tracking_tab import LiveTrackingTab

    map_path = os.path.abspath(os.path.join(CURRENT_DIR, "..", "data", "maps", "E1-3.json"))
    repo = GeofenceRepository(map_path)

    class FakeViewModel:
        current_anchor_layout = [
            {"anchor_id": idx, "x_m": 100_000_000.0 + idx, "y_m": -100_000_000.0 - idx}
            for idx in range(1000)
        ]

        def get_geofence_zones(self):
            return repo.get_zones()

        def get_map_anchors(self):
            return repo.get_anchors()

    class FakeCanvas:
        def __init__(self):
            self.zones = []
            self.anchors = []
            self.auto_fit_calls = 0

        def set_geofences(self, zones):
            self.zones = list(zones)

        def set_anchors(self, anchors):
            self.anchors = [dict(anchor) for anchor in anchors]

        def auto_fit(self):
            self.auto_fit_calls += 1

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab._vm = FakeViewModel()
    tab._canvas = FakeCanvas()
    tab._map_3d = FakeCanvas()

    LiveTrackingTab._sync_tracking_canvas_from_map(tab)

    assert len(tab._canvas.zones) == 27
    assert len(tab._canvas.anchors) == 7
    assert len(tab._map_3d.zones) == 27
    assert len(tab._map_3d.anchors) == 7
    assert max(abs(anchor["x"]) for anchor in tab._canvas.anchors) < 100.0
    assert {getattr(zone, "object_type", "zone") for zone in tab._canvas.zones} == {
        "room", "wall", "object", "zone"
    }


def test_geofence_repository_can_start_empty_until_explicit_load():
    map_path = os.path.abspath(os.path.join(CURRENT_DIR, "..", "data", "maps", "E1-3.json"))
    repo = GeofenceRepository(map_path, autoload=False)

    assert repo.get_zones() == []
    assert repo.get_anchors() == []
    assert repo.get_ground_truths() == []

    assert repo.load(map_path) is True
    assert len(repo.get_zones()) == 27
    assert len(repo.get_anchors()) == 7


def test_live_tracking_viewmodel_does_not_autoload_runtime_map():
    _ensure_qt_app()
    from PyQt6.QtCore import QObject, pyqtSignal
    import viewmodels.live_tracking_viewmodel as live_tracking_viewmodel

    class FakeRangingModel(QObject):
        position_updated = pyqtSignal(float, float, float, float)
        sensor_fusion_updated = pyqtSignal(dict)
        calib_data_updated = pyqtSignal(dict)
        anchor_distances_updated = pyqtSignal(list)
        stats_updated = pyqtSignal(dict)

        def clear_history(self):
            pass

    class FakeSharedAppState(QObject):
        anchor_layout_changed = pyqtSignal(list)
        device_session_reset = pyqtSignal(str)

    original_state = live_tracking_viewmodel.shared_app_state
    live_tracking_viewmodel.shared_app_state = FakeSharedAppState()
    try:
        vm = live_tracking_viewmodel.LiveTrackingViewModel(
            FakeRangingModel(), protocol_service=object()
        )

        assert vm.get_geofence_zones() == []
        assert vm.get_map_anchors() == []
        assert vm.get_ground_truths() == []
    finally:
        live_tracking_viewmodel.shared_app_state = original_state


def test_refresh_map_list_does_not_emit_or_replace_explicit_selection():
    _ensure_qt_app()
    from views.tabs.live_tracking_tab import LiveTrackingTab

    class FakeCombo:
        def __init__(self):
            self.items = []
            self.index = -1
            self.blocked = False
            self.events = []

        def blockSignals(self, blocked):
            self.blocked = bool(blocked)

        def clear(self):
            self.items = []
            self.index = -1
            if not self.blocked:
                self.events.append(-1)

        def addItem(self, label, data):
            self.items.append((label, data))
            if self.index < 0:
                self.index = 0
                if not self.blocked:
                    self.events.append(0)

        def currentData(self):
            if 0 <= self.index < len(self.items):
                return self.items[self.index][1]
            return None

        def findData(self, value):
            return next(
                (idx for idx, (_label, data) in enumerate(self.items) if data == value),
                -1,
            )

        def setCurrentIndex(self, index):
            self.index = index
            if not self.blocked:
                self.events.append(index)

    selected_path = os.path.abspath(
        os.path.join(CURRENT_DIR, "..", "data", "maps", "E1-3.json")
    )
    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab.cmb_user_map = FakeCombo()

    LiveTrackingTab._refresh_map_list(tab, selected_path=selected_path)

    assert tab.cmb_user_map.events == []
    assert tab.cmb_user_map.currentData() == selected_path

def test_live_tracking_device_session_reset_returns_ranging_button_to_start():
    from views.tabs.live_tracking_tab import LiveTrackingTab

    class FakeButton:
        def __init__(self):
            self._text = ""
            self.enabled = False
            self.styles = []

        def setText(self, text):
            self._text = text

        def text(self):
            return self._text

        def setStyleSheet(self, style):
            self.styles.append(style)

        def setEnabled(self, enabled):
            self.enabled = bool(enabled)

    class FakeDistanceGraph:
        def __init__(self):
            self.stop_calls = 0

        def stop_session(self):
            self.stop_calls += 1

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab.btn_start = FakeButton()
    tab._distance_graph = FakeDistanceGraph()
    tab._is_ranging = True
    tab._ranging_stop_requested = True
    LiveTrackingTab._sync_ranging_button(tab)

    assert tab.btn_start.text() == "Stop Ranging"

    LiveTrackingTab._on_device_session_reset(tab, "read from device refresh")
    assert tab._is_ranging is True
    assert tab._ranging_stop_requested is True
    assert tab.btn_start.text() == "Stop Ranging"
    assert tab._distance_graph.stop_calls == 0

    LiveTrackingTab._on_device_session_reset(tab, "switch device")

    assert tab._is_ranging is False
    assert tab._ranging_stop_requested is False
    assert tab.btn_start.text() == "Start Ranging"
    assert tab.btn_start.enabled is True
    assert tab._distance_graph.stop_calls == 1

def test_live_tracking_delete_shortcut_dispatches_ground_truth_context():
    from views.tabs.live_tracking_tab import LiveTrackingTab

    class FakeStack:
        def __init__(self, index):
            self.index = index

        def currentIndex(self):
            return self.index

    class FakeTabs:
        def __init__(self, current):
            self.current = current

        def currentWidget(self):
            return self.current

    class FakeLabel:
        def __init__(self):
            self.text = ""

        def setText(self, text):
            self.text = text

    class FakeEditor:
        def __init__(self):
            self.tab_ground_truth = object()
            self.tab_map_layout = object()
            self.editor_tabs = FakeTabs(self.tab_ground_truth)
            self.lbl_ground_truth_status = FakeLabel()

    class FakeCanvas:
        def __init__(self):
            self.draw_object_type = "ground_truth"
            self.edit_mode = "draw"
            self.current_draw_points = [(0.0, 0.0), (1.0, 0.0)]
            self.clear_calls = 0

        def clear_active_drawing(self):
            self.current_draw_points.clear()
            self.clear_calls += 1

    tab = LiveTrackingTab.__new__(LiveTrackingTab)
    tab.sidebar_stack = FakeStack(1)
    tab.geofence_editor_widget = FakeEditor()
    tab._canvas = FakeCanvas()
    calls = {"gt": 0, "zone": 0}
    tab._delete_selected_ground_truth = lambda: calls.__setitem__("gt", calls["gt"] + 1)
    tab._delete_selected_zone = lambda: calls.__setitem__("zone", calls["zone"] + 1)

    LiveTrackingTab._delete_current_selection(tab)
    assert tab._canvas.clear_calls == 1
    assert tab.geofence_editor_widget.lbl_ground_truth_status.text == "Ground Truth draft cleared"
    assert calls == {"gt": 0, "zone": 0}

    LiveTrackingTab._delete_current_selection(tab)
    assert calls == {"gt": 1, "zone": 0}

    tab.geofence_editor_widget.editor_tabs.current = tab.geofence_editor_widget.tab_map_layout
    tab._canvas.draw_object_type = "room"
    tab._canvas.edit_mode = "draw"
    LiveTrackingTab._delete_current_selection(tab)
    assert calls == {"gt": 1, "zone": 1}

def _ground_truth_canvas_for_track(track):
    from views.components.position_canvas import PositionCanvas

    class FakeSignal:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    canvas = PositionCanvas.__new__(PositionCanvas)
    canvas.edit_mode = "edit_ground_truth"
    canvas.ground_truths = [track]
    canvas._ground_truth_edit_track_id = str(track.id)
    canvas._ground_truth_selected_edges = []
    canvas._ground_truth_freehand_active = False
    canvas.ground_truth_edge_selection_changed = FakeSignal()
    canvas.update = lambda: None
    return canvas


def test_canvas_undo_restores_ground_truth_snapshot():
    from views.components.position_canvas import PositionCanvas

    class FakeSignal:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    track = GroundTruthTrack("gt_undo", "Undo Path", [(0.0, 0.0), (1.0, 0.0)])
    canvas = PositionCanvas.__new__(PositionCanvas)
    canvas.is_developer_mode = True
    canvas.current_draw_points = []
    canvas._undo_stack = []
    canvas.anchors = []
    canvas.geofence_zones = []
    canvas.ground_truths = [track]
    canvas.zones_undo_restore_requested = FakeSignal()
    canvas.zones_undo_remove_requested = FakeSignal()
    canvas.ground_truths_undo_restore_requested = FakeSignal()
    canvas._emit_anchor_layout_edited = lambda: None
    canvas.update = lambda: None
    canvas.selected_vertex_idx = None
    canvas.selected_edge_idx = None
    canvas.dragging_anchor_idx = None

    canvas._push_undo_state()
    canvas.ground_truths = []

    assert canvas.undo_last_action() is True
    assert canvas.ground_truths_undo_restore_requested.calls
    restored = canvas.ground_truths_undo_restore_requested.calls[-1][0]
    assert restored[0]["id"] == "gt_undo"
    assert restored[0]["points"][-1] == {"x": 1.0, "y": 0.0}

def test_ground_truth_corner_update_reuses_original_corner_geometry():
    track = GroundTruthTrack("gt_corner", "Corner", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    canvas = _ground_truth_canvas_for_track(track)
    canvas._ground_truth_selected_edges = [0, 1]

    ok, _message, updated = canvas.apply_ground_truth_corner("fillet", 0.2)
    assert ok is True
    assert updated.metadata["corner_ops"][0]["amount_m"] == 0.2
    selected_after_first = list(canvas._ground_truth_selected_edges)
    assert selected_after_first == [0, updated.metadata["corner_ops"][0]["end_edge"]]

    ok, _message, updated = canvas.apply_ground_truth_corner("chamfer", 0.4)
    assert ok is True
    op = updated.metadata["corner_ops"][0]
    assert op["mode"] == "chamfer"
    assert op["amount_m"] == 0.4
    assert updated.points == [(0.0, 0.0), (0.6, 0.0), (1.0, 0.4), (1.0, 1.0)]
    assert canvas._ground_truth_selected_edges == [0, 2]


def test_ground_truth_extend_replaces_existing_fillet_corner():
    track = GroundTruthTrack("gt_extend_corner", "Corner", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    canvas = _ground_truth_canvas_for_track(track)
    canvas._ground_truth_selected_edges = [0, 1]

    ok, _message, updated = canvas.apply_ground_truth_corner("fillet", 0.2)
    assert ok is True
    op = dict(updated.metadata["corner_ops"][0])
    canvas._ground_truth_selected_edges = [op["start_edge"], op["end_edge"]]
    assert canvas.ground_truth_selected_edges_can_corner() is True

    ok, _message, updated = canvas.apply_ground_truth_extend()
    assert ok is True
    assert updated.points == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    assert updated.metadata.get("corner_ops") == []
    assert canvas._ground_truth_selected_edges == [0, 1]


def test_ground_truth_corner_selection_accepts_generated_fillet_edges():
    track = GroundTruthTrack("gt_generated_corner", "Corner", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    canvas = _ground_truth_canvas_for_track(track)
    canvas._ground_truth_selected_edges = [0, 1]

    ok, _message, updated = canvas.apply_ground_truth_corner("fillet", 0.2)
    assert ok is True
    op = updated.metadata["corner_ops"][0]
    canvas._ground_truth_selected_edges = [op["start_edge"] + 1, op["end_edge"] - 1]

    assert canvas.ground_truth_selected_edges_can_corner() is True

def test_ground_truth_extend_moves_nearest_endpoints_to_line_intersection():
    track = GroundTruthTrack(
        "gt_extend",
        "Extend",
        [(0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (2.0, 2.0)],
    )
    canvas = _ground_truth_canvas_for_track(track)
    canvas._ground_truth_selected_edges = [0, 2]

    ok, _message, updated = canvas.apply_ground_truth_extend()
    assert ok is True
    assert updated.points[1] == (2.0, 0.0)
    assert updated.points[2] == (2.0, 0.0)
    assert updated.metadata.get("corner_ops") == []
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
