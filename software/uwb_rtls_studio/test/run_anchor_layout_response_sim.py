from __future__ import annotations

import argparse
import json
import math
import os
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

try:
    from common import protocol_pb2 as pb
    from common.commands import CommandFactory
    from common.transport import VvAddress, VvProtocol
except ModuleNotFoundError as exc:
    missing = exc.name or str(exc)
    message = (
        f"[SIM] Missing Python dependency: {missing}\n"
        "Run this script with the project venv:\n"
        "  software\\.venv\\Scripts\\python.exe software\\uwb_rtls_studio\\test\\run_anchor_layout_response_sim.py\n"
        "or install software\\requirements.txt into your active Python."
    )
    print(message, file=sys.stderr)
    raise SystemExit(2) from exc


DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 9999
DEFAULT_SERIAL_BAUD = 115200
DEFAULT_MAP_PATH = Path(STUDIO_DIR) / "data" / "maps" / "geofence_mapv1.json"
DEFAULT_ANCHORS = [
    {"anchor_id": 0, "x_m": 0.7, "y_m": 0.0, "z_m": 2.5},
    {"anchor_id": 1, "x_m": 0.7, "y_m": 8.4, "z_m": 2.5},
    {"anchor_id": 2, "x_m": 8.3, "y_m": 8.4, "z_m": 2.5},
    {"anchor_id": 3, "x_m": 8.3, "y_m": 0.0, "z_m": 2.5},
]
SCENARIOS = ("ellipse", "e1-303-zones")

# E1-303 map has Room 1 origin at global (-2.4, -6.0), yaw 0.
# These local-frame points become global positions crossing:
# outside allowed boundary -> Rule Zone 1 (allowed) -> Rule Zone 2 (forbidden).
E1_303_ZONE_ROUTE = [
    {"label": "outside_allowed", "local": (4.40, 6.00), "global": (2.00, 0.00)},
    {"label": "approach_allowed", "local": (8.80, 5.30), "global": (6.40, -0.70)},
    {"label": "allowed_rule_zone_1", "local": (10.05, 4.35), "global": (7.65, -1.65)},
    {"label": "allowed_rule_zone_1", "local": (10.05, 3.65), "global": (7.65, -2.35)},
    {"label": "forbidden_rule_zone_2", "local": (10.05, 2.55), "global": (7.65, -3.45)},
    {"label": "forbidden_rule_zone_2", "local": (10.05, 1.25), "global": (7.65, -4.75)},
    {"label": "allowed_rule_zone_1", "local": (10.05, 4.35), "global": (7.65, -1.65)},
    {"label": "outside_allowed", "local": (4.40, 6.00), "global": (2.00, 0.00)},
]
GENERIC_RESPONSES = {
    "time_sync_get": "time_sync_resp",
    "time_sync_set": "time_sync_resp",
    "sys_config_get": "sys_config_resp",
    "sys_ranging_cfg_get": "sys_ranging_cfg_resp",
    "sensor_fusion_cfg_get": "sensor_fusion_cfg_resp",
    "prefilter_cfg_get": "prefilter_cfg_resp",
    "device_type_get": "device_type_set",
    "ble_conn_params_get": "ble_conn_params_resp",
    "rtos_resource_get": "rtos_resource_resp",
    "rtos_task_stats_get": "rtos_task_stats_resp",
    "zone_profile_get": "zone_profile_resp",
}

def _fixed2(value: float) -> int:
    return int(round(float(value) * 100.0))


def _decode_fixed2(value) -> float:
    return float(value) / 100.0


def _anchor_mask(anchors: list[dict]) -> int:
    mask = 0
    for anchor in anchors:
        anchor_id = int(anchor["anchor_id"])
        if 0 <= anchor_id < 32:
            mask |= 1 << anchor_id
    return mask


def _coerce_anchor(anchor: dict, idx: int) -> dict:
    return {
        "anchor_id": int(anchor.get("anchor_id", anchor.get("id", idx))),
        "x_m": float(anchor.get("x_m", anchor.get("local_x_m", anchor.get("x", 0.0)))),
        "y_m": float(anchor.get("y_m", anchor.get("local_y_m", anchor.get("y", 0.0)))),
        "z_m": float(anchor.get("z_m", anchor.get("z", 2.5))),
    }


