import pylink
import time
import sys

def main():
    jlink = pylink.JLink()
    try:
        jlink.open()
        print("Connected to J-Link probe successfully.")
    except Exception as e:
        print(f"Failed to open J-Link: {e}")
        sys.exit(1)
        
    jlink.set_tif(pylink.enums.JLinkInterfaces.SWD)
    jlink.set_speed(1000)
    
    try:
        jlink.coresight_configure()
        print("CoreSight configured.")
    except Exception as e:
        print(f"Failed to configure CoreSight: {e}")
        jlink.close()
        sys.exit(1)
        
    # Select AP 1 (CTRL-AP), Bank 0 by writing to DP SELECT (DP reg 2)
    try:
        jlink.coresight_write(2, 0x01000000, ap=False)
        print("Selected CTRL-AP (AP 1).")
    except Exception as e:
        print(f"Failed to select CTRL-AP: {e}")
        jlink.close()
        sys.exit(1)

    # Read status registers
    try:
        approtect = jlink.coresight_read(3, ap=True)
        erase_status = jlink.coresight_read(2, ap=True)
        print(f"Current APPROTECTSTATUS: 0x{approtect:08X}")
        print(f"Current ERASEALLSTATUS: 0x{erase_status:08X}")
    except Exception as e:
        print(f"Failed to read status: {e}")
        
    # Assert reset via CTRL-AP RESET (AP reg 0)
    try:
        jlink.coresight_write(0, 1, ap=True)
        print("Asserted RESET via CTRL-AP.")
    except Exception as e:
        print(f"Failed to assert RESET: {e}")
        
    # Trigger ERASEALL (AP reg 1)
    try:
        jlink.coresight_write(1, 1, ap=True)
        print("Triggered ERASEALL. Erasing flash...")
    except Exception as e:
        print(f"Failed to trigger ERASEALL: {e}")

    # Wait for erase to complete
    time.sleep(2.0)
    
    # Try to connect to target core
    print("Attempting to connect to target CPU core...")
    try:
        # Before connecting, we must select AP 0 (AHB-AP) again
        jlink.coresight_write(2, 0x00000000, ap=False)
        jlink.connect("nRF52840_xxAA")
        print("Successfully connected to CPU core!")
        print("Target is unlocked. We can now flash firmware.")
        jlink.close()
        sys.exit(0)
    except Exception as e:
        print(f"Failed to connect to CPU core: {e}")
        
    # Release reset in case we failed, so target is not left hung
    try:
        jlink.coresight_write(2, 0x01000000, ap=False)
        jlink.coresight_write(0, 0, ap=True)
        print("Released RESET.")
    except Exception as e:
        print(f"Failed to release RESET: {e}")
        
    jlink.close()
    sys.exit(1)

if __name__ == "__main__":
    main()
