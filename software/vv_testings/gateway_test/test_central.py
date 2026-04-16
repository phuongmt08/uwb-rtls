from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

# Add the parent directory to sys.path so we can import modules from vv_testings
sys.path.append(str(Path(__file__).resolve().parent.parent))

from vv_commands import CommandFactory
from vv_test_session import VvTestSession
import protocol_pb2 as pb

# Global variables for the Live Dashboard
running = True
devices = {}  # Format: "AA:BB:CC:DD:EE:FF" -> {"bytes": b'...', "name": "...", "rssi": -60, "last_seen": 1234567.89}
factory = CommandFactory()

def rx_thread_func(session: VvTestSession):
    global running, devices
    while running:
        try:
            # Liên tục đọc gói tin trả về không block, timeout nhỏ
            pkts = session.recv_packets(timeout_s=0.1)
            for pkt in pkts:
                # Phân loại luồng gói tin trả về
                ptype = pkt.WhichOneof("params")
                
                if ptype == "ble_scan_result":
                    p = pkt.ble_scan_result
                    mac_str = ":".join(f"{b:02X}" for b in reversed(p.mac_address))
                    
                    is_new = mac_str not in devices
                    devices[mac_str] = {
                        "bytes": p.mac_address,
                        "name": p.name,
                        "rssi": p.rssi_dbm,
                        "last_seen": time.time()
                    }
                    if is_new:
                        print(f"\n[+] New Device: {mac_str} ('{p.name}') | RSSI: {p.rssi_dbm} dBm")
                        print("cmd> ", end="", flush=True)
                        
                elif ptype == "ble_conn_params_resp":
                    p = pkt.ble_conn_params_resp.params
                    print(f"\n[!] Conn Params -> Min: {p.min_interval_ms}ms, Max: {p.max_interval_ms}ms, Latency: {p.slave_latency}, Timeout: {p.sup_timeout_ms}ms")
                    print("cmd> ", end="", flush=True)
                    
                elif ptype == "ble_status_resp":
                    print(f"\n[!] BLE Status -> State Code: {pkt.ble_status_resp.state}")
                    print("cmd> ", end="", flush=True)
                    
        except Exception:
            pass
        time.sleep(0.01)

def print_help():
    print("\n--- AVAILABLE COMMANDS ---")
    print("  scan            : Start BLE scanning")
    print("  stop            : Stop BLE scanning")
    print("  list            : Xem danh sách thiết bị (lọc các thiết bị đã quá cũ)")
    print("  connect <mac>   : Kết nối tới 1 MAC (vd: connect AA:BB:CC:DD:EE:FF)")
    print("  disconnect      : Ngắt kết nối thiết bị hiện tại")
    print("  get             : Đọc Connection Params hiện tại (get_params)")
    print("  set             : Ghi Connection Params mới (min=30, max=60)")
    print("  help            : Hiển thị bảng lệnh này")
    print("  exit            : Thoát")
    print("--------------------------\n")

