from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QCoreApplication

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from utils.app_state import shared_app_state


_QT_APP = None


def _ensure_qt_app():
    global _QT_APP
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    _QT_APP = app
    return app


def test_stale_device_payload_does_not_mark_query_response_available():
    _ensure_qt_app()
    shared_app_state.connection_status = "Connected"
    shared_app_state.clear_query_payload_markers()
    shared_app_state.disable_device_session_payloads("unit test gate closed")

    shared_app_state.handle_incoming_packet("sys_config_resp", object())

    assert shared_app_state._response_payload_available("sys_config_resp") is False


def test_active_device_payload_marks_query_response_available():
    _ensure_qt_app()
    shared_app_state.connection_status = "Connected"
    shared_app_state.clear_query_payload_markers()
    shared_app_state.enable_device_session_payloads("unit test gate open")

    shared_app_state.handle_incoming_packet("sys_config_resp", object())

    assert shared_app_state._response_payload_available("sys_config_resp") is True
    shared_app_state.disable_device_session_payloads("unit test cleanup")

def test_last_final_report_returns_only_retryable_failed_gets():
    _ensure_qt_app()
    shared_app_state.cancel_query_pipeline("unit test last report")
    shared_app_state.clear_query_payload_markers()
    shared_app_state.enable_device_session_payloads("unit test last report")
    shared_app_state._query_flow_results["connected_device"] = {
        "device_information_get::device_information_resp::1": {
            "command_name": "device_information_get",
            "dst_addr": 1,
            "expected_response": "device_information_resp",
            "status": "SUCCESS",
            "traffic_class": "bootstrap",
        },
        "sys_config_get::sys_config_resp::1": {
            "command_name": "sys_config_get",
            "dst_addr": 1,
            "expected_response": "sys_config_resp",
            "status": "TIMEOUT",
            "traffic_class": "bootstrap",
        },
        "anchor_layout_get::anchor_layout_resp::1": {
            "command_name": "anchor_layout_get",
            "dst_addr": 1,
            "expected_response": "anchor_layout_resp",
            "status": "TIMEOUT",
            "traffic_class": "bootstrap",
        },
        "ranging_status_get::ranging_status_resp::1": {
            "command_name": "ranging_status_get",
            "dst_addr": 1,
            "expected_response": "ranging_status_resp",
            "status": "UNSUPPORTED",
            "traffic_class": "bootstrap",
            "failure_reason": "UNIMPLEMENTED",
        },
    }

    shared_app_state._print_final_flow_report("connected_device")

    failed = shared_app_state.failed_queries_from_last_report("connected_device")
    assert [item["command_name"] for item in failed] == ["sys_config_get", "anchor_layout_get"]
    assert all(item["flow_name"] == "connected_device" for item in failed)
    shared_app_state.cancel_query_pipeline("unit test cleanup")

class _Hdr:
    def __init__(self, seq: int):
        self.seq = seq


class _Pkt:
    def __init__(self, seq: int):
        self.hdr = _Hdr(seq)


def test_late_payload_after_final_fail_updates_report_and_stops_retry(capsys):
    _ensure_qt_app()
    shared_app_state.cancel_query_pipeline("unit test late payload")
    shared_app_state.clear_query_payload_markers()
    shared_app_state.enable_device_session_payloads("unit test late payload")
    shared_app_state.connection_status = "Connected"
    shared_app_state._query_flow_results["connected_device"] = {
        "sys_config_get::sys_config_resp::1": {
            "command_name": "sys_config_get",
            "dst_addr": 1,
            "expected_response": "sys_config_resp",
            "status": "TIMEOUT",
            "traffic_class": "bootstrap",
        },
        "anchor_layout_get::anchor_layout_resp::1": {
            "command_name": "anchor_layout_get",
            "dst_addr": 1,
            "expected_response": "anchor_layout_resp",
            "status": "TIMEOUT",
            "traffic_class": "bootstrap",
        },
    }
    shared_app_state._print_final_flow_report("connected_device")
    capsys.readouterr()

    shared_app_state.handle_incoming_packet("anchor_layout_resp", _Pkt(202))
    output = capsys.readouterr().out

    assert "Late Packet Report" in output
    assert "anchor_layout_get" in output
    assert "anchor_layout_resp" in output
    assert "202" in output
    assert "OK - UI UPDATED" in output
    failed = shared_app_state.failed_queries_from_last_report("connected_device")
    assert [item["command_name"] for item in failed] == ["sys_config_get"]
    shared_app_state.cancel_query_pipeline("unit test cleanup")
