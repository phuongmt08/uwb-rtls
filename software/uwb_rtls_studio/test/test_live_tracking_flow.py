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


def _fixed2(value: float) -> int:
    return int(round(value * 100.0))

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
# MOCK GUI DEVICE SIMULATION TEST FLOW (UWB_RTLS_TEST_MODE = 1)
# =============================================================================
class MockSerialDevice(QObject):
    """Simulates UWB Tag/MCU device firmware over SerialService signals"""
    def __init__(self, serial_service):
        super().__init__()
        self.serial_service = serial_service
        self.proto = VvProtocol()
        self.factory = CommandFactory()
        self.seq = 0
        self.angle = 0.0
        self.ranging_timer = QTimer(self)
        self.ranging_timer.setInterval(100)  # Stream at 10 Hz
        self.ranging_timer.timeout.connect(self.send_mock_data)

        # Monkeypatch SerialService instance methods to intercept communication
        self.serial_service.open = self.mock_open
        self.serial_service.write = self.mock_write
        self.serial_service.close = self.mock_close

    def mock_open(self, port: str) -> None:
        self.serial_service._running = True
        class DummySerial:
            port = "COM_MOCK"
            is_open = True
            def close(self): pass
        self.serial_service._serial = DummySerial()
        print(f"[Mock Device] Bypass serial connection, open mock session on {port}")

    def mock_close(self) -> None:
        self.ranging_timer.stop()
        self.serial_service._serial = None
        self.serial_service._running = False
        print("[Mock Device] Mock session closed")

    def mock_write(self, data: bytes) -> None:
        """Decode incoming frames from host and handle requests"""
        from services.query_state_machine import QueryQueueManager
        
        packets = self.proto.decode_from_frames(data)
        for pkt in packets:
            param_name = pkt.WhichOneof("params")
            src = pkt.hdr.addr.src
            dst = pkt.hdr.addr.dst
            seq = pkt.hdr.seq
            
            # Verify correct addressing
            if src != int(VvAddress.HOST):
                print(f"[Mock Device Warning] Bad source address header: src={src}")
                continue
                
            print(f"[Mock Device] Received Command '{param_name}' addressed to dst={dst} (Seq: {seq})")
            
            # Handle start/stop ranging actions specifically
            if param_name == "ranging_start":
                self.ranging_timer.start()
                print("[Mock Device] Ranging started. Streaming live coordinate telemetry...")
            elif param_name == "ranging_stop":
                self.ranging_timer.stop()
                print("[Mock Device] Ranging stopped")
            
            # Check if this command maps to a response command
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
                            
                    self.send_to_host(resp)
                    print(f"[Mock Device] Dynamically sent response '{response_name}' to Host")
                    continue

            # Fallback: Respond with ACK
            ack = self.factory.ack(src=dst, dst=src, seq=seq)
            ack.ack.ack_seq = seq
            ack.ack.response = self.proto.pb.PACKET_ACK_RESPONSE_ACK
            self.send_to_host(ack)
            print(f"[Mock Device] Sent generic ACK for command '{param_name}' to Host")

    def send_to_host(self, pkt):
        frame = self.proto.wrap_packet(pkt)
        self.serial_service.data_received.emit(frame)

    def send_mock_data(self):
        """Simulate figure-8 movement on canvas and stream packets"""
        self.angle += 0.03  # Speed of simulation (slightly slower for larger path)
        t = self.angle
        
        # Large bow-tie Lissajous parameters (covers most of 10.76 x 13.2 canvas)
        cx, cy = 5.38, 6.6
        Ax, Ay = 4.2, 5.0
        
        tag_x = cx + Ax * math.sin(2 * t)
        tag_y = cy + Ay * math.sin(t)
        tag_z = 1.2
        
        # Analytical derivatives for tangent heading
        dx = 2.0 * Ax * math.cos(2 * t)
        dy = Ay * math.cos(t)
        psi_rad = math.atan2(dy, dx)
        psi_deg = math.degrees(psi_rad) % 360.0
        
        # Smooth UKF path (filtered, very little noise)
        ukf_x = tag_x + 0.01 * math.sin(t * 2.0)
        ukf_y = tag_y + 0.01 * math.cos(t * 2.0)
        ukf_yaw_deg = psi_deg
        
        # Noisy Trilateration path (unfiltered, highly chaotic but follows the path)
        # Using deterministic sines for high-frequency jitter
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
        
        # 1. Ranging result packet (uses noisy trilateration coords to represent raw estimation)
        ranging_pkt = self.proto.pb.packet_t()
        ranging_pkt.hdr.addr.src = int(VvAddress.MCU)
        ranging_pkt.hdr.addr.dst = int(VvAddress.HOST)
        ranging_pkt.hdr.seq = seq
        
        res = ranging_pkt.ranging_result
        res.pos_x_m = tril_x
        res.pos_y_m = tril_y
        res.pos_z_m = tag_z
        res.rms_error_m = 0.250  # higher error for raw ranging
        res.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
        
        for aid, ax, ay, az in anchors:
            dist = math.hypot(tril_x - ax, tril_y - ay)
            a_item = res.anchors.add()
            a_item.anchor_id = aid
            a_item.distance_mm = int(dist * 1000)
            a_item.fp_amp = 480
            
        self.send_to_host(ranging_pkt)
        
        # 2. Sensor fusion result packet
        fusion_pkt = self.proto.pb.packet_t()
        fusion_pkt.hdr.addr.src = int(VvAddress.MCU)
        fusion_pkt.hdr.addr.dst = int(VvAddress.HOST)
        fusion_pkt.hdr.seq = seq
        
        fs = fusion_pkt.sensor_fusion_result
        fs.ukf_x_m = _fixed2(ukf_x)
        fs.ukf_y_m = _fixed2(ukf_y)
        fs.ukf_yaw_deg = _fixed2(ukf_yaw_deg)
        fs.tril_x_m = _fixed2(tril_x)
        fs.tril_y_m = _fixed2(tril_y)
        fs.yaw_deg = _fixed2(raw_yaw)
        fs.ranging_error_count = 0
        fs.timestamp_ms = res.timestamp_ms
        
        self.send_to_host(fusion_pkt)


