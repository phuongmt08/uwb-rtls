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
    parser = argparse.ArgumentParser(description="Run Anchor Survey to auto-solve anchor relative coordinates.")
    parser.add_argument("--port", help="Serial port of the anchor (if not specified, auto-probes)")
    parser.add_argument("--zone", type=int, default=1, choices=[1, 2, 3, 4], help="Zone ID to solve and save layout into (default: 1)")
    parser.add_argument("--prepare", action="store_true", help="Only configure the plugged anchor for survey (enable survey flag and switch zone) and exit without starting the wait loop.")
    args = parser.parse_args()

    if args.port:
        os.environ["VV_PORT"] = args.port

    # 1. Probe for Coordinator (A4) or Gateway (Anchor role = 2)
    probe = VvTestSession.auto_probe(role=2, debug=False)
    if probe is None:
        print("No compatible UWB device response on available ports")
        sys.exit(1)

    print(f"Connected to UWB Coordinator: {probe.port}")
    print(f"Serial Number: {probe.serial_number}")
    os.environ["VV_PORT"] = probe.port

    src = int(VvAddress.DEBUG)
    dst = int(VvAddress.BCAST)

    factory = CommandFactory()

    with VvTestSession(probe.port, baud=probe.baud, debug=False) as session:
        get_sys_cfg = factory.sys_config_get(src, dst, session.proto.next_seq())
        cfg_resp, _ = session.send_expect_param(get_sys_cfg, "sys_config_resp", timeout_s=0.5)
        if cfg_resp is None:
            print("Error: Could not read anchor device ID.")
            sys.exit(1)
        device_id = cfg_resp.sys_config_resp.config.device_id
        print(f"Anchor Device ID: A{device_id}")
        if not args.prepare and device_id != 4:
            print("Error: The final survey/solve step must run from coordinator A4.")
            sys.exit(1)

        # 1.5. Switch anchor to target zone
        print(f"\nSwitching anchor to Zone {args.zone}...")
        switch_pkt = factory.zone_switch(src, dst, session.proto.next_seq())
        switch_pkt.zone_switch.zone_id = args.zone
        session.send_packet(switch_pkt)
        
        # We wait 11 seconds to ensure the new zone configuration has been persisted to Flash
        print("Waiting 11 seconds for anchor to persist zone switch to Flash...")
        for i in range(11):
            time.sleep(1.0)
            sys.stdout.write(f"\rFlash Sync: {i+1}/11s...")
            sys.stdout.flush()
        print("\nZone switch completed.")

        # 2. Get current positioning calibration config
        print("\nFetching current calibration configuration...")
        get_cfg_pkt = factory.pos_calib_cfg_get(src, dst, session.proto.next_seq())
        resp, _ = session.send_expect_param(get_cfg_pkt, "pos_calib_cfg_resp", timeout_s=0.5)
        
        if resp is None:
            print("Error: Could not fetch calibration configuration.")
            sys.exit(1)
            
        print("Fetched successfully.")
        
        # 3. Set enable_anchor_auto_calib = True
        print("\nEnabling Anchor Survey mode in config...")
        set_cfg_pkt = factory.pos_calib_cfg_set(src, dst, session.proto.next_seq())
        set_cfg_pkt.pos_calib_cfg_set.config.CopyFrom(resp.pos_calib_cfg_resp.config)
        set_cfg_pkt.pos_calib_cfg_set.config.enable_anchor_auto_calib = True
        
        session.send_and_wait(set_cfg_pkt, timeout_s=0.5)
        print("Survey mode enabled in Flash config.")

        if args.prepare:
            print("\n==========================================================")
            print(f"Anchor prepared successfully on {probe.port}!")
            print("Survey flag is enabled & Zone is set in Flash.")
            print("You can now unplug this anchor and proceed to the next one.")
            print("==========================================================")
            sys.exit(0)

        # 4. Trigger reboot to start survey
        print("\nRebooting coordinator to start survey ranging...")
        reset_pkt = factory.device_reset(src, dst, session.proto.next_seq())
        session.send_packet(reset_pkt)
        print("Reboot command sent.")

    # 5. Wait for survey to complete
    survey_wait_s = 20.0
    print(f"\nWaiting {survey_wait_s} seconds for anchors to complete mutual ranging and solve coordinates...")
    for elapsed in range(int(survey_wait_s)):
        time.sleep(1.0)
        sys.stdout.write(f"\rProgress: {elapsed + 1}/{int(survey_wait_s)}s elapsed...")
        sys.stdout.flush()
    print("\nDone waiting. Attempting to reconnect...")

    # 6. Reconnect and get solved layout
    # Re-probe to ensure port is available after reboot
    probe = None
    for attempt in range(5):
        time.sleep(1.0)
        probe = VvTestSession.auto_probe(role=2, debug=False)
        if probe is not None:
            break
            
    if probe is None:
        print("Error: Device did not come back online after reboot.")
        sys.exit(1)

    with VvTestSession(probe.port, baud=probe.baud, debug=False) as session:
        print(f"\nConnected back to port: {probe.port}")
        
        # Fetch active zone profile layout
        print(f"Fetching solved layout from Zone {args.zone} profile...")
        get_profile_pkt = factory.zone_profile_get(src, dst, session.proto.next_seq())
        get_profile_pkt.zone_profile_get.zone_id = args.zone
        
        resp, _ = session.send_expect_param(get_profile_pkt, "zone_profile_resp", timeout_s=0.5)
        
        if resp is None:
            print("Error: Could not retrieve zone profile.")
            sys.exit(1)
            
        profile = resp.zone_profile_resp.profile
        print("\n==========================================================")
        print(f"SURVEY RESULTS FOR ZONE #{profile.zone_id}:")
        print(f"Preamble Code: {profile.preamble_code}")
        print(f"Anchors Count: {profile.anchors_count}")
        print("==========================================================")
        for i in range(profile.anchors_count):
            anc = profile.anchors[i]
            print(f"Anchor #{anc.anchor_id}: ({anc.x_m:.3f}, {anc.y_m:.3f}, {anc.z_m:.3f})")
        print("==========================================================")
        print("Anchor Survey completed successfully!")

if __name__ == "__main__":
    main()
