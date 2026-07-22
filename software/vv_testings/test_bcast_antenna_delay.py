"""
===============================================================================
  UWB RTLS - Test Broadcast Antenna Delay Set Command
===============================================================================
  Script test gửi lệnh BLE Broadcast antenna_delay_bcast_set xuống Central/Gateway
  để thay đổi antenna delay của Anchor target theo serial_number.

  Ví dụ sử dụng:
    python software/vv_testings/test_bcast_antenna_delay.py --serial 0xA1B2C3D4 --tx 16187 --rx 16187 --persist
    python software/vv_testings/test_bcast_antenna_delay.py --serial 0 --tx 16384 --rx 16384 (Wildcard cho tất cả Anchor)
===============================================================================
"""
from __future__ import annotations
import sys
import os
import time
import argparse

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.transport import VvAddress
from common.commands import CommandFactory
from common import protocol_pb2 as pb
from vv_test_session import VvTestSession


def _parse_int(val: str) -> int:
    val = val.strip()
    return int(val, 16) if val.lower().startswith("0x") else int(val)


def main():
    parser = argparse.ArgumentParser(description="Test BLE Broadcast Antenna Delay Set command.")
    parser.add_argument("--port", help="Serial port of Central/Gateway (if not specified, auto-probes)")
    parser.add_argument("--baud", type=int, default=115200, help="Baudrate (default: 115200)")
    parser.add_argument("--serial", required=True, help="Anchor hardware serial number (hex e.g. 0xA1B2C3D4 or dec, 0 = wildcard all)")
    parser.add_argument("--tx", type=int, default=16187, help="TX antenna delay in DW units (default: 16187)")
    parser.add_argument("--rx", type=int, default=16187, help="RX antenna delay in DW units (default: 16187)")
    parser.add_argument("--persist", action="store_true", help="Persist value to Flash on target Anchor")
    args = parser.parse_args()

    serial_number = _parse_int(args.serial)

    port = args.port
    baud = args.baud

    if not port:
        probe = VvTestSession.auto_probe(debug=False)
        if probe is not None:
            port = probe.port
            baud = probe.baud

    if not port:
        print("ERROR: No compatible serial port found.")
        sys.exit(1)

    print(f"Connecting to Central/Gateway on {port} @ {baud}...")

    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    with VvTestSession(port, baud=baud, debug=False) as session:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = src
        pkt.hdr.addr.dst = dst
        pkt.hdr.seq = session.proto.next_seq()

        pkt.antenna_delay_bcast_set.serial_number = serial_number
        pkt.antenna_delay_bcast_set.tx_antenna_delay = args.tx
        pkt.antenna_delay_bcast_set.rx_antenna_delay = args.rx
        pkt.antenna_delay_bcast_set.persist = args.persist

        target_str = f"0x{serial_number:08X}" if serial_number != 0 else "WILDCARD (All Anchors)"
        print(f"\nSending antenna_delay_bcast_set packet:")
        print(f"  - Target Serial: {target_str}")
        print(f"  - TX Delay:      {args.tx} DW units")
        print(f"  - RX Delay:      {args.rx} DW units")
        print(f"  - Combined:      {args.tx + args.rx} DW units")
        print(f"  - Persist Flash: {args.persist}")

        session.send_packet(pkt)
        print("\nPacket sent successfully! Waiting for response/logs (3 seconds)...")

        start_time = time.time()
        while time.time() - start_time < 3.0:
            packets = session.recv_packets(timeout_s=0.5)
            for resp_pkt in packets:
                ptype = resp_pkt.WhichOneof("params") or "<none>"
                print(f"  [RX] Received packet: {ptype}")
                if ptype == "bcast_apply_ack":
                    ack = resp_pkt.bcast_apply_ack
                    print(f"  >>> RECEIVED ACK from Serial 0x{ack.serial_number:08X}: status={ack.status}")

    print("\nTest completed.")


if __name__ == "__main__":
    main()
