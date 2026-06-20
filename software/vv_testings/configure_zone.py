from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import VvAddress
from vv_test_session import VvTestSession


ROLE_BY_NAME = {
    "any": None,
    "tag": 1,
    "anchor": 2,
}

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_POSITIONING_CONFIG = os.path.join(_REPO_ROOT, "firmware", "uwb", "sys", "positioning_config.h")
_DEFAULT_ZONE_CONFIG = os.path.join(os.path.dirname(__file__), "zone_profiles.py")


def _read_define(name: str, default: int) -> int:
    pattern = re.compile(rf"^\s*#define\s+{re.escape(name)}\s+(\d+)\b")
    try:
        with open(_POSITIONING_CONFIG, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                match = pattern.match(line)
                if match:
                    return int(match.group(1), 0)
    except OSError:
        pass
    return default


NUM_ANCHORS = _read_define("NUM_ANCHORS", 4)
MAX_ANCHORS_SUPPORTED = _read_define("MAX_ANCHORS_SUPPORTED", NUM_ANCHORS)


def _validate_anchor_list(anchors: list[tuple[int, float, float, float]]) -> list[tuple[int, float, float, float]]:
    if len(anchors) != NUM_ANCHORS:
        raise argparse.ArgumentTypeError(
            f"zone profile currently requires exactly {NUM_ANCHORS} anchors"
        )

    ids = [anchor_id for anchor_id, _, _, _ in anchors]
    if any(anchor_id < 1 or anchor_id > MAX_ANCHORS_SUPPORTED for anchor_id in ids):
        raise argparse.ArgumentTypeError(
            f"anchor ids must be in range 1..{MAX_ANCHORS_SUPPORTED}"
        )
    if len(set(ids)) != len(ids):
        raise argparse.ArgumentTypeError("anchor ids must not repeat within one zone")
    return anchors


def _load_zone_config(path: str) -> dict[int, dict[str, object]]:
    config_path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location("vv_zone_profiles", config_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load zone config: {config_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    zones = getattr(module, "ZONES", None)
    if not isinstance(zones, dict):
        raise SystemExit(f"{config_path} must define ZONES as a dict")
    return zones


def _zone_profile_from_config(path: str, zone_id: int) -> tuple[int, list[tuple[int, float, float, float]]]:
    zones = _load_zone_config(path)
    raw_zone = zones.get(zone_id) or zones.get(str(zone_id))
    if not isinstance(raw_zone, dict):
        raise SystemExit(f"Zone {zone_id} is not defined in {path}")

    try:
        preamble = int(raw_zone["preamble"])
        raw_anchors = raw_zone["anchors"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Zone {zone_id} must define preamble and anchors") from exc

    anchors: list[tuple[int, float, float, float]] = []
    if not isinstance(raw_anchors, list):
        raise SystemExit(f"Zone {zone_id} anchors must be a list")

    for raw_anchor in raw_anchors:
        if not isinstance(raw_anchor, dict):
            raise SystemExit(f"Zone {zone_id} anchor entries must be dicts")
        try:
            anchors.append((
                int(raw_anchor["id"]),
                float(raw_anchor["x"]),
                float(raw_anchor["y"]),
                float(raw_anchor["z"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"Zone {zone_id} anchor entries must define id, x, y, z"
            ) from exc

    try:
        return preamble, _validate_anchor_list(anchors)
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"Invalid Zone {zone_id} in {path}: {exc}") from exc


def _ack_ok(packets: list[pb.packet_t], seq: int) -> tuple[bool, str]:
    for pkt in packets:
        if pkt.WhichOneof("params") == "ack" and pkt.ack.ack_seq == seq:
            if pkt.ack.response == pb.PACKET_ACK_RESPONSE_ACK:
                return True, "ACK"
            return False, pb.packet_ack_response_t.Name(pkt.ack.response)
    return False, "NO_ACK"


def _print_profile(profile: pb.zone_profile_t) -> None:
    print(f"Zone {profile.zone_id}")
    print(f"  preamble_code={profile.preamble_code}")
    print(f"  anchor_count={profile.anchor_count}")
    for anchor in profile.anchors:
        print(
            f"  A{anchor.anchor_id}: "
            f"x={anchor.x_m:.3f} y={anchor.y_m:.3f} z={anchor.z_m:.3f}"
        )


def _open_session(args: argparse.Namespace, role: str) -> tuple[VvTestSession, object]:
    if args.port:
        os.environ["VV_PORT"] = args.port

    probe = VvTestSession.auto_probe(role=ROLE_BY_NAME[role], debug=args.debug)
    if probe is None:
        expected = "" if role == "any" else f" with role={role}"
        raise SystemExit(f"No compatible UWB device response{expected}")

    session = VvTestSession(probe.port, baud=probe.baud, debug=args.debug)
    return session, probe


def _send_expect_ack(session: VvTestSession, pkt: pb.packet_t, timeout_s: float) -> None:
    packets = session.send_and_wait(pkt, timeout_s=timeout_s)
    ok, status = _ack_ok(packets, pkt.hdr.seq)
    if not ok:
        raise SystemExit(f"Command failed: {status}")
    print(f"ACK seq={pkt.hdr.seq}")


def _cmd_get(args: argparse.Namespace) -> None:
    session, probe = _open_session(args, args.role)
    print(f"Connected: {probe.port} serial={probe.serial_number}")

    factory = CommandFactory()
    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    with session:
        pkt = factory.zone_profile_get(src, dst, session.proto.next_seq())
        pkt.zone_profile_get.zone_id = args.zone
        resp, _ = session.send_expect_param(pkt, "zone_profile_resp", timeout_s=args.timeout)
        if resp is None:
            raise SystemExit("No zone_profile_resp received")
        _print_profile(resp.zone_profile_resp.profile)


def _cmd_set(args: argparse.Namespace) -> None:
    session, probe = _open_session(args, args.role)
    print(f"Connected: {probe.port} serial={probe.serial_number}")

    factory = CommandFactory()
    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)
    preamble, anchors = _zone_profile_from_config(args.config, args.zone)

    with session:
        pkt = factory.zone_profile_set(src, dst, session.proto.next_seq())
        profile = pkt.zone_profile_set.profile
        profile.zone_id = args.zone
        profile.preamble_code = preamble
        profile.anchor_count = len(anchors)
        del profile.anchors[:]
        for anchor_id, x_m, y_m, z_m in anchors:
            anchor = profile.anchors.add()
            anchor.anchor_id = anchor_id
            anchor.x_m = x_m
            anchor.y_m = y_m
            anchor.z_m = z_m

        print("Sending zone_profile_set:")
        _print_profile(profile)
        _send_expect_ack(session, pkt, args.timeout)

        if args.verify:
            time.sleep(args.verify_delay)
            get_pkt = factory.zone_profile_get(src, dst, session.proto.next_seq())
            get_pkt.zone_profile_get.zone_id = args.zone
            resp, _ = session.send_expect_param(get_pkt, "zone_profile_resp", timeout_s=args.timeout)
            if resp is None:
                raise SystemExit("Profile set ACKed, but verification read failed")
            print("Verified profile:")
            _print_profile(resp.zone_profile_resp.profile)


def _cmd_switch(args: argparse.Namespace, role: str) -> None:
    session, probe = _open_session(args, role)
    print(f"Connected: {probe.port} serial={probe.serial_number}")

    factory = CommandFactory()
    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    with session:
        pkt = factory.zone_switch(src, dst, session.proto.next_seq())
        pkt.zone_switch.zone_id = args.zone
        print(f"Sending zone_switch zone={args.zone} to {role}")
        _send_expect_ack(session, pkt, args.timeout)

    if args.wait_s > 0.0:
        print(f"Waiting {args.wait_s:.1f}s for radio reconfigure...")
        time.sleep(args.wait_s)

    if args.persist_wait:
        print("Waiting 11s so firmware can persist default_zone_id after stable switch...")
        time.sleep(11.0)

    print(
        "Note: current protocol has no active-zone query command for anchors. "
        "Use device logs, or add a zone_status_get/resp command if host-side "
        "confirmation is required."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure UWB zone profiles and switch zones.")
    parser.add_argument("--port", help="Serial port. If omitted, auto-probes a matching device.")
    parser.add_argument("--config", default=_DEFAULT_ZONE_CONFIG,
                        help="Zone config Python file. Defaults to software/vv_testings/zone_profiles.py.")
    parser.add_argument("--debug", action="store_true", help="Print raw TX/RX debug logs.")
    parser.add_argument("--timeout", type=float, default=0.7, help="Response timeout in seconds.")

    sub = parser.add_subparsers(dest="cmd", required=True)

    get_p = sub.add_parser("get", help="Read a stored zone profile.")
    get_p.add_argument("--zone", type=int, required=True, choices=[1, 2, 3, 4])
    get_p.add_argument("--role", choices=ROLE_BY_NAME.keys(), default="any")

    set_p = sub.add_parser("set", help="Write a zone profile.")
    set_p.add_argument("--zone", type=int, required=True, choices=[1, 2, 3, 4])
    set_p.add_argument("--config", default=argparse.SUPPRESS,
                       help="Zone config Python file. Overrides the global --config.")
    set_p.add_argument("--role", choices=ROLE_BY_NAME.keys(), default="any")
    set_p.add_argument("--no-verify", dest="verify", action="store_false")
    set_p.add_argument("--verify-delay", type=float, default=0.5)
    set_p.set_defaults(verify=True)

    switch_tag_p = sub.add_parser("switch-tag", help="Switch a tag to a zone.")
    switch_tag_p.add_argument("--zone", type=int, required=True, choices=[1, 2, 3, 4])
    switch_tag_p.add_argument("--wait-s", type=float, default=1.0)
    switch_tag_p.add_argument("--persist-wait", action="store_true")

    switch_anchor_p = sub.add_parser("switch-anchor", help="Switch an anchor to a zone.")
    switch_anchor_p.add_argument("--zone", type=int, required=True, choices=[1, 2, 3, 4])
    switch_anchor_p.add_argument("--wait-s", type=float, default=1.0)
    switch_anchor_p.add_argument("--persist-wait", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cmd == "get":
        _cmd_get(args)
    elif args.cmd == "set":
        _cmd_set(args)
    elif args.cmd == "switch-tag":
        _cmd_switch(args, "tag")
    elif args.cmd == "switch-anchor":
        _cmd_switch(args, "anchor")
    else:
        raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