def run_mock_gui_test():
    print("\n=== STARTING AUTOMATED MOCK GUI TEST ===")
    print("[INFO] Shared macro is TEST/MOCK. Skipped to MainWindow without dongle/scan popups.")

    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    
    # Import QApplication inside function to prevent early setup issues
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    
    app = QApplication(sys.argv)
    
    # Load stylesheet & font like main.py
    from utils.theme import DARK_STYLESHEET
    app.setStyleSheet(DARK_STYLESHEET)
    
    from PyQt6.QtGui import QFont
    app.setFont(QFont("Segoe UI", 13))

    # Wire Dependency Injection flow
    from services.serial_service import SerialService
    from services.protocol_service import ProtocolService
    serial_service = SerialService()
    protocol_service = ProtocolService(serial_service)

    from repository.telemetry_repository import TelemetryRepository
    from repository.ble_scan_repository import BleScanRepository
    from repository.ranging_repository import RangingRepository
    from repository.config_repository import ConfigRepository
    from repository.diagnostics_repository import DiagnosticsRepository
    from repository.log_repository import LogRepository
    from repository.protocol_packet_repository import ProtocolPacketRepository
    from services.command_bus import init_shared_command_bus
    from models.telemetry_model import TelemetryModel

    telemetry_model = TelemetryModel()
    telemetry_repo = TelemetryRepository(telemetry_model=telemetry_model)
    ble_scan_repo = BleScanRepository()
    ranging_repo = RangingRepository()
    config_repo = ConfigRepository()
    diagnostics_repo = DiagnosticsRepository()
    log_repo = LogRepository()
    packet_repo = ProtocolPacketRepository(
        ranging_repo, telemetry_repo, ble_scan_repo, config_repo, diagnostics_repo, log_repo
    )
    protocol_service.set_packet_repository(packet_repo)
    command_bus = init_shared_command_bus(protocol_service)

    from utils.app_state import shared_app_state
    shared_app_state.init_query_manager(
        send_packet_fn=lambda cmd, dst, **kwargs: command_bus.send(cmd, dst_addr=dst, **kwargs)
    )

    from models.dongle_model import DongleModel
    from viewmodels.dongle_viewmodel import DongleViewModel
    dongle_model = DongleModel(serial_service, protocol_service)
    dongle_vm = DongleViewModel(dongle_model)

    # Instantiate Mock UWB Tag/MCU device
    mock_device = MockSerialDevice(serial_service)

    # Initialize Main Views and ViewModels
    from views.windows.main_window import MainWindow
    from models.ranging_model import RangingModel
    from models.device_model import DeviceModel
    from models.session_model import SessionModel
    from viewmodels.live_tracking_viewmodel import LiveTrackingViewModel
    from viewmodels.device_info_viewmodel import DeviceInfoViewModel
    from repository.session_repository import SessionRepository
    from repository.session_browser import SessionBrowser
    from services.session_run_manager import SessionRunManager
    from models.log_model import LogModel
    from viewmodels.log_viewmodel import LogViewModel
    from viewmodels.config_viewmodel import ConfigViewModel
    from viewmodels.main_viewmodel import MainViewModel
    
    session_repo = SessionRepository()
    session_browser = SessionBrowser(session_repo)
    log_model = LogModel(log_repository=log_repo, command_bus=command_bus)
    ranging_model = RangingModel(protocol_service, ranging_repo=ranging_repo, command_bus=command_bus)
    device_model = DeviceModel(protocol_service, telemetry_repo=telemetry_repo, ble_scan_repo=ble_scan_repo, command_bus=command_bus)
    
    device_info_vm = DeviceInfoViewModel(device_model, dongle_model, telemetry_repo=telemetry_repo, ble_scan_repo=ble_scan_repo, telemetry_model=telemetry_model)
    session_model = SessionModel()
    session_run_manager = SessionRunManager(session_model, session_repo, device_info_vm=device_info_vm, ranging_model=ranging_model, log_model=log_model)
    log_vm = LogViewModel(session_browser, log_model=log_model, session_run_manager=session_run_manager)
    
    live_tracking_vm = LiveTrackingViewModel(ranging_model, protocol_service, ranging_repo=ranging_repo, command_bus=command_bus, session_run_manager=session_run_manager)
    config_vm = ConfigViewModel(device_model, ranging_model, command_bus=command_bus)
    
    main_vm = MainViewModel(live_tracking_vm=live_tracking_vm, device_info_vm=device_info_vm, log_vm=log_vm, session_repository=session_repo, session_run_manager=session_run_manager)

    device_info_vm.set_connected_device("Mock UWB Tag", "00:11:22:33:44:55")

    window = MainWindow(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm,
        config_vm=config_vm,
        dongle_vm=dongle_vm,
        log_vm=log_vm,
        main_vm=main_vm,
        serial_service=serial_service
    )
    
    # Simulating the UI thread operations
    serial_service.open("COM_MOCK")
    window.show()
    
    QTimer.singleShot(0, device_info_vm.initialize)

    # --- AUTOMATED TEST FLOW TIMELINE ---
    # We will trigger UI actions step-by-step to simulate a user session
    
    def step_get_layout():
        print("\n[Test Scenario] Requesting Anchor Layout...")
        # Simulating request from MainWindow / config
        device_model.request_anchor_layout()
        
    def step_start_ranging():
        print("\n[Test Scenario] User clicks 'Start Ranging' button on Live tab...")
        window.tab_tracking.btn_start.click()
        
    def step_stop_ranging():
        print("\n[Test Scenario] User clicks 'Stop Ranging' button after observing simulation...")
        window.tab_tracking.btn_stop.click()

    def step_exit():
        print("\n[Test Scenario] Test completed. Closing MainWindow...")
        window.close()
        app.quit()

    # Schedule test steps
    QTimer.singleShot(1500, step_get_layout)       # Get anchors layout at 1.5s
    QTimer.singleShot(3500, step_start_ranging)   # Click start ranging at 3.5s
    QTimer.singleShot(11500, step_stop_ranging)   # Click stop ranging at 11.5s
    QTimer.singleShot(13000, step_exit)           # Close app at 13.0s

    sys.exit(app.exec())


# =============================================================================
# MAIN SCRIPT ENTRY
# =============================================================================
if __name__ == "__main__":
    print(f"[MODE] {mode_label()}")
    if TEST_MODE:
        run_mock_gui_test()
    else:
        run_real_device_test()
