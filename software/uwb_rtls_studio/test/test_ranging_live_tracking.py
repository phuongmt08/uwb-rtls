from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path



CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import VvAddress, VvProtocol
from data.raw_packet import RawPacket


DEFAULT_MAP_PATH = Path(STUDIO_DIR) / "data" / "runtime" / "live_tracking_demo_map.json"
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 9999
DEFAULT_SERIAL_BAUD = 115200
DEFAULT_ANCHORS = [
    {"anchor_id": 0, "x_m": 0.3, "y_m": 4.3, "z_m": 1.5},
    {"anchor_id": 1, "x_m": 0.6, "y_m": 9.9, "z_m": 1.5},
    {"anchor_id": 2, "x_m": 5.9, "y_m": 9.9, "z_m": 1.5},
    {"anchor_id": 3, "x_m": 6.1, "y_m": 4.3, "z_m": 1.5},
    {"anchor_id": 4, "x_m": 6.5, "y_m": 12.0, "z_m": 1.5},
    {"anchor_id": 5, "x_m": 7.7, "y_m": 6.6, "z_m": 1.5},
    {"anchor_id": 6, "x_m": 6.3, "y_m": 4.8, "z_m": 1.5},
]

def _ensure_qt_app():
    from PyQt6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


def test_sensor_fusion_result_reaches_ranging_model_with_velocity():
    app = _ensure_qt_app()
    from models.ranging_model import RangingModel
    from repository.ranging_repository import RangingRepository

    from models.ranging_model import RangingModel
    from repository.ranging_repository import RangingRepository

    repo = RangingRepository()
    model = RangingModel(protocol_service=None, ranging_repo=repo)
    received = []
    model.sensor_fusion_updated.connect(received.append)

    factory = CommandFactory()

    pkt1 = factory.sensor_fusion_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 1)
    pkt1.sensor_fusion_result.ukf_x_m = 1.0
    pkt1.sensor_fusion_result.ukf_y_m = 2.0
    pkt1.sensor_fusion_result.ukf_yaw_deg = 10.0
    pkt1.sensor_fusion_result.tril_x_m = 0.9
    pkt1.sensor_fusion_result.tril_y_m = 2.1
    pkt1.sensor_fusion_result.yaw_deg = 9.0
    pkt1.sensor_fusion_result.ranging_error_count = 3
    pkt1.sensor_fusion_result.timestamp_ms = 1000

    pkt2 = factory.sensor_fusion_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 2)
    pkt2.sensor_fusion_result.ukf_x_m = 2.5
    pkt2.sensor_fusion_result.ukf_y_m = 4.0
    pkt2.sensor_fusion_result.ukf_yaw_deg = 12.0
    pkt2.sensor_fusion_result.tril_x_m = 2.4
    pkt2.sensor_fusion_result.tril_y_m = 4.1
    pkt2.sensor_fusion_result.yaw_deg = 11.0
    pkt2.sensor_fusion_result.ranging_error_count = 4
    pkt2.sensor_fusion_result.timestamp_ms = 2000

    assert repo.handle_packet("sensor_fusion_result", pkt1) is True
    assert repo.handle_packet("sensor_fusion_result", pkt2) is True

    assert len(received) == 2
    latest = received[-1]
    assert math.isclose(latest["ukf_x_m"], 2.5)
    assert math.isclose(latest["ukf_y_m"], 4.0)
    assert math.isclose(latest["tril_x_m"], 2.4, rel_tol=1e-6)
    assert math.isclose(latest["tril_y_m"], 4.1, rel_tol=1e-6)
    assert latest["ranging_error_count"] == 4
    assert math.isclose(latest["vx_mps"], 1.5)
    assert math.isclose(latest["vy_mps"], 2.0)
    assert latest["seq"] == 2
    assert len(model.fusion_history) == 2
    assert model.fusion_history[-1]["source"] == "sensor_fusion"
    assert app is not None


