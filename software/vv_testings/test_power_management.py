from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import VvAddress
from vv_test_session import VvTestSession
from test_common import first_param, send_and_print


def run(session: VvTestSession, src: int, dst: int) -> bool:
    factory = CommandFactory()
    print("\n=== POWER MANAGEMENT & TELEMETRY TESTS ===")

    # Ensure battery_info_get builder is registered (fallback if not pre-registered)
    if not hasattr(factory, "battery_info_get"):
        def battery_info_get(src: int, dst: int, seq: int) -> pb.packet_t:
            pkt = pb.packet_t()
            pkt.hdr.addr.src = src
            pkt.hdr.addr.dst = dst
            pkt.hdr.seq = seq
            pkt.battery_info_get.dummy = 0
            return pkt
        factory.battery_info_get = battery_info_get

    ok = True

    # Send telemetry query packet
    pkts = send_and_print(
        session,
        "battery_info_get",
        factory.battery_info_get(src, dst, session.proto.next_seq()),
    )

    # Retrieve and print telemetry fields
    resp_pkt = first_param(pkts, "battery_info_resp")
    if resp_pkt is None:
        print("[FAIL] No battery_info_resp received from device!")
        return False

    resp = resp_pkt.battery_info_resp
    print("\n>>> DECODED HARDWARE TELEMETRY & POWER STATUS <<<")
    print(f"  Battery Voltage      : {resp.bat_voltage_mv} mV")
    print(f"  Battery SOC          : {resp.bat_soc_percent} %")
    print(f"  Remaining Time       : {resp.remaining_min} mins")
    print(f"  Is Charging          : {resp.is_charging}")
    print(f"  MCU Temperature      : {resp.mcu_temp_c:.2f} C")
    print(f"  VDDA (MCU Supply)    : {resp.vdda_mv} mV")
    print(f"  DW1000 Temperature   : {resp.uwb_temp_c:.2f} C")
    print(f"  DW1000 Supply Voltage: {resp.uwb_vbat_mv} mV")
    print(f"  IMU Temperature      : {resp.imu_temp_c:.2f} C")
    print(f"  Error Mask (Alerts)  : 0x{resp.error_mask:04X}")
    print("=================================================")

    return ok


def main() -> int:
    probe = VvTestSession.auto_probe(debug=False)
    if probe is None:
        print("No compatible anchor response on available ports")
        return 1

    print(f"Connected: {probe.port} @ {probe.baud}")
    print(f"Serial Number: {probe.serial_number}")

    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    all_ok = True
    with VvTestSession(probe.port, baud=probe.baud, debug=True) as session:
        all_ok &= run(session, src, dst)

    print("\n=== FINAL RESULT ===")
    print("POWER MANAGEMENT TEST OK" if all_ok else "POWER MANAGEMENT TEST FAILED")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
