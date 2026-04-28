import os
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox
from views.dfu_tab import DfuTab
from services.dfu_service import DfuDevice, DeviceInfo
from services.hex_service import HexService
from services.config_service import ConfigService
from utils.workers import WorkerSignals
from models.data_models import DfuError
from models.consts import APP_START, APP_END


def _is_pipe_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "pipe error" in text or "errno 32" in text

class DfuController(QObject):
    def __init__(self, view: DfuTab, signals: WorkerSignals, config: ConfigService, main_ctrl):
        super().__init__()
        self.view = view
        self.signals = signals
        self.config = config
        self.main_ctrl = main_ctrl
        self.dfu = DfuDevice()
        
        self.current_file = ""
        self.current_hex = None
        self.scanned_devices = []

        self._setup_connections()
        self._load_preferences()

    def _setup_connections(self):
        self.view.btn_scan.clicked.connect(self.on_scan)
        self.view.btn_auto_connect.clicked.connect(self.on_auto_connect)
        self.view.btn_connect.clicked.connect(self.on_connect)
        self.view.btn_browse.clicked.connect(self.on_browse)
        self.view.btn_flash.clicked.connect(self.on_flash)
        self.view.btn_verify.clicked.connect(self.on_verify)
        self.view.btn_erase_app.clicked.connect(self.on_erase_app)
        self.view.btn_mass_erase.clicked.connect(self.on_mass_erase)

    def _load_preferences(self):
        vid, pid = self.config.get_last_vid_pid()
        self.view.vid_edit.setText(vid)
        self.view.pid_edit.setText(pid)

        recent = self.config.get_recent_hex_paths()
        for p in recent:
            if os.path.exists(p):
                self.view.combo_file.addItem(p)
        if self.view.combo_file.count() > 0:
            self.view.combo_file.setCurrentIndex(0)

    def _push_recent_path(self, path: str):
        clean_path = os.path.normpath(path)
        existing = []
        for i in range(self.view.combo_file.count()):
            text = self.view.combo_file.itemText(i).strip()
            if text: existing.append(text)
        merged = [clean_path] + [item for item in existing if item.lower() != clean_path.lower()]
        merged = merged[:10]
        self.view.combo_file.clear()
        for item in merged:
            self.view.combo_file.addItem(item)
        self.view.combo_file.setCurrentText(clean_path)
        self.config.set_recent_hex_paths(merged)

    def on_browse(self):
        start_dir = self.config.get_last_hex_dir()
        path, _ = QFileDialog.getOpenFileName(self.view, "Select firmware", start_dir, "Intel HEX (*.hex)")
        if not path: return
        image = HexService.load_hex_image(path)
        self.current_file = path
        self.current_hex = image
        self._push_recent_path(path)
        self.config.set_last_hex_dir(os.path.dirname(path))
        self.signals.log.emit(f"Selected HEX: {path}")

    def on_scan(self):
        def job():
            vid_filter, pid_filter = self._parse_scan_filters()

            devices = DfuDevice.list_dfu_devices(vid_filter=vid_filter, pid_filter=pid_filter)
            self.scanned_devices = devices
            self.view.combo_devices.clear()
            if not devices:
                self.view.combo_devices.addItem("No scanned DFU device")
            else:
                for d in devices:
                    bus_text = "?" if d.bus is None else str(d.bus)
                    addr_text = "?" if d.address is None else str(d.address)
                    self.view.combo_devices.addItem(
                        f"{d.vid:04X}:{d.pid:04X} | bus {bus_text} addr {addr_text} | IF {d.interface_number}"
                    )

            if vid_filter is None or pid_filter is None:
                self.signals.log.emit(f"Scan complete: found {len(devices)} DFU device(s)")
            else:
                self.signals.log.emit(
                    f"Scan complete: found {len(devices)} DFU device(s) for {vid_filter:04X}:{pid_filter:04X}"
                )

            if devices:
                first = devices[0]
                self.view.vid_edit.setText(f"{first.vid:04X}")
                self.view.pid_edit.setText(f"{first.pid:04X}")
        self.main_ctrl.run_task(job)

    def on_connect(self):
        def job():
            vid = int(self.view.vid_edit.text().strip(), 16)
            pid = int(self.view.pid_edit.text().strip(), 16)
            self._connect_with_device_info(DeviceInfo(vid=vid, pid=pid, bus=None, address=None, interface_number=0))
        self.main_ctrl.run_task(job)

    def on_auto_connect(self):
        def job():
            if not self.scanned_devices:
                vid_filter, pid_filter = self._parse_scan_filters()
                self.scanned_devices = DfuDevice.list_dfu_devices(vid_filter=vid_filter, pid_filter=pid_filter)
            if not self.scanned_devices:
                raise DfuError("No DFU device found. Check USB cable/driver and click Scan again.")

            selected_index = self.view.combo_devices.currentIndex()
            if selected_index < 0 or selected_index >= len(self.scanned_devices):
                selected_index = 0

            selected = self.scanned_devices[selected_index]
            self.view.vid_edit.setText(f"{selected.vid:04X}")
            self.view.pid_edit.setText(f"{selected.pid:04X}")
            self._connect_with_device_info(selected)
        self.main_ctrl.run_task(job)

    def _connect_with_device_info(self, info: DeviceInfo):
        attempts = [info]
        if info.bus is not None or info.address is not None:
            attempts.append(
                DeviceInfo(
                    vid=info.vid,
                    pid=info.pid,
                    bus=None,
                    address=None,
                    interface_number=info.interface_number,
                )
            )

        last_error = None
        opened = None
        for idx, candidate in enumerate(attempts):
            self.dfu.close()
            self.dfu = DfuDevice(vid=candidate.vid, pid=candidate.pid, bus=candidate.bus, address=candidate.address)
            try:
                opened = self.dfu.open()
                break
            except DfuError as exc:
                last_error = exc
                if idx == 0 and len(attempts) > 1:
                    self.signals.log.emit(
                        "Auto-connect retry: scanned bus/address changed, retrying by VID/PID only..."
                    )

        if opened is None:
            raise last_error if last_error is not None else DfuError("Failed to connect DFU device")

        self.signals.log.emit(
            f"Connected to {opened.vid:04X}:{opened.pid:04X}, interface={opened.interface_number}, "
            f"bus={opened.bus}, addr={opened.address}"
        )
        self.signals.connected.emit(f"Connected: {opened.vid:04X}:{opened.pid:04X} (IF {opened.interface_number})")
        self.config.set_last_vid_pid(f"{opened.vid:04X}", f"{opened.pid:04X}")

        if self.dfu.ping_activity():
            self.signals.log.emit("DFU ping sent (activity should be visible on bootloader LED)")
        else:
            self.signals.log.emit("Connected, but DFU ping did not complete")

    def on_erase_app(self):
        selected = []
        for r in range(self.view.table_sectors.rowCount()):
            item = self.view.table_sectors.item(r, 0)
            if item and item.checkState() == Qt.Checked:
                selected.append(int(item.data(Qt.UserRole)))
        
        def job():
            if not selected: raise DfuError("No sector selected.")
            self.signals.log.emit("Erasing selected sectors...")
            for i, addr in enumerate(selected, 1):
                self.dfu.erase_address(addr)
                self.signals.progress.emit(int(i * 100 / len(selected)))
            self.signals.log.emit("Done erasing.")
        self.main_ctrl.run_task(job)

    def on_mass_erase(self):
        def job():
            self.signals.log.emit("Mass erase...")
            self.dfu.mass_erase()
            self.signals.progress.emit(100)
            self.signals.log.emit("Mass erase done.")
        self.main_ctrl.run_task(job)

    def _ensure_hex_loaded(self):
        path = self.view.combo_file.currentText().strip()
        if not path: raise DfuError("No HEX file selected")
        if self.current_hex is None or self.current_file != path:
            self.current_hex = HexService.load_hex_image(path)
            self.current_file = path
            self._push_recent_path(path)

    def _parse_scan_filters(self) -> tuple[int | None, int | None]:
        try:
            vid = int(self.view.vid_edit.text().strip(), 16)
            pid = int(self.view.pid_edit.text().strip(), 16)
            return vid, pid
        except Exception:
            return None, None

    def _check_in_app_range(self, start_address: int, size: int):
        end_addr = start_address + size - 1
        if start_address < APP_START or end_addr > APP_END:
            raise DfuError(
                f"Image out of app range: start=0x{start_address:08X}, end=0x{end_addr:08X}, "
                f"allowed=0x{APP_START:08X}..0x{APP_END:08X}"
            )

    def on_flash(self):
        def job():
            self._ensure_hex_loaded()
            payload = self.current_hex.data
            start = self.current_hex.start_address
            self._check_in_app_range(start, len(payload))
            xfer = int(self.view.spin_transfer.value())
            self.signals.log.emit(f"Flashing {len(payload)} bytes to 0x{start:08X} with transfer={xfer}...")
            def prog(sent, tot): self.signals.progress.emit(int(sent * 100 / tot))
            self.dfu.write_memory(start, payload, transfer_size=xfer, progress=prog)
            self.signals.progress.emit(100)
            self.signals.log.emit("Flash done.")
        self.main_ctrl.run_task(job)

    def on_verify(self):
        def job():
            self._ensure_hex_loaded()
            payload = self.current_hex.data
            start = self.current_hex.start_address
            self._check_in_app_range(start, len(payload))
            xfer = int(self.view.spin_transfer.value())
            self.signals.log.emit(
                f"Verifying {len(payload)} bytes from 0x{start:08X} with transfer={xfer}..."
            )
            def prog(done, tot): self.signals.progress.emit(int(done * 100 / tot))
            try:
                readback = self.dfu.read_memory(start, len(payload), transfer_size=xfer, progress=prog)
            except Exception as exc:
                if _is_pipe_error(exc) and xfer != 64:
                    self.signals.log.emit(
                        "Pipe error during verify; retrying with transfer size 64 for compatibility..."
                    )
                    readback = self.dfu.read_memory(start, len(payload), transfer_size=64, progress=prog)
                else:
                    raise

            if readback != payload:
                for i, (a, b) in enumerate(zip(payload, readback)):
                    if a != b:
                        raise DfuError(f"Verify failed at offset 0x{i:X}: file=0x{a:02X}, target=0x{b:02X}")
                if len(readback) != len(payload):
                    raise DfuError(f"Verify failed: size mismatch file={len(payload)} read={len(readback)}")
                raise DfuError("Verify failed: unknown mismatch")

            self.signals.progress.emit(100)
            self.signals.log.emit("Verify OK.")
        self.main_ctrl.run_task(job)

    def shutdown(self):
        self.dfu.close()
