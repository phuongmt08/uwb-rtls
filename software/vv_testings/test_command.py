from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import serial
from serial import SerialException
from serial.tools import list_ports

from parser_protocol import HostTransport, VvAddress, VvProtocol


BAUD_CANDIDATES = [115200]
READ_TIMEOUT_S = 0.05
DEBUG = True


@dataclass
class ProbeResult:
    port: str
    baud: int
    serial_number: int


def dbg(msg: str) -> None:
    if DEBUG:
        print(f"[DBG] {msg}")


def hex_preview(data: bytes, max_len: int = 64) -> str:
    if not data:
        return ""
    shown = data[:max_len]
    suffix = " ..." if len(data) > max_len else ""
    return shown.hex(" ") + suffix


def packet_name(pkt) -> str:
    return pkt.WhichOneof("params") or "<none>"


def packet_hdr(pkt) -> str:
    return f"src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}"


def recv_packets(proto: VvProtocol, ser: serial.Serial, timeout_s: float) -> list:
    deadline = time.time() + timeout_s
    packets = []

    while time.time() < deadline:
        data = ser.read(ser.in_waiting or 1)
        if not data:
            continue

        dbg(f"RX raw {len(data)}B: {hex_preview(data)}")
        try:
            decoded = proto.decode_from_frames(data)
        except Exception as exc:
            dbg(f"RX decode exception: {exc}")
            decoded = []

        for pkt in decoded:
            dbg(f"RX pkt {packet_name(pkt)} ({packet_hdr(pkt)})")

        packets.extend(decoded)
        if packets:
            break

    return packets


def send_packet(proto: VvProtocol, ser: serial.Serial, pkt) -> None:
    frame = proto.wrap_packet(pkt)
    dbg(f"TX pkt {packet_name(pkt)} ({packet_hdr(pkt)})")
    dbg(f"TX raw {len(frame)}B: {hex_preview(frame)}")
    ser.write(frame)
    ser.flush()


def _send_and_wait(
    proto: VvProtocol,
    ser: serial.Serial,
    pkt,
    expect_param: str | None,
    timeout_s: float,
):
    dbg(
        f"Stage send {packet_name(pkt)} expect={expect_param or '<any/none>'} timeout={timeout_s:.2f}s"
    )
    send_packet(proto, ser, pkt)
    packets = recv_packets(proto, ser, timeout_s=timeout_s)
    dbg(f"Stage recv packets={len(packets)}")
    if expect_param is None:
        return True, packets
    match = proto.first_param(packets, expect_param)
    dbg(f"Stage match {expect_param}: {'YES' if match is not None else 'NO'}")
    return match, packets


def _query_serial(proto: VvProtocol, ser: serial.Serial, src: int, dst: int) -> int | None:
    for attempt in range(2):
        dbg(f"Query serial attempt {attempt + 1}/2")
        seq = proto.next_seq()
        pkt = proto.build_device_info_get(src, dst, seq)
        match, _ = _send_and_wait(
            proto,
            ser,
            pkt,
            expect_param="device_information_resp",
            timeout_s=0.35,
        )
        if match is not None:
            dbg(f"Serial response found: {match.device_information_resp.serial_number}")
            return int(match.device_information_resp.serial_number)
    dbg("Serial response not found")
    return None


def try_probe_port(proto: VvProtocol, port_name: str, baud: int) -> ProbeResult | None:
    try:
        ser = serial.Serial(port_name, baud, timeout=READ_TIMEOUT_S)
        dbg(f"Opened {port_name} @ {baud}")
    except SerialException as exc:
        dbg(f"Open failed {port_name} @ {baud}: {exc}")
        return None

    src = int(VvAddress.HOST)
    dst = int(VvAddress.ANCHOR)

    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        dbg("Input/output buffers reset")
        time.sleep(0.06)

        serial_number = _query_serial(proto, ser, src, dst)
        if serial_number is not None:
            return ProbeResult(port_name, baud, serial_number)

        _send_and_wait(
            proto,
            ser,
            proto.build_none(src, dst, proto.next_seq()),
            expect_param=None,
            timeout_s=0.25,
        )

        _send_and_wait(
            proto,
            ser,
            proto.build_transport_set(src, dst, proto.next_seq(), HostTransport.USB),
            expect_param=None,
            timeout_s=0.25,
        )

        serial_number = _query_serial(proto, ser, src, dst)
        if serial_number is not None:
            return ProbeResult(port_name, baud, serial_number)

        dbg("Probe failed: no matching device_information_resp")
        return None
    finally:
        dbg(f"Closing {port_name}")
        ser.close()


def _score_port(p: list_ports.ListPortInfo) -> int:
    score = 0
    desc = (p.description or "").lower()
    manu = (p.manufacturer or "").lower()
    hwid = (p.hwid or "").lower()

    if "stm" in desc or "stm" in manu:
        score += 4
    if "usb serial" in desc or "virtual com" in desc:
        score += 2
    if "vid:pid=0483" in hwid:
        score += 6
    if "bluetooth" in desc:
        score -= 4
    return score


def prioritized_ports() -> Iterable[list_ports.ListPortInfo]:
    ports = list(list_ports.comports())
    ports.sort(key=_score_port, reverse=True)
    return ports


def main() -> int:
    proto = VvProtocol()
    ports = list(prioritized_ports())
    dbg(f"Detected ports: {len(ports)}")

    if not ports:
        print("No serial ports found")
        return 2

    for index, port_info in enumerate(ports, start=1):
        port = port_info.device
        print(f"[{index}/{len(ports)}] Probing {port} @ 115200...")
        for baud in BAUD_CANDIDATES:
            result = try_probe_port(proto, port, baud)
            if result is not None:
                print(f"Connected: {result.port} @ {result.baud}")
                print(f"Serial Number: {result.serial_number}")
                return 0

    print("No compatible anchor response on available ports")
    print("Available serial ports:")
    for p in ports:
        print(f"  - {p.device}: {p.description} {p.hwid}".rstrip())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
