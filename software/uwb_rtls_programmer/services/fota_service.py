import os
import serial
import struct
import sys
import time
from contextlib import contextmanager
from typing import Optional
import zlib

# Ensure common is in sys.path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common.transport import VvAddress
from common.commands import CommandFactory
from common import protocol_pb2 as pb
from utils.dongle_session import DongleSession
from serial.tools import list_ports

MEM_APP_START = 0x0800_C000
MEM_APP_END   = 0x0804_0000


FOTA_FLAG_COMPRESSED = 0x80000000
FOTA_FLAG_ACK_REQ     = 0x40000000
BLOCK_SIZE            = 8
MAX_BLOCK_RETRIES     = 5
WRITE_ACK_TIMEOUT_S   = 2
TX_PACKET_GAP_S       = 0.05
FOTA_RAW_BLOCK_SIZE   = 4096
FOTA_COMP_BLOCK_MAX   = 4160
FOTA_FRAME_MAGIC      = b"FD"
FOTA_FRAME_VERSION    = 1


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

def _compress_fota_blocks(data: bytes) -> tuple[bytes, list[int]]:
    """Encode independent 4 KB raw-DEFLATE frames for bounded-RAM decoding."""
    framed = bytearray()
    frame_end_offsets = []

    for offset in range(0, len(data), FOTA_RAW_BLOCK_SIZE):
        raw = data[offset : offset + FOTA_RAW_BLOCK_SIZE]
        compressor = zlib.compressobj(
            level=6,
            method=zlib.DEFLATED,
            wbits=-12,
        )
        compressed = compressor.compress(raw) + compressor.flush()
        if len(compressed) > FOTA_COMP_BLOCK_MAX:
            raise ValueError(
                f"compressed block too large: {len(compressed)} bytes"
            )

        framed.extend(
            struct.pack(
                "<2sBBHH",
                FOTA_FRAME_MAGIC,
                FOTA_FRAME_VERSION,
                0,
                len(raw),
                len(compressed),
            )
        )
        framed.extend(compressed)
        frame_end_offsets.append(len(framed))

    return bytes(framed), frame_end_offsets


def _split_chunks(data: bytes, chunk_size: int, pad_last: bool = True) -> list:
    chunks = []
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        if pad_last and len(chunk) % 4 != 0:
            chunk = chunk + b"\xFF" * (4 - len(chunk) % 4)
        chunks.append((MEM_APP_START + offset, bytes(chunk)))
    return chunks


def _group_chunks_for_ack(
    chunks: list,
    max_window_size: int,
    frame_end_offsets: list[int] | None = None,
) -> list:
    """End an ACK window at both its size limit and each DEFLATE frame."""
    if max_window_size <= 0:
        raise ValueError("max_window_size must be positive")

    frame_ends = frame_end_offsets or []
    frame_index = 0
    windows = []
    window = []

    for idx, (addr, data) in enumerate(chunks):
        window.append((idx, addr, data))
        chunk_end = (addr - MEM_APP_START) + len(data)
        completes_frame = False

        while frame_index < len(frame_ends) and frame_ends[frame_index] <= chunk_end:
            completes_frame = True
            frame_index += 1

        is_last = idx == len(chunks) - 1
        if len(window) >= max_window_size or completes_frame or is_last:
            windows.append(window)
            window = []

    return windows


