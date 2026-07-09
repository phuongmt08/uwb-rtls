"""CSV recorder for all protocol messages that occur during an app session."""
from __future__ import annotations

import csv
import json
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from time import time
from typing import Any

from PyQt6.QtCore import QObject
from google.protobuf.json_format import MessageToDict

log = logging.getLogger(__name__)


class SessionMessageRecorder(QObject):
    """Append every TX/RX protobuf packet to the active session as CSV."""

    FIELDNAMES = [
        "time_iso",
        "timestamp",
        "direction",
        "message",
        "seq",
        "src",
        "dst",
        "payload_summary",
        "payload_json",
        "packet_hex",
    ]

    def __init__(self, protocol_service, session_repository, session_model, parent=None):
        super().__init__(parent)
        self._session_repository = session_repository
        self._session_model = session_model
        self._write_queue: queue.Queue[tuple] = queue.Queue()
        self._header_written: set[str] = set()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="SessionMessageRecorderWriter",
            daemon=True,
        )
        self._writer_thread.start()
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.register("SessionMessageRecorderWriter", self._writer_thread)
        except Exception as exc:
            log.debug("Could not register SessionMessageRecorderWriter thread: %s", exc)

        protocol_service.packet_sent.connect(lambda name, pkt: self.record_packet("tx", name, pkt))
        protocol_service.packet_received.connect(lambda name, pkt: self.record_packet("rx", name, pkt))

    def record_packet(self, direction: str, param_name: str, pkt) -> None:
        session_id = getattr(self._session_model, "session_id", "")
        if not session_id or not getattr(self._session_model, "is_active", False):
            return
        self._write_queue.put((
            session_id,
            direction,
            param_name or "",
            pkt,
            datetime.now().isoformat(timespec="milliseconds"),
            f"{time():.6f}",
        ))

    def _writer_loop(self) -> None:
        while True:
            item = self._write_queue.get()
            if item is None:
                break
            if not item:
                continue
            try:
                self._write_packet(*item)
            except Exception as exc:
                log.warning("Failed to record session message: %s", exc)

    def _write_packet(
        self,
        session_id: str,
        direction: str,
        param_name: str,
        pkt,
        time_iso: str,
        timestamp: str,
    ) -> None:
        path = self._messages_csv_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path_key = str(path)
        write_header = path_key not in self._header_written and (not path.exists() or path.stat().st_size == 0)

        payload = self._payload_dict(param_name, pkt)
        record = {
            "time_iso": time_iso,
            "timestamp": timestamp,
            "direction": direction,
            "message": param_name or "",
            "seq": self._packet_attr(pkt, "hdr.seq"),
            "src": self._packet_attr(pkt, "hdr.addr.src"),
            "dst": self._packet_attr(pkt, "hdr.addr.dst"),
            "payload_summary": self._payload_summary(payload),
            "payload_json": json.dumps(payload, ensure_ascii=True, sort_keys=True),
            "packet_hex": self._safe_packet_hex(pkt),
        }

        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            if write_header:
                writer.writeheader()
                self._header_written.add(path_key)
            writer.writerow(record)

    def close(self) -> None:
        self._write_queue.put(None)
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1.0)
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.unregister("SessionMessageRecorderWriter")
        except Exception as exc:
            log.debug("Could not unregister SessionMessageRecorderWriter thread: %s", exc)

    def _messages_csv_path(self, session_id: str) -> Path:
        return Path(self._session_repository.get_session_folder(session_id)) / "messages" / "session_messages.csv"

    @staticmethod
    def _payload_dict(param_name: str, pkt) -> dict[str, Any]:
        try:
            payload_msg = getattr(pkt, param_name)
            try:
                return MessageToDict(
                    payload_msg,
                    preserving_proto_field_name=True,
                    always_print_fields_with_no_presence=True,
                )
            except TypeError:
                return MessageToDict(
                    payload_msg,
                    preserving_proto_field_name=True,
                    including_default_value_fields=True,
                )
        except Exception:
            return {}

    @staticmethod
    def _payload_summary(payload: dict[str, Any]) -> str:
        if not payload:
            return ""

        parts: list[str] = []
        for key, value in payload.items():
            if isinstance(value, list):
                text = f"[{len(value)} items]"
            elif isinstance(value, dict):
                text = "{...}"
            else:
                text = str(value)
            parts.append(f"{key}={text}")
            if len(parts) >= 8:
                break
        return "; ".join(parts)

    @staticmethod
    def _packet_attr(pkt, path: str) -> str:
        try:
            value = pkt
            for name in path.split("."):
                value = getattr(value, name)
            return str(int(value))
        except Exception:
            return ""

    @staticmethod
    def _safe_packet_hex(pkt) -> str:
        try:
            return pkt.SerializeToString().hex()
        except Exception:
            return ""
