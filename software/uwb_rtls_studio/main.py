"""
==============================================================================
  UWB RTLS Studio - Application Entry Point
==============================================================================
  File        : main.py
  Author      : Trung Quan
  Description : Entry point that boots the whole app.
                Flow: DonglePopup -> ScanPopup -> MainWindow.
                Real hardware mode uses the serial + protobuf backend.

  Wiring (Dependency Injection):
    1. Create Services (singleton): SerialService, ProtocolService
    2. Create ViewModels: DongleViewModel, ScanViewModel
    3. Create Views (popups): DonglePopup(vm), ScanPopup(vm)
    4. Run flow: popup1.exec() -> popup2.exec() -> MainWindow

  Notes:
    - Services are created once and shared across ViewModels.
    - ViewModels receive Services through constructor injection.
    - Views receive ViewModels through constructor binding.
    - main.py is the composition root for app wiring.
==============================================================================
"""
import sys
import os
import logging
import signal


_interrupt_requested = False


def _request_graceful_exit(_signum, _frame):
    global _interrupt_requested
    if _interrupt_requested:
        os._exit(130)
    _interrupt_requested = True


signal.signal(signal.SIGINT, _request_graceful_exit)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, _request_graceful_exit)

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Add parent directory for common module access
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

# Logging setup
from utils.logging_config import setup_logging
setup_logging()

# Initialize raw packet capture files at app startup so the runtime JSONL files
# exist immediately, even before the first packet arrives.
from data.raw_packet_store import shared_raw_packet_store
shared_raw_packet_store.stats()

from utils.runtime_mode import is_test_mode, mock_device_identity, mock_rtos_resource, mock_rtos_task_stats

TEST_MODE = is_test_mode()

import socket
import threading
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

