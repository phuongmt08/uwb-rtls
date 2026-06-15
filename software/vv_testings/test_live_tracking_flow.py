"""
===============================================================================
  UWB RTLS Studio - Live Tracking Flow Test Script
===============================================================================
  File        : software/vv_testings/test_live_tracking_flow.py
  Description : Integration test script for verifying ranging and fusion stream.
                Supports real device testing (USE_REAL_DEVICE = 1) and
                automated mock GUI visualization testing (USE_REAL_DEVICE = 0).
===============================================================================
"""
from __future__ import annotations
import sys
import os
import time
import math

# Add paths to make sure common and studio packages are importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../uwb_rtls_studio")))

# --- TEST SETTING MACRO ---
# Set to 1 to test with a real dongle and tag connected to PC.
# Set to 0 to run automated GUI mocking tests without hardware.
USE_REAL_DEVICE = 0
# --------------------------

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
# REAL DEVICE TEST FLOW (USE_REAL_DEVICE = 1)
# =============================================================================
def run_real_device_test():
    from vv_test_session import VvTestSession
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
# MOCK GUI DEVICE SIMULATION TEST FLOW (USE_REAL_DEVICE = 0)
# =============================================================================
class MockSerialDevice(QObject):
    """Simulates UWB Tag/MCU device firmware over SerialService signals"""
    def __init__(self, serial_service):
        super().__init__()
        self.serial_service = serial_service
        self.proto = VvProtocol()
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
        packets = self.proto.decode_from_frames(data)
        for pkt in packets:
            param_name = pkt.WhichOneof("params")
            src = pkt.hdr.addr.src
            dst = pkt.hdr.addr.dst
            seq = pkt.hdr.seq
            
            # Verify correct addressing (Host -> MCU)
            if src != int(VvAddress.HOST) or dst != int(VvAddress.MCU):
                print(f"[Mock Device Warning] Bad address headers: src={src}, dst={dst}")
                continue
                
            print(f"[Mock Device] Received Command '{param_name}' (Seq: {seq})")
            
            if param_name == "anchor_layout_get":
                # Respond with 4 anchors
                resp = make_anchor_layout_resp(src=dst, dst=src, seq=seq)
                self.send_to_host(resp)
                print("[Mock Device] Sent 'anchor_layout_resp' to Host")
            elif param_name == "ranging_start":
                # Start simulator timer
                self.ranging_timer.start()
                print("[Mock Device] Ranging started. Streaming live coordinate telemetry...")
            elif param_name == "ranging_stop":
                self.ranging_timer.stop()
                print("[Mock Device] Ranging stopped")

    def send_to_host(self, pkt):
        frame = self.proto.wrap_packet(pkt)
        self.serial_service.data_received.emit(frame)

    def send_mock_data(self):
        """Simulate circular movement on canvas and stream packets"""
        self.angle += 0.05
        cx, cy = 5.38, 6.6
        r = 3.2
        tag_x = cx + r * math.cos(self.angle)
        tag_y = cy + r * math.sin(self.angle)
        tag_z = 1.2
        
        anchors = [
            (1, 0.0, 0.0, 1.5),
            (2, 10.76, 0.0, 1.5),
            (3, 0.0, 13.2, 1.5),
            (4, 10.76, 13.2, 1.5),
        ]
        
        seq = self.seq
        self.seq += 1
        
        # 1. Ranging result packet
        ranging_pkt = self.proto.pb.packet_t()
        ranging_pkt.hdr.addr.src = int(VvAddress.MCU)
        ranging_pkt.hdr.addr.dst = int(VvAddress.HOST)
        ranging_pkt.hdr.seq = seq
        
        res = ranging_pkt.ranging_result
        res.pos_x_m = tag_x
        res.pos_y_m = tag_y
        res.pos_z_m = tag_z
        res.rms_error_m = 0.075
        res.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
        
        for aid, ax, ay, az in anchors:
            dist = math.hypot(tag_x - ax, tag_y - ay)
            a = res.anchors.add()
            a.anchor_id = aid
            a.distance_mm = int(dist * 1000)
            a.fp_amp = 480
            
        self.send_to_host(ranging_pkt)
        
        # 2. Sensor fusion result packet (with slight UKF filtering noise simulated)
        fusion_pkt = self.proto.pb.packet_t()
        fusion_pkt.hdr.addr.src = int(VvAddress.MCU)
        fusion_pkt.hdr.addr.dst = int(VvAddress.HOST)
        fusion_pkt.hdr.seq = seq
        
        fs = fusion_pkt.sensor_fusion_result
        fs.ukf_x_m = tag_x + 0.015 * math.sin(self.angle * 4.0)
        fs.ukf_y_m = tag_y + 0.015 * math.cos(self.angle * 4.0)
        fs.ukf_yaw_deg = math.degrees(self.angle) % 360.0
        fs.tril_x_m = tag_x
        fs.tril_y_m = tag_y
        fs.yaw_deg = (math.degrees(self.angle) + 4.0) % 360.0 # raw yaw
        fs.ranging_error_count = 0
        fs.timestamp_ms = res.timestamp_ms
        
        self.send_to_host(fusion_pkt)


def run_mock_gui_test():
    # 1. Set environment variable to bypass popups (skip Dongle and Scan dialogs)
    os.environ["UWB_RTLS_BYPASS_POPUPS"] = "1"
    print("\n=== STARTING AUTOMATED MOCK GUI TEST ===")
    print("[INFO] Bypassing connection dialog popups. Skipped to MainWindow.")

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
        window.live_tracking_tab.btn_start.click()
        
    def step_stop_ranging():
        print("\n[Test Scenario] User clicks 'Stop Ranging' button after observing simulation...")
        window.live_tracking_tab.btn_stop.click()

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
    # Import QObject and QTimer here to avoid issues when running headless / raw script
    from PyQt6.QtCore import QObject, QTimer
    
    if USE_REAL_DEVICE:
        run_real_device_test()
    else:
        run_mock_gui_test()