def run_interactive(session: VvTestSession, src: int, dst: int):
    global running, devices
    
    # 1. Start RX background thread
    rx_th = threading.Thread(target=rx_thread_func, args=(session,), daemon=True)
    rx_th.start()
    
    print("\n=== LIVE BLE CENTRAL DASHBOARD ===")
    print_help()
    
    # 2. Main command loop
    while running:
        try:
            cmd_line = input("cmd> ").strip().split()
            if not cmd_line:
                continue
                
            cmd = cmd_line[0].lower()
            
            if cmd == "exit" or cmd == "quit":
                running = False
                break
                
            elif cmd == "help":
                print_help()
                
            elif cmd == "scan":
                print("[+] Gửi lệnh Scan Start...")
                pkt = factory.ble_scan_start(src, dst, session.proto.next_seq())
                session.send_packet(pkt)
                
            elif cmd == "stop":
                print("[-] Gửi lệnh Scan Stop...")
                pkt = factory.ble_scan_stop(src, dst, session.proto.next_seq())
                session.send_packet(pkt)
                
            elif cmd == "list":
                print("\n--- LIVE DEVICES LIST ---")
                now = time.time()
                active_count = 0
                for mac, info in list(devices.items()):
                    # Nếu hơn 10 giây không có tín hiệu mới -> coi như rớt
                    if now - info["last_seen"] > 10.0:
                        del devices[mac]
                    else:
                        age = now - info["last_seen"]
                        print(f"  {mac} | RSSI: {info['rssi']:4d} | Name: '{info['name']}' (Last seen: {age:.1f}s ago)")
                        active_count += 1
                if active_count == 0:
                    print("  (Empty)")
                print("-------------------------")
                
            elif cmd == "connect":
                if len(cmd_line) < 2:
                    print("[!] Vui lòng nhập MAC. Ví dụ: connect AA:BB:CC:DD:EE:FF")
                    continue
                mac_target = cmd_line[1].upper()
                if mac_target not in devices:
                    print(f"[!] Lỗi: MAC {mac_target} chưa từng xuất hiện trong quá trình Scan.")
                    continue
                    
                print(f"[+] Gửi lệnh kết nối tới {mac_target} ...")
                # Nên gửi lệnh stop scan trước để an toàn!
                session.send_packet(factory.ble_scan_stop(src, dst, session.proto.next_seq()))
                time.sleep(0.5)
                
                pkt = factory.ble_connect(src, dst, session.proto.next_seq())
                pkt.ble_connect.mac_address = devices[mac_target]["bytes"]
                session.send_packet(pkt)
                
            elif cmd == "disconnect":
                print("[-] Gửi lệnh ngắt kết nối...")
                pkt = factory.ble_disconnect(src, dst, session.proto.next_seq())
                session.send_packet(pkt)
                
            elif cmd == "get":
                print("[+] Request Connection Params...")
                pkt = factory.ble_conn_params_get(src, dst, session.proto.next_seq())
                session.send_packet(pkt)
                
            elif cmd == "set":
                if len(cmd_line) == 3:
                    try:
                        min_val = int(cmd_line[1])
                        max_val = int(cmd_line[2])
                    except ValueError:
                        print("[!] Lỗi: Giá trị min hoặc max không hợp lệ.")
                        continue
                else:
                    print("[!] Sử dụng: set <min_ms> <max_ms>. Ví dụ: set 15 30")
                    continue
                    
                print(f"[+] Ghi đè Connection Params (min={min_val}ms, max={max_val}ms) ...")
                pkt = factory.ble_conn_params_set(src, dst, session.proto.next_seq())
                pkt.ble_conn_params_set.params.min_interval_ms = min_val
                pkt.ble_conn_params_set.params.max_interval_ms = max_val
                
                # Bắt buộc đặt các thông số này không C nó sẽ hiểu là 0
                pkt.ble_conn_params_set.params.slave_latency = 0
                pkt.ble_conn_params_set.params.sup_timeout_ms = 4000
                session.send_packet(pkt)
                
            else:
                print(f"[?] Lệnh không hợp lệ: {cmd}. Gõ 'help' để xem các lệnh.")
                
        except KeyboardInterrupt:
            running = False
            break
        except Exception as e:
            print(f"[!] Error: {e}")

    rx_th.join(timeout=1.0)
    print("\n[DONE] Thoát chương trình.")

import serial.tools.list_ports

def auto_detect_com_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        # Tùy biến điều kiện nhận diện ở đây nếu cần, ví dụ check 'JLink'
        # Nhưng thông thường cứ lấy cổng thiết bị USB Serial đầu tiên
        if 'USB' in port.description or 'JLink' in port.description or 'Serial' in port.description:
            return port.device
    
    # Rơi vào trường hợp không tìm thấy cổng phù hợp, lấy cổng mở được đầu tiên
    if len(ports) > 0:
        return ports[0].device
    return None

def main():
    import sys
    
    port = None
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        print("[!] Đang tự động dò tìm cổng COM...")
        port = auto_detect_com_port()
        
    if not port:
        print("[X] KHÔNG TÌM THẤY cổng COM nào đang cắm vào máy tính!")
        print("    Vui lòng cắm mạch vào hoặc chỉ định thủ công: python test_central.py COM<số>")
        sys.exit(1)
    baudrate = 115200
    
    print(f"Connecting to {port} at {baudrate}...")
    try:
        with VvTestSession(port, baudrate, debug=False) as session:
            SRC_DEBUG = pb.PACKET_ADDR_DEBUG
            DST_CENTRAL = pb.PACKET_ADDR_CENTRAL
            run_interactive(session, SRC_DEBUG, DST_CENTRAL)
    except Exception as e:
        print(f"Error during test execution: {e}")

if __name__ == "__main__":
    main()