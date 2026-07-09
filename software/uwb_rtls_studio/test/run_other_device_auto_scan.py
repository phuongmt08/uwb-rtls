#!/usr/bin/env python3
"""
UWB RTLS Studio - Test Helper: Other BLE Device Auto Scan/Connect

This helper acts as an external BLE device client for test mode only.
It connects to the app's mock TCP serial bridge on 127.0.0.1:9999 and:
  - advertises one "Other Device" entry
  - responds to ble_scan_start / ble_scan_stop
  - responds to ble_status_get / ble_conn_params_get
  - accepts ble_connect / ble_disconnect and emits state updates

Use this only with UWB_RTLS_TEST_MODE=1.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from typing import Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
studio_dir = os.path.dirname(current_dir)
software_dir = os.path.dirname(studio_dir)
root_dir = os.path.dirname(software_dir)
for path in (studio_dir, software_dir, root_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

from common.commands import CommandFactory
from common.transport import VvAddress, VvProtocol
from utils.runtime_mode import is_test_mode

DEFAULT_DEVICE_NAME = "Other Device"
DEFAULT_DEVICE_MAC = "AA:BB:CC:DD:EE:99"
HOST = "127.0.0.1"
PORT = 9999

_running = True
_scan_active = False
_ble_connected = False
_proto = VvProtocol()
_factory = CommandFactory()
_sock: Optional[socket.socket] = None
_sock_lock = threading.Lock()


def _mac_bytes(mac_text: str) -> bytes:
    return bytes.fromhex(mac_text.replace(":", ""))


def _send_frame(frame: bytes) -> None:
    global _sock
    with _sock_lock:
        if not _sock:
            return
        try:
            _sock.sendall(frame)
        except Exception:
            pass


def _send_packet(pkt) -> None:
    _send_frame(_proto.wrap_packet(pkt))


def _emit_scan_result(seq: int, name: str, mac: bytes, rssi: int = -57) -> None:
    pkt = _factory.ble_scan_result(VvAddress.CENTRAL, VvAddress.HOST, seq)
    pkt.ble_scan_result.mac_address = mac
    pkt.ble_scan_result.rssi_dbm = rssi
    pkt.ble_scan_result.name = name
    pkt.ble_scan_result.serial_number = 99001
    _send_packet(pkt)


def _emit_ble_status(seq: int, state, rssi: int = 0, reason: int = 0) -> None:
    pkt = _factory.ble_status_resp(VvAddress.CENTRAL, VvAddress.HOST, seq)
    pkt.ble_status_resp.state = state
    pkt.ble_status_resp.rssi_dbm = rssi
    pkt.ble_status_resp.disconnect_reason = reason
    _send_packet(pkt)


def _handle_packet(pkt) -> None:
    global _scan_active, _ble_connected

    param_name = pkt.WhichOneof("params")
    if not param_name:
        return

    dst = pkt.hdr.addr.dst
    seq = pkt.hdr.seq

    if dst == VvAddress.CENTRAL:
        if param_name == "ble_scan_start":
            _scan_active = True
            ack = _factory.ack(VvAddress.CENTRAL, VvAddress.HOST, seq)
            ack.ack.ack_seq = seq
            ack.ack.response = _proto.pb.PACKET_ACK_RESPONSE_ACK
            _send_packet(ack)
            return

        if param_name == "ble_scan_stop":
            _scan_active = False
            ack = _factory.ack(VvAddress.CENTRAL, VvAddress.HOST, seq)
            ack.ack.ack_seq = seq
            ack.ack.response = _proto.pb.PACKET_ACK_RESPONSE_ACK
            _send_packet(ack)
            return

        if param_name == "ble_status_get":
            if _ble_connected:
                _emit_ble_status(seq, _proto.pb.BLE_STATE_CONNECTED, rssi=-58, reason=0)
            else:
                _emit_ble_status(seq, _proto.pb.BLE_STATE_IDLE, rssi=0, reason=0)
            return

        if param_name == "ble_conn_params_get":
            pkt_resp = _factory.ble_conn_params_resp(VvAddress.CENTRAL, VvAddress.HOST, seq)
            params = pkt_resp.ble_conn_params_resp.params
            params.min_interval_ms = 15
            params.max_interval_ms = 30
            params.slave_latency = 0
            params.sup_timeout_ms = 4000
            params.phy = 1
            _send_packet(pkt_resp)
            return

        if param_name == "ble_connect":
            target_mac = ":".join(f"{b:02X}" for b in pkt.ble_connect.mac_address)
            print(f"[TEST] ble_connect requested for {target_mac}")
            ack = _factory.ack(VvAddress.CENTRAL, VvAddress.HOST, seq)
            ack.ack.ack_seq = seq
            ack.ack.response = _proto.pb.PACKET_ACK_RESPONSE_ACK
            _send_packet(ack)

            def _complete_connect():
                global _ble_connected
                time.sleep(0.6)
                _ble_connected = True
                _emit_ble_status(0, _proto.pb.BLE_STATE_CONNECTED, rssi=-58, reason=0)
                print(f"[TEST] connected: {target_mac}")

            threading.Thread(target=_complete_connect, daemon=True).start()
            return

        if param_name == "ble_disconnect":
            print("[TEST] ble_disconnect requested")
            ack = _factory.ack(VvAddress.CENTRAL, VvAddress.HOST, seq)
            ack.ack.ack_seq = seq
            ack.ack.response = _proto.pb.PACKET_ACK_RESPONSE_ACK
            _send_packet(ack)

            def _complete_disconnect():
                global _ble_connected
                time.sleep(0.3)
                _ble_connected = False
                _emit_ble_status(0, _proto.pb.BLE_STATE_IDLE, rssi=0, reason=0)
                print("[TEST] disconnected")

            threading.Thread(target=_complete_disconnect, daemon=True).start()
            return

    ack = _factory.ack(VvAddress.CENTRAL, VvAddress.HOST, seq)
    ack.ack.ack_seq = seq
    ack.ack.response = _proto.pb.PACKET_ACK_RESPONSE_ACK
    _send_packet(ack)


def _rx_loop() -> None:
    decoder = type(_proto.hdlc)()
    global _running
    while _running:
        try:
            if _sock is None:
                time.sleep(0.05)
                continue
            data = _sock.recv(4096)
            if not data:
                break
            chunks = decoder.feed(data)
            for chunk in chunks:
                if chunk.frame_type != 0:
                    continue
                try:
                    pkt = _proto.decode_packet(chunk.payload)
                except Exception as exc:
                    print(f"[TEST] decode failed: {exc}")
                    continue
                _handle_packet(pkt)
        except BlockingIOError:
            time.sleep(0.02)
        except Exception:
            break


def _scan_loop(name: str, mac: bytes, interval_s: float) -> None:
    seq = 1
    global _running
    while _running:
        if _scan_active:
            _emit_scan_result(seq, name, mac)
            seq = (seq + 1) & 0xFFFFFFFF
        time.sleep(interval_s)


def _banner() -> None:
    print("=" * 79)
    print(" UWB RTLS Studio - Other Device Auto Scan Helper")
    print("=" * 79)
    if is_test_mode():
        print("[TEST] UWB_RTLS_TEST_MODE=1 detected.")
    else:
        print("[WARN] UWB_RTLS_TEST_MODE is not 1. This helper is intended for test mode only.")


def main() -> None:
    global _running, _sock

    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default=DEFAULT_DEVICE_NAME, help="Advertised BLE device name")
    parser.add_argument("--mac", default=DEFAULT_DEVICE_MAC, help="Advertised BLE MAC address")
    parser.add_argument("--interval", type=float, default=0.5, help="Scan result interval in seconds")
    parser.add_argument("--host", default=HOST, help="App host to connect to")
    parser.add_argument("--port", type=int, default=PORT, help="App port to connect to")
    args = parser.parse_args()

    _banner()
    print(f"[TEST] Connecting to app bridge at {args.host}:{args.port} ...")
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.connect((args.host, args.port))
    _sock.setblocking(False)

    print(f"[TEST] Advertising device: {args.name} ({args.mac})")
    print("[TEST] Waiting for scan/connect commands from the app ...")

    mac_bytes = _mac_bytes(args.mac)
    threading.Thread(target=_rx_loop, daemon=True).start()
    threading.Thread(target=_scan_loop, args=(args.name, mac_bytes, args.interval), daemon=True).start()

    try:
        while _running:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        with _sock_lock:
            if _sock:
                try:
                    _sock.close()
                except Exception:
                    pass
                _sock = None


if __name__ == "__main__":
    main()
