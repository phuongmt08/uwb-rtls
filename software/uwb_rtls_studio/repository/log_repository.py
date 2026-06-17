"""
Repository for device log stream packets.

Firmware sends `log_data` as one or more complete logger records in a protobuf
bytes field. This repository parses those records once, emits live log entries
for the Log tab, and keeps a session buffer that can be saved when the session
ends.
"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

LOG_TYPE_DEVICE_LOG = 1
LOG_HEADER_LEN = 9
LOG_LEN_FIELD = 2
EPOCH_MS_MIN_FOR_DATETIME = 946684800000

OBJECT_LABELS = {
    0x00: "BOOTLOADER",
    0x01: "APPLICATION",
    0x02: "NETWORK",
    0x03: "UWB DRIVER",
    0x04: "RANGING",
    0x05: "POSITIONING",
    0x06: "SERIAL",
    0x07: "IO",
    0x08: "IMU",
    0x09: "BLE",
    0x0D: "FLASH",
    0x0F: "TASK",
    0x10: "ANCHOR",
    0x11: "TAG",
    0x12: "GATEWAY",
    0x13: "PM",
    0x14: "FUSION",
    0x15: "SYS CFG",
    0x16: "BATTERY",
    0x7F: "SPECIAL",
}


class LogRepository(QObject):
    log_entry_added = pyqtSignal(dict)
    log_segment_received = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset_by_type: dict[int, int] = {}

    def handle_packet(self, param_name: str, pkt) -> bool:
        if param_name != "log_data":
            return False

        segment = pkt.log_data
        log_type = int(getattr(segment, "type", LOG_TYPE_DEVICE_LOG))
        raw = bytes(getattr(segment, "data", b""))
        offset = self._offset_by_type.get(log_type, 0)
        self._offset_by_type[log_type] = offset + len(raw)

        segment_info = {
            "log_type": log_type,
            "seq": int(getattr(pkt.hdr, "seq", 0)),
            "dst_addr": int(getattr(pkt.hdr.addr, "src", 0)),
            "offset": offset,
            "length": len(raw),
            "entries": self.parse_log_payload(raw, log_type=log_type),
        }
        segment_info["entry_count"] = len(segment_info["entries"])
        self.log_segment_received.emit(segment_info)
        return True

    def parse_log_payload(self, raw: bytes, log_type: int = LOG_TYPE_DEVICE_LOG) -> list[dict]:
        if not raw:
            return []

        entries = self._parse_binary_records(raw, log_type)
        if entries:
            return entries
        return self._parse_text_records(raw, log_type)

    def _parse_binary_records(self, raw: bytes, log_type: int) -> list[dict]:
        entries: list[dict] = []
        cursor = 0

        while cursor + LOG_LEN_FIELD + LOG_HEADER_LEN <= len(raw):
            record_len = int.from_bytes(raw[cursor:cursor + LOG_LEN_FIELD], "little")
            if record_len < LOG_HEADER_LEN or record_len > 512:
                return []

            record_start = cursor + LOG_LEN_FIELD
            record_end = record_start + record_len
            padded_end = cursor + ((LOG_LEN_FIELD + record_len + 3) & ~3)
            if record_end > len(raw) or padded_end > len(raw):
                return []

            record = raw[record_start:record_end]
            entry = self._parse_record(record, log_type)
            if entry:
                entries.append(entry)
            cursor = padded_end

        return entries if cursor == len(raw) else []

    def _parse_record(self, record: bytes, log_type: int) -> dict | None:
        if len(record) < LOG_HEADER_LEN:
            return None

        level_code = record[0]
        obj_code_raw = record[1]
        timestamp_ms = int.from_bytes(record[2:8], "little")
        msg_len = record[8]
        msg_end = LOG_HEADER_LEN + msg_len
        if msg_end > len(record):
            return None

        message = record[LOG_HEADER_LEN:msg_end].decode("utf-8", errors="replace")
        obj_code = obj_code_raw & 0x7F
        device_side = "TAG" if (obj_code_raw & 0x80) else "ANCHOR"
        source = OBJECT_LABELS.get(obj_code, f"OBJ 0x{obj_code:02X}")
        if source not in {"TAG", "ANCHOR"}:
            source = f"{device_side}/{source}"

        return {
            "timestamp": self._format_timestamp(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "level": self._level_name(level_code),
            "level_code": level_code,
            "source": source,
            "object_code": obj_code,
            "object_code_hex": f"0x{obj_code:02X}",
            "log_type": log_type,
            "message": message,
        }

    def _format_timestamp(self, timestamp_ms: int) -> str:
        if timestamp_ms >= EPOCH_MS_MIN_FOR_DATETIME:
            try:
                return datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except (OverflowError, OSError, ValueError):
                pass
        return str(timestamp_ms)

    def _parse_text_records(self, raw: bytes, log_type: int) -> list[dict]:
        text = raw.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines and text.strip():
            lines = [text.strip()]

        now = datetime.now().strftime("%H:%M:%S")
        return [
            {
                "timestamp": now,
                "timestamp_ms": 0,
                "level": "INFO",
                "level_code": 0xFE,
                "source": "DEVICE",
                "object_code": 0,
                "object_code_hex": "0x00",
                "log_type": log_type,
                "message": line,
            }
            for line in lines
        ]

    def _level_name(self, level_code: int) -> str:
        if level_code == 0xFE:
            return "INFO"
        if level_code == 0xFF:
            return "DEBUG"
        if level_code == 0xFD:
            return "WARN"
        return f"ERROR 0x{level_code:02X}"
