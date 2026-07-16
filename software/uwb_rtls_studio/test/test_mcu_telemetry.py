"""
===============================================================================
  UWB RTLS Studio — MCU Telemetry & BLE Status Test Utility
===============================================================================
  File        : test/test_mcu_telemetry.py
  Description : Script kiểm tra giao tiếp protobuf từ Host tới MCU và Central.
                - Host <-> MCU: Lấy dữ liệu telemetry (battery, dev info, config).
                - Host <-> Central: Kiểm tra BLE status và Connection Parameters.
                
  Chú ý: Vui lòng đóng ứng dụng chính (UWB RTLS Studio) trước khi chạy script 
         để giải phóng cổng COM của Dongle.
===============================================================================
"""
from __future__ import annotations

import os
import sys
import time
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

from services.dongle_detect_service import DongleDetectService
from common.transport import VvProtocol, VvAddress
from common.commands import CommandFactory
from common import protocol_pb2 as pb

# Cấu hình log
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("services.dongle_detect_service").setLevel(logging.WARNING)

# ===============================================================================
# CONFIGURATION FOR TESTING
# ===============================================================================
from utils.runtime_mode import is_test_mode
USE_VIRTUAL_COM = is_test_mode()  # Set to False to auto-detect and use real Dongle
VIRTUAL_COM_PORT = "TCP" # "TCP" or COM port (e.g. "COM11")

# Cấu hình bật (1) hoặc tắt (0) từng lệnh truy vấn get
ENABLED_QUERIES = {
    "device_information_get": 1,
    "battery_info_get":       1,
    "time_sync_get":          0,
    "anchor_layout_get":      0,
    "sys_config_get":         0,
    "sys_ranging_cfg_get":    0,
    "sensor_fusion_cfg_get":  0,
    "pos_calib_cfg_get":      0,
    "ranging_status_get":     0,
    "calib_status_get":       0,
    "ble_status_get":         0,
    "ble_conn_params_get":    0,
    "rtos_resource_get":      0,
    "rtos_task_stats_get":    0,
}
# ===============================================================================

seq_counter = 0
is_running = True
ble_state = None
scanned_devices = {}
script_state = "DETECTING"  # DETECTING -> SCANNING -> CONNECTING -> TELEMETRY
expected_resp = None
expected_seq = 0
resp_event = threading.Event()
last_disconnect_reason = 0
received_packet = None

ACK_RESPONSES = {
    0: "UNSPECIFIED (Không xác định)",
    1: "ACK (Chấp nhận)",
    2: "NACK_BAD_CRC (Lỗi CRC gói tin)",
    3: "NACK_UNIMPLEMENTED (Chưa được cài đặt / Không hỗ trợ - Thiết bị có thể đang chạy ở chế độ BOOTLOADER)",
    4: "NACK_TIMED_OUT (Hết thời gian xử lý)",
    5: "NACK_BUSY (Thiết bị đang bận)",
    6: "NACK_CMD_FAILED (Thực thi lệnh thất bại)",
    7: "NACK_INVALID_TYPE (Kiểu gói tin không hợp lệ)",
}