class MockSerialService(QObject):
    """TCP socket bridge acting as a mock serial service for offline/remote debugging."""
    data_received = pyqtSignal(bytes)
    connection_lost = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._open = False
        self._server_socket = None
        self._clients = []
        self._clients_lock = threading.Lock()
        self._listen_thread = None
        self._running = False
        
        # Load simulator dependencies
        from common.transport import VvProtocol
        from common.commands import CommandFactory
        self._proto = VvProtocol()
        self._factory = CommandFactory()
        self._seq = 1
        self._angle = 0.0
        self._ranging_active = False
        self._ranging_thread = None
        self._ranging_lock = threading.Lock()

    @property
    def is_open(self):
        return self._open

    @property
    def port_name(self):
        return "MOCK_TCP_9999"

    def open(self, port: str):
        if self._open:
            return
        self._open = True
        self._running = True
        
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_socket.bind(("127.0.0.1", 9999))
            self._server_socket.listen(5)
            logging.info("Mock TCP Server listening on 127.0.0.1:9999")
        except Exception as e:
            logging.error(f"Failed to bind Mock TCP Server on 9999: {e}")
            self.error_occurred.emit(f"Mock server bind error: {e}")
            self._open = False
            self._running = False
            return
            
        self._listen_thread = threading.Thread(
            target=self._accept_loop,
            name="MockTcpAccept",
            daemon=True
        )
        self._listen_thread.start()

    def _accept_loop(self):
        while self._running:
            try:
                client_sock, addr = self._server_socket.accept()
                logging.info(f"Mock TCP client connected from {addr}")
                with self._clients_lock:
                    self._clients.append(client_sock)
                t = threading.Thread(
                    target=self._client_read_loop,
                    args=(client_sock,),
                    name=f"MockTcpClientRead-{addr}",
                    daemon=True
                )
                t.start()
            except Exception:
                break

    def _client_read_loop(self, client_sock):
        while self._running:
            try:
                data = client_sock.recv(4096)
                if not data:
                    break
                # External simulator bytes should enter the same path as real serial RX.
                self.data_received.emit(data)
            except Exception:
                break
        
        with self._clients_lock:
            if client_sock in self._clients:
                self._clients.remove(client_sock)
        try:
            client_sock.close()
        except Exception:
            pass

    def write(self, data: bytes):
        # Only broadcast command bytes to connected external test clients.
        # No local loopback processing, ensuring fields remain "-" until a test script connects.
        with self._clients_lock:
            dead_clients = []
            for client in self._clients:
                try:
                    client.sendall(data)
                except Exception:
                    dead_clients.append(client)
            for client in dead_clients:
                if client in self._clients:
                    self._clients.remove(client)
                try:
                    client.close()
                except Exception:
                    pass
    # If macro = 1, then the following code block will be included in the final output. If macro = 0, it will be excluded.
    def handle_incoming_packet(self, pkt, client_sock=None):
        param_name = pkt.WhichOneof("params")
        if not param_name:
            return
            
        src = pkt.hdr.addr.src
        dst = pkt.hdr.addr.dst
        seq = pkt.hdr.seq
        
        # Control commands
        if param_name == "ranging_start":
            with self._ranging_lock:
                self._ranging_active = True
                if self._ranging_thread is None or not self._ranging_thread.is_alive():
                    self._ranging_thread = threading.Thread(target=self._ranging_stream_loop, daemon=True)
                    self._ranging_thread.start()
            # Respond with ACK
            ack = self._factory.ack(src=dst, dst=src, seq=seq)
            ack.ack.ack_seq = seq
            ack.ack.response = self._proto.pb.PACKET_ACK_RESPONSE_ACK
            self._send_frame(self._proto.wrap_packet(ack), client_sock)
            return
        elif param_name == "ranging_stop":
            with self._ranging_lock:
                self._ranging_active = False
            # Respond with ACK
            ack = self._factory.ack(src=dst, dst=src, seq=seq)
            ack.ack.ack_seq = seq
            ack.ack.response = self._proto.pb.PACKET_ACK_RESPONSE_ACK
            self._send_frame(self._proto.wrap_packet(ack), client_sock)
            return

        # Query commands mapping
        from services.query_state_machine import QueryQueueManager
        response_name = QueryQueueManager.RESPONSE_MAP.get(param_name)
        
        if response_name:
            resp_method = getattr(self._factory, response_name, None)
            if resp_method:
                resp = resp_method(src=dst, dst=src, seq=seq)
                
                # Populate responses
                if response_name == "device_information_resp":
                    resp.device_information_resp.device_type = self._proto.pb.DEVICE_TYPE_TAG
                    resp.device_information_resp.role = self._proto.pb.DEVICE_ROLE_TAG
                    resp.device_information_resp.fw_version = "mock-device-v1.0"
                    resp.device_information_resp.hw_version = "nRF52840-UWB"
                elif response_name == "anchor_layout_resp":
                    positions = [
                        (1, 0.0, 0.0, 1.5),
                        (2, 10.76, 0.0, 1.5),
                        (3, 0.0, 13.2, 1.5),
                        (4, 10.76, 13.2, 1.5),
                    ]
                    for aid, x, y, z in positions:
                        a = resp.anchor_layout_resp.anchors.add()
                        a.anchor_id = aid
                        a.x_m = x
                        a.y_m = y
                        a.z_m = z
                elif response_name == "battery_info_resp":
                    b = resp.battery_info_resp
                    b.bat_voltage_mv = 3850
                    b.bat_soc_percent = 95
                    b.remaining_min = 480
                    b.is_charging = False
                    b.mcu_temp_c = 28.5
                    b.mcu_voltage_mv = 3300
                    b.uwb_temp_c = 33.0
                    b.uwb_voltage_mv = 3290
                    b.imu_temp_c = 29.0
                    b.error_mask = 0
                elif response_name == "ble_status_resp":
                    resp.ble_status_resp.state = self._proto.pb.BLE_STATE_CONNECTED
                    resp.ble_status_resp.rssi_dbm = -58
                    resp.ble_status_resp.disconnect_reason = 0
                elif response_name == "ble_conn_params_resp":
                    cp = resp.ble_conn_params_resp
                    cp.min_interval_ms = 15
                    cp.max_interval_ms = 30
                    cp.slave_latency = 0
                    cp.sup_timeout_ms = 2000
                    cp.phy = 1
                elif response_name == "rtos_resource_resp":
                    res_data = mock_rtos_resource()
                    resp.rtos_resource_resp.sample_window_ms = res_data["sample_window_ms"]
                    resp.rtos_resource_resp.cpu_busy_permille = res_data["cpu_busy_permille"]
                    resp.rtos_resource_resp.heap_free_bytes = res_data["heap_free_bytes"]
                    resp.rtos_resource_resp.heap_min_ever_free_bytes = res_data["heap_min_ever_free_bytes"]
                    resp.rtos_resource_resp.min_stack_free_bytes = res_data["min_stack_free_bytes"]
                    resp.rtos_resource_resp.min_stack_task_id = res_data["min_stack_task_id"]
                    resp.rtos_resource_resp.task_count = res_data["task_count"]
                    resp.rtos_resource_resp.health_flags = res_data["health_flags"]
                elif response_name == "rtos_task_stats_resp":
                    for t in mock_rtos_task_stats():
                        item = resp.rtos_task_stats_resp.tasks.add()
                        item.task_id = t["task_id"]
                        item.cpu_permille = t["cpu_permille"]
                        item.stack_min_free_bytes = t["stack_min_free_bytes"]
                        item.name = t["name"]

                self._send_frame(self._proto.wrap_packet(resp), client_sock)
                return

        # Generic ACK response fallback
        ack = self._factory.ack(src=dst, dst=src, seq=seq)
        ack.ack.ack_seq = seq
        ack.ack.response = self._proto.pb.PACKET_ACK_RESPONSE_ACK
        self._send_frame(self._proto.wrap_packet(ack), client_sock)

    def _send_frame(self, frame: bytes, client_sock=None):
        # Update the UI
        self.data_received.emit(frame)
        # Send to the specific client if active
        if client_sock:
            try:
                client_sock.sendall(frame)
            except Exception:
                pass

    def _ranging_stream_loop(self):
        import math
        import time
        from common.transport import VvAddress
        
        while self._running:
            with self._ranging_lock:
                if not self._ranging_active:
                    break
            
            self._angle += 0.04
            t = self._angle
            
            # Figure-8 Lissajous path (fits 10.76 x 13.2 canvas)
            cx, cy = 5.38, 6.6
            Ax, Ay = 4.2, 5.0
            
            tag_x = cx + Ax * math.sin(2 * t)
            tag_y = cy + Ay * math.sin(t)
            tag_z = 1.2
            
            dx = 2.0 * Ax * math.cos(2 * t)
            dy = Ay * math.cos(t)
            psi_deg = math.degrees(math.atan2(dy, dx)) % 360.0
            
            seq = self._seq
            self._seq += 1

            # 1. Ranging result
            ranging_pkt = self._proto.pb.packet_t()
            ranging_pkt.hdr.addr.src = int(VvAddress.MCU)
            ranging_pkt.hdr.addr.dst = int(VvAddress.HOST)
            ranging_pkt.hdr.seq = seq
            
            res = ranging_pkt.ranging_result
            res.pos_x_m = tag_x + 0.15 * math.sin(t * 12.0)
            res.pos_y_m = tag_y + 0.15 * math.cos(t * 10.0)
            res.pos_z_m = tag_z
            res.rms_error_m = 0.180
            res.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
            
            # Populate distances to 4 anchors
            anchors = [(1, 0.0, 0.0, 1.5), (2, 10.76, 0.0, 1.5), (3, 0.0, 13.2, 1.5), (4, 10.76, 13.2, 1.5)]
            for aid, ax, ay, az in anchors:
                dist = math.hypot(tag_x - ax, tag_y - ay)
                a_item = res.anchors.add()
                a_item.anchor_id = aid
                a_item.distance_mm = int(dist * 1000)
                a_item.fp_amp = 500

            frame_ranging = self._proto.wrap_packet(ranging_pkt)

            # 2. Sensor fusion result
            fusion_pkt = self._proto.pb.packet_t()
            fusion_pkt.hdr.addr.src = int(VvAddress.MCU)
            fusion_pkt.hdr.addr.dst = int(VvAddress.HOST)
            fusion_pkt.hdr.seq = seq
            
            fs = fusion_pkt.sensor_fusion_result
            fs.ukf_x_m = tag_x
            fs.ukf_y_m = tag_y
            fs.ukf_yaw_deg = psi_deg
            fs.tril_x_m = res.pos_x_m
            fs.tril_y_m = res.pos_y_m
            fs.yaw_deg = psi_deg
            fs.ranging_error_count = 0
            fs.timestamp_ms = res.timestamp_ms
            
            frame_fusion = self._proto.wrap_packet(fusion_pkt)
            
            # Send frames to UI
            self._send_frame(frame_ranging)
            self._send_frame(frame_fusion)
            
            # Broadcast frames to all connected TCP clients (e.g. test scripts)
            with self._clients_lock:
                for client in self._clients:
                    try:
                        client.sendall(frame_ranging)
                        client.sendall(frame_fusion)
                    except Exception:
                        pass
                        
            time.sleep(0.1)  # 10 Hz

    def close(self):
        if not self._open:
            return
        self._running = False
        self._open = False
        with self._ranging_lock:
            self._ranging_active = False
        
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
            
        with self._clients_lock:
            for client in self._clients:
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()
            
        logging.info("Mock TCP Server closed")

