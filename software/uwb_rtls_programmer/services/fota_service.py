import os
import sys
import time

# Ensure common is in sys.path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common.transport import VvAddress
from common.commands import CommandFactory
from common import protocol_pb2 as pb
from utils.dongle_session import DongleSession

MEM_APP_START = 0x0800_C000
MEM_APP_END   = 0x0804_0000

class HexParseError(Exception):
    pass

def _parse_intel_hex(hex_path: str) -> bytes:
    raw: dict[int, int] = {}
    base_addr = 0
    upper_linear = 0
    with open(hex_path, "r") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line.startswith(":"): continue
            try: rec = bytes.fromhex(line[1:])
            except ValueError as exc: raise HexParseError(f"Line {lineno}: {exc}") from exc
            
            byte_count = rec[0]
            address = (rec[1] << 8) | rec[2]
            record_type = rec[3]
            data = rec[4 : 4 + byte_count]
            checksum = rec[4 + byte_count]

            calc_sum = (sum(rec[: 4 + byte_count]) & 0xFF)
            expected = ((~calc_sum + 1) & 0xFF)
            if checksum != expected:
                raise HexParseError(f"Line {lineno}: checksum mismatch")
                
            if record_type == 0x00:
                abs_addr = upper_linear + base_addr + address
                for i, b in enumerate(data): raw[abs_addr + i] = b
            elif record_type == 0x01: break
            elif record_type == 0x02: base_addr = ((data[0] << 8) | data[1]) << 4
            elif record_type == 0x04: upper_linear = ((data[0] << 8) | data[1]) << 16

    if not raw: raise HexParseError("HEX file contains no data records")
    app_bytes = {addr: val for addr, val in raw.items() if MEM_APP_START <= addr < MEM_APP_END}
    if not app_bytes: raise HexParseError("No data found in app region")
    
    max_addr = max(app_bytes.keys())
    image_len = max_addr - MEM_APP_START + 1
    padded_len = (image_len + 3) & ~3
    blob = bytearray(b"\xFF" * padded_len)
    for addr, val in app_bytes.items(): blob[addr - MEM_APP_START] = val
    return bytes(blob)

def _split_chunks(data: bytes, chunk_size: int) -> list:
    chunks = []
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        if len(chunk) % 4 != 0:
            chunk = chunk + b"\xFF" * (4 - len(chunk) % 4)
        chunks.append((MEM_APP_START + offset, bytes(chunk)))
    return chunks