def test_ranging_result_keeps_seq_and_anchor_distances_for_session_export():
    app = _ensure_qt_app()
    repo = RangingRepository()
    model = RangingModel(protocol_service=None, ranging_repo=repo)

    factory = CommandFactory()
    pkt = factory.ranging_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 42)
    pkt.ranging_result.pos_x_m = 1.2
    pkt.ranging_result.pos_y_m = 3.4
    pkt.ranging_result.pos_z_m = 0.5
    pkt.ranging_result.rms_error_m = 0.03
    pkt.ranging_result.ClearField("anchors")

    for anchor_id, distance_mm in [(1, 1100), (2, 2200), (3, 3300), (4, 4400)]:
        anchor = pkt.ranging_result.anchors.add()
        anchor.anchor_id = anchor_id
        anchor.distance_mm = distance_mm
        anchor.fp_amp = 500 + anchor_id

    assert repo.handle_packet("ranging_result", pkt) is True

    assert len(model.position_history) == 1
    sample = model.position_history[0]
    assert sample["source"] == "ranging"
    assert sample["seq"] == 42
    assert sample["d1_mm"] == 1100
    assert sample["d2_mm"] == 2200
    assert sample["d3_mm"] == 3300
    assert sample["d4_mm"] == 4400
    assert sample["anchor_mask"] == 0b11110
    assert math.isclose(sample["x_m"], 1.2, rel_tol=1e-6)
    assert app is not None


def test_raw_packet_store_tracks_sequence_gaps_without_dropping_packets():
    from data.raw_packet_store import RawPacketStore

    store = RawPacketStore(max_packets=10)
    store.clear()

    packet_1 = RawPacket(
        param_name="ranging_result",
        payload=b"one",
        src_addr=pb.PACKET_ADDR_MCU,
        dst_addr=pb.PACKET_ADDR_HOST,
        seq=1,
        received_at=1.0,
    )
    packet_3 = RawPacket(
        param_name="ranging_result",
        payload=b"three",
        src_addr=pb.PACKET_ADDR_MCU,
        dst_addr=pb.PACKET_ADDR_HOST,
        seq=3,
        received_at=2.0,
    )

    store.append(packet_1)
    store.append(packet_3)

    stats = store.stats()
    assert len(store.recent_packets()) == 2
    assert stats["packet_gap_count"] == 1
    assert stats["last_packet_gap"]["previous_seq"] == 1
    assert stats["last_packet_gap"]["current_seq"] == 3


def test_ranging_status_response_parses_success_rate():
    from repository.ranging_repository import RangingRepository

    repo = RangingRepository()
    factory = CommandFactory()
    pkt = factory.ranging_status_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 1)
    pkt.ranging_status_resp.ranging_period_ms = 200
    pkt.ranging_status_resp.ranging_total_count = 100
    pkt.ranging_status_resp.ranging_success_count = 95
    pkt.ranging_status_resp.ranging_failed_count = 3
    pkt.ranging_status_resp.ranging_timeout_count = 2
    pkt.ranging_status_resp.last_ranging_time_ms = 11
    pkt.ranging_status_resp.last_rms_error_m = 0.05
    pkt.ranging_status_resp.last_avg_rssi_dbm = -65

    stats = repo.parse_ranging_status(pkt.ranging_status_resp)

    assert stats["ranging_period_ms"] == 200
    assert stats["total_count"] == 100
    assert stats["success_count"] == 95
    assert stats["failed_count"] == 3
    assert stats["timeout_count"] == 2
    assert math.isclose(stats["success_rate_percent"], 95.0)