def main():
    # High DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    app = QApplication(sys.argv)

    interrupt_state = {"handled": False, "callback": lambda: app.quit()}
    interrupt_timer = QTimer()
    interrupt_timer.setInterval(100)

    def _process_pending_interrupt():
        global _interrupt_requested
        if not _interrupt_requested or interrupt_state["handled"]:
            return
        interrupt_state["handled"] = True
        try:
            interrupt_state["callback"]()
        except Exception:
            app.quit()

    interrupt_timer.timeout.connect(_process_pending_interrupt)
    interrupt_timer.start()

    # Apply global stylesheet (dark theme & custom scrollbars)
    from utils.theme import DARK_STYLESHEET
    app.setStyleSheet(DARK_STYLESHEET)

    # Apply global font
    font = QFont("Segoe UI", 13)
    app.setFont(font)

    # ------------------------------------------------------------
    # STEP 1: Create Services (singleton, shared)
    # ------------------------------------------------------------
    from services.serial_service import SerialService
    from services.protocol_service import ProtocolService

    serial_service = MockSerialService() if TEST_MODE else SerialService()
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
        ranging_repo,
        telemetry_repo,
        ble_scan_repo,
        config_repo,
        diagnostics_repo,
        log_repo,
    )
    protocol_service.set_packet_repository(packet_repo)
    command_bus = init_shared_command_bus(protocol_service)

    # Initialize global query manager in shared app state
    from utils.app_state import shared_app_state
    shared_app_state.init_query_manager(
        send_packet_fn=lambda cmd, dst, **kwargs: command_bus.send(cmd, dst_addr=dst, **kwargs)
    )

    # STEP 2 & 3: Connection Flow
    # ------------------------------------------------------------
    from models.dongle_model import DongleModel
    from viewmodels.dongle_viewmodel import DongleViewModel
    from views.popups.dongle_popup import DonglePopup

    from models.scan_model import ScanModel
    from viewmodels.scan_viewmodel import ScanViewModel
    from views.popups.scan_popup import ScanPopup
    dongle_model = DongleModel(serial_service, protocol_service)
    dongle_vm = DongleViewModel(dongle_model)
    # Connection loop
    connected_name = ""
    connected_mac = ""
    initial_scan_devices = []

    if TEST_MODE:
        connected_name, connected_mac = mock_device_identity()
        initial_scan_devices = [{"name": connected_name, "mac": connected_mac, "rssi": 0, "serial": "", "order": 0}]
    else:
        app_should_exit = False
        while True:
            dongle_popup = DonglePopup(dongle_vm)
            if dongle_popup.exec() != 1:  # 1 = QDialog.DialogCode.Accepted
                app_should_exit = True
                break

            # Dongle ok -> Scan popup
            scan_model = ScanModel(protocol_service, serial_service, command_bus=command_bus, ble_scan_repo=ble_scan_repo)
            scan_vm = ScanViewModel(scan_model)
            scan_popup = ScanPopup(scan_vm)

            res = scan_popup.exec()
            if res == 1:
                # Extract connected device info from scan_model before cleanup
                connected_mac = scan_model.connected_mac
                initial_scan_devices = [dict(dev) for dev in sorted(scan_model._devices.values(), key=lambda d: d.get("order", 0))]
                if connected_mac and connected_mac in scan_model._devices:
                    dev = scan_model._devices[connected_mac]
                    connected_name = dev.get("name", "")

                # Disconnect scan_model from protocol to avoid duplicate handlers
                scan_model.cleanup()
                try:
                    protocol_service.packet_received.disconnect(scan_model._on_packet)
                except TypeError:
                    pass
                break
            elif res == 2:
                # Dongle disconnected during scan -> return to dongle popup and retry from the start
                continue
            else:
                app_should_exit = True
                break

        if app_should_exit:
            protocol_service.close()
            serial_service.close()
            shared_raw_packet_store.close()
            os._exit(0)

    # STEP 4: Main Window
    # ------------------------------------------------------------
    from views.windows.main_window import MainWindow
    from models.ranging_model import RangingModel
    from models.device_model import DeviceModel
    from models.session_model import SessionModel
    from viewmodels.live_tracking_viewmodel import LiveTrackingViewModel
    from viewmodels.device_info_viewmodel import DeviceInfoViewModel
    from repository.session_repository import SessionRepository
    from repository.session_browser import SessionBrowser
    from services.session_run_manager import SessionRunManager
    from services.session_message_recorder import SessionMessageRecorder
    from models.log_model import LogModel
    from viewmodels.log_viewmodel import LogViewModel
    from viewmodels.config_viewmodel import ConfigViewModel
    from viewmodels.calibration_viewmodel import CalibrationViewModel
    from viewmodels.main_viewmodel import MainViewModel
    
    session_repo = SessionRepository()
    session_browser = SessionBrowser(session_repo)
    log_model = LogModel(log_repository=log_repo, command_bus=command_bus)

    ranging_model = RangingModel(protocol_service, ranging_repo=ranging_repo, command_bus=command_bus)
    device_model = DeviceModel(
        protocol_service,
        telemetry_repo=telemetry_repo,
        ble_scan_repo=ble_scan_repo,
        config_repo=config_repo,
        command_bus=command_bus,
    )
    device_info_vm = DeviceInfoViewModel(
        device_model,
        dongle_model,
        telemetry_repo=telemetry_repo,
        ble_scan_repo=ble_scan_repo,
        telemetry_model=telemetry_model,
    )
    session_model = SessionModel()
    session_run_manager = SessionRunManager(
        session_model,
        session_repo,
        device_info_vm=device_info_vm,
        ranging_model=ranging_model,
        log_model=log_model,
    )
    session_message_recorder = SessionMessageRecorder(protocol_service, session_repo, session_model)
    log_vm = LogViewModel(session_browser, log_model=log_model, session_run_manager=session_run_manager)
    live_tracking_vm = LiveTrackingViewModel(
        ranging_model,
        protocol_service,
        ranging_repo=ranging_repo,
        command_bus=command_bus,
        session_run_manager=session_run_manager,
        ble_scan_repo=ble_scan_repo,
    )
    config_vm = ConfigViewModel(
        device_model,
        ranging_model,
        command_bus=command_bus,
        ble_scan_repo=ble_scan_repo,
    )
    calibration_vm = CalibrationViewModel(device_model)
    main_vm = MainViewModel(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm,
        log_vm=log_vm,
        session_repository=session_repo,
        session_run_manager=session_run_manager,
    )

    # Keep connected identity in the model for command/session routing only.
    # Device-data fields stay blank until a real protocol response is parsed.

    if TEST_MODE:
        serial_service.open("COM_MOCK")

    window = MainWindow(
        live_tracking_vm=live_tracking_vm,
        device_info_vm=device_info_vm,
        config_vm=config_vm,
        calibration_vm=calibration_vm,
        dongle_vm=dongle_vm,
        log_vm=log_vm,
        main_vm=main_vm,
        serial_service=serial_service,
        protocol_service=protocol_service,
        command_bus=command_bus,
    )
    interrupt_state["callback"] = window.request_interrupt_shutdown
    if connected_name and connected_mac:
        device_info_vm.set_connected_device(connected_name, connected_mac, initial_scan_devices)




    window.showMaximized()
    # Initialize device data after UI is fully ready
    # QTimer.singleShot(0) defers to the next event loop iteration,
    # ensuring all Qt signals are wired before we request telemetry.
    QTimer.singleShot(0, device_info_vm.initialize)

    exit_code = app.exec()

    # Cleanup
    session_message_recorder.close()
    protocol_service.close()
    serial_service.close()
    shared_raw_packet_store.close()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
