from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from common.transport import VvAddress
from vv_test_session import VvTestSession

import test_calibration
import test_command_matrix
import test_config
import test_device
import test_time_sync


def main() -> int:
    probe = VvTestSession.auto_probe(debug=False)
    if probe is None:
        print("No compatible anchor response on available ports")
        return 1

    print(f"Connected: {probe.port} @ {probe.baud}")
    print(f"Serial Number: {probe.serial_number}")

    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    results: list[tuple[str, bool]] = []
    with VvTestSession(probe.port, baud=probe.baud, debug=True) as session:
        results.append(("time_sync", test_time_sync.run(session, src, dst)))
        results.append(("config", test_config.run(session, src, dst)))
        results.append(("calibration", test_calibration.run(session, src, dst)))
        results.append(("device", test_device.run(session, src, dst)))
        results.append(("mcu_command_selftest", test_command_matrix.run(session, src, dst)))

    print("\n=== FINAL RESULT ===")
    pass_count = sum(1 for _, ok in results if ok)
    fail_count = len(results) - pass_count
    for name, ok in results:
        print(f"{name:<22} {'PASS' if ok else 'FAIL'}")
    print(f"pass={pass_count} fail={fail_count} total={len(results)}")
    print("OVERALL PASS" if fail_count == 0 else "OVERALL FAIL")
    all_ok = fail_count == 0
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
