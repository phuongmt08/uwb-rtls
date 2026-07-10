from __future__ import annotations
import sys
import os
import time
import argparse

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.transport import VvAddress
from common.commands import CommandFactory
from vv_test_session import VvTestSession

def main():
    parser = argparse.ArgumentParser(description="Run Center-Tag calibration.")
    parser.add_argument("--port", help="Serial port of the Tag (if not specified, auto-probes)")
    parser.add_argument("--zone", type=int, default=1, choices=[1, 2, 3, 4], help="Zone ID to calibrate under (default: 1)")
    parser.add_argument("--x", type=float, required=True, help="Measured reference-tag X coordinate in the selected zone")
    parser.add_argument("--y", type=float, required=True, help="Measured reference-tag Y coordinate in the selected zone")
    parser.add_argument("--z", type=float, required=True, help="Measured reference-tag Z coordinate in the selected zone")
    parser.add_argument("--apply", action="store_true", help="Apply the averaged candidate delay after review")
    args = parser.parse_args()

    if args.port:
        os.environ["VV_PORT"] = args.port

    # Probe for Tag (role = 1)
    probe = VvTestSession.auto_probe(role=1, debug=False)
    if probe is None:
        print("No compatible UWB device response on available ports")
        sys.exit(1)

    print(f"Connected to UWB Device (Tag): {probe.port}")
    print(f"Serial Number: {probe.serial_number}")

    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    factory = CommandFactory()

    with VvTestSession(probe.port, baud=probe.baud, debug=False) as session:
        # 1. Switch Tag to the target zone
        print(f"\nSwitching Tag to Zone {args.zone}...")
        switch_pkt = factory.zone_switch(src, dst, session.proto.next_seq())
        switch_pkt.zone_switch.zone_id = args.zone
        session.send_packet(switch_pkt)
        
        # Wait for the zone switch to take effect and be saved
        print("Waiting 11 seconds for Tag to persist zone switch to Flash...")
        for i in range(11):
            time.sleep(1.0)
            sys.stdout.write(f"\rFlash Sync: {i+1}/11s...")
            sys.stdout.flush()
        print("\nZone switch completed.")

        # 2. Start calibration ranging
        print("\nSending calib_start command...")
        # Start command with target of 32 samples per anchor
        start_pkt = factory.calib_start(src, dst, session.proto.next_seq())
        start_pkt.calib_start.sample_target = 32
        start_pkt.calib_start.tag_x_m = args.x
        start_pkt.calib_start.tag_y_m = args.y
        start_pkt.calib_start.tag_z_m = args.z
        start_pkt.calib_start.reference_position_valid = True
        
        session.send_and_wait(start_pkt, timeout_s=0.2)
        print("Calibration started on Tag. Ranging is active.")

        # 3. Poll calibration status
        print("\nPolling calibration status...")
        state_names = {
            0: "UNSPECIFIED",
            1: "IDLE",
            2: "COLLECTING",
            3: "CALCULATING",
            4: "DONE",
            5: "ERROR"
        }
        
        start_time = time.time()
        max_duration = 60.0  # 60s timeout
        
        status = None
        while time.time() - start_time < max_duration:
            time.sleep(1.0)
            
            get_status_pkt = factory.calib_status_get(src, dst, session.proto.next_seq())
            resp, packets = session.send_expect_param(get_status_pkt, "calib_status_resp", timeout_s=0.3)
            
            if resp is None:
                print("Warning: No status response received...")
                continue
                
            status = resp.calib_status_resp
            state_str = state_names.get(status.state, f"UNKNOWN({status.state})")
            progress = status.progress_percent
            
            print(f"[{state_str}] Progress: {progress}% (Samples: {status.sample_count}/{status.sample_target * 4})")
            
            if status.state in [4, 5]:  # DONE or ERROR
                break
        
        if status is None or status.state != 4:  # If not DONE
            print(f"\nCalibration ended with status: {state_names.get(status.state) if status else 'NO_RESPONSE'}")
            sys.exit(1)
            
        print("\nCalibration COMPLETED! Candidate delays resolved:")
        print("==========================================================")
        for i in range(status.candidates_count):
            cand = status.candidates[i]
            print(f"Anchor #{cand.anchor_id}:")
            print(f"  - Known Distance:   {cand.known_m:.3f} m")
            print(f"  - Measured Mean:    {cand.mean_m:.3f} m")
            print(f"  - Ranging Error:    {cand.error_m:+.3f} m")
            print(f"  - Standard Dev:     {cand.std_m:.3f} m")
            print(f"  - Timeout Rate:     {cand.timeout_rate:.2%}")
            print(f"  - Suggested TX Ant: {cand.suggested_tx_delay}")
            print(f"  - Suggested RX Ant: {cand.suggested_rx_delay}")
            print("----------------------------------------------------------")
            
        if args.apply:
            anchor_mask = 0xF
            print(f"Applying average Tag Antenna Delay suggestions (mask=0x{anchor_mask:X})...")
            apply_pkt = factory.calib_candidate_apply(src, dst, session.proto.next_seq())
            apply_pkt.calib_candidate_apply.anchor_mask = anchor_mask
            session.send_and_wait(apply_pkt, timeout_s=0.5)
            print("\nTag antenna delay apply request accepted.")
        else:
            print("\nCandidates were not applied. Re-run with --apply after reviewing them.")

if __name__ == "__main__":
    main()
