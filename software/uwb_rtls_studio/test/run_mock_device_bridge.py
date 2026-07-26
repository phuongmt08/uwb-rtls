#!/usr/bin/env python3
"""
===============================================================================
  UWB RTLS Studio - Mock Device & Serial TCP Bridge
===============================================================================
  File        : software/uwb_rtls_studio/test/run_mock_device_bridge.py
  Description : Standalone CLI utility supporting two modes:
                1. Mock Mode (default): Simulates UWB Tag/MCU firmware over TCP
                   socket (port 9999). Responds to protobuf commands and streams
                   ranging/telemetry data (figure-8 lissajous path).
                2. Bridge Mode (--port COMx): Connects to a physical serial port
                   and forwards all bytes bidirectionally to TCP port 9999.
===============================================================================
"""
from __future__ import annotations
import os
import sys
import time
import math
import socket
import argparse
import threading
from pathlib import Path

# Add paths to make sure common and studio packages are importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))      # software/uwb_rtls_studio
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))   # software
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))) # root

import serial
from common.transport import VvAddress, VvProtocol
from common.commands import CommandFactory
from utils.runtime_mode import mock_rtos_resource, mock_rtos_task_stats, mock_device_identity

class MockDeviceSimulator:
    """Simulates a UWB Tag device firmware over a TCP socket connection."""
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.proto = VvProtocol()
        self.factory = CommandFactory()
        self.seq = 0
        self.angle = 0.0
        self.running = True
        self.ranging_active = False
        self.ranging_thread = None
        self.ranging_lock = threading.Lock()

    def start(self):
        # Spawn thread to read from TCP socket
        t = threading.Thread(target=self._recv_loop, name="MockDeviceRecv", daemon=True)
        t.start()
        print("[MOCK DEVICE] Started simulator loop. Ready to handle commands.")

    def _recv_loop(self):
        buffer = bytearray()
        while self.running:
            try:
                data = self.sock.recv(1024)
                if not data:
                    print("[MOCK DEVICE] GUI App closed the connection.")
                    break
                buffer.extend(data)
                
                # Extract HDLC frames
                # VvProtocol handles HDLC decoding and packet framing.
                # Since we read from streaming TCP, we use a simple wrapper logic.
                # Actually, decode_from_frames is built to handle incomplete buffers:
                # But it expects a complete frame/chunk. Let's pass the raw buffer to it
                # and clear it when parsed.
                try:
                    packets = self.proto.decode_from_frames(bytes(buffer))
                    # Clear buffer if successfully parsed
                    buffer.clear()
                    for pkt in packets:
                        self.handle_packet(pkt)
                except Exception:
                    # Incomplete frame, wait for more data
                    if len(buffer) > 4096:
                        # Avoid unbounded growth
                        buffer.clear()
            except Exception as e:
                print(f"[MOCK DEVICE] Error receiving from socket: {e}")
                break
        self.stop()

    def handle_packet(self, pkt):
        param_name = pkt.WhichOneof("params")
        src = pkt.hdr.addr.src
        dst = pkt.hdr.addr.dst
        seq = pkt.hdr.seq

        if src != int(VvAddress.HOST):
            print(f"[MOCK DEVICE] Warning: Bad source header address: {src}")
            return

        print(f"[MOCK DEVICE] RX command: '{param_name}' (Seq: {seq})")

        # Handle specific control commands
        if param_name == "ranging_start":
            with self.ranging_lock:
                self.ranging_active = True
                if self.ranging_thread is None or not self.ranging_thread.is_alive():
                    self.ranging_thread = threading.Thread(target=self._ranging_stream_loop, daemon=True)
                    self.ranging_thread.start()
            print("[MOCK DEVICE] Ranging started. Streaming coordinates...")
        elif param_name == "ranging_stop":
            with self.ranging_lock:
                self.ranging_active = False
            print("[MOCK DEVICE] Ranging stopped.")

        # Build appropriate response
        from services.query_state_machine import QueryQueueManager
        response_name = QueryQueueManager.RESPONSE_MAP.get(param_name)
        
        if response_name:
            resp_method = getattr(self.factory, response_name, None)
            if resp_method:
                resp = resp_method(src=dst, dst=src, seq=seq)
                
                # Populate mock parameters
                if response_name == "device_information_resp":
                    resp.device_information_resp.device_type = self.proto.pb.DEVICE_TYPE_TAG
                    resp.device_information_resp.role = self.proto.pb.DEVICE_ROLE_TAG
                    resp.device_information_resp.fw_version = "mock-device-v1.0"
                    resp.device_information_resp.hw_version = "nRF52840-UWB"
                elif response_name == "device_type_set":
                    resp.device_type_set.device_type = self.proto.pb.DEVICE_TYPE_TAG
                elif response_name == "anchor_layout_resp":
                    # Build mock anchor layout
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
                    resp.ble_status_resp.state = self.proto.pb.BLE_STATE_CONNECTED
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

                self.send_packet(resp)
                print(f"[MOCK DEVICE] TX response: '{response_name}'")
                return

        # Generic ACK fallback
        ack = self.factory.ack(src=dst, dst=src, seq=seq)
        ack.ack.ack_seq = seq
        ack.ack.response = self.proto.pb.PACKET_ACK_RESPONSE_ACK
        self.send_packet(ack)
        print(f"[MOCK DEVICE] TX ACK for '{param_name}'")

    def send_packet(self, pkt):
        frame = self.proto.wrap_packet(pkt)
        try:
            self.sock.sendall(frame)
        except Exception as e:
            print(f"[MOCK DEVICE] Write failed: {e}")
            self.stop()

    def _ranging_stream_loop(self):
        while self.running:
            with self.ranging_lock:
                if not self.ranging_active:
                    break
            
            self.angle += 0.04
            t = self.angle
            
            # Lissajous layout coordinates (fits 10.76 x 13.2 canvas)
            cx, cy = 5.38, 6.6
            Ax, Ay = 4.2, 5.0
            
            tag_x = cx + Ax * math.sin(2 * t)
            tag_y = cy + Ay * math.sin(t)
            tag_z = 1.2
            
            # Simulated Yaw angles
            dx = 2.0 * Ax * math.cos(2 * t)
            dy = Ay * math.cos(t)
            psi_deg = math.degrees(math.atan2(dy, dx)) % 360.0
            
            seq = self.seq
            self.seq += 1

            # 1. Stream Ranging Result (Trilateration + Anchor distances)
            ranging_pkt = self.proto.pb.packet_t()
            ranging_pkt.hdr.addr.src = int(VvAddress.MCU)
            ranging_pkt.hdr.addr.dst = int(VvAddress.HOST)
            ranging_pkt.hdr.seq = seq
            
            res = ranging_pkt.ranging_result
            res.pos_x_m = tag_x + 0.15 * math.sin(t * 12.0)  # add mock noise to trilateration
            res.pos_y_m = tag_y + 0.15 * math.cos(t * 10.0)
            res.pos_z_m = tag_z
            res.rms_error_m = 0.180
            res.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
            
            # Populate distance to 4 anchors
            anchors = [(1, 0.0, 0.0, 1.5), (2, 10.76, 0.0, 1.5), (3, 0.0, 13.2, 1.5), (4, 10.76, 13.2, 1.5)]
            for aid, ax, ay, az in anchors:
                dist = math.hypot(tag_x - ax, tag_y - ay)
                a_item = res.anchors.add()
                a_item.anchor_id = aid
                a_item.distance_mm = int(dist * 1000)
                a_item.fp_amp = 500

            self.send_packet(ranging_pkt)

            # 2. Stream Sensor Fusion Result (UKF filter output)
            fusion_pkt = self.proto.pb.packet_t()
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
            fs.position_cov_xx_m2 = 0.01
            fs.position_cov_xy_m2 = 0.002
            fs.position_cov_yy_m2 = 0.015
            fs.position_cov_valid = True
            
            self.send_packet(fusion_pkt)
            time.sleep(0.1)  # 10 Hz

    def stop(self):
        self.running = False
        with self.ranging_lock:
            self.ranging_active = False
        try:
            self.sock.close()
        except Exception:
            pass


