"""
===============================================================================
  UWB RTLS Studio - Live Tracking Flow Test Script
===============================================================================
  File        : software/uwb_rtls_studio/test/test_live_tracking_flow.py
  Description : Integration test script for verifying ranging and fusion stream.
                Uses shared UWB_RTLS_TEST_MODE macro:
                1 = mock GUI without hardware, 0 = real dongle/device flow.
===============================================================================
"""
from __future__ import annotations
import sys
import os
import time
import math
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)
from PyQt6.QtCore import QObject, QTimer

# Add paths to make sure common and studio packages are importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))      # software/uwb_rtls_studio
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))   # software
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))) # root

from utils.runtime_mode import is_test_mode, mode_label

TEST_MODE = is_test_mode()

from common.transport import VvAddress, VvProtocol
from common.commands import CommandFactory

def make_anchor_layout_resp(src: int, dst: int, seq: int) -> any:
    """Helper to generate a mock anchor layout packet with 4 anchors"""
    factory = CommandFactory()
    pkt = factory.anchor_layout_resp(src, dst, seq)
    
    # Define 4 anchor coordinates (A1, A2, A3, A4)
    positions = [
        (1, 0.0, 0.0, 1.5),
        (2, 10.76, 0.0, 1.5),
        (3, 0.0, 13.2, 1.5),
        (4, 10.76, 13.2, 1.5),
    ]
    for aid, x, y, z in positions:
        a = pkt.anchor_layout_resp.anchors.add()
        a.anchor_id = aid
        a.x_m = x
        a.y_m = y
        a.z_m = z
    return pkt

# =============================================================================
# REAL DEVICE TEST FLOW (UWB_RTLS_TEST_MODE = 0)
# =============================================================================
def run_real_device_test():
    from vv_testings.vv_test_session import VvTestSession
    print("\n=== STARTING REAL DEVICE TEST ===")
    
    # 1. Auto detect dongle
    print("Auto-probing dongle port...")
    probe = VvTestSession.auto_probe(src=int(VvAddress.HOST), debug=True)
    if probe is None:
        print("[ERROR] Could not detect a connected UWB Dongle! Please check connection.")
        sys.exit(1)
        
    print(f"[SUCCESS] Detected Dongle on port {probe.port} @ {probe.baud} (Serial: {probe.serial_number})")
    
    # Define addresses: Source is Host, Destination is MCU (Tag)
    src_addr = int(VvAddress.HOST)
    dst_addr = int(VvAddress.MCU)
    
    # 2. Open test session
    with VvTestSession(probe.port, baud=probe.baud, debug=True) as session:
        factory = CommandFactory()
        
        # 3. Probe device type (Check connectivity)
        print("\nStep 1: Check device information...")
        info_pkt = factory.device_information_get(src_addr, dst_addr, session.proto.next_seq())
        res, pkts = session.send_expect_param(info_pkt, "device_information_resp", timeout_s=0.8)
        if res is not None:
            print(f"[SUCCESS] Connected to device. Role={res.device_information_resp.role}")
        else:
            print("[WARNING] Device did not respond to information query. Continuing...")

        # 4. Get Anchor Layout
        print("\nStep 2: Get Anchor Layout from device...")
        layout_pkt = factory.anchor_layout_get(src_addr, dst_addr, session.proto.next_seq())
        res, pkts = session.send_expect_param(layout_pkt, "anchor_layout_resp", timeout_s=1.0)
        if res is not None:
            print(f"[SUCCESS] Received Anchor Layout with {len(res.anchor_layout_resp.anchors)} anchors:")
            for a in res.anchor_layout_resp.anchors:
                print(f"  Anchor {a.anchor_id}: X={a.x_m:.2f}m, Y={a.y_m:.2f}m, Z={a.z_m:.2f}m")
        else:
            print("[ERROR] Failed to retrieve Anchor Layout.")
            
        # 5. Start Ranging (MCU will start streaming)
        print("\nStep 3: Triggering ranging_start...")
        start_pkt = factory.ranging_start(src_addr, dst_addr, session.proto.next_seq())
        session.send_packet(start_pkt)
        
        print("Listening for stream data for 5 seconds...")
        deadline = time.time() + 5.0
        ranging_count = 0
        fusion_count = 0
        
        while time.time() < deadline:
            pkts = session.recv_packets(timeout_s=0.1)
            for pkt in pkts:
                param_name = pkt.WhichOneof("params")
                if param_name == "ranging_result":
                    ranging_count += 1
                    r = pkt.ranging_result
                    print(f"[RX ranging_result #{ranging_count}] Pos: ({r.pos_x_m:.3f}, {r.pos_y_m:.3f}, {r.pos_z_m:.3f}) | RMS: {r.rms_error_m:.3f}")
                elif param_name == "sensor_fusion_result":
                    fusion_count += 1
                    f = pkt.sensor_fusion_result
                    print(f"[RX sensor_fusion_result #{fusion_count}] UKF: ({f.ukf_x_m:.3f}, {f.ukf_y_m:.3f}) | Yaw: {f.ukf_yaw_deg:.1f}° | Tril: ({f.tril_x_m:.3f}, {f.tril_y_m:.3f})")
        
        print(f"\nStream summary: Received {ranging_count} ranging_results, {fusion_count} sensor_fusion_results")
        
        # 6. Stop Ranging
        print("\nStep 4: Triggering ranging_stop...")
        stop_pkt = factory.ranging_stop(src_addr, dst_addr, session.proto.next_seq())
        session.send_and_wait(stop_pkt, timeout_s=0.3)
        print("[SUCCESS] Ranging stopped. Real device test completed successfully.")

