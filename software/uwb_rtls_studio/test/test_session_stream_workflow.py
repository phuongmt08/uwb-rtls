import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT = os.path.normpath(os.path.join(ROOT, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from common import protocol_pb2 as pb
from PyQt6.QtCore import QCoreApplication
from models.session_model import SessionModel
from models.telemetry_model import TelemetryModel
from repository.telemetry_repository import TelemetryRepository
from utils import command_flags


_QT_APP = None

def _ensure_qt_app():
    global _QT_APP
    _QT_APP = QCoreApplication.instance() or _QT_APP or QCoreApplication([])
    return _QT_APP


def test_session_model_keeps_ranging_and_log_runs_independent():
    _ensure_qt_app()
    model = SessionModel()
    session_id = model.start_app_session(device_snapshot={"device_name": "tag"})

    log_run = model.open_log_run(device_key="tag-1")
    ranging_run = model.open_ranging_run()
    model.close_ranging_run(sample_count=2, files=["ranging_run_001.csv"])

    assert model.session_id == session_id
    assert model.active_run("ranging") is None
    assert model.active_run("log") == log_run

    model.close_log_run(line_count=3, files=["log_run_001.csv"])
    assert model.active_runs() == []

    runs = model.build_runs_meta()
    assert [run["stream_type"] for run in runs] == ["log", "ranging"]
    assert runs[0]["sample_count"] == 3
    assert runs[1]["sample_count"] == 2
    assert ranging_run.index == 1


def test_telemetry_model_displays_missing_fields_as_placeholders():
    _ensure_qt_app()
    model = TelemetryModel()
    model.handle_battery_info({"bat_voltage_mv": 3820})

    display = model.display_snapshot()
    assert display["bat_voltage_str"] == "3.82V"
    assert display["bat_soc_percent"] is None
    assert display["remaining_str"] == "--"
    assert display["mcu_temp_str"] == "--"



def test_telemetry_model_tracks_ble_status_and_connection_params():
    _ensure_qt_app()
    model = TelemetryModel()

    model.handle_ble_status({
        "state": 5,
        "state_name": "CONNECTED",
        "display_state": "Connected",
        "rssi_dbm": -58,
        "disconnect_reason": 0,
        "disconnect_reason_hex": "0x00",
        "disconnect_reason_name": "Success",
    }, received_at=1000.0)
    model.handle_ble_conn_params({
        "conn_interval": "30 - 50 ms",
        "slave_latency": 0,
        "supervision_timeout": 4000,
        "phy": "2M",
    }, received_at=1001.0)

    ble = model.snapshot()["ble_status"]
    assert ble["display_state"]["value"] == "Connected"
    assert ble["rssi_dbm"]["value"] == -58
    assert ble["conn_interval"]["value"] == "30 - 50 ms"
    assert ble["phy"]["freshness"] == "fresh"


def test_telemetry_repository_does_not_parse_absent_proto_fields_as_zero():
    repo = TelemetryRepository()
    resp = pb.battery_info_resp_t()
    resp.bat_voltage_mv = 3820

    parsed = repo.parse_battery_info(resp)

    assert parsed["bat_voltage_mv"] == 3820
    assert parsed["bat_soc_percent"] is None
    assert parsed["mcu_temp_c"] is None


def test_command_flag_can_disable_one_command(monkeypatch):
    monkeypatch.setitem(command_flags.COMMAND_ENABLE, "anchor_layout_get", 0)
    monkeypatch.setitem(command_flags.COMMAND_ENABLE, "battery_info_get", 1)

    assert command_flags.is_command_enabled("anchor_layout_get") is False
    assert command_flags.is_command_enabled("battery_info_get") is True


def test_ranging_run_streams_sensor_fusion_records_until_stop(tmp_path, monkeypatch):
    _ensure_qt_app()
    from PyQt6.QtCore import QObject, pyqtSignal
    import repository.session_repository as session_repository_module
    from repository.session_repository import SessionRepository
    from services.session_run_manager import SessionRunManager

    data_root = tmp_path / "data"
    sessions_dir = data_root / "sessions"
    store_dir = data_root / "session_store"
    hot_dir = store_dir / "hot"
    archive_dir = store_dir / "archive"
    browser_dir = data_root / "session_browser"
    monkeypatch.setattr(session_repository_module, "DATA_DIR", str(data_root))
    monkeypatch.setattr(session_repository_module, "SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setattr(session_repository_module, "INDEX_FILE", str(sessions_dir / "index.json"))
    monkeypatch.setattr(session_repository_module, "SESSION_STORE_DIR", str(store_dir))
    monkeypatch.setattr(session_repository_module, "HOT_STORE_DIR", str(hot_dir))
    monkeypatch.setattr(session_repository_module, "ARCHIVE_STORE_DIR", str(archive_dir))
    monkeypatch.setattr(session_repository_module, "SESSION_BROWSER_DIR", str(browser_dir))
    monkeypatch.setattr(session_repository_module, "BROWSER_RANGING_DIR", str(browser_dir / "ranging"))
    monkeypatch.setattr(session_repository_module, "BROWSER_LOG_DIR", str(browser_dir / "log"))

    class FakeRangingModel(QObject):
        session_sample_recorded = pyqtSignal(dict)

        def __init__(self):
            super().__init__()
            self.is_ranging = False

        def stop_ranging(self):
            self.is_ranging = False

    repo = SessionRepository()
    session_model = SessionModel()
    ranging_model = FakeRangingModel()
    manager = SessionRunManager(session_model, repo, ranging_model=ranging_model)

    manager.open_ranging_run()
    for seq in range(1, 301):
        ranging_model.session_sample_recorded.emit({
            "source": "sensor_fusion",
            "seq": seq,
            "timestamp_ms": seq * 100,
            "received_at": 1000.5 + seq,
            "ukf_step": 1 if seq % 3 == 0 else 0,
            "ukf_x_m": float(seq) + 0.5,
            "ukf_y_m": 1.5,
            "ukf_yaw_deg": 0.0,
            "tril_x_m": float(seq),
            "tril_y_m": 1.0,
            "yaw_deg": 0.0,
            "anchor_mask": 0x0F,
            "ranging_error_count": 0,
            "anchors": [
                {"anchor_id": 1, "distance_mm": 1000 + seq, "weight": 90},
                {"anchor_id": 2, "distance_mm": 2000 + seq, "weight": 80},
                {"anchor_id": 3, "distance_mm": 3000 + seq, "weight": 70},
                {"anchor_id": 4, "distance_mm": 4000 + seq, "weight": 60},
            ],
        })

    session_id, files = manager.close_ranging_run(send_end=False)

    assert len(files) == 1
    assert files[0].endswith("_ranging_001.csv")
    assert len(files[0].split("_", 1)[0].split("-")) == 3

    record_path = os.path.join(repo.get_session_storage_folder(session_id), files[0])
    rows = open(record_path, "r", encoding="utf-8").read().splitlines()
    assert len(rows) == 300
    assert "Update" in rows[-1]
    assert "| ukf_x:" in rows[-1]
    assert "| d1:  1.300000" in rows[-1]
    assert "| w1: 90" in rows[-1]

    runs = repo.list_session_runs(session_id, "ranging")
    assert runs[0]["sample_count"] == 300
    assert runs[0]["files"] == files
