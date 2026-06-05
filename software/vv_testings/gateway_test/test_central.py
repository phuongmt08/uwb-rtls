from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from datetime import datetime

# Add the parent directories to sys.path so we can import vv_testings and common.
sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from common.commands import CommandFactory
from common import protocol_pb2 as pb
from vv_test_session import VvTestSession

# Global variables for the Live Dashboard
running = True
devices = {}  # Format: "AA:BB:CC:DD:EE:FF" -> {"bytes": b'...', "name": "...", "rssi": -60, "last_seen": 1234567.89}
factory = CommandFactory()
LOST_DEVICE_TIMEOUT_S = 5.0
packet_debug_enabled = False

# Constants for Log Parsing
MAX_RECORD_LEN = 512
EPOCH_MS_MIN_FOR_DATETIME = 946684800000  # 2000-01-01 00:00:00 UTC

class FlashLogStreamParser:
    """Parse flash-log stream entries:
    [len_lo][len_hi][raw_record(len)][pad to 4-byte].
    raw_record = [log_type][obj_code][timestamp(6)][msg_len][msg].
    """
    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        self._buf.extend(chunk)
        lines: list[str] = []
        while True:
            if len(self._buf) < 2:
                break
            rec_len = int(self._buf[0]) | (int(self._buf[1]) << 8)
            if rec_len == 0 or rec_len > MAX_RECORD_LEN:
                del self._buf[0:1]
                continue
            entry_len = (2 + rec_len + 3) & ~3
            if len(self._buf) < entry_len:
                break
            rec = bytes(self._buf[2 : 2 + rec_len])
            del self._buf[:entry_len]
            line = self._decode_record(rec)
            if line is not None:
                lines.append(line)
        return lines

    @staticmethod
    def _format_timestamp(timestamp: int) -> str:
        if timestamp >= EPOCH_MS_MIN_FOR_DATETIME:
            try:
                dt = datetime.fromtimestamp(timestamp / 1000.0)
                return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except (OverflowError, OSError, ValueError):
                pass
        return str(timestamp)

    @staticmethod
    def _decode_record(rec: bytes) -> str | None:
        if len(rec) < 9:
            return None
        log_type = rec[0]
        obj_code = rec[1]
        timestamp = int.from_bytes(rec[2:8], byteorder="little", signed=False)
        msg_len = rec[8]
        if 9 + msg_len > len(rec):
            return None
        msg = rec[9 : 9 + msg_len].decode("utf-8", errors="replace")
        if log_type == 0xFE:
            level = "INFO"
            color = "\033[37m"  # white
        elif log_type == 0xFF:
            level = "DEBUG"
            color = "\033[36m"  # cyan
        elif log_type == 0xFD:
            level = "WARN"
            color = "\033[33m"  # yellow
        else:
            level = "ERROR"
            color = "\033[31m"  # red
        ts_text = FlashLogStreamParser._format_timestamp(timestamp)
        reset = "\033[0m"
        return f"{color}[{ts_text}] [{level:<5}] [0x{obj_code:02X}] {msg}{reset}"

log_parser = FlashLogStreamParser()

BLE_STATE_NAMES = {
    pb.BLE_STATE_UNSPECIFIED: "UNSPECIFIED",
    pb.BLE_STATE_IDLE: "IDLE",
    pb.BLE_STATE_SCANNING: "SCANNING",
    pb.BLE_STATE_ADVERTISING: "ADVERTISING",
    pb.BLE_STATE_CONNECTING: "CONNECTING",
    pb.BLE_STATE_CONNECTED: "CONNECTED",
}

def _safe_enum_name(enum_desc, value: int) -> str:
    item = enum_desc.values_by_number.get(value)
    return item.name if item else str(value)

def _packet_debug_line(pkt: pb.packet_t) -> str:
    ptype = pkt.WhichOneof("params") or "<none>"

    seq = "-"
    src = "-"
    dst = "-"
    if pkt.HasField("hdr"):
        seq = str(pkt.hdr.seq)
        if pkt.hdr.HasField("addr"):
            src = _safe_enum_name(pb.device_addr_t.DESCRIPTOR, pkt.hdr.addr.src)
            dst = _safe_enum_name(pb.device_addr_t.DESCRIPTOR, pkt.hdr.addr.dst)

    detail = ""
    if ptype == "ble_status_resp":
        state_name = BLE_STATE_NAMES.get(pkt.ble_status_resp.state, f"UNKNOWN({pkt.ble_status_resp.state})")
        detail = f" state={state_name} rssi={pkt.ble_status_resp.rssi_dbm}"
        if pkt.ble_status_resp.HasField("disconnect_reason"):
            detail += f" disconnect_reason=0x{pkt.ble_status_resp.disconnect_reason:02X}"
    elif ptype == "ble_scan_result":
        mac = ":".join(f"{b:02X}" for b in reversed(pkt.ble_scan_result.mac_address))
        detail = f" mac={mac} name='{pkt.ble_scan_result.name}' rssi={pkt.ble_scan_result.rssi_dbm}"
    elif ptype == "ble_conn_params_resp":
        p = pkt.ble_conn_params_resp.params
        detail = f" min={p.min_interval_ms} max={p.max_interval_ms} lat={p.slave_latency} to={p.sup_timeout_ms}"
    elif ptype == "log_data":
        detail = f" type={pkt.log_data.type} len={len(pkt.log_data.data)}"

    return f"[RX][seq={seq}][src={src}->dst={dst}] {ptype}{detail}"

