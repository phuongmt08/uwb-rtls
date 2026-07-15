"""
===============================================================================
  UWB RTLS Studio — Protocol Service
===============================================================================
  File        : services/protocol_service.py
  Description : Encapsulates Protobuf packet serialization/deserialization wrapped in HDLC.
                Acts as the central dispatcher routing parsed packets to subscriber models.

  MVVM Role   : SERVICE — Protocol serialization and routing.
  Giải thích flow:
        RX (nhận):
        SerialService.data_received → on_serial_data()
            → HdlcCodec.feed() → decode protobuf → emit packet_received
        TX (gửi):
        send_command(packet_t) → HDLC wrap → SerialService.write()
  Thread Model:
    ┌────────────────────────────────────────────────────────────────────────┐
    │ 🧵 Thread Contexts:                                                     │
    │ 1. Main GUI Thread:                                                     │
    │    - `send_command()` and `send_packet()` run on this thread.          │
    │    - `on_serial_data()` is executed here: raw bytes emitted from the    │
    │      background thread are marshalled to the Main GUI Thread via Qt's  │
    │      thread-safe Event Loop (Queued Connection).                        │
    │    - Decoded packet signals are emitted on the Main GUI Thread.         │
    └────────────────────────────────────────────────────────────────────────┘

  Signals:
    - packet_received(str, object)  : (param_name, packet_t) dispatched to Models.
    - ack_received(int, int, int)   : (ack_seq, response_code, src_addr) for transactions.
    - decode_error(str)             : Decapsulation or parsing failure alert.
===============================================================================
"""
from __future__ import annotations

import sys
import os
import logging
import queue
import threading
from time import time

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from google.protobuf.message import DecodeError

# Add common to path
_common_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "common")
)
if _common_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(_common_dir))

from common.parser_protocol import VvProtocol
from common.transport import HdlcCodec, FRAME_TYPE_PROTOBUF, VvAddress
from common.commands import CommandFactory, default_destination_for
from common.lenient_decode import try_lenient_packet_decode
from common import protocol_pb2 as pb
from data.raw_packet import RawSerialChunk
from data.raw_packet_store import shared_raw_packet_store

log = logging.getLogger(__name__)


