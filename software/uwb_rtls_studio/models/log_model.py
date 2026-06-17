"""
Log domain model.

The repository parses incoming log payloads. This model owns the application
state and business decisions around logs: buffering, clearing, app-generated
entries, and acknowledging received firmware log segments.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from common.transport import VvAddress

log = logging.getLogger(__name__)

LOG_PACKET_TRACE_TO_LIVE_LOG = True
PACKET_TRACE_TAG_WIDTH = 7
PACKET_TRACE_NAME_WIDTH = 18
PACKET_TRACE_COUNT_WIDTH = 5
PACKET_TRACE_SEQ_WIDTH = 5
PACKET_TRACE_ADDR_WIDTH = 13


class LogModel(QObject):
    LOG_POLL_TIMEOUT_S = 3.0
    # Total send attempts for one log ACK, including the first send.
    LOG_LOG_ACK_MAX_SEND_ATTEMPTS = 1
    # Total send attempts for the initial log poll request, including first send.
    LOG_POLL_MAX_SEND_ATTEMPTS = 3
    LOG_ACK_RETRY_PERIOD_S = 1.0

    log_entry_added = pyqtSignal(dict)
    log_segment_received = pyqtSignal(dict)

    def __init__(self, log_repository=None, command_bus=None, parent=None):
        super().__init__(parent)
        self._log_repository = log_repository
        self._command_bus = command_bus
        self._live_logs: list[dict] = []
        self._session_logs: list[dict] = []
        self._log_stream_requested = False
        self._log_first_segment_seen = False
        self._log_stream_started_at = 0.0
        self._log_first_segment_deadline = 0.0
        self._last_log_poll_at = 0.0
        self._log_poll_retry_count = 0
        self._pending_log_ack_seq = None
        self._pending_log_ack_dst = None
        self._pending_log_ack_sent_at = 0.0
        self._pending_log_ack_confirm_seq = None
        self._pending_log_ack_retries = 0
        self._deferred_ack_trace_by_ack_seq: dict[int, str] = {}
        self._latest_mcu_log_seq = None
        self._tx_counts: dict[str, int] = {}
        self._rx_counts: dict[str, int] = {}

        if self._log_repository:
            self._log_repository.log_entry_added.connect(self._on_repository_log_entry)
            self._log_repository.log_segment_received.connect(self._on_log_segment_received)
        protocol = getattr(self._command_bus, "_protocol", None) if self._command_bus else None
        if protocol is not None and hasattr(protocol, "ack_received"):
            protocol.ack_received.connect(self._on_ack_received)

    @property
    def live_logs(self) -> list[dict]:
        return [entry.copy() for entry in self._live_logs]

    @property
    def session_logs(self) -> list[dict]:
        return [entry.copy() for entry in self._session_logs]

    def clear_session_logs(self) -> None:
        self._session_logs.clear()

    def clear_live_logs(self) -> None:
        self._live_logs.clear()
        self._log_stream_requested = False
        self._log_first_segment_seen = False
        self._log_stream_started_at = 0.0
        self._log_first_segment_deadline = 0.0
        self._last_log_poll_at = 0.0
        self._log_poll_retry_count = 0
        self._pending_log_ack_seq = None
        self._pending_log_ack_dst = None
        self._pending_log_ack_sent_at = 0.0
        self._pending_log_ack_confirm_seq = None
        self._pending_log_ack_retries = 0
        self._deferred_ack_trace_by_ack_seq.clear()
        self._latest_mcu_log_seq = None
        self._tx_counts.clear()
        self._rx_counts.clear()

    def add_live_log(self, timestamp: str, level: str, source: str, message: str) -> dict:
        entry = {
            "timestamp": timestamp or datetime.now().strftime("%H:%M:%S"),
            "level": level or "INFO",
            "source": source or "APP",
            "message": message or "",
        }
        self._append_entry(entry)
        return entry

    def send_host_log_packet(self, packet_name: str, **params) -> dict:
        """Send a developer-selected host packet to the MCU from the Log tab."""
        if not self._command_bus:
            return {"ok": False, "error": "Command bus is not available"}

        protocol = getattr(self._command_bus, "_protocol", None)
        commands = getattr(protocol, "_commands", None) if protocol is not None else None
        if protocol is None or commands is None:
            return {"ok": False, "error": "Protocol service is not available"}

        try:
            dst_addr = int(params.get("dst_addr", VvAddress.MCU))
            seq = protocol.next_seq()

            if packet_name == "none":
                pkt = commands.none(VvAddress.HOST, dst_addr, seq)
            elif packet_name == "ack":
                pkt = commands.ack(VvAddress.HOST, dst_addr, seq)
                pkt.ack.ack_seq = int(params.get("ack_seq", 0))
                pkt.ack.response = int(params.get("response", 1))
            elif packet_name == "time_sync_set":
                pkt = commands.time_sync_set(
                    VvAddress.HOST,
                    dst_addr,
                    seq,
                    unix_time_ms=params.get("unix_time_ms"),
                    timezone_offset=int(params.get("timezone_offset", 420)),
                )
            elif packet_name == "host_transport_set":
                pkt = commands.host_transport_set(VvAddress.HOST, dst_addr, seq)
                pkt.host_transport_set.transport = int(params.get("transport", 1))
            elif packet_name == "log_data":
                pkt = commands.log_data(
                    VvAddress.HOST,
                    dst_addr,
                    seq,
                    log_type=int(params.get("log_type", 1)),
                    data=bytes(params.get("data", b"")),
                )
            elif packet_name == "log_clear":
                pkt = commands.log_clear(
                    VvAddress.HOST,
                    dst_addr,
                    seq,
                    log_type=int(params.get("log_type", 1)),
                    offset=int(params.get("offset", 0)),
                    length=int(params.get("length", 0)),
                )
            else:
                return {"ok": False, "error": f"Unsupported packet: {packet_name}"}

            self._send_packet(protocol, pkt)
            return {"ok": True, "seq": int(pkt.hdr.seq), "packet": packet_name}
        except Exception as exc:
            log.warning("LogModel: Failed to send developer host packet %s: %s", packet_name, exc)
            return {"ok": False, "error": str(exc)}

    def acknowledge_log_segment(self, segment_info: dict) -> bool:
        if not self._command_bus:
            return False

        try:
            protocol = getattr(self._command_bus, "_protocol", None)
            commands = getattr(protocol, "_commands", None) if protocol is not None else None
            if protocol is None or commands is None:
                return False

            ack_seq = int(segment_info.get("seq", 0))
            ack_dst = int(segment_info.get("dst_addr", VvAddress.MCU))
            ack_pkt = commands.ack(VvAddress.HOST, ack_dst, protocol.next_seq())
            ack_pkt.ack.ack_seq = ack_seq
            self._send_packet(protocol, ack_pkt, live_log_ack_after_seq=ack_seq)
            self._pending_log_ack_seq = ack_seq
            self._pending_log_ack_dst = ack_dst
            self._pending_log_ack_sent_at = time.monotonic()
            self._pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)
            self._pending_log_ack_retries = 0
            return True
        except Exception as exc:
            log.warning("Failed to send log ack for segment %s: %s", segment_info, exc)
            return False

    def _on_repository_log_entry(self, entry: dict) -> None:
        self._append_entry(entry)

    def _on_log_segment_received(self, segment_info: dict) -> None:
        self._log_first_segment_seen = True
        self._latest_mcu_log_seq = int(segment_info.get("seq", 0))
        self._print_rx_log_segment(segment_info)
        self.acknowledge_log_segment(segment_info)
        for entry in segment_info.get("entries", []):
            self._append_entry(entry)
        self.log_segment_received.emit(segment_info)
        self._flush_deferred_ack_trace(int(segment_info.get("seq", 0)))

    def _append_entry(self, entry: dict) -> None:
        safe_entry = dict(entry or {})
        self._live_logs.append(safe_entry)
        self._session_logs.append(safe_entry.copy())
        self.log_entry_added.emit(safe_entry.copy())

    def _send_log_poll(self) -> bool:
        if not self._command_bus:
            return False

        try:
            poll_pkt = self._command_bus.send("log_data", dst_addr=VvAddress.MCU)
            if poll_pkt is None:
                return False
            self._print_tx_packet(poll_pkt, "log_data")

            self._last_log_poll_at = time.monotonic()
            return True
        except Exception as exc:
            log.warning("LogModel: Failed to send log poll/ack: %s", exc)
            return False

    def request_log_stream(self, force: bool = False) -> bool:
        """Trigger firmware/device log streaming for the current connected device."""
        if self._log_first_segment_seen:
            return False
        if self._log_stream_requested and not force:
            return False
        self._log_stream_requested = True
        self._log_first_segment_seen = False
        self._log_stream_started_at = time.monotonic()
        self._log_first_segment_deadline = self._log_stream_started_at + self.LOG_POLL_TIMEOUT_S
        self._log_poll_retry_count = 0
        log.info("LogModel: Requesting log stream via command_bus...")
        return self._send_log_poll()

    def poll_log_timeout(self) -> bool:
        """Retry log start poll until the first segment arrives, then retry pending ACKs."""
        if not self._log_stream_requested:
            return False

        now = time.monotonic()
        if self._retry_pending_log_ack(now):
            return True

        if not self._log_first_segment_seen:
            if now < self._log_first_segment_deadline:
                return False
            if self._log_poll_retry_count >= max(0, self.LOG_POLL_MAX_SEND_ATTEMPTS - 1):
                return False
            self._log_poll_retry_count += 1
            self._log_first_segment_deadline = now + self.LOG_POLL_TIMEOUT_S
            return self._send_log_poll()

        return False

    def _track_log_ack(self, ack_pkt) -> None:
        self._pending_log_ack_seq = int(ack_pkt.ack.ack_seq)
        self._pending_log_ack_dst = int(ack_pkt.hdr.addr.dst)
        self._pending_log_ack_sent_at = time.monotonic()
        self._pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)
        self._pending_log_ack_retries = 0

    def _retry_pending_log_ack(self, now: float) -> bool:
        if self._pending_log_ack_seq is None or self._pending_log_ack_dst is None:
            return False

        if self._latest_mcu_log_seq is not None and self._pending_log_ack_seq != self._latest_mcu_log_seq:
            self._clear_pending_log_ack()
            return False

        if self._pending_log_ack_retries >= max(0, self.LOG_LOG_ACK_MAX_SEND_ATTEMPTS - 1):
            self._clear_pending_log_ack()
            return False

        if now - self._pending_log_ack_sent_at < self.LOG_ACK_RETRY_PERIOD_S:
            return False

        try:
            protocol = getattr(self._command_bus, "_protocol", None)
            commands = getattr(protocol, "_commands", None) if protocol is not None else None
            if protocol is None or commands is None:
                return False

            ack_pkt = commands.ack(VvAddress.HOST, self._pending_log_ack_dst, protocol.next_seq())
            ack_pkt.ack.ack_seq = self._pending_log_ack_seq
            self._send_packet(protocol, ack_pkt)
            self._pending_log_ack_sent_at = now
            self._pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)
            self._pending_log_ack_retries += 1
            return True
        except Exception as exc:
            log.warning("LogModel: Failed to retry log ack: %s", exc)
            return False

    def _clear_pending_log_ack(self) -> None:
        self._pending_log_ack_seq = None
        self._pending_log_ack_dst = None
        self._pending_log_ack_sent_at = 0.0
        self._pending_log_ack_confirm_seq = None
        self._pending_log_ack_retries = 0

    def _on_ack_received(self, ack_seq: int, response: int) -> None:
        if self._pending_log_ack_confirm_seq is not None and int(ack_seq) == self._pending_log_ack_confirm_seq:
            line = f"[FLOW]  host_ack confirmed by MCU seq={self._pending_log_ack_confirm_seq}"
            print(line, flush=True)
            self._append_packet_trace_entry(line)
            self._clear_pending_log_ack()

    def _send_packet(self, protocol, pkt, live_log_ack_after_seq: int | None = None) -> None:
        protocol.send_packet(pkt)
        self._print_tx_packet(
            pkt,
            pkt.WhichOneof("params") or "<none>",
            live_log_ack_after_seq=live_log_ack_after_seq,
        )

    @staticmethod
    def _addr_text(value: int) -> str:
        try:
            addr = VvAddress(int(value))
            return f"{addr.name}({int(addr)})"
        except (ValueError, TypeError):
            return f"UNKNOWN({value})"

    def _packet_display_name(self, pkt, name: str) -> str:
        if name != "ack":
            return name
        try:
            src = VvAddress(int(pkt.hdr.addr.src))
            return f"{src.name.lower()}_ack"
        except (AttributeError, TypeError, ValueError):
            return name

    def _packet_extra_text(self, pkt, name: str) -> str:
        if name == "ack":
            return f"ack_seq={pkt.ack.ack_seq} response={pkt.ack.response}"
        if name == "log_data":
            return f"type={pkt.log_data.type} bytes={len(pkt.log_data.data)}"
        return ""

    def _format_packet_trace(self, tag: str, name: str, count: int, seq: int, src: int, dst: int, extra: str = "") -> str:
        suffix = f" {extra}" if extra else ""
        return (
            f"{tag:<{PACKET_TRACE_TAG_WIDTH}} "
            f"{name:<{PACKET_TRACE_NAME_WIDTH}} "
            f"#{count:<{PACKET_TRACE_COUNT_WIDTH}} "
            f"seq={seq:<{PACKET_TRACE_SEQ_WIDTH}} "
            f"src={self._addr_text(src):<{PACKET_TRACE_ADDR_WIDTH}} "
            f"dst={self._addr_text(dst):<{PACKET_TRACE_ADDR_WIDTH}}"
            f"{suffix}"
        )

    def _print_tx_packet(self, pkt, name: str, live_log_ack_after_seq: int | None = None) -> None:
        self._tx_counts[name] = self._tx_counts.get(name, 0) + 1
        count = self._tx_counts[name]
        prefix = "[POLL]" if name == "log_data" else "[ACK]" if name == "ack" else "[TX]"
        display_name = self._packet_display_name(pkt, name)
        extra = self._packet_extra_text(pkt, name)
        line = self._format_packet_trace(
            prefix,
            display_name,
            count,
            int(pkt.hdr.seq),
            int(pkt.hdr.addr.src),
            int(pkt.hdr.addr.dst),
            extra,
        )
        print(line, flush=True)
        if live_log_ack_after_seq is not None:
            self._deferred_ack_trace_by_ack_seq[int(live_log_ack_after_seq)] = line
        else:
            self._append_packet_trace_entry(line)

    def _print_rx_log_segment(self, segment_info: dict) -> None:
        name = "log_data"
        self._rx_counts[name] = self._rx_counts.get(name, 0) + 1
        line = self._format_packet_trace(
            "[RX]",
            name,
            self._rx_counts[name],
            int(segment_info.get("seq", 0)),
            int(segment_info.get("dst_addr", VvAddress.MCU)),
            int(VvAddress.HOST),
            f"type={segment_info.get('log_type', 0)} bytes={segment_info.get('length', 0)}",
        )
        print(line, flush=True)
        self._append_packet_trace_entry(line)

    def _append_packet_trace_entry(self, line: str) -> None:
        if not LOG_PACKET_TRACE_TO_LIVE_LOG:
            return
        self._append_entry({
            "raw_line": line,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": "DEBUG",
            "source": "PROTOCOL",
            "message": line,
        })

    def _flush_deferred_ack_trace(self, ack_seq: int) -> None:
        line = self._deferred_ack_trace_by_ack_seq.pop(int(ack_seq), None)
        if line:
            self._append_packet_trace_entry(line)