HCI_ERRORS = {
    0x00: "HCI_SUCCESS (Không có lỗi)",
    0x01: "HCI_ERR_UNKNOWN_CONN_IDENTIFIER (Lệnh không xác định)",
    0x02: "HCI_ERR_UNKNOWN_CONN_IDENTIFIER (ID kết nối không hợp lệ)",
    0x03: "HCI_ERR_HW_FAILURE (Lỗi phần cứng)",
    0x05: "HCI_ERR_AUTH_FAILURE (Xác thực thất bại / Sai PIN hoặc Key)",
    0x06: "HCI_ERR_PIN_OR_KEY_MISSING (Thiếu PIN hoặc Encryption Key)",
    0x07: "HCI_ERR_MEM_CAPACITY_EXCEEDED (Quá tải bộ nhớ)",
    0x08: "HCI_ERR_CONNECTION_TIMEOUT (Supervision Timeout - Thiết bị ở quá xa, hết pin, bị nhiễu nặng hoặc đột ngột tắt nguồn khiến Dongle mất kết nối)",
    0x09: "HCI_ERR_CONN_LIMIT_EXCEEDED (Quá giới hạn kết nối tối đa)",
    0x0B: "HCI_ERR_CONN_ALREADY_EXISTS (Kết nối đã tồn tại)",
    0x0C: "HCI_ERR_COMMAND_DISALLOWED (Lệnh không được phép)",
    0x0D: "HCI_ERR_CONN_REJ_LIMITED_RESOURCES (Bị từ chối do thiết bị hết tài nguyên)",
    0x0E: "HCI_ERR_CONN_REJ_SECURITY_REASONS (Bị từ chối do bảo mật)",
    0x0F: "HCI_ERR_CONN_REJ_UNACCEPTABLE_BD_ADDR (Bị từ chối do địa chỉ MAC không hợp lệ)",
    0x10: "HCI_ERR_CONN_ACCEPT_TIMEOUT_EXCEEDED (Hết thời gian chờ chấp nhận kết nối)",
    0x11: "HCI_ERR_UNSUPPORTED_FEATURE (Tính năng không được hỗ trợ bởi thiết bị)",
    0x12: "HCI_ERR_INVALID_HCI_CMD_PARAMS (Tham số HCI không hợp lệ)",
    0x13: "HCI_ERR_REMOTE_USER_TERMINATED (Thiết bị đích chủ động ngắt kết nối - có thể do nó đã kết nối với máy khác, hoặc reset)",
    0x14: "HCI_ERR_REMOTE_DEV_TERMINATION_LOW_RESOURCES (Thiết bị ngắt do cạn tài nguyên)",
    0x15: "HCI_ERR_REMOTE_DEV_TERMINATION_POWER_OFF (Thiết bị ngắt do tắt nguồn/hết pin)",
    0x16: "HCI_ERR_LOCAL_HOST_TERMINATED (Host cục bộ chủ động ngắt kết nối)",
    0x1E: "HCI_ERR_UNSPECIFIED_ERROR (Lỗi không xác định)",
    0x22: "HCI_ERR_LMP_RESPONSE_TIMEOUT (Hết thời gian phản hồi Link Layer)",
    0x3B: "HCI_ERR_UNACCEPTABLE_CONN_PARAMS (Tham số kết nối không được chấp nhận)",
    0x3D: "HCI_ERR_CONN_TERMINATED_MIC_FAILURE (Lỗi MIC - mất mã hóa bảo mật)",
    0x3E: "HCI_ERR_CONN_FAILED_TO_BE_ESTABLISHED (Lỗi bắt tay khởi tạo kết nối - thiết bị ở xa, tín hiệu yếu khiến các gói tin bắt tay đầu tiên bị mất)",
}


def get_next_seq():
    global seq_counter
    seq_counter = (seq_counter + 1) & 0xFFFFFFFF
    return seq_counter

def print_protobuf_message(msg, indent=6):
    from google.protobuf.message import Message
    for field, value in msg.ListFields():
        if field.name == "mac_address" and isinstance(value, bytes):
            mac_str = ":".join(f"{b:02X}" for b in value)
            print(f"{' ' * indent}- {field.name}: {mac_str}")
        elif isinstance(value, bytes):
            print(f"{' ' * indent}- {field.name}: 0x{value.hex().upper()}")
        elif isinstance(value, Message):
            print(f"{' ' * indent}- {field.name} (message):")
            print_protobuf_message(value, indent + 3)
        elif "Repeated" in str(type(value)) or isinstance(value, (list, tuple)):
            print(f"{' ' * indent}- {field.name} (list):")
            for item in value:
                if isinstance(item, Message):
                    print(f"{' ' * (indent + 3)}- Item:")
                    print_protobuf_message(item, indent + 6)
                else:
                    print(f"{' ' * (indent + 3)}- {item}")
        else:
            if field.type == field.TYPE_ENUM:
                try:
                    enum_desc = field.enum_type
                    enum_name = enum_desc.values_by_number[value].name
                    print(f"{' ' * indent}- {field.name}: {enum_name} ({value})")
                except Exception:
                    print(f"{' ' * indent}- {field.name}: {value}")
            else:
                print(f"{' ' * indent}- {field.name}: {value}")