class TcpSerialAdapter:
    def __init__(self, host: str, port: int):
        from utils.runtime_mode import is_test_mode
        import time
        self.is_open = False
        self.host = host
        self.port = port
        self._server = None
        self._conn = None

        if is_test_mode():
            # Test mode: connect as a client to GUI TCP Server
            self._conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"[Simulator TCP Client] Connecting to GUI App on {host}:{port}...")
            while True:
                try:
                    self._conn.connect((host, port))
                    self._conn.settimeout(0.05)
                    self.is_open = True
                    print(f"[Simulator TCP Client] Connected to GUI App")
                    break
                except ConnectionRefusedError:
                    print(f"⏳ Waiting for GUI App TCP server on {host}:{port}...")
                    time.sleep(1.0)
        else:
            # Real mode: bind as server
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind((host, port))
            self._server.listen(1)

    def accept(self):
        from utils.runtime_mode import is_test_mode
        if is_test_mode():
            return  # already connected as client
        print(f"[Simulator] Waiting for host connection on {self.host}:{self.port} ...")
        self._conn, addr = self._server.accept()
        self._conn.settimeout(0.05)
        self.is_open = True
        print(f"[Simulator] Host connected from {addr[0]}:{addr[1]}")

    def write(self, data: bytes) -> None:
        if self._conn and self.is_open:
            self._conn.sendall(data)

    def read(self, size: int = 4096) -> bytes:
        if not self._conn or not self.is_open:
            return b""
        try:
            chunk = self._conn.recv(size)
            if not chunk:
                self.is_open = False
                return b""
            return chunk
        except socket.timeout:
            return b""
        except OSError:
            self.is_open = False
            return b""

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        return None

    def close(self) -> None:
        self.is_open = False
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None


class ComSerialAdapter:
    def __init__(self, port_name: str, baudrate: int):
        import serial
        self._serial = serial.Serial(port=port_name, baudrate=baudrate, timeout=0.05)
        self.is_open = bool(self._serial and self._serial.is_open)
        self.port_name = port_name
        self.baudrate = baudrate

    def accept(self):
        print(f"[Simulator] Opened serial peripheral on {self.port_name} @ {self.baudrate} baud")

    def write(self, data: bytes) -> None:
        if self.is_open:
            self._serial.write(data)

    def read(self, size: int = 4096) -> bytes:
        if not self.is_open:
            return b""
        chunk = self._serial.read(size)
        if self._serial and not self._serial.is_open:
            self.is_open = False
        return chunk

    def reset_input_buffer(self) -> None:
        self._serial.reset_input_buffer()

    def reset_output_buffer(self) -> None:
        self._serial.reset_output_buffer()

    def close(self) -> None:
        self.is_open = False
        try:
            self._serial.close()
        except Exception:
            pass


