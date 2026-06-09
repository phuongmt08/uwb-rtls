"""
===============================================================================
  UWB RTLS Studio — MCU Telemetry & BLE Status Test Utility
===============================================================================
  File        : test/test_mcu_telemetry.py
  Description : Script kiểm tra giao tiếp protobuf từ Host tới MCU và Central.
                - Host <-> MCU: Lấy dữ liệu telemetry (battery, dev info).
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

# Tắt bớt log debug của các service khác để tránh loãng màn hình console
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("services.dongle_detect_service").setLevel(logging.WARNING)

seq_counter = 0
is_running = True
ble_state = None
scanned_devices = {}
script_state = "DETECTING"  # DETECTING -> SCANNING -> CONNECTING -> TELEMETRY

def get_next_seq():
    global seq_counter
    seq_counter = (seq_counter + 1) & 0xFFFFFFFF
    return seq_counter

def main():
    global is_running, ble_state, scanned_devices, script_state
    
    print("==================================================================")
    print("   UWB RTLS STUDIO - MCU & CENTRAL TELEMETRY TEST SCRIPT          ")
    print("==================================================================")
    
    # 1. Tự động tìm cổng COM của Dongle
    print("Scanning COM ports for Dongle...")
    detector = DongleDetectService()
    dongle_info = detector.find_dongle_port()
    
    if not dongle_info:
        print("❌ ERROR: Không tìm thấy Dongle. Vui lòng cắm Dongle vào máy tính và thử lại.")
        sys.exit(1)
        
    print(f"✅ Found Dongle on port: {dongle_info.port} ({dongle_info.description})")
    
    # 2. Mở cổng Serial kết nối với Dongle
    try:
        ser = serial.Serial(port=dongle_info.port, baudrate=115200, timeout=1.0)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception as e:
        print(f"❌ ERROR: Không thể mở cổng {dongle_info.port}. Lỗi: {e}")
        print("👉 Vui lòng kiểm tra xem bạn có đang mở ứng dụng UWB RTLS Studio hay không (nếu có, hãy đóng lại).")
        sys.exit(1)
        
    proto = VvProtocol()
    commands = CommandFactory()
    
    # Hàm tiện ích để gửi gói tin đi
    def send_packet(cmd_name: str, dst_addr: int, src_addr: int = pb.PACKET_ADDR_HOST, **kwargs):
        seq = get_next_seq()
        builder = getattr(commands, cmd_name)
        pkt = builder(src_addr, dst_addr, seq, **kwargs)
        frame = proto.wrap_packet(pkt)
        ser.write(frame)
        ser.flush()
        if script_state in ("CONNECTING", "TELEMETRY"):
            print(f"\n[TX] Send Command '{cmd_name}' -> Dst: {VvAddress(dst_addr).name} (seq={seq})")
        return seq

    # 3. Luồng đọc dữ liệu phản hồi trong nền
    def rx_thread_func():
        decoder = type(proto.hdlc)()
        global is_running, ble_state, scanned_devices, script_state
        
        while is_running:
            try:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    chunks = decoder.feed(data)
                    for chunk in chunks:
                        if chunk.frame_type != 0:  # FRAME_TYPE_PROTOBUF
                            continue
                        try:
                            pkt = proto.decode_packet(chunk.payload)
                            param_name = pkt.WhichOneof("params")
                            if not param_name:
                                continue
                                
                            src_addr = pkt.hdr.addr.src
                            dst_addr = pkt.hdr.addr.dst
                            
                            src_name = VvAddress(src_addr).name if src_addr in VvAddress.__members__.values() else str(src_addr)
                            dst_name = VvAddress(dst_addr).name if dst_addr in VvAddress.__members__.values() else str(dst_addr)
                            
                            # ── STATE: SCANNING (Thu thập thiết bị quảng bá ngầm, không in ra terminal)
                            if script_state == "SCANNING":
                                if param_name == "ble_scan_result":
                                    res = pkt.ble_scan_result
                                    mac_hex = ":".join(f"{b:02X}" for b in res.mac_address)
                                    scanned_devices[mac_hex] = {
                                        "name": res.name or f"UWB-{mac_hex[-5:]}",
                                        "rssi": res.rssi_dbm,
                                        "serial": res.serial_number
                                    }
                            
                            # ── STATE: CONNECTING (Cập nhật trạng thái BLE khi kết nối)
                            elif script_state == "CONNECTING":
                                if param_name == "ble_status_resp":
                                    ble_state = pkt.ble_status_resp.state
                            
                            # ── STATE: TELEMETRY (Hiển thị chi tiết gói tin khi đã kết nối thành công)
                            elif script_state == "TELEMETRY":
                                # Log header của gói tin nhận được
                                print(f"\n[RX] '{param_name}' from {src_name} to {dst_name} (seq={pkt.hdr.seq})")
                                
                                # Nhận diện luồng Host <-> MCU
                                if src_addr == pb.PACKET_ADDR_MCU and dst_addr == pb.PACKET_ADDR_HOST:
                                    print("   🌟 [MCU -> HOST] Telemetry packet received from MCU!")
                                # Nhận diện luồng Host <-> Central
                                elif src_addr == pb.PACKET_ADDR_CENTRAL and dst_addr == pb.PACKET_ADDR_HOST:
                                    print("   🔷 [CENTRAL -> HOST] Control/State packet received from Central!")
                                
                                # Nhận diện gói tin ble_adv_status (Trạng thái quảng bá của Anchor/Tag)
                                if param_name == "ble_adv_status":
                                    print("   📢 [BROADCAST -> HOST] Advertising status payload from a device!")
                                
                                # In các trường dữ liệu chi tiết trong packet
                                param_val = getattr(pkt, param_name)
                                for field, value in param_val.ListFields():
                                    if field.name == "mac_address" and isinstance(value, bytes):
                                        mac_str = ":".join(f"{b:02X}" for b in value)
                                        print(f"      - {field.name}: {mac_str}")
                                    elif field.name == "state" and param_name == "ble_status_resp":
                                        ble_state = value
                                        state_name = pb.ble_state_t.Name(value)
                                        print(f"      - state: {state_name} ({value})")
                                    elif field.name == "device" and param_name == "ble_adv_status":
                                        dev_type_name = pb.device_type_t.Name(value)
                                        print(f"      - device_type: {dev_type_name} ({value})")
                                    elif field.name == "status_flags" and param_name == "ble_adv_status":
                                        print(f"      - status_flags: 0b{value:08b} (0x{value:02X})")
                                    else:
                                        print(f"      - {field.name}: {value}")
                                        
                        except Exception as e:
                            if script_state == "TELEMETRY":
                                print(f"   ⚠️ Lỗi giải mã packet: {e}")
                else:
                    time.sleep(0.02)
            except Exception as e:
                if script_state == "TELEMETRY":
                    print(f"\n❌ Lỗi đọc cổng Serial: {e}")
                is_running = False
                break

    rx_thread = threading.Thread(target=rx_thread_func, daemon=True)
    rx_thread.start()
    
    # 4. Kiểm tra trạng thái BLE hiện tại của Dongle
    script_state = "CONNECTING"
    send_packet("ble_status_get", dst_addr=pb.PACKET_ADDR_CENTRAL)
    time.sleep(0.8)
    
    if ble_state is None:
        ble_state = pb.BLE_STATE_IDLE

    # 5. Nếu chưa kết nối thiết bị nào, bắt đầu quét BLE tìm thiết bị
    if ble_state != pb.BLE_STATE_CONNECTED:
        print(f"\nDongle status is: {pb.ble_state_t.Name(ble_state)}")
        print("Dongle chưa được kết nối. Bắt đầu quét thiết bị BLE trong 5 giây...")
        
        # Bắt đầu quét BLE
        scanned_devices.clear()
        script_state = "SCANNING"
        send_packet("ble_scan_start", dst_addr=pb.PACKET_ADDR_CENTRAL)
        
        # Đợi 5 giây thu thập thông tin thiết bị (không in tràn terminal)
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
            
        # Cho người dùng lựa chọn thiết bị kết nối
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
                
        # Thực hiện gửi lệnh kết nối
        target_mac_bytes = bytes.fromhex(target_mac_str.replace(":", ""))
        script_state = "CONNECTING"
        send_packet("ble_connect", dst_addr=pb.PACKET_ADDR_CENTRAL, mac_address=target_mac_bytes)
        print(f"\nConnecting to {target_info['name']} ({target_mac_str})... Đang chờ phản hồi kết nối...")
        
        # Đợi tối đa 10 giây để kết nối thành công
        connected = False
        for _ in range(20):
            if ble_state == pb.BLE_STATE_CONNECTED:
                connected = True
                break
            time.sleep(0.5)
            
        if not connected:
            print("❌ Kết nối thất bại hoặc hết thời gian chờ (Timeout)!")
            is_running = False
            ser.close()
            sys.exit(0)
            
        print("🎉 Kết nối thành công!")
    else:
        print("\nDongle đã được kết nối từ trước.")

    # 6. Vòng lặp gửi yêu cầu Telemetry khi đã kết nối
    script_state = "TELEMETRY"
    print("\n==============================================================")
    print("  BẮT ĐẦU VÒNG LẶP KIỂM TRA DATA TELEMETRY (MỖI 3 GIÂY)        ")
    print("  Bấm Ctrl + C để dừng kiểm tra.                              ")
    print("==============================================================")
    
    try:
        while is_running:
            # Gửi các gói tin yêu cầu lấy dữ liệu từ MCU (Host -> MCU)
            print("\n--- [START QUERY PERIOD] ---")
            send_packet("device_information_get", dst_addr=pb.PACKET_ADDR_MCU)
            send_packet("battery_info_get", dst_addr=pb.PACKET_ADDR_MCU)
            
            # Gửi yêu cầu lấy trạng thái/tham số kết nối từ Central (Host -> Central)
            send_packet("ble_status_get", dst_addr=pb.PACKET_ADDR_CENTRAL)
            send_packet("ble_conn_params_get", dst_addr=pb.PACKET_ADDR_CENTRAL)
            
            time.sleep(3.0)
            
    except KeyboardInterrupt:
        print("\nStopping telemetry test script...")
    finally:
        is_running = False
        try:
            ser.close()
            print("Closed serial connection.")
        except Exception:
            pass
        print("Finished.")

if __name__ == "__main__":
    main()
