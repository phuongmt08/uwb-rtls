import os
import time
import usb.core
import usb.util
import usb.backend.libusb1

from models.consts import *
from models.data_models import DeviceInfo, DfuError

def _is_pipe_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "pipe error" in text or "errno 32" in text

class DfuDevice:
    def __init__(
        self,
        vid: int = 0x0483,
        pid: int = 0xDF11,
        timeout_ms: int = 3000,
        bus: int | None = None,
        address: int | None = None,
    ):
        self.vid = vid
        self.pid = pid
        self.timeout_ms = timeout_ms
        self.bus = bus
        self.address = address
        self.dev = None
        self.interface_number = 0

    @staticmethod
    def get_usb_backend():
        backend = usb.backend.libusb1.get_backend()
        if backend is not None:
            return backend

        try:
            import libusb_package

            return usb.backend.libusb1.get_backend(find_library=lambda _name: libusb_package.get_library_path())
        except Exception:
            return None

    @staticmethod
    def _find_dfu_interface_number(device) -> int | None:
        try:
            for config in device:
                for interface in config:
                    if interface.bInterfaceClass == 0xFE and interface.bInterfaceSubClass == 0x01:
                        return int(interface.bInterfaceNumber)
        except Exception:
            return None
        return None

    @staticmethod
    def list_dfu_devices(vid_filter: int | None = None, pid_filter: int | None = None) -> list[DeviceInfo]:
        backend = DfuDevice.get_usb_backend()
        devices = usb.core.find(find_all=True, backend=backend)
        if devices is None:
            return []

        result = []
        for device in devices:
            if vid_filter is not None and int(device.idVendor) != vid_filter:
                continue
            if pid_filter is not None and int(device.idProduct) != pid_filter:
                continue
            interface_number = DfuDevice._find_dfu_interface_number(device)
            if interface_number is None:
                continue
            result.append(
                DeviceInfo(
                    vid=int(device.idVendor),
                    pid=int(device.idProduct),
                    bus=getattr(device, "bus", None),
                    address=getattr(device, "address", None),
                    interface_number=interface_number,
                )
            )
        return result

    def open(self) -> DeviceInfo:
        backend = DfuDevice.get_usb_backend()
        # On Windows, querying by VID/PID can hit a stale libusb cache if the device 
        # just re-enumerated. Querying ALL devices forces a cache flush!
        all_devices = usb.core.find(find_all=True, backend=backend) or []
        candidates = []
        if all_devices:
            for dev in all_devices:
                if int(dev.idVendor) == self.vid and int(dev.idProduct) == self.pid:
                    candidates.append(dev)

        if not candidates:
            raise DfuError(f"Cannot find DFU device VID:PID = {self.vid:04X}:{self.pid:04X}")

        dev = None
        for candidate in candidates:
            if self.bus is not None and getattr(candidate, "bus", None) != self.bus:
                continue
            if self.address is not None and getattr(candidate, "address", None) != self.address:
                continue
            interface_number = self._find_dfu_interface_number(candidate)
            if interface_number is None:
                continue
            dev = candidate
            break

        if dev is None:
            raise DfuError("Matching USB device found but no DFU interface available")

        self.dev = dev
        try:
            cfg = dev.get_active_configuration()
        except usb.core.USBError:
            dev.set_configuration()
            cfg = dev.get_active_configuration()

        dfu_intf = None
        for intf in cfg:
            if intf.bInterfaceClass == 0xFE and intf.bInterfaceSubClass == 0x01:
                dfu_intf = intf
                break

        if dfu_intf is None:
            raise DfuError("No DFU interface found on device")

        self.interface_number = int(dfu_intf.bInterfaceNumber)
        if os.name != "nt":
            if dev.is_kernel_driver_active(self.interface_number):
                dev.detach_kernel_driver(self.interface_number)
        usb.util.claim_interface(dev, self.interface_number)
        try:
            dev.set_interface_altsetting(self.interface_number, 0)
        except Exception:
            pass

        return DeviceInfo(
            vid=self.vid,
            pid=self.pid,
            bus=getattr(dev, "bus", None),
            address=getattr(dev, "address", None),
            interface_number=self.interface_number,
        )

    def close(self):
        if self.dev is None:
            return
        try:
            usb.util.release_interface(self.dev, self.interface_number)
        except Exception:
            pass
        usb.util.dispose_resources(self.dev)
        self.dev = None

    def _ctrl_out(self, request: int, value: int, data: bytes = b""):
        if self.dev is None:
            raise DfuError("Device not opened")
        return self.dev.ctrl_transfer(
            0x21, request, value, self.interface_number, data, timeout=self.timeout_ms
        )

    def _ctrl_in(self, request: int, value: int, length: int) -> bytes:
        if self.dev is None:
            raise DfuError("Device not opened")
        response = self.dev.ctrl_transfer(
            0xA1, request, value, self.interface_number, length, timeout=self.timeout_ms
        )
        return bytes(response)

    def get_state(self) -> int:
        raw = self._ctrl_in(REQ_GETSTATE, 0, 1)
        return raw[0]

    def get_status(self) -> tuple[int, int, int, int]:
        raw = self._ctrl_in(REQ_GETSTATUS, 0, 6)
        if len(raw) != 6:
            raise DfuError("Invalid GETSTATUS response")
        status = raw[0]
        poll_timeout_ms = int(raw[1]) | (int(raw[2]) << 8) | (int(raw[3]) << 16)
        state = raw[4]
        i_string = raw[5]
        return status, poll_timeout_ms, state, i_string

    def clear_status(self):
        self._ctrl_out(REQ_CLRSTATUS, 0, b"")

    def abort(self):
        self._ctrl_out(REQ_ABORT, 0, b"")

    def _wait_ready(self, max_tries: int = 300):
        for _ in range(max_tries):
            try:
                status, poll, state, _ = self.get_status()
                if status != 0:
                    raise DfuError(f"DFU status error: {status}")
                if state in (STATE_DFU_IDLE, STATE_DFU_DNLOAD_IDLE, STATE_DFU_UPLOAD_IDLE):
                    return state
                sleep_time = max(poll / 1000.0, 0.01)
                time.sleep(sleep_time)
            except Exception as e:
                # If the STM32 is erasing flash, the CPU halts and USB will timeout/STALL 
                # causing a Pipe error. We should gracefully sleep and retry.
                if _ == max_tries - 1:
                    raise e
                time.sleep(0.05)
                
        raise DfuError("Timeout waiting DFU ready state")

    def _safe_recover_idle(self):
        for attempt in range(4):
            try:
                state = self.get_state()
                if state == STATE_DFU_ERROR:
                    self.clear_status()
                    time.sleep(0.02)
                self.abort()
                time.sleep(0.02)
                return  # Success
            except Exception:
                # If Windows suspended the port or the pipe is stale, 
                # this will fail once but usually wakes it up for the next try
                time.sleep(0.1)

    def _dnload_cmd_and_wait(self, block_num: int, payload: bytes):
        self._ctrl_out(REQ_DNLOAD, block_num, payload)
        return self._wait_ready()

    def set_address_pointer(self, address: int):
        cmd = b"\x21" + int(address).to_bytes(4, "little")
        self._dnload_cmd_and_wait(0, cmd)

    def erase_address(self, address: int):
        old_timeout = self.timeout_ms
        self.timeout_ms = 30000
        try:
            self._safe_recover_idle()
            self._wait_ready()
            cmd = b"\x41" + int(address).to_bytes(4, "little")
            self._dnload_cmd_and_wait(0, cmd)
        finally:
            self.timeout_ms = old_timeout

    def mass_erase(self):
        old_timeout = self.timeout_ms
        self.timeout_ms = 30000
        try:
            self._safe_recover_idle()
            self._wait_ready()
            self._dnload_cmd_and_wait(0, b"\x41")
        finally:
            self.timeout_ms = old_timeout

    def write_memory(
        self,
        start_address: int,
        data: bytes,
        transfer_size: int = 1024,
        progress=None,
    ):
        if not data:
            return

        print("[TRACE] Calling _safe_recover_idle...")
        self._safe_recover_idle()
        print("[TRACE] Calling _wait_ready...")
        self._wait_ready()
        print(f"[TRACE] Calling set_address_pointer({start_address:08X})...")
        self.set_address_pointer(start_address)
        print("[TRACE] Address pointer set successfully!")

        total = len(data)
        sent = 0
        block = 2

        while sent < total:
            chunk = data[sent : sent + transfer_size]
            self._dnload_cmd_and_wait(block, chunk)
            sent += len(chunk)
            block += 1
            if progress:
                progress(sent, total)

        self._ctrl_out(REQ_DNLOAD, block, b"")
        try:
            self._wait_ready(max_tries=120)
        except Exception:
            pass

    def read_memory(self, start_address: int, size: int, transfer_size: int = 1024, progress=None) -> bytes:
        if size <= 0:
            return b""

        self._safe_recover_idle()
        self._wait_ready()
        self.set_address_pointer(start_address)

        received = bytearray()
        block = 2

        while len(received) < size:
            ask = min(transfer_size, size - len(received))
            chunk = self._ctrl_in(REQ_UPLOAD, block, ask)
            if not chunk:
                break
            received.extend(chunk)
            block += 1
            if progress:
                progress(len(received), size)
            if len(chunk) < ask:
                break

        return bytes(received[:size])

    def ping_activity(self) -> bool:
        try:
            _ = self.get_state()
            _ = self.get_status()
            return True
        except Exception:
            return False