class FotaService:
    def __init__(self):
        self.factory = CommandFactory()

    def auto_probe_dongle(self):
        return DongleSession.auto_probe(src=int(VvAddress.DEBUG), debug=False)

    def ping_dongle(self, port):
        try:
            with DongleSession(port, baud=115200, debug=False) as session:
                seq = session.proto.next_seq()
                pkt = session.proto.pb.packet_t()
                pkt.hdr.addr.src = int(VvAddress.DEBUG)
                pkt.hdr.addr.dst = int(VvAddress.CENTRAL)
                pkt.hdr.seq = seq
                pkt.device_information_get.dummy = 0
                match, _ = session.send_expect_param(pkt, "device_information_resp", timeout_s=0.5)
                return match is not None
        except Exception:
            return False

    def scan_nearby_devices(self, port, log_cb, result_cb):
        if not port:
            log_cb("[FOTA] ERROR: Dongle not detected. Please plug in the Dongle first.")
            return

        with DongleSession(port, baud=115200, debug=False) as session:
            seq = session.proto.next_seq()
            pkt = self.factory._base(int(VvAddress.DEBUG), int(VvAddress.CENTRAL), seq)
            pkt.ble_scan_start.duration_ms = 4000
            pkt.ble_scan_start.interval_ms = 100
            pkt.ble_scan_start.window_ms = 50
            pkt.ble_scan_start.active_scanning = True
            
            session.send_packet(pkt)
            
            results = {}
            deadline = time.time() + 4.5
            while time.time() < deadline:
                pkts = session.recv_packets(0.1)
                for p in pkts:
                    if p.WhichOneof("params") == "ble_scan_result":
                        mac = p.ble_scan_result.mac_address.hex().upper()
                        mac_str = ":".join(mac[i:i+2] for i in range(0, len(mac), 2))
                        if mac_str not in results:
                            results[mac_str] = {
                                'name': p.ble_scan_result.name,
                                'rssi': p.ble_scan_result.rssi_dbm,
                                'sn': p.ble_scan_result.serial_number
                            }
                            result_cb({mac_str: results[mac_str]})

    def connect_to_device(self, port, mac_bytes, mac_str, log_cb, connected_cb):
        if not port:
            log_cb("[FOTA] ERROR: Dongle not detected.")
            return

        with DongleSession(port, baud=115200, debug=False) as session:
            # Send stop scan first for safety
            seq_stop = session.proto.next_seq()
            pkt_stop = self.factory._base(int(VvAddress.DEBUG), int(VvAddress.CENTRAL), seq_stop)
            pkt_stop.ble_scan_stop.dummy = 0
            session.send_packet(pkt_stop)
            time.sleep(0.5)

            seq = session.proto.next_seq()
            pkt = self.factory._base(int(VvAddress.DEBUG), int(VvAddress.CENTRAL), seq)
            pkt.ble_connect.mac_address = mac_bytes
            session.send_packet(pkt)
            
            deadline = time.time() + 5.0
            connected = False
            while time.time() < deadline:
                pkts = session.recv_packets(0.1)
                for p in pkts:
                    if p.WhichOneof("params") == "ble_status_resp":
                        if p.ble_status_resp.state == pb.BLE_STATE_CONNECTED:
                            connected = True
                            break
                if connected: break
            
            if connected:
                log_cb(f"[FOTA] Connected successfully to {mac_str}!")
                connected_cb(f"Connected: {mac_str}")
            else:
                log_cb("[FOTA] Failed to connect. Check if the device is advertising.")
                connected_cb("Disconnected")

    def disconnect_device(self, port, log_cb, disconnected_cb):
        if not port:
            return
        with DongleSession(port, baud=115200, debug=False) as session:
            seq = session.proto.next_seq()
            pkt = self.factory._base(int(VvAddress.DEBUG), int(VvAddress.CENTRAL), seq)
            pkt.ble_disconnect.reason = 0
            session.send_packet(pkt)
            log_cb("[FOTA] Disconnect command sent to Central Dongle.")
            time.sleep(0.5)
            disconnected_cb("Disconnected")

    def execute_ota_flash(self, port, hex_path, chunk_size, log_cb, progress_cb, status_cb):
        if chunk_size % 4 != 0:
            log_cb(f"[FOTA] ERROR: Chunk size ({chunk_size}) must be a multiple of 4.")
            return
            
        try:
            firmware_blob = _parse_intel_hex(hex_path)
        except Exception as e:
            log_cb(f"[FOTA] ERROR parsing HEX: {e}")
            return
            
        chunks = _split_chunks(firmware_blob, chunk_size)
        log_cb(f"[FOTA] Image size: {len(firmware_blob)} bytes. Split into {len(chunks)} chunks.")
        
        if not port:
            log_cb("[FOTA] ERROR: Dongle not detected.")
            return
            
        src = int(VvAddress.DEBUG)
        dst = int(VvAddress.PERIPHERAL)
        
        with DongleSession(port, baud=115200, debug=False) as session:
            # 1. enter_to_bootloader
            log_cb("[FOTA] [STEP 1] Sending enter_to_bootloader...")
            seq = session.proto.next_seq()
            pkt = self.factory.enter_to_bootloader(src, dst, seq)
            session.send_packet(pkt)
            
            deadline = time.time() + 0.5
            ack_ok = False
            while time.time() < deadline:
                for p in session.recv_packets(0.1):
                    if p.WhichOneof("params") == "ack" and p.ack.ack_seq == seq:
                        ack_ok = True
            if ack_ok:
                log_cb("[FOTA] enter_to_bootloader ACK received, waiting for reboot...")
            else:
                log_cb("[FOTA] No ACK for enter_to_bootloader, assuming already in bootloader.")
            
            time.sleep(2.0)
            _ = session.recv_packets(0.2)
            
            # 2. flash_erase
            log_cb("[FOTA] [STEP 2] Sending flash_erase...")
            seq = session.proto.next_seq()
            pkt = self.factory.flash_erase(src, dst, seq)
            pkt.flash_erase.partition_id = 1
            pkt.flash_erase.flash_addr_region = pb.FLASH_ADDR_REGION_APPLICATION
            session.send_packet(pkt)
            
            erasing = False
            receiving = False
            deadline = time.time() + 8.0
            while time.time() < deadline:
                for p in session.recv_packets(0.1):
                    if p.WhichOneof("params") == "fota_state_resp":
                        if p.fota_state_resp.state == pb.FOTA_STATE_ERASING:
                            erasing = True
                            log_cb("[FOTA] Device is ERASING...")
                        elif p.fota_state_resp.state == pb.FOTA_STATE_RECEIVING:
                            receiving = True
                            log_cb("[FOTA] Device is RECEIVING (Erase Complete).")
                        elif p.fota_state_resp.state == pb.FOTA_STATE_ERROR:
                            log_cb("[FOTA] [FAIL] Device reported ERROR during erase.")
                            return
                if receiving: break
                
            if not receiving:
                log_cb("[FOTA] [FAIL] Never reached RECEIVING state after erase.")
                return
                
            # 3. flash_write chunks
            log_cb(f"[FOTA] [STEP 3] Sending {len(chunks)} chunks...")
            total = len(chunks)
            for idx, (addr, data) in enumerate(chunks, 1):
                retry_count = 0
                success = False
                while retry_count < 3 and not success:
                    seq = session.proto.next_seq()
                    pkt = self.factory._base(src, dst, seq)
                    pkt.flash_write.address = addr
                    pkt.flash_write.data = data
                    session.send_packet(pkt)
                    
                    ack_deadline = time.time() + 0.5
                    while time.time() < ack_deadline:
                        for p in session.recv_packets(0.05):
                            if p.WhichOneof("params") == "ack" and p.ack.ack_seq == seq:
                                success = True
                                break
                        if success: break
                    
                    if not success:
                        retry_count += 1
                        log_cb(f"[FOTA] [WARN] No ACK for chunk {idx}/{total}, retrying ({retry_count}/3)...")
                        
                if not success:
                    log_cb(f"[FOTA] [FAIL] Chunk {idx}/{total} failed after 3 retries. Aborting OTA.")
                    return
                
                pct = int((idx / total) * 100)
                progress_cb(pct)
                if idx % 10 == 0 or idx == total:
                    log_cb(f"[FOTA] Written {idx}/{total} chunks ({pct}%)...")
                    
            log_cb("[FOTA] All chunks written successfully.")
            
            # 4. flash_verify
            log_cb("[FOTA] [STEP 4] Sending flash_verify...")
            seq = session.proto.next_seq()
            pkt = self.factory.flash_verify(src, dst, seq)
            session.send_packet(pkt)
            
            finished = False
            deadline = time.time() + 5.0
            while time.time() < deadline:
                for p in session.recv_packets(0.1):
                    if p.WhichOneof("params") == "fota_state_resp":
                        if p.fota_state_resp.state == pb.FOTA_STATE_VERIFYING:
                            log_cb("[FOTA] Device is VERIFYING...")
                        elif p.fota_state_resp.state == pb.FOTA_STATE_FINISHED:
                            finished = True
                            log_cb("[FOTA] Device reported FINISHED.")
                        elif p.fota_state_resp.state == pb.FOTA_STATE_ERROR:
                            log_cb("[FOTA] Device reported ERROR during verification.")
                            return
                if finished: break
            
            if finished:
                log_cb("[FOTA] FOTA TEST ✓ PASSED. Device will reboot now.")
                progress_cb(100)
            else:
                log_cb("[FOTA] [FAIL] Image verification failed (no FINISHED state).")
                
            # Device reboots and severs connection, so we are now disconnected
            status_cb("Disconnected")