# =============================================================================
# MOCK DEVICE CLIENT FOR APP TEST MODE (UWB_RTLS_TEST_MODE = 1)
# =============================================================================
class MockTcpDeviceClient:
    """Connects to the app's mock TCP server and behaves like a firmware device."""
    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        import socket
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(0.2)
        self.proto = VvProtocol()
        self.factory = CommandFactory()
        self.running = True
        self.ranging_active = False
        self.seq = 1
        self.angle = 0.0

    def connect(self):
        print(f"[Mock Device] Connecting to app TCP server on {self.host}:{self.port}...")
        while self.running:
            try:
                self.sock.connect((self.host, self.port))
                print("[Mock Device] Connected to app")
                return
            except OSError:
                print("[Mock Device] Waiting for app TCP server...")
                time.sleep(1.0)

    def send_packet(self, pkt) -> None:
        frame = self.proto.wrap_packet(pkt)
        self.sock.sendall(frame)

    def handle_packet(self, pkt):
        from services.query_state_machine import QueryQueueManager

        param_name = pkt.WhichOneof("params")
        if not param_name or param_name == "ack":
            return

        src = pkt.hdr.addr.src
        dst = pkt.hdr.addr.dst
        seq = pkt.hdr.seq
        print(f"[Mock Device] RX {param_name} seq={seq} src={src} dst={dst}")

        if param_name == "ranging_start":
            self.ranging_active = True
            ack = self.factory.ack(src=dst, dst=src, seq=seq)
            ack.ack.ack_seq = seq
            ack.ack.response = self.proto.pb.PACKET_ACK_RESPONSE_ACK
            self.send_packet(ack)
            print("[Mock Device] Ranging enabled")
            return

        if param_name == "ranging_stop":
            self.ranging_active = False
            ack = self.factory.ack(src=dst, dst=src, seq=seq)
            ack.ack.ack_seq = seq
            ack.ack.response = self.proto.pb.PACKET_ACK_RESPONSE_ACK
            self.send_packet(ack)
            print("[Mock Device] Ranging disabled")
            return

        response_name = QueryQueueManager.RESPONSE_MAP.get(param_name)
        if response_name:
            resp_method = getattr(self.factory, response_name, None)
            if resp_method:
                if response_name == "anchor_layout_resp":
                    resp = make_anchor_layout_resp(src=dst, dst=src, seq=seq)
                else:
                    resp = resp_method(src=dst, dst=src, seq=seq)
                    if response_name == "ble_status_resp":
                        resp.ble_status_resp.state = self.proto.pb.BLE_STATE_CONNECTED
                        resp.ble_status_resp.rssi_dbm = -60
                        resp.ble_status_resp.disconnect_reason = 0
                    elif response_name == "device_information_resp":
                        resp.device_information_resp.device_type = self.proto.pb.DEVICE_TYPE_TAG
                        resp.device_information_resp.role = self.proto.pb.DEVICE_ROLE_TAG
                self.send_packet(resp)
                print(f"[Mock Device] TX {response_name}")
                return

        ack = self.factory.ack(src=dst, dst=src, seq=seq)
        ack.ack.ack_seq = seq
        ack.ack.response = self.proto.pb.PACKET_ACK_RESPONSE_ACK
        self.send_packet(ack)

    def recv_loop(self):
        while self.running:
            try:
                data = self.sock.recv(4096)
                if not data:
                    print("[Mock Device] App disconnected")
                    break
                for pkt in self.proto.decode_from_frames(data):
                    self.handle_packet(pkt)
            except TimeoutError:
                continue
            except OSError:
                break
        self.running = False

    def stream_loop(self):
        while self.running:
            if self.ranging_active:
                self.send_mock_data()
            time.sleep(0.1)

    def send_mock_data(self):
        self.angle += 0.03
        t = self.angle

        cx, cy = 5.38, 6.6
        Ax, Ay = 4.2, 5.0

        tag_x = cx + Ax * math.sin(2 * t)
        tag_y = cy + Ay * math.sin(t)
        tag_z = 1.2

        dx = 2.0 * Ax * math.cos(2 * t)
        dy = Ay * math.cos(t)
        psi_rad = math.atan2(dy, dx)
        psi_deg = math.degrees(psi_rad) % 360.0

        ukf_x = tag_x + 0.01 * math.sin(t * 2.0)
        ukf_y = tag_y + 0.01 * math.cos(t * 2.0)
        ukf_yaw_deg = psi_deg

        noise_x = 0.40 * math.sin(t * 25.0) + 0.15 * math.cos(t * 58.0)
        noise_y = 0.40 * math.cos(t * 22.0) + 0.15 * math.sin(t * 53.0)
        tril_x = tag_x + noise_x
        tril_y = tag_y + noise_y
        raw_yaw = (psi_deg + 25.0 * math.sin(t * 15.0)) % 360.0

        anchors = [
            (1, 0.0, 0.0, 1.5),
            (2, 10.76, 0.0, 1.5),
            (3, 0.0, 13.2, 1.5),
            (4, 10.76, 13.2, 1.5),
        ]

        seq = self.seq
        self.seq += 1

        ranging_pkt = self.proto.pb.packet_t()
        ranging_pkt.hdr.addr.src = int(VvAddress.MCU)
        ranging_pkt.hdr.addr.dst = int(VvAddress.HOST)
        ranging_pkt.hdr.seq = seq

        res = ranging_pkt.ranging_result
        res.pos_x_m = tril_x
        res.pos_y_m = tril_y
        res.pos_z_m = tag_z
        res.rms_error_m = 0.250
        res.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF

        for aid, ax, ay, az in anchors:
            dist = math.hypot(tril_x - ax, tril_y - ay)
            a_item = res.anchors.add()
            a_item.anchor_id = aid
            a_item.distance_mm = int(dist * 1000)
            a_item.fp_amp = 480

        self.send_packet(ranging_pkt)

        fusion_pkt = self.proto.pb.packet_t()
        fusion_pkt.hdr.addr.src = int(VvAddress.MCU)
        fusion_pkt.hdr.addr.dst = int(VvAddress.HOST)
        fusion_pkt.hdr.seq = seq

        fs = fusion_pkt.sensor_fusion_result
        fs.ukf_x_m = ukf_x
        fs.ukf_y_m = ukf_y
        fs.ukf_yaw_deg = ukf_yaw_deg
        fs.tril_x_m = tril_x
        fs.tril_y_m = tril_y
        fs.yaw_deg = raw_yaw
        fs.ranging_error_count = 0
        fs.timestamp_ms = res.timestamp_ms

        self.send_packet(fusion_pkt)

    def run(self):
        import threading
        self.connect()
        rx_thread = threading.Thread(target=self.recv_loop, name="mock-device-rx", daemon=True)
        tx_thread = threading.Thread(target=self.stream_loop, name="mock-device-stream", daemon=True)
        rx_thread.start()
        tx_thread.start()
        print("[Mock Device] Ready. Use the app to start/stop ranging and end session.")
        try:
            while self.running:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            try:
                self.sock.close()
            except OSError:
                pass
            rx_thread.join(timeout=1.0)
            tx_thread.join(timeout=1.0)
            print("[Mock Device] Stopped")


def run_mock_gui_test():
    print("\n=== STARTING MOCK DEVICE CLIENT ===")
    print("[INFO] This process does not open the app. Start the app separately, then use its UI.")
    MockTcpDeviceClient().run()# =============================================================================
# MAIN SCRIPT ENTRY
# =============================================================================
if __name__ == "__main__":
    print(f"[MODE] {mode_label()}")
    if TEST_MODE:
        run_mock_gui_test()
    else:
        run_real_device_test()
