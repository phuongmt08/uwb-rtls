"""
===============================================================================
  UWB RTLS Studio — Virtual Device Peripheral Simulator
===============================================================================
  File        : test/test_device_peripheral.py
  Description : Script giả lập thiết bị ngoại vi (Device Peripheral).
                - Nhận và phản hồi các lệnh lấy telemetry từ Host qua COM ảo.
                - Giả lập quét BLE (gửi scan results khi Host bắt đầu quét).
                - Giả lập kết nối BLE (chuyển trạng thái khi Host yêu cầu connect).
                - Giả lập gửi tọa độ Tag chuyển động tròn (Ranging Results & Sensor Fusion).
===============================================================================
"""
from __future__ import annotations

import os
import sys
import time
import math
import random
import threading
import logging

import serial

# Thiết lập sys.path để import đúng các service và module common
current_dir = os.path.dirname(os.path.abspath(__file__))
studio_dir = os.path.dirname(current_dir)
software_dir = os.path.dirname(studio_dir)

if studio_dir not in sys.path:
    sys.path.insert(0, studio_dir)
if software_dir not in sys.path:
    sys.path.insert(0, software_dir)

from common.transport import VvProtocol, VvAddress
from common.commands import CommandFactory
from common import protocol_pb2 as pb

# Thiết lập Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("device_peripheral")

# ===============================================================================
# CONFIGURATION FOR SIMULATOR
# ===============================================================================
VIRTUAL_COM_PORT = "TCP"  # Cổng COM ảo phía Peripheral (Host dùng COM11) hoặc "TCP"
# ===============================================================================

# Global State
is_running = True
ble_state = pb.BLE_STATE_IDLE
ranging_active = False
angle = 0.0

# Mock Anchors Layout (có thể được cập nhật qua lệnh anchor_layout_set)
anchors = [
    {"anchor_id": 0, "x_m": 0.0, "y_m": 0.0, "z_m": 1.2},
    {"anchor_id": 1, "x_m": 5.0, "y_m": 0.0, "z_m": 1.5},
    {"anchor_id": 2, "x_m": 0.0, "y_m": 4.0, "z_m": 1.5},
    {"anchor_id": 3, "x_m": 5.0, "y_m": 4.0, "z_m": 1.8},
]

# Serial resources
ser: serial.Serial | None = None
ser_lock = threading.Lock()
proto = VvProtocol()
commands = CommandFactory()


def _fixed2(value: float) -> int:
    return int(round(value * 100.0))


def send_to_host(pkt: pb.packet_t) -> None:
    """Gói gói tin protobuf vào khung HDLC và ghi xuống cổng Serial."""
    global ser
    frame = proto.wrap_packet(pkt)
    param_name = pkt.WhichOneof("params") or "unknown"
    src_name = VvAddress(pkt.hdr.addr.src).name if pkt.hdr.addr.src in VvAddress.__members__.values() else str(pkt.hdr.addr.src)
    dst_name = VvAddress(pkt.hdr.addr.dst).name if pkt.hdr.addr.dst in VvAddress.__members__.values() else str(pkt.hdr.addr.dst)
    
    with ser_lock:
        if ser and ser.is_open:
            ser.write(frame)
            ser.flush()
            log.debug(f"[TX] Send '{param_name}' from {src_name} -> {dst_name} (seq={pkt.hdr.seq})")