class FotaService:
    def __init__(self):
        self.factory = CommandFactory()
        self._persistent_session = None
        self._device_verification_attempted = False
        self._verified_device_info = None
        self._scan_results_by_mac: dict[str, dict] = {}

    def open_persistent_session(self, port):
        self.close_persistent_session()
        session = DongleSession(port, baud=115200, debug=False)
        session.__enter__()
        self._persistent_session = session
        return session

    def close_persistent_session(self):
        session = self._persistent_session
        self._persistent_session = None
        self._device_verification_attempted = False
        self._verified_device_info = None
        if session is not None:
            session.__exit__(None, None, None)

    @contextmanager
    def _session(self, port):
        session = self._persistent_session
        if session is not None and session.port == port and session.ser is not None:
            yield session
            return
        with DongleSession(port, baud=115200, debug=False) as temporary:
            yield temporary

    def _reset_device_verification(self):
        self._device_verification_attempted = False
        self._verified_device_info = None

    def recv_unsolicited_ble_status(self, timeout_s=0.05):
        session = self._persistent_session
        if session is None or session.ser is None:
            return []

        statuses = []
        for pkt in session.recv_packets(timeout_s):
            if pkt.WhichOneof("params") != "ble_status_resp":
                continue
            statuses.append(
                self._validated_ble_status(session, pkt.ble_status_resp)
            )
        return statuses

    def auto_probe_dongle(self, ignore_ports=None):
        return DongleSession.auto_probe(src=int(VvAddress.HOST), debug=False, ignore_ports=ignore_ports)

    def ping_dongle(self, port):
        # Elegant, conflict-free hotplug check: just verify if the virtual COM port still exists in the OS
        return any(p.device == port for p in list_ports.comports())

    @staticmethod
    def ble_status_dict(resp) -> dict:
        return {
            "state": int(resp.state),
            "rssi_dbm": int(resp.rssi_dbm),
            "disconnect_reason": int(resp.disconnect_reason),
        }

    def _get_device_information(self, session, timeout_s=0.6):
        seq = session.proto.next_seq()
        pkt = self.factory.device_information_get(
            int(VvAddress.HOST), int(VvAddress.MCU), seq
        )
        match, _ = session.send_expect_param(
            pkt, "device_information_resp", timeout_s=timeout_s
        )
        if match is None:
            return None

        info = match.device_information_resp
        return {
            "device_type": int(info.device_type),
            "role": int(info.role),
            "serial_number": int(info.serial_number),
            "hw_version": int(info.hw_version),
        }

    def _validated_ble_status(self, session, resp) -> dict:
        status = self.ble_status_dict(resp)
        status["central_state"] = status["state"]
        status["device_verified"] = False
        status["device_info"] = None

        if status["state"] != pb.BLE_STATE_CONNECTED:
            self._reset_device_verification()
            return status

        # Verify the BLE-to-MCU path exactly once per connection.
        # The 5-second status polls reuse this cached result.
        if not self._device_verification_attempted:
            self._device_verification_attempted = True
            for attempt in range(3):
                self._verified_device_info = self._get_device_information(session)
                if self._verified_device_info is not None:
                    break
                if attempt < 2:
                    time.sleep(0.1)

        if self._verified_device_info is not None:
            status["device_verified"] = True
            status["device_info"] = self._verified_device_info
            return status

        status["state"] = pb.BLE_STATE_CONNECTING
        return status

    def get_ble_status(self, port) -> Optional[dict]:
        if not port:
            return None
        try:
            with self._session(port) as session:
                seq = session.proto.next_seq()
                pkt = self.factory.ble_status_get(int(VvAddress.HOST), int(VvAddress.CENTRAL), seq)
                match, _ = session.send_expect_param(pkt, "ble_status_resp", timeout_s=0.3)
                if match is not None:
                    return self._validated_ble_status(session, match.ble_status_resp)
        except Exception:
            pass
        return None

    def scan_nearby_devices(self, port, log_cb, result_cb, ble_status_cb=None):
        if not port:
            log_cb("[FOTA] ERROR: Dongle not detected. Please plug in the Dongle first.")
            return

        with self._session(port) as session:
            seq = session.proto.next_seq()
            pkt = self.factory.ble_scan_start(int(VvAddress.HOST), int(VvAddress.CENTRAL), seq)
            pkt.ble_scan_start.duration_ms = 2000
            pkt.ble_scan_start.interval_ms = 100
            pkt.ble_scan_start.window_ms = 100
            pkt.ble_scan_start.active_scanning = True
            
            session.send_packet(pkt)
            
            results = {}
            deadline = time.time() + 2.2
            while time.time() < deadline:
                pkts = session.recv_packets(0.1)
                for p in pkts:
                    param_name = p.WhichOneof("params")
                    if param_name == "ble_status_resp" and ble_status_cb:
                        ble_status_cb(self.ble_status_dict(p.ble_status_resp))

                    if param_name == "ble_scan_result":
                        mac = p.ble_scan_result.mac_address.hex().upper()
                        mac_str = ":".join(mac[i:i+2] for i in range(0, len(mac), 2))
                        previous = (
                            results.get(mac_str)
                            or self._scan_results_by_mac.get(mac_str)
                            or {}
                        )
                        name = str(p.ble_scan_result.name or "").strip() or "-"
                        scan_serial = int(
                            getattr(p.ble_scan_result, "serial_number", 0) or 0
                        )
                        current = {
                            "name": name,
                            "rssi": int(p.ble_scan_result.rssi_dbm),
                            "sn": scan_serial or int(previous.get("sn") or 0),
                        }
                        if results.get(mac_str) != current:
                            results[mac_str] = current
                            self._scan_results_by_mac[mac_str] = current.copy()
                            result_cb({mac_str: current.copy()})

    def connect_to_device(self, port, mac_bytes, mac_str, log_cb, connected_cb, ble_status_cb=None):
        if not port:
            log_cb("[FOTA] ERROR: Dongle not detected.")
            return

        with self._session(port) as session:
            # Send stop scan first for safety
            seq_stop = session.proto.next_seq()
            pkt_stop = self.factory.ble_scan_stop(int(VvAddress.HOST), int(VvAddress.CENTRAL), seq_stop)
            session.send_packet(pkt_stop)
            time.sleep(0.5)

            seq = session.proto.next_seq()
            pkt = self.factory.ble_connect(int(VvAddress.HOST), int(VvAddress.CENTRAL), seq)
            pkt.ble_connect.mac_address = mac_bytes
            self._reset_device_verification()
            session.send_packet(pkt)
            
            deadline = time.time() + 5.0
            connected = False
            while time.time() < deadline:
                pkts = session.recv_packets(0.1)
                for p in pkts:
                    if p.WhichOneof("params") == "ble_status_resp":
                        if p.ble_status_resp.state == pb.BLE_STATE_CONNECTED:
                            status = self._validated_ble_status(session, p.ble_status_resp)
                            if ble_status_cb:
                                ble_status_cb(status)
                            connected = status["device_verified"]
                            if connected:
                                info = status["device_info"]
                                log_cb(
                                    "[FOTA] Device information verified: "
                                    f"SN={info['serial_number']} type={info['device_type']} "
                                    f"role={info['role']} hw={info['hw_version']}"
                                )
                                break
                        elif ble_status_cb:
                            ble_status_cb(self.ble_status_dict(p.ble_status_resp))
                if connected: break
            
            if connected:
                log_cb(f"[FOTA] Connected successfully to {mac_str}!")
                connected_cb(f"Connected: {mac_str}")
            else:
                log_cb(
                    "[FOTA] Failed to verify device_information_resp over BLE; "
                    "connection is not usable."
                )
                connected_cb("Disconnected")

    def disconnect_device(self, port, log_cb, disconnected_cb, ble_status_cb=None):
        if not port:
            return
        with self._session(port) as session:
            seq = session.proto.next_seq()
            pkt = self.factory.ble_disconnect(int(VvAddress.HOST), int(VvAddress.CENTRAL), seq)
            session.send_packet(pkt)
            log_cb("[FOTA] Disconnect command sent to Central Dongle.")
            
            # Poll status to verify it's actually disconnected
            deadline = time.time() + 3.0
            disconnected = False
            while time.time() < deadline:
                # Query status
                seq_status = session.proto.next_seq()
                pkt_status = self.factory.ble_status_get(int(VvAddress.HOST), int(VvAddress.CENTRAL), seq_status)
                session.send_packet(pkt_status)
                
                # Receive responses
                pkts = session.recv_packets(0.15)
                for p in pkts:
                    if p.WhichOneof("params") == "ble_status_resp":
                        if ble_status_cb:
                            ble_status_cb(self.ble_status_dict(p.ble_status_resp))
                        log_cb(f"[FOTA] Dongle state reported: {p.ble_status_resp.state}")
                        if p.ble_status_resp.state != pb.BLE_STATE_CONNECTED:
                            disconnected = True
                            break
                if disconnected:
                    break
                time.sleep(0.1)
            
            if disconnected:
                log_cb("[FOTA] Disconnection confirmed by Dongle state.")
            else:
                log_cb("[FOTA] WARNING: Dongle did not confirm disconnection (timeout).")
                
            disconnected_cb("Disconnected")

    def execute_ota_flash(self, port, hex_path, chunk_size, mac_bytes, mac_str, log_cb, progress_cb, status_cb, ble_status_cb=None):
        start_time = time.time()
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
            
        src = int(VvAddress.HOST)
        dst = int(VvAddress.MCU)
        
        with self._session(port) as session:
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
                
                # Perform scan & reconnect sequence
                log_cb("[FOTA] BLE connection dropped due to device reboot.")
                log_cb("[FOTA] Sending disconnect to clean up Central connection state...")
                try:
                    seq_disc = session.proto.next_seq()
                    pkt_disc = self.factory.ble_disconnect(src, int(VvAddress.CENTRAL), seq_disc)
                    session.send_packet(pkt_disc)
                    time.sleep(1.5)
                except Exception as e:
                    log_cb(f"[FOTA] [WARN] Failed to send clean disconnect command: {e}")
                
                # Reconnect
                reconnect_ok = self._scan_and_reconnect(session, src, mac_bytes, mac_str, log_cb, ble_status_cb)
                if not reconnect_ok:
                    log_cb("[FOTA] [FAIL] Could not reconnect to device in Bootloader. Aborting OTA.")
                    return
            else:
                log_cb("[FOTA] No ACK for enter_to_bootloader, assuming already in bootloader.")
                time.sleep(1.0)
            
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

            # 2.5. Optimize BLE connection params for fast FOTA transfer (slave_latency = 0)
            try:
                log_cb("[FOTA] Setting BLE slave_latency=0 for high-speed transfer...")
                seq_params = session.proto.next_seq()
                pkt_params = self.factory.ble_conn_params_set(
                    src=src,
                    dst=int(VvAddress.CENTRAL),
                    seq=seq_params,
                    min_interval_ms=8,
                    max_interval_ms=15,
                    slave_latency=0,
                    sup_timeout_ms=4000
                )
                session.send_packet(pkt_params)
                time.sleep(0.2)
                _ = session.recv_packets(0.1)
            except Exception as e:
                log_cb(f"[FOTA] [WARN] Could not set BLE conn params: {e}")

            # 3. Stream independent raw-DEFLATE frames. Each frame expands to
            # at most 4 KB, so the bootloader never buffers the complete image.
            try:
                compressed_blob, frame_end_offsets = _compress_fota_blocks(
                    firmware_blob
                )
                frame_count = len(frame_end_offsets)
                ratio = len(compressed_blob) / len(firmware_blob) * 100.0
                log_cb(
                    "[FOTA] [STEP 3] Block DEFLATE: "
                    f"{len(firmware_blob)} B -> {len(compressed_blob)} B "
                    f"in {frame_count} frames [{ratio:.1f}%] "
                    f"({100.0 - ratio:.1f}% saved)"
                )
                raw_chunks = _split_chunks(
                    compressed_blob,
                    chunk_size,
                    pad_last=False,
                )
                use_compress = True
            except Exception as e:
                log_cb(
                    f"[FOTA] [WARN] Block DEFLATE failed ({e}), "
                    "sending uncompressed."
                )
                raw_chunks = _split_chunks(firmware_blob, chunk_size)
                frame_end_offsets = []
                use_compress = False

            total_chunks = len(raw_chunks)
            raw_windows = _group_chunks_for_ack(
                raw_chunks,
                BLOCK_SIZE,
                frame_end_offsets,
            )
            sync_mode = ", DEFLATE-frame sync" if use_compress else ""
            log_cb(
                f"[FOTA] Streaming {total_chunks} chunks "
                f"(ACK window<={BLOCK_SIZE}{sync_mode}, "
                f"TX gap={TX_PACKET_GAP_S * 1000:.0f} ms)..."
            )

            packet_windows = []
            for raw_window in raw_windows:
                packet_window = []
                for position, (idx, addr, data) in enumerate(raw_window):
                    req_ack = position == len(raw_window) - 1

                    flags = 0
                    if use_compress:
                        flags |= FOTA_FLAG_COMPRESSED
                    if req_ack:
                        flags |= FOTA_FLAG_ACK_REQ

                    packet_addr = flags | (addr & 0x0FFFFFFF)
                    seq = session.proto.next_seq()
                    pkt = self.factory._base(src, dst, seq)
                    pkt.flash_write.address = packet_addr
                    pkt.flash_write.data = data
                    packet_window.append((idx, seq, req_ack, pkt))
                packet_windows.append(packet_window)

            i = 0
            t_write_start = time.time()
            for block in packet_windows:
                last_in_block = block[-1]
                block_seq_to_chunk = {
                    seq: idx for idx, seq, _req_ack, _pkt in block
                }
                block_retry_count = 0

                while block_retry_count < MAX_BLOCK_RETRIES:
                    # Retrying the full block is safe because the bootloader
                    # skips compressed input addresses it has already committed.
                    transport_error = None
                    for idx, seq, req_ack, pkt in block:
                        try:
                            session.send_packet(pkt)
                        except (
                            serial.SerialTimeoutException,
                            serial.SerialException,
                        ) as exc:
                            transport_error = exc
                            break
                        time.sleep(TX_PACKET_GAP_S)

                    ack_received = False
                    nack_info = None
                    if transport_error is None:
                        deadline = time.time() + WRITE_ACK_TIMEOUT_S
                        while time.time() < deadline:
                            for p in session.recv_packets(0.05):
                                if p.WhichOneof("params") != "ack":
                                    continue

                                ack_seq = int(p.ack.ack_seq)
                                ack_response = int(p.ack.response)
                                if (ack_response != pb.PACKET_ACK_RESPONSE_ACK and
                                        ack_seq in block_seq_to_chunk):
                                    nack_info = (
                                        ack_seq,
                                        ack_response,
                                        block_seq_to_chunk[ack_seq],
                                    )
                                    log_cb(
                                        "[FOTA] [WARN] Bootloader NACK: "
                                        f"seq={ack_seq}, response={ack_response}, "
                                        f"chunk={block_seq_to_chunk[ack_seq] + 1}/"
                                        f"{total_chunks}, block_end_seq="
                                        f"{last_in_block[1]}"
                                    )
                                    deadline = 0
                                    break

                                if ack_seq == last_in_block[1]:
                                    if ack_response == pb.PACKET_ACK_RESPONSE_ACK:
                                        ack_received = True
                            if ack_received:
                                break
                    else:
                        log_cb(
                            "[FOTA] [WARN] Serial TX backpressure at chunk "
                            f"{idx + 1}/{total_chunks}: {transport_error}"
                        )
                        if not isinstance(
                            transport_error,
                            serial.SerialTimeoutException,
                        ):
                            log_cb(
                                "[FOTA] [FAIL] Dongle COM handle became "
                                "invalid; waiting for automatic reconnect."
                            )
                            return
                        time.sleep(0.1)

                    if ack_received:
                        break

                    block_retry_count += 1
                    if nack_info is not None:
                        log_cb(
                            "[FOTA] [WARN] Retrying block after NACK "
                            f"({block_retry_count}/{MAX_BLOCK_RETRIES})..."
                        )
                    else:
                        log_cb(
                            f"[FOTA] [WARN] No matching ACK for block chunk "
                            f"{last_in_block[0]+1}/{total_chunks} "
                            f"(expected ack_seq={last_in_block[1]}), retrying "
                            f"({block_retry_count}/{MAX_BLOCK_RETRIES})..."
                        )

                if not ack_received:
                    log_cb(
                        f"[FOTA] [FAIL] Block ending at chunk "
                        f"{last_in_block[0]+1}/{total_chunks} failed after "
                        f"{MAX_BLOCK_RETRIES} attempts. Aborting OTA."
                    )
                    return

                i += len(block)
                pct = int((i / total_chunks) * 100)
                progress_cb(pct)
                if i % 20 == 0 or i == total_chunks:
                    log_cb(f"[FOTA] Streamed {i}/{total_chunks} chunks ({pct}%)...")

            t_write_elapsed = max(0.001, time.time() - t_write_start)
            log_cb(f"[FOTA] All chunks written in {t_write_elapsed:.2f}s ({len(firmware_blob)/t_write_elapsed/1024.0:.1f} KB/s effective throughput).")
            
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
                elapsed_time = time.time() - start_time
                log_cb(f"[FOTA] FOTA TEST ✓ PASSED. Device will reboot now. (OTA took {elapsed_time:.1f}s)")
                progress_cb(100)
            else:
                log_cb("[FOTA] [FAIL] Image verification failed (no FINISHED state).")
                
            # 5. Restore BLE connection params (slave_latency = 6)
            try:
                seq_params = session.proto.next_seq()
                pkt_params = self.factory.ble_conn_params_set(
                    src=src,
                    dst=int(VvAddress.CENTRAL),
                    seq=seq_params,
                    min_interval_ms=7.5,
                    max_interval_ms=15,
                    slave_latency=4,
                    sup_timeout_ms=4000
                )
                session.send_packet(pkt_params)
                log_cb("[FOTA] Restored BLE slave_latency=4 for low-latency operation.")
            except Exception:
                pass

            # The background monitor thread will query the actual BLE status of the dongle
            # and automatically transition the UI to Disconnected when the link is severed.

    def _scan_and_reconnect(self, session, src, mac_bytes, mac_str, log_cb, ble_status_cb=None):
        import time
        from common.transport import VvAddress
        log_cb(f"[FOTA] Re-scanning for {mac_str} in Bootloader mode...")
        deadline = time.time() + 8.0
        found = False
        
        # Send scan start
        seq = session.proto.next_seq()
        pkt_scan = self.factory.ble_scan_start(src, int(VvAddress.CENTRAL), seq)
        pkt_scan.ble_scan_start.duration_ms = 8000
        session.send_packet(pkt_scan)
        
        while time.time() < deadline:
            for p in session.recv_packets(0.1):
                if p.WhichOneof("params") == "ble_scan_result":
                    if p.ble_scan_result.mac_address == mac_bytes:
                        found = True
                        break
            if found:
                break
                
        # Send scan stop
        seq = session.proto.next_seq()
        pkt_stop = self.factory.ble_scan_stop(src, int(VvAddress.CENTRAL), seq)
        session.send_packet(pkt_stop)
        time.sleep(0.2)
        
        if not found:
            log_cb("[FOTA] [FAIL] Could not find device advertising in Bootloader.")
            return False
            
        log_cb("[FOTA] Device found! Sending Connect command...")
        seq = session.proto.next_seq()
        pkt_conn = self.factory.ble_connect(src, int(VvAddress.CENTRAL), seq)
        pkt_conn.ble_connect.mac_address = mac_bytes
        self._reset_device_verification()
        session.send_packet(pkt_conn)
        
        deadline = time.time() + 8.0
        connected = False
        last_state = "Unknown"
        while time.time() < deadline:
            for p in session.recv_packets(0.1):
                if p.WhichOneof("params") == "ble_status_resp":
                    if p.ble_status_resp.state == pb.BLE_STATE_CONNECTED:
                        status = self._validated_ble_status(session, p.ble_status_resp)
                        if ble_status_cb:
                            ble_status_cb(status)
                        last_state = str(status["state"])
                        connected = status["device_verified"]
                        if connected:
                            break
                    else:
                        status = self.ble_status_dict(p.ble_status_resp)
                        if ble_status_cb:
                            ble_status_cb(status)
                        last_state = str(status["state"])
            if connected:
                break
                
        if connected:
            log_cb("[FOTA] Successfully reconnected to device in Bootloader mode!")
            time.sleep(1.0) # Wait for MTU / param exchange
            return True
        else:
            log_cb(f"[FOTA] [FAIL] Connection timeout. Dongle reported last state: {last_state} (expected {pb.BLE_STATE_CONNECTED}).")
            return False