def main():
    global is_running, ble_state, scanned_devices, script_state, expected_resp
    
    print("==================================================================")
    print("   UWB RTLS STUDIO - MCU & CENTRAL TELEMETRY TEST SCRIPT          ")
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
                print(f"🔌 [TCP Mode] Waiting for connection on {host}:{port}...")
                self.conn, addr = self.sock.accept()
                self.conn.setblocking(False)
                self.is_open = True
            else:
                while True:
                    try:
                        self.sock.connect((host, port))
                        break
                    except ConnectionRefusedError:
                        print(f"⏳ Đang chờ Peripheral (TCP {host}:{port}) bật lên...")
                        import time
                        time.sleep(1)
                self.conn = self.sock
                self.conn.setblocking(False)
                self.is_open = True
                print(f"✅ [TCP Mode] Connected to {host}:{port}")

        def write(self, data):
            if self.is_open and self.conn:
                try:
                    self.conn.send(data)
                except BlockingIOError:
                    pass
                except Exception:
                    self.is_open = False

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

    port_to_use = None
    if USE_VIRTUAL_COM:
        port_to_use = VIRTUAL_COM_PORT
        print(f"🔌 Configuration: Using VIRTUAL COM PORT / TCP: {port_to_use}")
    else:
        # 1. Tự động tìm cổng COM của Dongle
        print("Scanning COM ports for Dongle...")
        detector = DongleDetectService()
        dongle_info = detector.find_dongle_port()
        
        if dongle_info:
            print(f"✅ Found Dongle on port: {dongle_info.port} ({dongle_info.description})")
            port_to_use = dongle_info.port
        else:
            print("⚠️ Không tự động nhận diện được Dongle qua handshake.")
            # Liệt kê các cổng COM hiện có trên máy tính để người dùng chọn
            ports = detector.list_all_ports()
            if not ports:
                print("❌ ERROR: Không tìm thấy cổng COM nào trên máy tính. Vui lòng cắm Dongle vào.")
                sys.exit(1)
                
            print("\nCác cổng COM khả dụng:")
            for idx, p in enumerate(ports, 1):
                print(f"  [{idx}] {p.device} - {p.description}")
                
            while True:
                choice = input(f"\nNhập số thứ tự cổng COM (1-{len(ports)}) hoặc nhập trực tiếp tên COM (ví dụ: COM3), hoặc 'q' để thoát: ").strip()
                if choice.lower() == 'q':
                    sys.exit(0)
                if not choice:
                    continue
                try:
                    sel_idx = int(choice) - 1
                    if 0 <= sel_idx < len(ports):
                        port_to_use = ports[sel_idx].device
                        break
                except ValueError:
                    port_to_use = choice.upper()
                    break
    
    # 2. Mở kết nối
    try:
        if port_to_use.upper().startswith("COM") or port_to_use.startswith("/dev/"):
            ser = serial.Serial(port=port_to_use, baudrate=115200, timeout=3.0)
        else:
            ser = TcpSerialAdapter(is_server=False, host='127.0.0.1', port=9999)
            
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        print(f"🔌 Opened connection to: {port_to_use}")
    except Exception as e:
        print(f"❌ ERROR: Không thể mở kết nối. Lỗi: {e}")
        sys.exit(1)
        
    proto = VvProtocol()
    commands = CommandFactory()
    
    # Hàm tiện ích để gửi gói tin đi (in ra raw TX và decode TX)
    def send_packet(cmd_name: str, dst_addr: int, src_addr: int = pb.PACKET_ADDR_HOST, command_params: dict | None = None):
        seq = get_next_seq()
        global expected_seq
        expected_seq = seq
        builder = getattr(commands, cmd_name)
        pkt = builder(src_addr, dst_addr, seq, **dict(command_params or {}))
        frame = proto.wrap_packet(pkt)
        ser.write(frame)
        ser.flush()
        
        # In dữ liệu raw dạng hex gửi đi
        hex_frame = frame.hex().upper()
        if len(hex_frame) > 80:
            hex_frame_snippet = hex_frame[:60] + "...[" + str(len(frame)) + " bytes]..." + hex_frame[-10:]
        else:
            hex_frame_snippet = hex_frame
        # print(f"\n📤 [RAW TX] {hex_frame_snippet}")
        print(f"👉 [TX] Send Command '{cmd_name}' (seq={seq}) -> Dst: {VvAddress(dst_addr).name}")
        return seq

    # 3. Luồng đọc và giải mã dữ liệu phản hồi
    def rx_thread_func():
        global is_running, ble_state, scanned_devices, script_state, expected_resp, expected_seq, last_disconnect_reason, received_packet
        
        while is_running:
            try:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    # Giải mã HDLC và Protobuf (không in dữ liệu raw RX lên terminal)
                    packets = proto.decode_from_frames(data)
                    for pkt in packets:
                        param_name = pkt.WhichOneof("params")
                        if not param_name:
                            continue
                            
                        src_addr = pkt.hdr.addr.src
                        dst_addr = pkt.hdr.addr.dst
                        src_name = VvAddress(src_addr).name if src_addr in VvAddress.__members__.values() else str(src_addr)
                        dst_name = VvAddress(dst_addr).name if dst_addr in VvAddress.__members__.values() else str(dst_addr)
                        
                        # Cập nhật trạng thái BLE
                        if param_name == "ble_status_resp":
                            ble_state = pkt.ble_status_resp.state
                            if pkt.ble_status_resp.disconnect_reason:
                                last_disconnect_reason = pkt.ble_status_resp.disconnect_reason
                            
                            # Chỉ in chi tiết trạng thái BLE khi đang trong quá trình CONNECTING
                            if script_state == "CONNECTING":
                                state_name = pb.ble_state_t.Name(ble_state)
                                reason_str = ""
                                if pkt.ble_status_resp.disconnect_reason:
                                    reason_desc = HCI_ERRORS.get(pkt.ble_status_resp.disconnect_reason, "Lỗi không xác định")
                                    reason_str = f", Reason: 0x{pkt.ble_status_resp.disconnect_reason:02X} ({reason_desc})"
                                print(f"   ℹ️ [BLE STATUS] State: {state_name}, RSSI: {pkt.ble_status_resp.rssi_dbm} dBm{reason_str}")
                            
                        # Nếu đang quét thiết bị, lưu kết quả
                        if script_state == "SCANNING" and param_name == "ble_scan_result":
                            res = pkt.ble_scan_result
                            mac_hex = ":".join(f"{b:02X}" for b in res.mac_address)
                            scanned_devices[mac_hex] = {
                                "name": res.name or f"UWB-{mac_hex[-5:]}",
                                "rssi": res.rssi_dbm,
                                "serial": res.serial_number
                            }
                        
                        # Đánh dấu nhận được gói tin mong chờ
                        if expected_resp == param_name:
                            received_packet = pkt
                            resp_event.set()
                        elif param_name == "ack" and pkt.ack.ack_seq == expected_seq:
                            received_packet = pkt
                            resp_event.set()
                        # Không in các gói tin realtime chạy nền khác để tránh trôi màn hình khi người khác đang test
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"\n❌ Lỗi thread đọc Serial: {e}")
                is_running = False
                break

    rx_thread = threading.Thread(target=rx_thread_func, daemon=True)
    rx_thread.start()
    
    # 4. Kiểm tra trạng thái BLE hiện tại của Dongle
    script_state = "CONNECTING"
    print("\n--- [CHECK CURRENT BLE STATUS] ---")
    ble_state = None
    for attempt in range(3):
        send_packet("ble_status_get", dst_addr=pb.PACKET_ADDR_CENTRAL)
        time.sleep(0.4)
        if ble_state is not None:
            break
            
    if ble_state is None:
        ble_state = pb.BLE_STATE_IDLE
        print("⚠️ Không nhận được phản hồi status từ Dongle, giả định BLE_STATE_IDLE.")

    # 5. Nếu chưa kết nối thiết bị nào, tiến hành quét và kết nối BLE
    if ble_state != pb.BLE_STATE_CONNECTED:
        print(f"\nDongle status is: {pb.ble_state_t.Name(ble_state)}")
        print("Dongle chưa được kết nối. Bắt đầu quét thiết bị BLE...")
        
        scanned_devices.clear()
        script_state = "SCANNING"
        send_packet("ble_scan_start", dst_addr=pb.PACKET_ADDR_CENTRAL)
        
        # Đợi 5 giây thu thập thông tin thiết bị
        for i in range(5):
            print(f"Scanning... {5 - i}s còn lại...")
            time.sleep(1.0)
            
        # Dừng quét BLE
        send_packet("ble_scan_stop", dst_addr=pb.PACKET_ADDR_CENTRAL)
        time.sleep(0.5)
        
        # Hiển thị kết quả danh sách thiết bị
        if not scanned_devices:
            print("❌ Không tìm thấy thiết bị UWB BLE nào xung quanh.")
            is_running = False
            ser.close()
            sys.exit(0)
            
        print("\nDanh sách thiết bị quét được:")
        device_list = list(scanned_devices.items())
        for idx, (mac, info) in enumerate(device_list, 1):
            serial_str = f"0x{info['serial']:08X}" if info['serial'] else "N/A"
            print(f" [{idx}] {info['name']} - MAC: {mac} (RSSI: {info['rssi']} dBm, Serial: {serial_str})")
            
        # Cho người dùng lựa chọn thiết bị
        while True:
            choice = input(f"\nNhập số thứ tự thiết bị muốn kết nối (1-{len(device_list)}) hoặc 'q' để thoát: ").strip()
            if choice.lower() == 'q':
                is_running = False
                ser.close()
                sys.exit(0)
            try:
                sel_idx = int(choice) - 1
                if 0 <= sel_idx < len(device_list):
                    target_mac_str, target_info = device_list[sel_idx]
                    break
                else:
                    print(f"Vui lòng chọn trong khoảng 1-{len(device_list)}!")
            except ValueError:
                print("Lựa chọn không hợp lệ!")
                
        # Gửi lệnh kết nối
        target_mac_bytes = bytes.fromhex(target_mac_str.replace(":", ""))
        script_state = "CONNECTING"
        
        rssi = target_info['rssi']
        print(f"\n📊 Target Device RSSI: {rssi} dBm")
        if rssi < -80:
            print("   ⚠️ WARNING: Tín hiệu BLE của thiết bị khá yếu (dưới -80 dBm).")
            print("   Kết nối có thể thất bại hoặc không ổn định do khoảng cách xa hoặc vật cản!")
            
        send_packet("ble_connect", dst_addr=pb.PACKET_ADDR_CENTRAL, mac_address=target_mac_bytes)
        print(f"\nConnecting to {target_info['name']} ({target_mac_str})... Đang chờ kết nối...")
        
        # Đợi tối đa 10 giây để kết nối thành công, chủ động poll ble_status_get như app chính
        connected = False
        last_disconnect_reason = 0  # Reset trước khi kết nối
        for _ in range(20):
            send_packet("ble_status_get", dst_addr=pb.PACKET_ADDR_CENTRAL)
            time.sleep(0.5)
            if ble_state == pb.BLE_STATE_CONNECTED:
                connected = True
                break
            
        if not connected:
            print("\n❌ Kết nối thất bại hoặc hết thời gian chờ (Timeout)!")
            print("\n🔍 CHẨN ĐOÁN LỖI KẾT NỐI (KHOẢNG CÁCH ~10M):")
            print(f" 1. Tín hiệu BLE suy hao (RSSI của thiết bị lúc quét: {rssi} dBm):")
            print("    Ở khoảng cách 10m (đặc biệt trong phòng có tường, cửa, vách ngăn), sóng BLE bị suy giảm mạnh.")
            print("    Scan vẫn được vì quảng bá (advertising) chỉ cần nhận được 1 gói tin đơn lẻ là hiển thị lên thiết bị.")
            print("    Nhưng Connect yêu cầu truyền nhận liên tục và đồng bộ 2 chiều (bắt tay thiết lập connection link).")
            print("    Nếu mất gói tin liên tục trong pha bắt tay này, kết nối sẽ timeout thất bại (lỗi HCI 0x3E hoặc 0x08).")
            print(" 2. Thiết bị đã kết nối ở nơi khác:")
            print("    Một thiết bị BLE (Tag/Anchor) thông thường chỉ chấp nhận kết nối với 1 Host (Dongle) tại một thời điểm.")
            print("    Nếu thiết bị đang kết nối với một điện thoại hoặc một Dongle khác gần đó, nó sẽ từ chối kết nối mới.")
            print(" 3. Thiết bị hết pin hoặc pin yếu:")
            print("    Khi pin của Tag/Anchor giảm thấp, công suất phát sóng (Tx power) giảm mạnh, tăng cao tỉ lệ lỗi CRC gói tin.")
            
            if last_disconnect_reason:
                reason_desc = HCI_ERRORS.get(last_disconnect_reason, "Lỗi không xác định")
                print(f"\n👉 DONGLE BÁO LỖI CHI TIẾT (HCI Disconnect Reason): 0x{last_disconnect_reason:02X} - {reason_desc}")
            else:
                print("\n👉 Không nhận được mã lỗi cụ thể từ Dongle (HCI Reason). Lỗi có thể do phần cứng timeout do không nhận được phản hồi bắt tay.")
                
            is_running = False
            ser.close()
            sys.exit(0)
            
        print("🎉 Kết nối thành công!")
        print("⏳ Đang đợi 1.5 giây để đường truyền BLE ổn định (Service Discovery / MTU Negotiation)...")
        time.sleep(1.5)
    else:
        print("\nDongle đã kết nối từ trước.")

    # 6. Vòng lặp gửi yêu cầu Telemetry khi đã kết nối
    script_state = "TELEMETRY"
    print("\n==============================================================")
    print("  BẮT ĐẦU VÒNG LẶP KIỂM TRA DATA TELEMETRY (MỖI 3 GIÂY)        ")
    print("  Bấm Ctrl + C để dừng kiểm tra.                              ")
    print("==============================================================")
    
    # (Tạm thời bỏ qua việc tự động set layout và start ranging để tránh ảnh hưởng/xung đột với người khác đang test)
    # print("\n--- [TESTING ANCHOR LAYOUT SET] ---")
    # dummy_anchors = [
    #     {"anchor_id": 0, "x_m": 0.0, "y_m": 0.0, "z_m": 1.2},
    #     {"anchor_id": 1, "x_m": 5.0, "y_m": 0.0, "z_m": 1.5},
    #     {"anchor_id": 2, "x_m": 0.0, "y_m": 4.0, "z_m": 1.5},
    #     {"anchor_id": 3, "x_m": 5.0, "y_m": 4.0, "z_m": 1.8},
    # ]
    # send_packet("anchor_layout_set", dst_addr=pb.PACKET_ADDR_MCU, anchors=dummy_anchors)
    # time.sleep(1.0)

    # print("\n--- [START RANGING] ---")
    # send_packet("ranging_start", dst_addr=pb.PACKET_ADDR_MCU)
    # time.sleep(1.0)

    try:
        if ble_state != pb.BLE_STATE_CONNECTED:
            print("\n⚠️ Mất kết nối BLE! Dừng script.")
            is_running = False
            return
            
        print("\n--- [START QUERY PERIOD] ---")
        queries = [
            ("device_information_get", pb.PACKET_ADDR_MCU, "device_information_resp"),
            ("battery_info_get", pb.PACKET_ADDR_MCU, "battery_info_resp"),
            ("time_sync_get", pb.PACKET_ADDR_MCU, "time_sync_resp"),
            ("anchor_layout_get", pb.PACKET_ADDR_MCU, "anchor_layout_resp"),
            ("sys_config_get", pb.PACKET_ADDR_MCU, "sys_config_resp"),
            ("sys_ranging_cfg_get", pb.PACKET_ADDR_MCU, "sys_ranging_cfg_resp"),
            ("sensor_fusion_cfg_get", pb.PACKET_ADDR_MCU, "sensor_fusion_cfg_resp"),
            ("pos_calib_cfg_get", pb.PACKET_ADDR_MCU, "pos_calib_cfg_resp"),
            ("ranging_status_get", pb.PACKET_ADDR_MCU, "ranging_status_resp"),
            ("calib_status_get", pb.PACKET_ADDR_MCU, "calib_status_resp"),
            ("ble_status_get", pb.PACKET_ADDR_CENTRAL, "ble_status_resp"),
            ("ble_conn_params_get", pb.PACKET_ADDR_CENTRAL, "ble_conn_params_resp"),
            ("rtos_resource_get", pb.PACKET_ADDR_MCU, "rtos_resource_resp"),
            ("rtos_task_stats_get", pb.PACKET_ADDR_MCU, "rtos_task_stats_resp"),
        ]
        
        # Lọc danh sách truy vấn dựa trên cấu hình ENABLED_QUERIES ở đầu file
        active_queries = [q for q in queries if ENABLED_QUERIES.get(q[0], 0) == 1]
        
        if not active_queries:
            print("⚠️ Không có lệnh truy vấn nào được kích hoạt trong ENABLED_QUERIES.")
            return
            
        success_count = 0
        for cmd_name, dst, resp_name in active_queries:
            expected_resp = resp_name
            query_success = False
            
            # Cơ chế tự động gửi lại (Retry tối đa 3 lần nếu gặp lỗi mất gói)
            for attempt in range(1, 4):
                global received_packet
                received_packet = None
                resp_event.clear()
                
                if attempt > 1:
                    print(f"   ⏳ [RETRY {attempt}/3] Đang gửi lại truy vấn '{cmd_name}'...")
                send_packet(cmd_name, dst_addr=dst)
                
                # Đợi phản hồi trong 0.4s (400ms) cho mỗi lần thử
                if resp_event.wait(0.4) and received_packet is not None:
                    query_success = True
                    break
            
            if query_success:
                actual_resp = received_packet.WhichOneof("params")
                if actual_resp == "ack":
                    ack_code = received_packet.ack.response
                    ack_desc = ACK_RESPONSES.get(ack_code, f"UNKNOWN_ACK_CODE_{ack_code}")
                    if ack_code == pb.PACKET_ACK_RESPONSE_ACK:
                        success_count += 1
                        print(f"   ✅ [ACK] Lệnh '{cmd_name}' được thiết bị xác nhận thành công.")
                    elif ack_code == pb.PACKET_ACK_RESPONSE_NACK_UNIMPLEMENTED:
                        print(f"   ❌ [UNIMPLEMENTED] Thiết bị báo lệnh '{cmd_name}' không được hỗ trợ (NACK_UNIMPLEMENTED).")
                        print("      💡 Nguyên nhân: Thiết bị đang chạy ở chế độ BOOTLOADER, hoặc firmware chưa nạp app chính.")
                    else:
                        print(f"   ❌ [NACK] Thiết bị từ chối lệnh '{cmd_name}': {ack_desc}")
                elif actual_resp == resp_name:
                    success_count += 1
                    print(f"   ✅ [SUCCESS] Nhận được dữ liệu cho '{cmd_name}':")
                    param_val = getattr(received_packet, resp_name)
                    print_protobuf_message(param_val, indent=6)
                else:
                    print(f"   ⚠️ [WARNING] Nhận được gói tin phản hồi '{actual_resp}' thay vì '{resp_name}' mong đợi.")
            else:
                print(f"   ❌ [FAILED] Không nhận được phản hồi cho '{cmd_name}' sau 3 lần thử.")
            time.sleep(0.3) # Giãn cách 300ms giữa các truy vấn để BLE link kịp xử lý và tránh nghẽn
            
        print(f"\n========================================")
        print(f"  Summary: {success_count}/{len(active_queries)} queries completed successfully.")
        print(f"========================================\n")
            
    except KeyboardInterrupt:
        print("\nStopping telemetry test script...")
    finally:
        is_running = False
        print("\n======================================================")
        print("   CLEANUP: DISCONNECTING BLE DEVICE & SERIAL")
        print("======================================================")
        
        # 1. Gửi lệnh ngắt kết nối BLE tới Central
        try:
            print("📤 Gửi lệnh 'ble_disconnect' tới Central...")
            send_packet("ble_disconnect", dst_addr=pb.PACKET_ADDR_CENTRAL)
            time.sleep(0.8) # Đợi 800ms để Dongle xử lý ngắt kết nối
        except Exception as e:
            print(f"⚠️ Không thể gửi lệnh ngắt kết nối BLE: {e}")
            
        # 2. Đóng cổng Serial kết nối đến Dongle
        try:
            ser.close()
            print("🔌 Đóng kết nối cổng Serial thành công.")
        except Exception as e:
            print(f"⚠️ Lỗi khi đóng cổng Serial: {e}")
            
        print("\nFinished.")

if __name__ == "__main__":
    main()
