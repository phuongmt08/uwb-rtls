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


def _ensure_qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


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