class ProtocolService(QObject):
    """Encode/decode protobuf packets, dispatch tới subscribers."""

    # ── Signals ──────────────────────────────────────────────────────
    packet_received = pyqtSignal(str, object)   # (param_name, packet_t)
    packet_sent = pyqtSignal(str, object)       # (param_name, packet_t)
    ack_received = pyqtSignal(int, int, int)    # (ack_seq, response_code, src_addr)
    decode_error = pyqtSignal(str)              # error message
    _decoded_packets_ready = pyqtSignal(list)
    _decode_error_ready = pyqtSignal(str)

    def __init__(self, serial_service, parent=None):
        super().__init__(parent)
        self._serial = serial_service
        self._protocol = VvProtocol()   # parser_protocol.VvProtocol — encode/decode/HDLC + build_*() methods
        self._protocol.hdlc.emit_bad_checksum_chunks = True
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._packet_repository = None
        self._last_log_data_seq: int | None = None
        self._packet_event_times: dict[tuple[str, int], float] = {}
        self._rx_queue: queue.Queue[bytes | None] = queue.Queue()
        self._decoder_lock = threading.Lock()
        self._rx_generation = 0
        self._rx_stop = threading.Event()
        self._rx_thread = threading.Thread(
            target=self._rx_worker_loop,
            name="ProtocolRxWorker",
            daemon=True,
        )
        self._decoded_packets_ready.connect(
            self._dispatch_decoded_packets,
            Qt.ConnectionType.QueuedConnection,
        )
        self._decode_error_ready.connect(
            self._emit_decode_error,
            Qt.ConnectionType.QueuedConnection,
        )

        # Connect serial RX → decode
        self._serial.data_received.connect(self.on_serial_data)
        self._rx_thread.start()
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.register("ProtocolRxWorker", self._rx_thread)
        except Exception as exc:
            log.debug("Could not register ProtocolRxWorker thread: %s", exc)

    # ── Properties ───────────────────────────────────────────────────

    @property
    def pb(self):
        """Expose protobuf module cho external use."""
        return pb

    @property
    def commands(self) -> CommandFactory:
        """Expose CommandFactory cho external use (nằm bên trong VvProtocol)."""
        return self._protocol._commands

    def set_packet_repository(self, repository) -> None:
        """Attach decoded-packet repository for raw/debug and shared parsers."""
        self._packet_repository = repository

    # ── RX Path ──────────────────────────────────────────────────────

    def on_serial_data(self, data: bytes) -> None:
        """Queue raw serial bytes for the protocol worker."""
        if not data:
            return

        with self._decoder_lock:
            generation = self._rx_generation
        self._rx_queue.put((generation, data))

    def reset_decoder(self, reason: str = "") -> None:
        """Reset HDLC state and discard bytes belonging to the old link."""
        with self._decoder_lock:
            self._rx_generation += 1
            reset = getattr(self._protocol.hdlc, "reset", None)
            if callable(reset):
                reset()
            else:
                self._protocol.hdlc._reset()
            while True:
                try:
                    self._rx_queue.get_nowait()
                except queue.Empty:
                    break
        flush = getattr(self._serial, "reset_input_buffer", None)
        if callable(flush):
            flush()
        log.info("[PROTOCOL] HDLC/RX reset%s", f" reason={reason}" if reason else "")

    def _rx_worker_loop(self) -> None:
        """Decode HDLC/protobuf frames away from the GUI thread."""
        while not self._rx_stop.is_set():
            try:
                data = self._rx_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if data is None:
                break

            generation = None
            if isinstance(data, tuple) and len(data) == 2:
                generation, data = data
            with self._decoder_lock:
                if generation is not None and generation != self._rx_generation:
                    log.debug("[PROTOCOL] discarded stale RX chunk generation=%s current=%s", generation, self._rx_generation)
                    continue
            shared_raw_packet_store.append_serial_chunk(RawSerialChunk.from_bytes(data))
            packets = self._decode_packets(data)
            if packets:
                self._decoded_packets_ready.emit(packets)

    def _decode_packets(self, data: bytes) -> list:
        packets = []
        try:
            hdlc_error = None
            with self._decoder_lock:
                codec = self._protocol.hdlc
                before_errors = int(getattr(codec, "error_count", 0) or 0)
                chunks = codec.feed(data)
                after_errors = int(getattr(codec, "error_count", 0) or 0)
                if after_errors > before_errors:
                    hdlc_error = dict(getattr(codec, "last_error", {}) or {})
                    hdlc_error["count_delta"] = after_errors - before_errors
                    hdlc_error["total_errors"] = after_errors

            if hdlc_error is not None:
                msg = f"HDLC decode error: {hdlc_error}"
                log.warning(msg)
                try:
                    shared_raw_packet_store.append_decode_error("hdlc", msg, hdlc_error)
                except Exception:
                    pass
                self._decode_error_ready.emit(msg)

            for chunk in chunks:
                if chunk.frame_type != FRAME_TYPE_PROTOBUF:
                    log.debug(
                        "Ignoring non-protobuf HDLC frame: type=%s payload_len=%s",
                        chunk.frame_type,
                        len(chunk.payload),
                    )
                    continue

                try:
                    pkt = self._protocol.decode_packet(chunk.payload)
                    if not getattr(chunk, "checksum_ok", True):
                        recovered = pkt.WhichOneof("params")
                        log.warning(
                            "Recovered protobuf packet despite HDLC checksum error: param=%s seq=%s error=%s",
                            recovered,
                            getattr(getattr(pkt, "hdr", None), "seq", "-"),
                            getattr(chunk, "error", None),
                        )
                    packets.append(pkt)
                except DecodeError as exc:
                    recovered_pkt = try_lenient_packet_decode(chunk.payload)
                    if recovered_pkt is not None:
                        recovered_param = recovered_pkt.WhichOneof("params")
                        log.warning(
                            "Recovered %s using lenient protobuf decode after strict parse failed: "
                            "seq=%s payload_len=%s hdlc_checksum_ok=%s err=%s",
                            recovered_param,
                            getattr(getattr(recovered_pkt, "hdr", None), "seq", "-"),
                            len(chunk.payload),
                            getattr(chunk, "checksum_ok", True),
                            exc,
                        )
                        packets.append(recovered_pkt)
                        continue

                    details = {"payload_len": len(chunk.payload), "payload_hex": chunk.payload.hex()}
                    msg = (
                        f"Protobuf decode error: payload_len={len(chunk.payload)} "
                        f"err={exc} payload_hex={chunk.payload[:32].hex()}"
                    )
                    log.warning(msg)
                    try:
                        shared_raw_packet_store.append_decode_error("protobuf", msg, details)
                    except Exception:
                        pass
                    self._decode_error_ready.emit(msg)
        except Exception as exc:
            msg = f"HDLC decode error: {exc}"
            log.warning(msg)
            try:
                shared_raw_packet_store.append_decode_error("hdlc_exception", msg, {})
            except Exception:
                pass
            self._decode_error_ready.emit(msg)
        return packets

    def _emit_decode_error(self, message: str) -> None:
        self.decode_error.emit(message)

    def _dispatch_decoded_packets(self, packets: list) -> None:
        """Dispatch decoded packets on the Qt/main thread."""
        for pkt in packets:
            param = pkt.WhichOneof("params")
            if param is None:
                continue

            self._remember_packet_event_time("rx", pkt, time())

            if param == "log_data":
                self._warn_on_log_seq_gap(int(pkt.hdr.seq))

            if param == "ack":
                ack_src = int(pkt.hdr.addr.src)
                try:
                    from utils.app_state import shared_app_state
                    shared_app_state.handle_incoming_ack(pkt.ack.ack_seq, pkt.ack.response, ack_src)
                except Exception as exc:
                    log.error("Failed to forward ACK to shared_app_state: %s", exc)
                self.ack_received.emit(pkt.ack.ack_seq, pkt.ack.response, ack_src)
                self.packet_received.emit(param, pkt)
                log.debug("RX: %s seq=%d ack_seq=%d", param, pkt.hdr.seq, pkt.ack.ack_seq)
                continue
            if self._packet_repository:
                try:
                    self._packet_repository.handle_packet(param, pkt)
                except Exception as exc:
                    log.error("Failed to forward packet to packet repository: %s", exc)

            try:
                from utils.app_state import shared_app_state
                shared_app_state.handle_incoming_packet(param, pkt)
            except Exception as exc:
                log.error("Failed to forward packet to shared_app_state: %s", exc)

            self.packet_received.emit(param, pkt)
            log.debug("RX: %s seq=%d", param, pkt.hdr.seq)

    def _warn_on_log_seq_gap(self, seq: int) -> None:
        if self._last_log_data_seq is not None:
            expected = (self._last_log_data_seq + 1) & 0xFFFFFFFF
            if seq != expected:
                msg = f"[MISS] log_data seq jump: expected={expected} got={seq}"
                print(msg, flush=True)
                log.warning(msg)
        self._last_log_data_seq = seq

    # ── TX Path ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Stop the protocol RX worker during application shutdown."""
        self._rx_stop.set()
        self._rx_queue.put(None)
        if self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.unregister("ProtocolRxWorker")
        except Exception as exc:
            log.debug("Could not unregister ProtocolRxWorker thread: %s", exc)

    def next_seq(self) -> int:
        """Thread-safe sequence number generator."""
        with self._seq_lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            return self._seq

    def send_packet(self, pkt: pb.packet_t) -> None:
        """Encode packet → HDLC frame → serial write."""
        frame = self._protocol.wrap_packet(pkt)
        self._remember_packet_event_time("tx", pkt, time())
        self._serial.write(frame)
        param = pkt.WhichOneof("params") or "unknown"
        log.debug("TX: %s seq=%d", param, pkt.hdr.seq)
        self.packet_sent.emit(param, pkt)

    def packet_event_time(self, direction: str, pkt) -> float | None:
        """Return host-side event timestamp captured at the service boundary."""
        return self._packet_event_times.get((direction, id(pkt)))

    def _remember_packet_event_time(self, direction: str, pkt, event_time: float) -> None:
        self._packet_event_times[(direction, id(pkt))] = float(event_time)

    def send_command(
        self,
        builder_name: str,
        dst_addr: int | None = None,
        src_addr: int = VvAddress.HOST,
        command_params: dict | None = None,
    ) -> pb.packet_t:
        """Build and send a command by name."""
        seq = self.next_seq()
        target_addr = default_destination_for(builder_name) if dst_addr is None else dst_addr
        builder = getattr(self._protocol, f"build_{builder_name}")
        params = dict(command_params or {})
        pkt = builder(src_addr, target_addr, seq, **params)
        self.send_packet(pkt)
        return pkt