def load_anchors(map_path: Path | None, inline_anchors: str = "") -> list[dict]:
    if inline_anchors:
        anchors: list[dict] = []
        for idx, chunk in enumerate(inline_anchors.split(";")):
            chunk = chunk.strip()
            if not chunk:
                continue
            anchor_id_text, xyz_text = chunk.split(":", 1)
            x_text, y_text, z_text = [part.strip() for part in xyz_text.split(",")]
            anchors.append(
                {
                    "anchor_id": int(anchor_id_text.strip().lstrip("A")),
                    "x_m": float(x_text),
                    "y_m": float(y_text),
                    "z_m": float(z_text),
                }
            )
        if anchors:
            return anchors

    if map_path and map_path.is_file():
        payload = json.loads(map_path.read_text(encoding="utf-8-sig"))
        raw_anchors = payload.get("map_objects", {}).get("anchors") or payload.get("anchors") or []
        anchors = [_coerce_anchor(anchor, idx) for idx, anchor in enumerate(raw_anchors)]
        if anchors:
            print(f"[SIM] Loaded {len(anchors)} anchors from map: {map_path}")
            return anchors
        print(f"[SIM] Map has no anchors, using built-in A0-A3: {map_path}")
    elif map_path:
        print(f"[SIM] Map not found, using built-in A0-A3: {map_path}")

    return [dict(anchor) for anchor in DEFAULT_ANCHORS]


class TcpClientTransport:
    def __init__(self, host: str, port: int, reconnect_s: float = 1.0):
        self.host = host
        self.port = port
        self.reconnect_s = reconnect_s
        self.sock: socket.socket | None = None
        self.is_open = False

    def open(self) -> None:
        while not self.is_open:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.host, self.port))
                sock.settimeout(0.05)
                self.sock = sock
                self.is_open = True
                print(f"[SIM] Connected to GUI TCP server at {self.host}:{self.port}")
            except ConnectionRefusedError:
                print(f"[SIM] Waiting for GUI TCP server at {self.host}:{self.port} ...")
                time.sleep(self.reconnect_s)

    def read(self, size: int = 4096) -> bytes:
        if not self.sock or not self.is_open:
            return b""
        try:
            chunk = self.sock.recv(size)
            if not chunk:
                self.is_open = False
                return b""
            return chunk
        except socket.timeout:
            return b""
        except OSError:
            self.is_open = False
            return b""

    def write(self, data: bytes) -> None:
        if self.sock and self.is_open:
            self.sock.sendall(data)

    def close(self) -> None:
        self.is_open = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class SerialTransport:
    def __init__(self, port_name: str, baudrate: int):
        import serial

        self.port_name = port_name
        self.baudrate = baudrate
        self.serial = serial.Serial(port=port_name, baudrate=baudrate, timeout=0.05)
        self.is_open = bool(self.serial and self.serial.is_open)
        print(f"[SIM] Opened serial port {port_name} @ {baudrate}")

    def open(self) -> None:
        return None

    def read(self, size: int = 4096) -> bytes:
        if not self.is_open:
            return b""
        chunk = self.serial.read(size)
        if not self.serial.is_open:
            self.is_open = False
        return chunk

    def write(self, data: bytes) -> None:
        if self.is_open:
            self.serial.write(data)

    def close(self) -> None:
        self.is_open = False
        try:
            self.serial.close()
        except Exception:
            pass