def rx_thread_func(session: VvTestSession):
    global running, devices, packet_debug_enabled
    while running:
        try:
            # Liên tục đọc gói tin trả về không block, timeout nhỏ
            pkts = session.recv_packets(timeout_s=0.1)
            for pkt in pkts:
                if packet_debug_enabled:
                    print(f"\n[DBG] {_packet_debug_line(pkt)}")
                    print("cmd> ", end="", flush=True)

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
                    state_name = BLE_STATE_NAMES.get(pkt.ble_status_resp.state, f"UNKNOWN({pkt.ble_status_resp.state})")
                    if pkt.ble_status_resp.HasField("disconnect_reason"):
                        print(f"\n[!] BLE Status -> {state_name} | disconnect_reason=0x{pkt.ble_status_resp.disconnect_reason:02X}")
                    else:
                        print(f"\n[!] BLE Status -> {state_name}")
                    print("cmd> ", end="", flush=True)

                elif ptype == "log_data":
                    p = pkt.log_data
                    lines = log_parser.feed(p.data)
                    for line in lines:
                        print(f"\n{line}")
                    if lines:
                        print("cmd> ", end="", flush=True)

            # Firmware gửi scan_result mỗi 2s, nếu quá timeout không thấy cập nhật thì coi là mất.
            now = time.time()
            for mac, info in list(devices.items()):
                if now - info["last_seen"] > LOST_DEVICE_TIMEOUT_S:
                    print(f"\n[-] Lost Device: {mac} ('{info['name']}') | last RSSI: {info['rssi']} dBm")
                    del devices[mac]
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
    print("  stub [mcu|peri]   : Gửi 'device_information_get' để test đường truyền")
    print("  debug on/off    : Bật/tắt log packet RX từ central")
    print("  help            : Hiển thị bảng lệnh này")
    print("  exit            : Thoát")
    print("--------------------------\n")

def run_interactive(session: VvTestSession, src: int, dst: int):
    global running, devices, packet_debug_enabled
    
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
                    # Nếu hơn LOST_DEVICE_TIMEOUT_S không có tín hiệu mới -> coi như rớt
                    if now - info["last_seen"] > LOST_DEVICE_TIMEOUT_S:
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
                packet_debug_enabled = True
                print("[DBG] Packet debug ON (để theo dõi packet central trả về khi connect)")
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

            elif cmd == "stub":
                target_name = cmd_line[1].lower() if len(cmd_line) > 1 else "mcu"
                if target_name in ("mcu", "tag", "anchor"):
                    target_dst = pb.PACKET_ADDR_MCU
                    target_label = "MCU"
                elif target_name in ("peri", "peripheral"):
                    target_dst = pb.PACKET_ADDR_PERIPHERAL
                    target_label = "PERIPHERAL"
                else:
                    print("[!] Sử dụng: stub [mcu|peri]")
                    continue

                print(f"[+] Gửi stub (device_information_get) tới {target_label} (0x{target_dst:02X}) ...")
                pkt = factory.device_information_get(pb.PACKET_ADDR_HOST, target_dst, session.proto.next_seq())
                session.send_packet(pkt)

            elif cmd == "debug":
                if len(cmd_line) < 2 or cmd_line[1].lower() not in ("on", "off"):
                    print("[!] Sử dụng: debug on|off")
                    continue
                packet_debug_enabled = (cmd_line[1].lower() == "on")
                print(f"[DBG] Packet debug {'ON' if packet_debug_enabled else 'OFF'}")
                
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
    import argparse
    
    parser = argparse.ArgumentParser(description="Live BLE Central Dashboard")
    parser.add_argument("--port", type=str, default=None, help="COM port, example COM21")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    args = parser.parse_args()
    
    port = args.port
    baudrate = args.baud
    
    if not port:
        print("[!] Đang tự động dò tìm cổng COM...")
        try:
            probe = VvTestSession.auto_probe(debug=False)
            if probe:
                port = probe.port
                baudrate = probe.baud
                print(f"[+] Tìm thấy thiết bị UWB tại {port} ({baudrate})")
        except Exception:
            pass
            
        if not port:
            port = auto_detect_com_port()
        
    if not port:
        print("[X] KHÔNG TÌM THẤY cổng COM nào đang cắm vào máy tính!")
        print("    Vui lòng cắm mạch vào hoặc chỉ định thủ công qua: python -m gateway_test.test_central --port COM<số>")
        sys.exit(1)
    
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