def run_mock_mode(host: str, port: int):
    print(f"\n=== MOCK SIMULATION MODE ===")
    print(f"Connecting to GUI App TCP Server on {host}:{port}...")
    
    # Try connecting up to 5 times (in case the GUI App is booting up)
    sock = None
    for attempt in range(1, 6):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            print(f"[SUCCESS] Connected on attempt {attempt}/5")
            break
        except Exception as e:
            print(f"Attempt {attempt}/5 failed: {e}")
            if attempt < 5:
                time.sleep(1.0)
            else:
                print("[ERROR] Could not connect to GUI App. Is it running in TEST_MODE?")
                sys.exit(1)
                
    simulator = MockDeviceSimulator(sock)
    simulator.start()
    
    try:
        # Keep main thread alive
        while simulator.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Mock Simulator...")
    finally:
        simulator.stop()


def run_bridge_mode(port_name: str, baud: int, host: str, port: int):
    print(f"\n=== SERIAL BRIDGE MODE ===")
    print(f"1. Opening Serial Port: {port_name} @ {baud}...")
    try:
        ser = serial.Serial(port=port_name, baudrate=baud, timeout=0.1)
        print(f"[SUCCESS] Serial port {port_name} opened.")
    except Exception as e:
        print(f"[ERROR] Failed to open serial port {port_name}: {e}")
        sys.exit(1)

    print(f"2. Connecting to GUI App TCP Server on {host}:{port}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        print(f"[SUCCESS] Connected to GUI App socket.")
    except Exception as e:
        print(f"[ERROR] Failed to connect to GUI App TCP server: {e}")
        ser.close()
        sys.exit(1)

    bridge_active = True
    
    def serial_to_tcp():
        nonlocal bridge_active
        print("[BRIDGE] Start thread: Serial -> TCP")
        while bridge_active:
            try:
                data = ser.read(512)
                if data:
                    sock.sendall(data)
            except Exception as ex:
                print(f"\n[BRIDGE] Error in Serial -> TCP: {ex}")
                bridge_active = False
                break

    def tcp_to_serial():
        nonlocal bridge_active
        print("[BRIDGE] Start thread: TCP -> Serial")
        while bridge_active:
            try:
                data = sock.recv(1024)
                if not data:
                    print("\n[BRIDGE] GUI App closed the connection.")
                    bridge_active = False
                    break
                ser.write(data)
            except Exception as ex:
                print(f"\n[BRIDGE] Error in TCP -> Serial: {ex}")
                bridge_active = False
                break

    t1 = threading.Thread(target=serial_to_tcp, name="SerialToTcp", daemon=True)
    t2 = threading.Thread(target=tcp_to_serial, name="TcpToSerial", daemon=True)
    
    t1.start()
    t2.start()
    
    print("[BRIDGE] Bidirectional bridging active. Press Ctrl+C to terminate.")
    
    try:
        while bridge_active:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping Serial Bridge...")
    finally:
        bridge_active = False
        try:
            sock.close()
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
        print("[BRIDGE] Closed all connections.")


def main():
    parser = argparse.ArgumentParser(description="Mock UWB Device Simulator and COM-to-TCP Bridge.")
    parser.add_argument("--port", help="Physical COM port to bridge (e.g. COM3). If omitted, runs Mock Simulator.")
    parser.add_argument("--baud", type=int, default=115200, help="COM port baudrate (default: 115200).")
    parser.add_argument("--host", default="127.0.0.1", help="TCP Host address (default: 127.0.0.1).")
    parser.add_argument("--tcp-port", type=int, default=9999, help="TCP Host port (default: 9999).")
    args = parser.parse_args()

    if args.port:
        run_bridge_mode(args.port, args.baud, args.host, args.tcp_port)
    else:
        run_mock_mode(args.host, args.tcp_port)

if __name__ == "__main__":
    sys.exit(main())
