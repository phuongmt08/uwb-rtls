from __future__ import annotations

from vv_transport import VvAddress
from vv_test_session import VvTestSession

import test_calibration
import test_command_matrix
import test_config
import test_device
import test_time_sync


def main() -> int:
    probe = VvTestSession.auto_probe(debug=True)
    if probe is None:
        print("No compatible anchor response on available ports")
        return 1

    print(f"Connected: {probe.port} @ {probe.baud}")
    print(f"Serial Number: {probe.serial_number}")

    src = int(VvAddress.HOST)
    dst = int(VvAddress.ANCHOR)

    all_ok = True
    with VvTestSession(probe.port, baud=probe.baud, debug=True) as session:
        all_ok &= test_time_sync.run(session, src, dst)
        all_ok &= test_config.run(session, src, dst)
        all_ok &= test_calibration.run(session, src, dst)
        all_ok &= test_device.run(session, src, dst)
        all_ok &= test_command_matrix.run(session, src, dst)

    print("\n=== FINAL RESULT ===")
    print("TRANSPORT OK" if all_ok else "HAS NO-RX CASES")
    print("Please review printed SET/GET payloads above to manually confirm behavior.")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