class AnchorLayoutResponder:
    def __init__(self, transport, anchors: list[dict], hz: float, stream_on_boot: bool, scenario: str):
        self.transport = transport
        self.anchors = anchors
        self.period_s = 1.0 / max(float(hz), 1.0)
        self.stream_on_boot = stream_on_boot
        self.scenario = scenario
        self.proto = VvProtocol()
        self.factory = CommandFactory()
        self.factory.set_device_identity(pb.DEVICE_TYPE_TAG, pb.DEVICE_ROLE_TAG)
        self.running = True
        self.ranging_active = stream_on_boot
        self.seq = 1
        self.angle = 0.0
        self.total_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.timeout_count = 0
        self.last_ranging_time_ms = 0
        self.last_rms_error_m = 0.0
        self.avg_rssi_dbm = -62
        self._route_pos = 0.0
        self._last_route_label = ""
        self._lock = threading.Lock()

    def _send_packet(self, pkt) -> None:
        frame = self.proto.wrap_packet(pkt)
        with self._lock:
            self.transport.write(frame)

    def _ack(self, src: int, dst: int, seq: int):
        pkt = self.factory.ack(src=src, dst=dst, seq=seq)
        pkt.ack.ack_seq = seq
        pkt.ack.response = pb.PACKET_ACK_RESPONSE_ACK
        return pkt

    def _send_anchor_layout_resp(self, host_addr: int, seq: int) -> None:
        pkt = self.factory.anchor_layout_resp(int(VvAddress.MCU), host_addr, seq)
        del pkt.anchor_layout_resp.anchors[:]
        for anchor in self.anchors:
            item = pkt.anchor_layout_resp.anchors.add()
            item.anchor_id = int(anchor["anchor_id"])
            item.x_m = float(anchor["x_m"])
            item.y_m = float(anchor["y_m"])
            item.z_m = float(anchor["z_m"])
        self._send_packet(pkt)
        print(f"[SIM] TX anchor_layout_resp seq={seq} anchors={len(self.anchors)}")

    def _send_device_information_resp(self, host_addr: int, seq: int) -> None:
        pkt = self.factory.device_information_resp(int(VvAddress.MCU), host_addr, seq)
        pkt.device_information_resp.device_type = pb.DEVICE_TYPE_TAG
        pkt.device_information_resp.role = pb.DEVICE_ROLE_TAG
        pkt.device_information_resp.serial_number = 0xA11C0001
        pkt.device_information_resp.hw_version = 1
        pkt.device_information_resp.fw_version.major = 1
        pkt.device_information_resp.fw_version.minor = 0
        pkt.device_information_resp.fw_version.patch = 0
        pkt.device_information_resp.fw_version.build = 1
        pkt.device_information_resp.uid = b"ANCHLAYT"
        self._send_packet(pkt)
        print(f"[SIM] TX device_information_resp seq={seq}")

    def _send_ble_status_resp(self, host_addr: int, seq: int) -> None:
        pkt = self.factory.ble_status_resp(int(VvAddress.CENTRAL), host_addr, seq)
        pkt.ble_status_resp.state = pb.BLE_STATE_CONNECTED
        pkt.ble_status_resp.rssi_dbm = -55
        pkt.ble_status_resp.disconnect_reason = 0
        self._send_packet(pkt)

    def _send_battery_info_resp(self, host_addr: int, seq: int) -> None:
        pkt = self.factory.battery_info_resp(int(VvAddress.MCU), host_addr, seq)
        pkt.battery_info_resp.bat_voltage_mv = 3850
        pkt.battery_info_resp.bat_soc_percent = 91
        pkt.battery_info_resp.remaining_min = 420
        pkt.battery_info_resp.is_charging = False
        pkt.battery_info_resp.mcu_temp_c = 30.0
        pkt.battery_info_resp.mcu_voltage_mv = 3300
        pkt.battery_info_resp.uwb_temp_c = 34.0
        pkt.battery_info_resp.uwb_voltage_mv = 3290
        pkt.battery_info_resp.imu_temp_c = 31.0
        pkt.battery_info_resp.error_mask = 0
        self._send_packet(pkt)

    def _send_ranging_status_resp(self, host_addr: int, seq: int) -> None:
        pkt = self.factory.ranging_status_resp(int(VvAddress.MCU), host_addr, seq)
        status = pkt.ranging_status_resp
        status.ranging_period_ms = int(self.period_s * 1000)
        status.ranging_total_count = self.total_count
        status.ranging_success_count = self.success_count
        status.ranging_failed_count = self.failed_count
        status.ranging_timeout_count = self.timeout_count
        status.last_ranging_time_ms = self.last_ranging_time_ms
        status.last_rms_error_m = self.last_rms_error_m
        status.last_avg_rssi_dbm = int(self.avg_rssi_dbm)
        status.last_update_timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
        self._send_packet(pkt)

    def _send_generic_resp(self, param_name: str, host_addr: int, seq: int) -> bool:
        response_name = GENERIC_RESPONSES.get(param_name)
        if not response_name:
            return False
        builder = getattr(self.factory, response_name, None)
        if not builder:
            return False
        if response_name == "device_type_set":
            pkt = builder(int(VvAddress.MCU), host_addr, seq, device_type=pb.DEVICE_TYPE_TAG)
        else:
            pkt = builder(int(VvAddress.MCU), host_addr, seq)
        self._send_packet(pkt)
        print(f"[SIM] TX {response_name} seq={seq}")
        return True

    def _pose(self) -> tuple[float, float, float, float]:
        if self.scenario == "e1-303-zones":
            return self._pose_e1_303_zones()

        xs = [a["x_m"] for a in self.anchors]
        ys = [a["y_m"] for a in self.anchors]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cx = (min_x + max_x) * 0.5
        cy = (min_y + max_y) * 0.5
        ax = max((max_x - min_x) * 0.32, 0.5)
        ay = max((max_y - min_y) * 0.32, 0.5)
        self.angle += 0.11
        x = cx + ax * math.sin(self.angle * 1.7)
        y = cy + ay * math.sin(self.angle)
        yaw = math.degrees(math.atan2(ay * math.cos(self.angle), ax * 1.7 * math.cos(self.angle * 1.7))) % 360.0
        return x, y, 1.2, yaw

    def _pose_e1_303_zones(self) -> tuple[float, float, float, float]:
        route = E1_303_ZONE_ROUTE
        if len(route) < 2:
            return 0.0, 0.0, 1.2, 0.0

        # About 30 seconds for one full loop at 5 Hz, slow enough to keep
        # the allowed-zone segment visible before any speed-limit transition.
        self._route_pos = (self._route_pos + 1.0 / 150.0) % len(route)
        idx = int(self._route_pos)
        nxt = (idx + 1) % len(route)
        frac = self._route_pos - idx
        x0, y0 = route[idx]["local"]
        x1, y1 = route[nxt]["local"]
        x = x0 + (x1 - x0) * frac
        y = y0 + (y1 - y0) * frac
        yaw = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0

        label = str(route[idx]["label"])
        if label != self._last_route_label:
            gx, gy = route[idx]["global"]
            print(f"[SIM] E1-303 scenario: {label} global=({gx:.2f},{gy:.2f}) local=({x0:.2f},{y0:.2f})")
            self._last_route_label = label
        return x, y, 1.2, yaw

    def _stream_once(self) -> None:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        x, y, z, yaw_deg = self._pose()
        tril_x = x + 0.04 * math.sin(self.angle * 2.3)
        tril_y = y + 0.04 * math.cos(self.angle * 1.9)
        timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF

        pkt = self.factory.sensor_fusion_result(int(VvAddress.MCU), int(VvAddress.HOST), seq)
        fs = pkt.sensor_fusion_result
        fs.ukf_step = self.total_count + 1
        fs.ukf_x_m = _fixed2(x)
        fs.ukf_y_m = _fixed2(y)
        fs.ukf_yaw_deg = _fixed2(yaw_deg)
        fs.tril_x_m = _fixed2(tril_x)
        fs.tril_y_m = _fixed2(tril_y)
        fs.yaw_deg = _fixed2((yaw_deg + 3.0 * math.sin(self.angle)) % 360.0)
        fs.anchor_mask = _anchor_mask(self.anchors)
        fs.ranging_error_count = self.failed_count
        fs.timestamp_ms = timestamp_ms
        fs.zone_id = 1
        del fs.anchors[:]

        total_rssi = 0.0
        for anchor in self.anchors:
            dx = tril_x - anchor["x_m"]
            dy = tril_y - anchor["y_m"]
            dz = z - anchor["z_m"]
            distance_m = math.sqrt(dx * dx + dy * dy + dz * dz)
            item = fs.anchors.add()
            item.anchor_id = int(anchor["anchor_id"])
            item.distance_mm = max(1, int(round(distance_m * 1000.0)))
            item.weight = max(1, min(100, int(round(100.0 - distance_m * 4.0))))
            total_rssi += -44.0 - distance_m * 2.0

        self._send_packet(pkt)

        self.total_count += 1
        self.success_count += 1
        self.last_ranging_time_ms = int(self.period_s * 1000)
        self.last_rms_error_m = 0.035 + 0.01 * abs(math.sin(self.angle))
        self.avg_rssi_dbm = total_rssi / max(len(self.anchors), 1)

        distances = ", ".join(f"A{a.anchor_id}={a.distance_mm}mm/W{a.weight}" for a in fs.anchors[:4])
        print(
            f"[SIM] TX sensor_fusion_result seq={seq} "
            f"mask=0x{fs.anchor_mask:08X} ukf=({_decode_fixed2(fs.ukf_x_m):.2f},{_decode_fixed2(fs.ukf_y_m):.2f}) "
            f"{distances}"
        )

    def _handle_packet(self, pkt) -> None:
        param_name = pkt.WhichOneof("params")
        if not param_name:
            return

        src = int(pkt.hdr.addr.src)
        dst = int(pkt.hdr.addr.dst)
        seq = int(pkt.hdr.seq)
        print(f"[SIM] RX {param_name} seq={seq} src={src} dst={dst}")

        if param_name == "ack":
            return
        if param_name == "device_information_get":
            self._send_device_information_resp(src, seq)
            return
        if param_name == "anchor_layout_get":
            self._send_anchor_layout_resp(src, seq)
            return
        if param_name == "ble_status_get":
            self._send_ble_status_resp(src, seq)
            return
        if param_name == "battery_info_get":
            self._send_battery_info_resp(src, seq)
            return
        if param_name == "ranging_status_get":
            self._send_ranging_status_resp(src, seq)
            return
        if self._send_generic_resp(param_name, src, seq):
            return
        if param_name == "ranging_start":
            self.ranging_active = True
            self._send_packet(self._ack(dst, src, seq))
            print("[SIM] Ranging stream ON")
            return
        if param_name == "ranging_stop":
            self.ranging_active = False
            self._send_packet(self._ack(dst, src, seq))
            print("[SIM] Ranging stream OFF")
            return

        self._send_packet(self._ack(dst, src, seq))

    def _rx_loop(self) -> None:
        while self.running and self.transport.is_open:
            chunk = self.transport.read(4096)
            if not chunk:
                continue
            for pkt in self.proto.decode_from_frames(chunk):
                self._handle_packet(pkt)
        self.running = False

    def _stream_loop(self) -> None:
        while self.running and self.transport.is_open:
            if self.ranging_active:
                self._stream_once()
            time.sleep(self.period_s)
        self.running = False

    def run(self) -> None:
        print("[SIM] Anchor layout responder ready")
        print(f"[SIM] Anchors: {len(self.anchors)}")
        for anchor in self.anchors:
            print(f"  A{anchor['anchor_id']}: ({anchor['x_m']:.3f}, {anchor['y_m']:.3f}, {anchor['z_m']:.3f})")

        self.transport.open()
        rx_thread = threading.Thread(target=self._rx_loop, name="anchor-layout-rx", daemon=True)
        stream_thread = threading.Thread(target=self._stream_loop, name="anchor-layout-stream", daemon=True)
        rx_thread.start()
        stream_thread.start()

        try:
            while self.running and self.transport.is_open:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n[SIM] Ctrl+C received")
        finally:
            self.running = False
            self.transport.close()
            rx_thread.join(timeout=1.0)
            stream_thread.join(timeout=1.0)
            print("[SIM] Stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mock tag responder for anchor_layout_get/anchor_layout_resp and sensor_fusion_result."
    )
    parser.add_argument("--host", default=DEFAULT_TCP_HOST, help="GUI TCP host in macro/test mode")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="GUI TCP port in macro/test mode")
    parser.add_argument("--serial-port", default="", help="Use a virtual/real serial port instead of TCP")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_SERIAL_BAUD, help="Serial baudrate")
    parser.add_argument("--map", default=str(DEFAULT_MAP_PATH), help="Map JSON to source anchors from")
    parser.add_argument(
        "--anchors",
        default="",
        help='Inline anchors, e.g. "0:0.7,0,2.5;1:0.7,8.4,2.5;2:8.3,8.4,2.5;3:8.3,0,2.5"',
    )
    parser.add_argument("--hz", type=float, default=5.0, help="sensor_fusion_result streaming rate")
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default="ellipse",
        help="Motion profile: ellipse keeps the old path; e1-303-zones crosses Rule Zone 1/2 in geofence_mapv1.",
    )
    parser.add_argument(
        "--stream-on-boot",
        action="store_true",
        help="Start streaming sensor_fusion_result immediately instead of waiting for ranging_start",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    map_path = Path(args.map) if args.map else None
    anchors = load_anchors(map_path, args.anchors)
    transport = (
        SerialTransport(args.serial_port, args.baudrate)
        if args.serial_port
        else TcpClientTransport(args.host, args.tcp_port)
    )
    responder = AnchorLayoutResponder(
        transport=transport,
        anchors=anchors,
        hz=args.hz,
        stream_on_boot=args.stream_on_boot,
        scenario=args.scenario,
    )
    responder.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