class LiveTrackingPeripheralSimulator:
    def __init__(self, map_path: Path, host: str, port: int, hz: float = 10.0, serial_port: str = "", baudrate: int = DEFAULT_SERIAL_BAUD):
        self.map_path = Path(map_path)
        self.host = host
        self.port = port
        self.period_s = 1.0 / max(hz, 1.0)
        self.proto = VvProtocol()
        self.factory = CommandFactory()
        self.transport = ComSerialAdapter(serial_port, baudrate) if serial_port else TcpSerialAdapter(host, port)
        self.running = True
        self.ranging_active = False
        self.seq = 1
        self.angle = 0.0
        self._lock = threading.Lock()
        self._rx_buffer = bytearray()
        self._anchors = self._load_anchors(self.map_path)
        self._path_points = self._build_path_points()
        self._path_progress = 0.0
        self._ranging_total_count = 0
        self._ranging_success_count = 0
        self._ranging_failed_count = 0
        self._ranging_timeout_count = 0
        self._last_ranging_time_ms = 0
        self._last_rms_error_m = 0.0
        self._last_avg_rssi_dbm = -62

    @staticmethod
    def _load_anchors(map_path: Path) -> list[dict]:
        if not map_path.is_file():
            print(
                f"[Simulator] Map file not found: {map_path}\n"
                "[Simulator] Using built-in A0-A6 demo anchor layout."
            )
            return [dict(anchor) for anchor in DEFAULT_ANCHORS]

        payload = json.loads(map_path.read_text(encoding="utf-8-sig"))
        anchors = payload.get("map_objects", {}).get("anchors") or payload.get("anchors") or []
        normalized = []
        for idx, anchor in enumerate(anchors):
            normalized.append(
                {
                    "anchor_id": int(anchor.get("anchor_id", idx)),
                    "x_m": float(anchor.get("x_m", anchor.get("x", 0.0))),
                    "y_m": float(anchor.get("y_m", anchor.get("y", 0.0))),
                    "z_m": float(anchor.get("z_m", anchor.get("z", 1.5))),
                }
            )
        if not normalized:
            print(
                f"[Simulator] No anchors found in map file: {map_path}\n"
                "[Simulator] Using built-in A0-A6 demo anchor layout."
            )
            return [dict(anchor) for anchor in DEFAULT_ANCHORS]
        print(f"[Simulator] Loaded {len(normalized)} anchors from: {map_path}")
        return normalized

    def _build_path_points(self) -> list[tuple[float, float, float]]:
        return [
            (0.8, 5.0, 1.2),
            (1.5, 8.8, 1.2),
            (4.8, 9.3, 1.2),
            (5.8, 6.2, 1.2),
            (6.9, 2.0, 1.2),
            (7.1, 6.4, 1.2),
            (7.0, 11.6, 1.2),
            (6.7, 7.0, 1.2),
            (3.2, 7.4, 1.2),
            (0.8, 5.0, 1.2),
        ]

    def _send_packet(self, pkt) -> None:
        frame = self.proto.wrap_packet(pkt)
        with self._lock:
            if self.transport.is_open:
                self.transport.write(frame)

    def _make_ack(self, src: int, dst: int, seq: int):
        ack = self.factory.ack(src=src, dst=dst, seq=seq)
        ack.ack.ack_seq = seq
        ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
        return ack

    def _send_anchor_layout_resp(self, dst: int, seq: int) -> None:
        pkt = self.factory.anchor_layout_resp(int(VvAddress.MCU), dst, seq)
        del pkt.anchor_layout_resp.anchors[:]
        for anchor in self._anchors:
            item = pkt.anchor_layout_resp.anchors.add()
            item.anchor_id = anchor["anchor_id"]
            item.x_m = anchor["x_m"]
            item.y_m = anchor["y_m"]
            item.z_m = anchor["z_m"]
        self._send_packet(pkt)

    def _send_device_info_resp(self, dst: int, seq: int) -> None:
        pkt = self.factory.device_information_resp(int(VvAddress.MCU), dst, seq)
        pkt.device_information_resp.device_type = pb.DEVICE_TYPE_TAG
        pkt.device_information_resp.role = pb.DEVICE_ROLE_TAG
        pkt.device_information_resp.hw_version = 2
        pkt.device_information_resp.serial_number = 98765
        pkt.device_information_resp.fw_version.major = 1
        pkt.device_information_resp.fw_version.minor = 0
        pkt.device_information_resp.fw_version.patch = 0
        pkt.device_information_resp.uid = b"\x12\x34\x56\x78\x9A\xBC\xDE\xF0"
        self._send_packet(pkt)

    def _send_ble_status_resp(self, dst: int, seq: int) -> None:
        pkt = self.factory.ble_status_resp(int(VvAddress.CENTRAL), dst, seq)
        pkt.ble_status_resp.state = pb.BLE_STATE_CONNECTED
        pkt.ble_status_resp.rssi_dbm = -58
        pkt.ble_status_resp.disconnect_reason = 0
        self._send_packet(pkt)

    def _send_ranging_status_resp(self, dst: int, seq: int) -> None:
        pkt = self.factory.ranging_status_resp(int(VvAddress.MCU), dst, seq)
        pkt.ranging_status_resp.ranging_period_ms = int(self.period_s * 1000)
        pkt.ranging_status_resp.ranging_total_count = self._ranging_total_count
        pkt.ranging_status_resp.ranging_success_count = self._ranging_success_count
        pkt.ranging_status_resp.ranging_failed_count = self._ranging_failed_count
        pkt.ranging_status_resp.ranging_timeout_count = self._ranging_timeout_count
        pkt.ranging_status_resp.last_ranging_time_ms = self._last_ranging_time_ms
        pkt.ranging_status_resp.last_rms_error_m = self._last_rms_error_m
        pkt.ranging_status_resp.last_avg_rssi_dbm = self._last_avg_rssi_dbm
        self._send_packet(pkt)

    def _handle_host_packet(self, pkt) -> None:
        param_name = pkt.WhichOneof("params")
        if not param_name:
            return

        src = pkt.hdr.addr.src
        dst = pkt.hdr.addr.dst
        seq = pkt.hdr.seq
        print(f"[Simulator] RX {param_name} seq={seq} src={src} dst={dst}")

        if param_name == "device_information_get":
            self._send_device_info_resp(src, seq)
            return
        if param_name == "anchor_layout_get":
            self._send_anchor_layout_resp(src, seq)
            return
        if param_name == "ble_status_get":
            self._send_ble_status_resp(src, seq)
            return
        if param_name == "ranging_status_get":
            self._send_ranging_status_resp(src, seq)
            return
        if param_name == "ranging_start":
            self.ranging_active = True
            self._send_packet(self._make_ack(dst, src, seq))
            print("[Simulator] Ranging stream enabled")
            return
        if param_name == "ranging_stop":
            self.ranging_active = False
            self._send_packet(self._make_ack(dst, src, seq))
            print("[Simulator] Ranging stream disabled")
            return

        self._send_packet(self._make_ack(dst, src, seq))

    def _rx_loop(self) -> None:
        while self.running and self.transport.is_open:
            chunk = self.transport.read(4096)
            if not chunk:
                continue
            for pkt in self.proto.decode_from_frames(chunk):
                self._handle_host_packet(pkt)
        self.running = False

    @staticmethod
    def _lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    def _next_pose(self) -> tuple[float, float, float, float, float, float, float]:
        points = self._path_points
        segment_count = len(points) - 1
        self._path_progress = (self._path_progress + 0.04) % segment_count
        idx = int(self._path_progress)
        frac = self._path_progress - idx
        p0 = points[idx]
        p1 = points[idx + 1]

        tag_x = self._lerp(p0[0], p1[0], frac)
        tag_y = self._lerp(p0[1], p1[1], frac)
        tag_z = self._lerp(p0[2], p1[2], frac)
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        yaw_deg = math.degrees(math.atan2(dy, dx)) % 360.0

        self.angle += 0.14
        tril_x = tag_x + 0.06 * math.sin(self.angle * 2.1)
        tril_y = tag_y + 0.06 * math.cos(self.angle * 1.7)
        ukf_x = tag_x + 0.01 * math.sin(self.angle * 0.6)
        ukf_y = tag_y + 0.01 * math.cos(self.angle * 0.6)
        raw_yaw_deg = (yaw_deg + 6.0 * math.sin(self.angle)) % 360.0
        rms_error = 0.04 + 0.01 * abs(math.sin(self.angle * 1.3))
        return tril_x, tril_y, tag_z, ukf_x, ukf_y, yaw_deg, raw_yaw_deg, rms_error

    def _stream_once(self) -> None:
        seq = self.seq
        self.seq += 1
        tril_x, tril_y, tag_z, ukf_x, ukf_y, yaw_deg, raw_yaw_deg, rms_error = self._next_pose()
        timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF

        ranging_pkt = self.factory.ranging_result(int(VvAddress.MCU), int(VvAddress.HOST), seq)
        ranging_pkt.ranging_result.pos_x_m = tril_x
        ranging_pkt.ranging_result.pos_y_m = tril_y
        ranging_pkt.ranging_result.pos_z_m = tag_z
        ranging_pkt.ranging_result.rms_error_m = rms_error
        ranging_pkt.ranging_result.timestamp_ms = timestamp_ms
        ranging_pkt.ranging_result.ClearField("anchors")

        total_rssi = 0.0
        for anchor in self._anchors:
            dx = tril_x - anchor["x_m"]
            dy = tril_y - anchor["y_m"]
            dz = tag_z - anchor["z_m"]
            distance_m = math.sqrt(dx * dx + dy * dy + dz * dz)
            item = ranging_pkt.ranging_result.anchors.add()
            item.anchor_id = anchor["anchor_id"]
            item.distance_mm = max(1, int(distance_m * 1000.0))
            item.fp_amp = int(530 - min(distance_m * 18.0, 180.0))
            total_rssi += -45.0 - distance_m * 2.0
        self._send_packet(ranging_pkt)

        fusion_pkt = self.factory.sensor_fusion_result(int(VvAddress.MCU), int(VvAddress.HOST), seq)
        fusion_pkt.sensor_fusion_result.ukf_x_m = ukf_x
        fusion_pkt.sensor_fusion_result.ukf_y_m = ukf_y
        fusion_pkt.sensor_fusion_result.ukf_yaw_deg = yaw_deg
        fusion_pkt.sensor_fusion_result.tril_x_m = tril_x
        fusion_pkt.sensor_fusion_result.tril_y_m = tril_y
        fusion_pkt.sensor_fusion_result.yaw_deg = raw_yaw_deg
        fusion_pkt.sensor_fusion_result.ranging_error_count = 0
        fusion_pkt.sensor_fusion_result.timestamp_ms = timestamp_ms
        self._send_packet(fusion_pkt)

        self._ranging_total_count += 1
        self._ranging_success_count += 1
        self._last_ranging_time_ms = int(self.period_s * 1000)
        self._last_rms_error_m = rms_error
        self._last_avg_rssi_dbm = int(total_rssi / max(len(self._anchors), 1))

    def _stream_loop(self) -> None:
        while self.running and self.transport.is_open:
            if self.ranging_active:
                self._stream_once()
            time.sleep(self.period_s)
        self.running = False

    def run(self) -> None:
        if self.map_path.is_file():
            print(f"[Simulator] Using map: {self.map_path}")
        else:
            print("[Simulator] Using built-in demo map layout")
        print(f"[Simulator] Loaded {len(self._anchors)} anchors")
        if hasattr(self.transport, "reset_input_buffer"):
            self.transport.reset_input_buffer()
        if hasattr(self.transport, "reset_output_buffer"):
            self.transport.reset_output_buffer()
        for anchor in self._anchors:
            print(
                f"  - A{anchor['anchor_id']}: "
                f"({anchor['x_m']:.3f}, {anchor['y_m']:.3f}, {anchor['z_m']:.3f})"
            )

        self.transport.accept()
        rx_thread = threading.Thread(target=self._rx_loop, name="sim-rx", daemon=True)
        tx_thread = threading.Thread(target=self._stream_loop, name="sim-stream", daemon=True)
        rx_thread.start()
        tx_thread.start()

        try:
            while self.running and self.transport.is_open:
                time.sleep(0.2)
        finally:
            self.running = False
            self.transport.close()
            rx_thread.join(timeout=1.0)
            tx_thread.join(timeout=1.0)
            print("[Simulator] Stopped")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone live-tracking ranging simulator for UWB RTLS Studio"
    )
    parser.add_argument(
        "--map",
        default=str(DEFAULT_MAP_PATH),
        help="Path to map JSON used for anchor layout and demo route",
    )
    parser.add_argument("--host", default=DEFAULT_TCP_HOST, help="TCP host to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_TCP_PORT, help="TCP port to bind")
    parser.add_argument("--serial-port", default="", help="Peripheral-side COM port for a virtual COM pair")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_SERIAL_BAUD, help="Serial baudrate when using --serial-port")
    parser.add_argument("--hz", type=float, default=10.0, help="Streaming rate in Hz")
    return parser.parse_args()


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    args = _parse_args()
    simulator = LiveTrackingPeripheralSimulator(
        map_path=Path(args.map),
        host=args.host,
        port=args.port,
        hz=args.hz,
        serial_port=args.serial_port,
        baudrate=args.baudrate,
    )
    simulator.run()


if __name__ == "__main__":
    main()