def handle_host_packet(pkt: pb.packet_t) -> None:
    """Xử lý gói tin nhận từ Host và gửi phản hồi tương ứng."""
    global ble_state, ranging_active, anchors
    
    param_name = pkt.WhichOneof("params")
    if not param_name:
        return
        
    src_addr = pkt.hdr.addr.src
    dst_addr = pkt.hdr.addr.dst
    req_seq = pkt.hdr.seq
    
    src_name = VvAddress(src_addr).name if src_addr in VvAddress.__members__.values() else str(src_addr)
    dst_name = VvAddress(dst_addr).name if dst_addr in VvAddress.__members__.values() else str(dst_addr)
    
    log.info(f"[RX] Received '{param_name}' from {src_name} to {dst_name} (seq={req_seq})")
    
    # ──── 1. Xử lý các lệnh MCU Telemetry (Host -> MCU) ────
    if dst_addr == pb.PACKET_ADDR_MCU:
        if param_name == "device_information_get":
            resp = commands.device_information_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            # Customizing fields
            resp.device_information_resp.device_type = pb.DEVICE_TYPE_TAG
            resp.device_information_resp.role = pb.DEVICE_ROLE_TAG
            resp.device_information_resp.fw_version.major = 1
            resp.device_information_resp.fw_version.minor = 3
            resp.device_information_resp.fw_version.patch = 0
            resp.device_information_resp.hw_version = 2
            resp.device_information_resp.serial_number = 98765
            resp.device_information_resp.uid = b"\xde\xad\xbe\xef\x00\x00\x00\x01"
            send_to_host(resp)
            
        elif param_name == "battery_info_get":
            resp = commands.battery_info_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            # Customizing battery and sensors
            resp.battery_info_resp.bat_voltage_mv = 3820
            resp.battery_info_resp.bat_soc_percent = 92
            resp.battery_info_resp.remaining_min = 360
            resp.battery_info_resp.is_charging = False
            resp.battery_info_resp.mcu_temp_c = 27.5
            resp.battery_info_resp.mcu_voltage_mv = 3300
            resp.battery_info_resp.uwb_temp_c = 32.0
            resp.battery_info_resp.uwb_voltage_mv = 3290
            resp.battery_info_resp.imu_temp_c = 28.0
            resp.battery_info_resp.error_mask = 0
            send_to_host(resp)
            
        elif param_name == "time_sync_get":
            resp = commands.time_sync_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            resp.time_sync_resp.unix_time_ms = int(time.time() * 1000)
            resp.time_sync_resp.timezone_offset = 7 * 60  # ICT (UTC+7)
            send_to_host(resp)
            
        elif param_name == "anchor_layout_get":
            resp = commands.anchor_layout_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            del resp.anchor_layout_resp.anchors[:]
            for a in anchors:
                item = resp.anchor_layout_resp.anchors.add()
                item.anchor_id = a["anchor_id"]
                item.x_m = a["x_m"]
                item.y_m = a["y_m"]
                item.z_m = a["z_m"]
            send_to_host(resp)
            
        elif param_name == "anchor_layout_set":
            # Cập nhật layout mới nhận được từ Host
            new_anchors = []
            for item in pkt.anchor_layout_set.anchors:
                new_anchors.append({
                    "anchor_id": item.anchor_id,
                    "x_m": item.x_m,
                    "y_m": item.y_m,
                    "z_m": item.z_m
                })
            if new_anchors:
                anchors = new_anchors
                log.info(f"⚓ Updated anchor layout: {anchors}")
            
            # Gửi ACK phản hồi
            ack = commands.ack(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            ack.ack.ack_seq = req_seq
            ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
            send_to_host(ack)
            
        elif param_name == "sys_config_get":
            resp = commands.sys_config_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            cfg = resp.sys_config_resp.config
            cfg.role = pb.DEVICE_ROLE_TAG
            cfg.device_id = 98765
            cfg.ranging_period_ms = 200
            cfg.rx_timeout_ms = 150
            cfg.uwb_channel = 5
            cfg.uwb_prf = 64
            cfg.uwb_data_rate = 6800
            cfg.uwb_preamble_code = 9
            cfg.tx_antenna_delay = 16384
            cfg.rx_antenna_delay = 16384
            cfg.tx_power = 0
            send_to_host(resp)
            
        elif param_name == "sys_ranging_cfg_get":
            resp = commands.sys_ranging_cfg_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            resp.sys_ranging_cfg_resp.config.rx_timeout_ms = 150
            resp.sys_ranging_cfg_resp.config.ranging_period_ms = 200
            send_to_host(resp)
            
        elif param_name == "sensor_fusion_cfg_get":
            resp = commands.sensor_fusion_cfg_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            cfg = resp.sensor_fusion_cfg_resp.config
            cfg.alpha = 0.001
            cfg.kappa = 0.0
            cfg.beta = 2.0
            cfg.q_a = 0.1
            cfg.q_g = 0.01
            cfg.r_uwb = 0.1
            send_to_host(resp)
            
        elif param_name == "pos_calib_cfg_get":
            resp = commands.pos_calib_cfg_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            cfg = resp.pos_calib_cfg_resp.config
            cfg.enable_anchor_auto_calib = False
            cfg.enable_tag_auto_calib = False
            cfg.ref_distance_xy_m = 2.0
            cfg.tag_height_m = 1.0
            cfg.anchor_height_m = 2.5
            cfg.calib_anchor_id = 0
            send_to_host(resp)
            
        elif param_name == "ranging_status_get":
            resp = commands.ranging_status_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            resp.ranging_status_resp.ranging_period_ms = 200
            resp.ranging_status_resp.ranging_total_count = 1000
            resp.ranging_status_resp.ranging_success_count = 990
            resp.ranging_status_resp.ranging_failed_count = 5
            resp.ranging_status_resp.ranging_timeout_count = 5
            resp.ranging_status_resp.last_ranging_time_ms = 10
            resp.ranging_status_resp.last_rms_error_m = 0.05
            resp.ranging_status_resp.last_avg_rssi_dbm = -65
            resp.ranging_status_resp.last_update_timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
            send_to_host(resp)
            
        elif param_name == "calib_status_get":
            # Note: There isn't a pre-built factory command for calib_status_resp, so we build it manually or use a base if needed.
            # But earlier we saw `commands.calib_status_get` in catalog, is there a `calib_status_resp` builder? 
            # If not, we just build it manually using protocol base. Let's use `_base` via CommandFactory.
            resp = commands._base(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            resp.calib_status_resp.state = pb.CALIB_STATE_IDLE
            resp.calib_status_resp.progress_percent = 100
            resp.calib_status_resp.current_iteration = 0
            resp.calib_status_resp.total_iterations = 0
            resp.calib_status_resp.last_pair_error_mean_m = 0.0
            resp.calib_status_resp.current_antenna_delay = 16384
            resp.calib_status_resp.peer_ready_mask = 0
            resp.calib_status_resp.last_pair_error_spread_m = 0.0
            resp.calib_status_resp.rejected_batch_count = 0
            resp.calib_status_resp.last_pair_error_rms_m = 0.0
            resp.calib_status_resp.last_pair_error_max_abs_m = 0.0
            resp.calib_status_resp.last_pair_error_mean_abs_m = 0.0
            send_to_host(resp)
            
        elif param_name == "ranging_start":
            ranging_active = True
            log.info("▶ Started Ranging simulation loop")
            # Gửi ACK
            ack = commands.ack(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            ack.ack.ack_seq = req_seq
            ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
            send_to_host(ack)
            
        elif param_name == "ranging_stop":
            ranging_active = False
            log.info("■ Stopped Ranging simulation loop")
            # Gửi ACK
            ack = commands.ack(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, req_seq)
            ack.ack.ack_seq = req_seq
            ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
            send_to_host(ack)

    # ──── 2. Xử lý các lệnh BLE Link / Control (Host -> Central) ────
    elif dst_addr == pb.PACKET_ADDR_CENTRAL:
        if param_name == "ble_status_get":
            resp = commands.ble_status_resp(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, req_seq)
            resp.ble_status_resp.state = ble_state
            resp.ble_status_resp.rssi_dbm = -65 if ble_state == pb.BLE_STATE_CONNECTED else 0
            resp.ble_status_resp.disconnect_reason = 0
            send_to_host(resp)
            
        elif param_name == "ble_conn_params_get":
            resp = commands.ble_conn_params_resp(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, req_seq)
            resp.ble_conn_params_resp.params.min_interval_ms = 15
            resp.ble_conn_params_resp.params.max_interval_ms = 30
            resp.ble_conn_params_resp.params.slave_latency = 0
            resp.ble_conn_params_resp.params.sup_timeout_ms = 4000
            send_to_host(resp)
            
        elif param_name == "ble_scan_start":
            ble_state = pb.BLE_STATE_SCANNING
            log.info("🔎 Dongle starts scanning...")
            # Gửi ACK
            ack = commands.ack(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, req_seq)
            ack.ack.ack_seq = req_seq
            ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
            send_to_host(ack)
            
        elif param_name == "ble_scan_stop":
            ble_state = pb.BLE_STATE_IDLE
            log.info("🔎 Dongle stops scanning.")
            # Gửi ACK
            ack = commands.ack(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, req_seq)
            ack.ack.ack_seq = req_seq
            ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
            send_to_host(ack)
            
        elif param_name == "ble_connect":
            mac_str = ":".join(f"{b:02X}" for b in pkt.ble_connect.mac_address)
            log.info(f"🔗 Connecting to device MAC: {mac_str}...")
            
            # Giả lập kết nối thành công sau 0.5s bằng cách gửi trạng thái CONNECTED
            def trigger_connect():
                global ble_state
                time.sleep(0.5)
                ble_state = pb.BLE_STATE_CONNECTED
                log.info(f"🎉 Connected successfully to {mac_str}!")
                
                # Gửi thông báo cập nhật trạng thái ble_status_resp (Central -> Host)
                evt = commands.ble_status_resp(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, 0)
                evt.ble_status_resp.state = pb.BLE_STATE_CONNECTED
                evt.ble_status_resp.rssi_dbm = -60
                evt.ble_status_resp.disconnect_reason = 0
                send_to_host(evt)
                
            threading.Thread(target=trigger_connect, daemon=True).start()
            
        elif param_name == "ble_disconnect":
            log.info("🔌 Disconnecting device...")
            ble_state = pb.BLE_STATE_IDLE
            ranging_active = False
            
            resp = commands.ble_status_resp(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, req_seq)
            resp.ble_status_resp.state = pb.BLE_STATE_IDLE
            resp.ble_status_resp.rssi_dbm = 0
            resp.ble_status_resp.disconnect_reason = 0
            send_to_host(resp)


def rx_loop() -> None:
    """Vòng lặp đọc dữ liệu liên tục từ cổng Serial và giải mã HDLC."""
    global is_running, ser
    decoder = type(proto.hdlc)()
    
    while is_running:
        try:
            if ser and ser.is_open and ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                chunks = decoder.feed(data)
                for chunk in chunks:
                    if chunk.frame_type != 0:  # FRAME_TYPE_PROTOBUF
                        continue
                    try:
                        pkt = proto.decode_packet(chunk.payload)
                        handle_host_packet(pkt)
                    except Exception as e:
                        log.error(f"Error parsing protobuf packet: {e}")
            else:
                time.sleep(0.01)
        except Exception as e:
            log.error(f"Serial read error or disconnected: {e}")
            break


def scan_stream_loop() -> None:
    """Thread gửi kết quả quét BLE (ble_scan_result) khi thiết bị đang ở trạng thái SCANNING."""
    global ble_state, is_running
    
    mock_devices = [
        {"mac": b"\xAA\xBB\xCC\xDD\xEE\x11", "name": "UWB-Tag-EE11", "rssi": -55, "serial": 10001},
        {"mac": b"\xAA\xBB\xCC\xDD\xEE\x22", "name": "UWB-Anchor-EE22", "rssi": -65, "serial": 10002},
        {"mac": b"\xAA\xBB\xCC\xDD\xEE\x33", "name": "UWB-Anchor-EE33", "rssi": -72, "serial": 10003},
    ]
    seq = 0
    
    while is_running:
        if ble_state == pb.BLE_STATE_SCANNING:
            seq = (seq + 1) & 0xFFFFFFFF
            # Gửi ngẫu nhiên 1 trong các thiết bị giả lập
            dev = random.choice(mock_devices)
            pkt = commands.ble_scan_result(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, seq)
            pkt.ble_scan_result.mac_address = dev["mac"]
            pkt.ble_scan_result.rssi_dbm = dev["rssi"] + random.randint(-4, 4)
            pkt.ble_scan_result.name = dev["name"]
            pkt.ble_scan_result.serial_number = dev["serial"]
            
            send_to_host(pkt)
        time.sleep(0.5)  # Gửi với tần suất 2Hz (mỗi 500ms)


def ranging_stream_loop() -> None:
    """Thread gửi dữ liệu ranging (ranging_result, sensor_fusion_result) liên tục khi đang bật Ranging."""
    global ranging_active, ble_state, angle, anchors, is_running
    seq = 0
    
    while is_running:
        # Chỉ stream data khi thiết bị đã được kết nối BLE và Host đã bật lệnh Ranging
        if ranging_active and ble_state == pb.BLE_STATE_CONNECTED:
            seq = (seq + 1) & 0xFFFFFFFF
            
            # Cập nhật góc quay để giả lập chuyển động tròn của Tag
            angle += 0.05
            if angle > 2 * math.pi:
                angle -= 2 * math.pi
                
            # Giả lập tọa độ Tag (chạy quanh tâm [2.5, 2.0] với bán kính 1.5m)
            tag_x = 2.5 + 1.5 * math.cos(angle)
            tag_y = 2.0 + 1.5 * math.sin(angle)
            tag_z = 1.0 + 0.15 * math.sin(2 * angle)  # Biến thiên chiều cao Z nhẹ nhàng
            rms_error = 0.015 + random.uniform(0.0, 0.01)
            
            # 1. Tạo gói tin ranging_result
            pkt_rr = commands.ranging_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, seq)
            pkt_rr.ranging_result.pos_x_m = tag_x
            pkt_rr.ranging_result.pos_y_m = tag_y
            pkt_rr.ranging_result.pos_z_m = tag_z
            pkt_rr.ranging_result.rms_error_m = rms_error
            pkt_rr.ranging_result.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
            
            # Tính khoảng cách hình học từ Tag đến các Anchor để đưa vào mảng anchors
            del pkt_rr.ranging_result.anchors[:]
            for a in anchors:
                dist = math.sqrt((tag_x - a["x_m"])**2 + (tag_y - a["y_m"])**2 + (tag_z - a["z_m"])**2)
                dist_mm = int(dist * 1000)
                
                a_ranging = pkt_rr.ranging_result.anchors.add()
                a_ranging.anchor_id = a["anchor_id"]
                a_ranging.distance_mm = dist_mm
                a_ranging.fp_amp = int(500 + random.uniform(-12, 12))  # First Path Amplitude giả lập
                
            send_to_host(pkt_rr)
            
            # 2. Tạo gói tin sensor_fusion_result (để Dashboard vẽ thêm dòng dự đoán lọc Kalman/Fusion)
            pkt_sf = commands.sensor_fusion_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, seq)
            pkt_sf.sensor_fusion_result.ukf_x_m = _fixed2(tag_x)
            pkt_sf.sensor_fusion_result.ukf_y_m = _fixed2(tag_y)
            pkt_sf.sensor_fusion_result.ukf_yaw_deg = _fixed2(math.degrees(angle))
            
            # Thêm chút nhiễu trắng cho tọa độ thô (Trilateration) để phân biệt với tọa độ mượt của UKF
            pkt_sf.sensor_fusion_result.tril_x_m = _fixed2(tag_x + random.uniform(-0.05, 0.05))
            pkt_sf.sensor_fusion_result.tril_y_m = _fixed2(tag_y + random.uniform(-0.05, 0.05))
            pkt_sf.sensor_fusion_result.yaw_deg = _fixed2(math.degrees(angle))
            pkt_sf.sensor_fusion_result.ranging_error_count = 0
            pkt_sf.sensor_fusion_result.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
            
            send_to_host(pkt_sf)
            
        time.sleep(0.1)  # Gửi dữ liệu ở tần số 10Hz


def main() -> None:
    global is_running, ser
    
    print("==================================================================")
    print("   UWB RTLS STUDIO - VIRTUAL DEVICE PERIPHERAL SIMULATOR          ")
    print("==================================================================")
    
    # --- TCP SERIAL ADAPTER INLINE ---
    class TcpSerialAdapter:
        def __init__(self, is_server=False, host='127.0.0.1', port=9999):
            import socket, select
            self.is_open = False
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.conn = None
            self._select = select.select
            
            if is_server:
                self.sock.bind((host, port))
                self.sock.listen(1)
                print(f"🔌 [TCP Mode] Waiting for Host connection on {host}:{port}...")
                self.conn, addr = self.sock.accept()
                print(f"✅ [TCP Mode] Connected by Host at {addr}")
                self.conn.setblocking(False)
                self.is_open = True
            else:
                self.sock.connect((host, port))
                self.conn = self.sock
                self.conn.setblocking(False)
                self.is_open = True

        def write(self, data):
            if self.is_open and self.conn:
                try: self.conn.sendall(data)
                except Exception: self.is_open = False

        def flush(self): pass

        @property
        def in_waiting(self):
            if not self.is_open or not self.conn: return 0
            r, _, _ = self._select([self.conn], [], [], 0)
            return 4096 if r else 0

        def read(self, size=1):
            if not self.is_open or not self.conn: return b""
            try:
                data = self.conn.recv(size)
                if not data: self.is_open = False
                return data
            except BlockingIOError: return b""
            except Exception:
                self.is_open = False
                return b""

        def reset_input_buffer(self): pass
        def reset_output_buffer(self): pass
        def close(self):
            self.is_open = False
            if self.conn:
                try: self.conn.close()
                except: pass
            if self.sock and self.conn != self.sock:
                try: self.sock.close()
                except: pass

    try:
        if VIRTUAL_COM_PORT.upper().startswith("COM"):
            print(f"🔌 Opening VIRTUAL COM PORT: {VIRTUAL_COM_PORT}...")
            ser = serial.Serial(port=VIRTUAL_COM_PORT, baudrate=115200, timeout=1.0)
        else:
            # Dùng TCP Adapter nếu VIRTUAL_COM_PORT được gán là "TCP"
            ser = TcpSerialAdapter(is_server=True, host='127.0.0.1', port=9999)
        
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception as e:
        print(f"❌ ERROR: Không thể mở kết nối. Lỗi: {e}")
        sys.exit(1)
        
    print("\nStatus: WAITING FOR HOST COMMANDS... Press Ctrl + C to exit.")
    
    # Khởi chạy các luồng xử lý
    threading.Thread(target=rx_loop, daemon=True).start()
    threading.Thread(target=scan_stream_loop, daemon=True).start()
    threading.Thread(target=ranging_stream_loop, daemon=True).start()
    
    try:
        while is_running:
            # Gửi thông tin trạng thái quảng bá BLE của thiết bị mỗi 3 giây ngầm định
            if ble_state == pb.BLE_STATE_CONNECTED:
                # Cập nhật thông tin ble_adv_status định kỳ (Central -> Host)
                adv_seq = random.randint(1, 100000)
                pkt = commands.ble_adv_status(pb.PACKET_ADDR_CENTRAL, pb.PACKET_ADDR_HOST, adv_seq)
                pkt.ble_adv_status.device = pb.DEVICE_TYPE_TAG
                pkt.ble_adv_status.device_id = 98765
                pkt.ble_adv_status.serial_number = 10001
                pkt.ble_adv_status.bat_soc_percent = 92
                pkt.ble_adv_status.status_flags = 0
                pkt.ble_adv_status.warning_count = 0
                pkt.ble_adv_status.error_count = 0
                pkt.ble_adv_status.local_timestamp_s = int(time.time()) & 0xFFFFFFFF
                send_to_host(pkt)
            time.sleep(3.0)
    except KeyboardInterrupt:
        print("\nStopping peripheral simulator...")
    finally:
        is_running = False
        if ser:
            try:
                ser.close()
                print("Closed serial connection.")
            except Exception:
                pass
        print("Finished.")


if __name__ == "__main__":
    main()
