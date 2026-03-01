import os
import re
import sys
import time
import threading
import shutil
import subprocess
from dataclasses import dataclass

import usb.core
import usb.util
import usb.backend.libusb1
from PySide6.QtCore import QObject, Qt, Signal, QSettings
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_START = 0x0800C000
APP_END = 0x0803FFFF
APP_SECTORS = [0x0800C000, 0x08010000, 0x08020000]
MAX_RECENT_PATHS = 10
MAX_VERSIONED_BUILDS = 10
ERASE_SECTORS = [
    ("S3", "App", 0x0800C000, 0x0800FFFF),
    ("S4", "App", 0x08010000, 0x0801FFFF),
    ("S5", "App", 0x08020000, 0x0803FFFF),
    ("S6", "Data", 0x08040000, 0x0805FFFF),
    ("S7", "Data", 0x08060000, 0x0807FFFF),
]

REQ_DNLOAD = 0x01
REQ_UPLOAD = 0x02
REQ_GETSTATUS = 0x03
REQ_CLRSTATUS = 0x04
REQ_GETSTATE = 0x05
REQ_ABORT = 0x06

STATE_DFU_IDLE = 2
STATE_DFU_DNLOAD_SYNC = 3
STATE_DFU_DNBUSY = 4
STATE_DFU_DNLOAD_IDLE = 5
STATE_DFU_MANIFEST_SYNC = 6
STATE_DFU_MANIFEST = 7
STATE_DFU_UPLOAD_IDLE = 9
STATE_DFU_ERROR = 10


@dataclass
class DeviceInfo:
    vid: int
    pid: int
    bus: int | None
    address: int | None
    interface_number: int


class DfuError(RuntimeError):
    pass


def _is_pipe_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "pipe error" in text or "errno 32" in text


@dataclass
class HexImage:
    start_address: int
    data: bytes


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
        candidates = list(
            usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid, backend=backend) or []
        )
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
            status, poll, state, _ = self.get_status()
            if status != 0:
                raise DfuError(f"DFU status error: {status}")
            if state in (STATE_DFU_IDLE, STATE_DFU_DNLOAD_IDLE, STATE_DFU_UPLOAD_IDLE):
                return state
            sleep_time = max(poll / 1000.0, 0.01)
            time.sleep(sleep_time)
        raise DfuError("Timeout waiting DFU ready state")

    def _safe_recover_idle(self):
        try:
            state = self.get_state()
            if state == STATE_DFU_ERROR:
                self.clear_status()
                time.sleep(0.02)
            self.abort()
            time.sleep(0.02)
        except Exception:
            pass

    def _dnload_cmd_and_wait(self, block_num: int, payload: bytes):
        self._ctrl_out(REQ_DNLOAD, block_num, payload)
        return self._wait_ready()

    def set_address_pointer(self, address: int):
        cmd = b"\x21" + int(address).to_bytes(4, "little")
        self._dnload_cmd_and_wait(0, cmd)

    def erase_address(self, address: int):
        cmd = b"\x41" + int(address).to_bytes(4, "little")
        self._dnload_cmd_and_wait(0, cmd)

    def mass_erase(self):
        self._dnload_cmd_and_wait(0, b"\x41")

    def write_memory(
        self,
        start_address: int,
        data: bytes,
        transfer_size: int = 1024,
        progress=None,
    ):
        if not data:
            return

        self._safe_recover_idle()
        self._wait_ready()
        self.set_address_pointer(start_address)

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


class WorkerSignals(QObject):
    log = Signal(str)
    progress = Signal(int)
    done = Signal(bool, str)
    connected = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UWB-RTLS Programmer")
        self.resize(860, 640)

        self.dfu = DfuDevice()
        self.current_file = ""
        self.current_hex: HexImage | None = None
        self.scanned_devices: list[DeviceInfo] = []
        self.settings = QSettings("uwb-rtls", "uwb_rtls_programmer")

        self._build_ui()
        self._load_preferences()

        self.signals = WorkerSignals()
        self.signals.log.connect(self._append_log)
        self.signals.progress.connect(self.progress.setValue)
        self.signals.done.connect(self._on_task_done)
        self.signals.connected.connect(self._on_connected)

        self._check_usb_backend()

    def _check_usb_backend(self):
        backend = DfuDevice.get_usb_backend()
        if backend is not None:
            return
        pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
        msg = (
            "USB backend is missing (PyUSB cannot find libusb).\n\n"
            f"Current Python version: {pyver}.\n"
            "Fix on Windows:\n"
            "1) Install libusb backend (WinUSB/libusbK) for the DFU interface using Zadig.\n"
            "2) Use Python 3.12 for this app (3.13+ may miss compatible libusb binaries).\n"
            "3) Install Python package: pip install libusb-package.\n"
            "4) If needed, ensure libusb-1.0.dll is available to Python (PATH or app folder).\n"
            "5) Restart the app and click Scan again."
        )
        self._append_log("ERROR: " + msg.replace("\n", " "))
        QMessageBox.warning(self, "USB backend missing", msg)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)

        device_box = QGroupBox("Device")
        device_layout = QGridLayout(device_box)

        self.vid_edit = QLineEdit("")
        self.pid_edit = QLineEdit("")
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self.on_scan)
        self.auto_connect_btn = QPushButton("Auto Connect")
        self.auto_connect_btn.clicked.connect(self.on_auto_connect)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.on_connect)
        self.device_combo = QComboBox()
        self.device_combo.setEditable(False)
        self.device_combo.addItem("No scanned DFU device")
        self.dev_label = QLabel("Not connected")

        device_layout.addWidget(QLabel("VID (hex):"), 0, 0)
        device_layout.addWidget(self.vid_edit, 0, 1)
        device_layout.addWidget(QLabel("PID (hex):"), 0, 2)
        device_layout.addWidget(self.pid_edit, 0, 3)
        device_layout.addWidget(self.connect_btn, 0, 4)
        device_layout.addWidget(self.scan_btn, 0, 5)
        device_layout.addWidget(self.auto_connect_btn, 0, 6)
        device_layout.addWidget(QLabel("Scanned DFU devices:"), 1, 0, 1, 2)
        device_layout.addWidget(self.device_combo, 1, 2, 1, 5)
        device_layout.addWidget(self.dev_label, 2, 0, 1, 7)

        file_box = QGroupBox("Firmware")
        file_layout = QGridLayout(file_box)

        self.file_combo = QComboBox()
        self.file_combo.setEditable(True)
        self.file_combo.setInsertPolicy(QComboBox.NoInsert)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self.on_browse)

        self.transfer_size = QSpinBox()
        self.transfer_size.setRange(64, 2048)
        self.transfer_size.setSingleStep(64)
        self.transfer_size.setValue(1024)

        file_layout.addWidget(QLabel("File:"), 0, 0)
        file_layout.addWidget(self.file_combo, 0, 1, 1, 4)
        file_layout.addWidget(self.browse_btn, 0, 5)
        file_layout.addWidget(QLabel("Transfer size:"), 1, 0)
        file_layout.addWidget(self.transfer_size, 1, 1)
        file_layout.addWidget(QLabel("bytes"), 1, 2)

        operations_box = QGroupBox("Operations")
        operations_box.setMaximumHeight(250)
        operations_layout = QHBoxLayout(operations_box)

        action_panel = QWidget()
        action_panel_layout = QVBoxLayout(action_panel)

        self.erase_app_btn = QPushButton("Erase App Sectors")
        self.erase_app_btn.clicked.connect(self.on_erase_app)
        self.mass_erase_btn = QPushButton("Mass Erase")
        self.mass_erase_btn.clicked.connect(self.on_mass_erase)
        self.flash_btn = QPushButton("Flash")
        self.flash_btn.clicked.connect(self.on_flash)
        self.verify_btn = QPushButton("Verify")
        self.verify_btn.clicked.connect(self.on_verify)
        self.build_btn = QPushButton("Build")
        self.build_btn.clicked.connect(self.on_build)

        action_panel_layout.addWidget(self.build_btn)
        action_panel_layout.addWidget(self.flash_btn)
        action_panel_layout.addWidget(self.verify_btn)
        action_panel_layout.addWidget(self.erase_app_btn)
        action_panel_layout.addWidget(self.mass_erase_btn)

        self.sectors_table = QTableWidget(len(ERASE_SECTORS), 4)
        self.sectors_table.setHorizontalHeaderLabels(["Erase", "Sector", "Type", "Address Range"])
        self.sectors_table.verticalHeader().setVisible(False)
        self.sectors_table.setSelectionMode(QTableWidget.NoSelection)
        self.sectors_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sectors_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.sectors_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sectors_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sectors_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.sectors_table.setMaximumHeight(200)

        for row, (sector_name, sector_type, start, end) in enumerate(ERASE_SECTORS):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setData(Qt.UserRole, start)
            self.sectors_table.setItem(row, 0, check_item)
            self.sectors_table.setItem(row, 1, QTableWidgetItem(sector_name))
            self.sectors_table.setItem(row, 2, QTableWidgetItem(sector_type))
            self.sectors_table.setItem(row, 3, QTableWidgetItem(f"0x{start:08X} - 0x{end:08X}"))

        operations_layout.addWidget(action_panel, 1)
        operations_layout.addWidget(self.sectors_table, 3)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        info = QLabel(f"App range: 0x{APP_START:08X} - 0x{APP_END:08X}")
        info.setAlignment(Qt.AlignLeft)

        main.addWidget(device_box)
        main.addWidget(file_box)
        main.addWidget(operations_box)
        main.addWidget(info)
        main.addWidget(self.progress)
        main.addWidget(self.log)

    def _set_busy(self, busy: bool):
        self.connect_btn.setEnabled(not busy)
        self.scan_btn.setEnabled(not busy)
        self.auto_connect_btn.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.erase_app_btn.setEnabled(not busy)
        self.sectors_table.setEnabled(not busy)
        self.mass_erase_btn.setEnabled(not busy)
        self.flash_btn.setEnabled(not busy)
        self.verify_btn.setEnabled(not busy)
        self.build_btn.setEnabled(not busy)

    def _get_uwb_project_dir(self) -> str:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
        return os.path.join(repo_root, "firmware", "uwb")

    def _resolve_make_command(self) -> str:
        for cmd in ("make", "mingw32-make"):
            if shutil.which(cmd):
                return cmd
        raise DfuError("Cannot find 'make' in PATH. Install Make (or mingw32-make) and try again.")

    def _run_make_target(self, target: str):
        uwb_dir = self._get_uwb_project_dir()
        makefile_path = os.path.join(uwb_dir, "Makefile")
        if not os.path.exists(makefile_path):
            raise DfuError(f"Missing Makefile: {makefile_path}")

        make_cmd = self._resolve_make_command()
        cmd = [make_cmd, "-f", "Makefile", target]

        self.signals.log.emit(f"Running build command: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            cwd=uwb_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        assert process.stdout is not None
        for line in process.stdout:
            self.signals.log.emit(line.rstrip())

        process.wait()
        if process.returncode != 0:
            raise DfuError(f"Build failed with exit code {process.returncode}")

    def _get_repo_root(self) -> str:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.abspath(os.path.join(script_dir, "..", ".."))

    def _get_current_git_hash(self) -> str:
        try:
            output = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self._get_repo_root(),
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            value = output.strip()
            if not value:
                return "nogit"
            return value
        except Exception:
            return "nogit"

    def _archive_versioned_build(self):
        uwb_dir = self._get_uwb_project_dir()
        build_dir = os.path.join(uwb_dir, "build")
        version_dir = os.path.join(uwb_dir, "build_version")
        hex_path = os.path.join(build_dir, "uwb-rtls.hex")
        map_path = os.path.join(build_dir, "uwb-rtls.map")

        os.makedirs(version_dir, exist_ok=True)

        if not os.path.exists(hex_path):
            raise DfuError(f"Missing build HEX output: {hex_path}")
        if not os.path.exists(map_path):
            raise DfuError(f"Missing build MAP output: {map_path}")

        git_hash = self._get_current_git_hash()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        pattern = re.compile(r"^uwb-rtls_([0-9a-fA-F]+|nogit)_(\d{8}_\d{6})\\.hex$")

        for name in os.listdir(version_dir):
            match = pattern.match(name)
            if not match:
                continue
            if match.group(1).lower() != git_hash.lower():
                continue
            old_hex = os.path.join(version_dir, name)
            old_map = old_hex[:-4] + ".map"
            try:
                os.remove(old_hex)
            except Exception:
                pass
            if os.path.exists(old_map):
                try:
                    os.remove(old_map)
                except Exception:
                    pass

        versioned_base = os.path.join(version_dir, f"uwb-rtls_{git_hash}_{timestamp}")
        versioned_hex = versioned_base + ".hex"
        versioned_map = versioned_base + ".map"
        shutil.copy2(hex_path, versioned_hex)
        shutil.copy2(map_path, versioned_map)

        archives = []
        for name in os.listdir(version_dir):
            match = pattern.match(name)
            if not match:
                continue
            archive_hex = os.path.join(version_dir, name)
            try:
                mtime = os.path.getmtime(archive_hex)
            except Exception:
                continue
            archives.append((mtime, archive_hex))

        archives.sort(reverse=True)
        for _, old_hex in archives[MAX_VERSIONED_BUILDS:]:
            old_map = old_hex[:-4] + ".map"
            try:
                os.remove(old_hex)
            except Exception:
                pass
            if os.path.exists(old_map):
                try:
                    os.remove(old_map)
                except Exception:
                    pass

        self.signals.log.emit(f"Saved versioned HEX/MAP: {os.path.basename(versioned_hex)}")

    def _get_checked_sector_addresses(self) -> list[int]:
        selected = []
        for row in range(self.sectors_table.rowCount()):
            item = self.sectors_table.item(row, 0)
            if item is None:
                continue
            if item.checkState() == Qt.Checked:
                selected.append(int(item.data(Qt.UserRole)))
        return selected

    def _load_preferences(self):
        vid = self.settings.value("last_vid", "")
        pid = self.settings.value("last_pid", "")

        if not vid:
            vid = "0483"
        if not pid:
            pid = "DF11"

        vid = str(vid)
        pid = str(pid)
        self.vid_edit.setText(vid)
        self.pid_edit.setText(pid)

        normalized = self._get_firmware_candidates()

        for path in normalized:
            self.file_combo.addItem(path)

        self._save_recent_paths(normalized)
        if self.file_combo.count() > 0:
            self.file_combo.setCurrentIndex(0)

    def _save_recent_paths(self, paths: list[str]):
        self.settings.setValue("recent_hex_paths", paths[:MAX_RECENT_PATHS])
        self.settings.sync()

    def _get_firmware_candidates(self) -> list[str]:
        candidates: list[str] = []

        recent = self.settings.value("recent_hex_paths", [])
        if isinstance(recent, str):
            recent = [recent] if recent else []
        for path in recent:
            if not path:
                continue
            clean = os.path.normpath(str(path))
            if os.path.exists(clean):
                candidates.append(clean)

        uwb_dir = self._get_uwb_project_dir()
        build_hex = os.path.join(uwb_dir, "build", "uwb-rtls.hex")
        if os.path.exists(build_hex):
            candidates.append(os.path.normpath(build_hex))

        version_dir = os.path.join(uwb_dir, "build_version")
        if os.path.isdir(version_dir):
            hex_files = []
            for name in os.listdir(version_dir):
                if not name.lower().endswith(".hex"):
                    continue
                full_path = os.path.join(version_dir, name)
                try:
                    mtime = os.path.getmtime(full_path)
                except Exception:
                    continue
                hex_files.append((mtime, os.path.normpath(full_path)))
            hex_files.sort(reverse=True)
            for _, file_path in hex_files:
                candidates.append(file_path)

        merged: list[str] = []
        seen: set[str] = set()
        for path in candidates:
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(path)
            if len(merged) >= MAX_RECENT_PATHS:
                break

        return merged

    def _push_recent_path(self, path: str):
        clean_path = os.path.normpath(path)
        existing = []
        for i in range(self.file_combo.count()):
            text = self.file_combo.itemText(i).strip()
            if text:
                existing.append(os.path.normpath(text))

        merged = [clean_path] + [item for item in existing if item.lower() != clean_path.lower()]
        merged = merged[:MAX_RECENT_PATHS]

        self.file_combo.clear()
        for item in merged:
            self.file_combo.addItem(item)
        self.file_combo.setCurrentText(clean_path)
        self._save_recent_paths(merged)

    def _parse_scan_filters(self) -> tuple[int | None, int | None]:
        try:
            vid = int(self.vid_edit.text().strip(), 16)
            pid = int(self.pid_edit.text().strip(), 16)
            return vid, pid
        except Exception:
            return None, None

    def _append_log(self, text: str):
        lower = text.lower()

        if text.startswith("ERROR:") or " error:" in lower or "failed" in lower:
            color = QColor("#ff6b6b")
        elif "warning" in lower:
            color = QColor("#ffd166")
        elif text.startswith("Running build command:") or lower.startswith("make"):
            color = QColor("#80caff")
        elif "done" in lower or "ok" in lower or "finished building" in lower:
            color = QColor("#95d5b2")
        else:
            color = QColor("#e6e6e6")

        fmt = QTextCharFormat()
        fmt.setForeground(color)

        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text + "\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _run_task(self, fn):
        self._set_busy(True)
        self.progress.setValue(0)

        def target():
            try:
                fn()
                self.signals.done.emit(True, "OK")
            except Exception as exc:
                self.signals.done.emit(False, str(exc))

        threading.Thread(target=target, daemon=True).start()

    def _on_task_done(self, ok: bool, msg: str):
        self._set_busy(False)
        if not ok:
            self._append_log(f"ERROR: {msg}")
            QMessageBox.critical(self, "Operation failed", msg)

    def _on_connected(self, txt: str):
        self.dev_label.setText(txt)

    def _populate_scanned_devices(self, devices: list[DeviceInfo]):
        self.scanned_devices = devices
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("No scanned DFU device")
            return
        for device in devices:
            bus_text = "?" if device.bus is None else str(device.bus)
            addr_text = "?" if device.address is None else str(device.address)
            text = (
                f"{device.vid:04X}:{device.pid:04X} | bus {bus_text} addr {addr_text} | IF {device.interface_number}"
            )
            self.device_combo.addItem(text)

    def on_scan(self):
        def job():
            vid_filter, pid_filter = self._parse_scan_filters()
            devices = DfuDevice.list_dfu_devices(vid_filter=vid_filter, pid_filter=pid_filter)
            self._populate_scanned_devices(devices)
            if vid_filter is None or pid_filter is None:
                self.signals.log.emit(f"Scan complete: found {len(devices)} DFU device(s)")
            else:
                self.signals.log.emit(
                    f"Scan complete: found {len(devices)} DFU device(s) for {vid_filter:04X}:{pid_filter:04X}"
                )
            if devices:
                first = devices[0]
                self.vid_edit.setText(f"{first.vid:04X}")
                self.pid_edit.setText(f"{first.pid:04X}")

        self._run_task(job)

    def _connect_with_device_info(self, selected_device: DeviceInfo):
        self.dfu.close()
        self.dfu = DfuDevice(
            vid=selected_device.vid,
            pid=selected_device.pid,
            bus=selected_device.bus,
            address=selected_device.address,
        )
        info = self.dfu.open()
        self.signals.log.emit(
            f"Connected to {info.vid:04X}:{info.pid:04X}, interface={info.interface_number}, "
            f"bus={info.bus}, addr={info.address}"
        )
        self.signals.connected.emit(
            f"Connected: {info.vid:04X}:{info.pid:04X} (IF {info.interface_number})"
        )
        self.settings.setValue("last_vid", f"{info.vid:04X}")
        self.settings.setValue("last_pid", f"{info.pid:04X}")
        self.settings.sync()
        if self.dfu.ping_activity():
            self.signals.log.emit("DFU ping sent (activity should be visible on bootloader LED)")
        else:
            self.signals.log.emit("Connected, but DFU ping did not complete")

    def on_auto_connect(self):
        def job():
            if not self.scanned_devices:
                vid_filter, pid_filter = self._parse_scan_filters()
                devices = DfuDevice.list_dfu_devices(vid_filter=vid_filter, pid_filter=pid_filter)
                self._populate_scanned_devices(devices)
            if not self.scanned_devices:
                raise DfuError("No DFU device found. Check USB cable/driver and click Scan again.")

            selected_index = self.device_combo.currentIndex()
            if selected_index < 0 or selected_index >= len(self.scanned_devices):
                selected_index = 0

            selected_device = self.scanned_devices[selected_index]
            self.vid_edit.setText(f"{selected_device.vid:04X}")
            self.pid_edit.setText(f"{selected_device.pid:04X}")
            self._connect_with_device_info(selected_device)

        self._run_task(job)

    def on_connect(self):
        def job():
            vid = int(self.vid_edit.text().strip(), 16)
            pid = int(self.pid_edit.text().strip(), 16)
            self._connect_with_device_info(
                DeviceInfo(vid=vid, pid=pid, bus=None, address=None, interface_number=0)
            )

        self._run_task(job)

    def on_browse(self):
        start_dir = str(self.settings.value("last_hex_dir", ""))
        path, _ = QFileDialog.getOpenFileName(self, "Select firmware", start_dir, "Intel HEX (*.hex)")
        if not path:
            return
        image = self._load_hex_image(path)
        self.current_file = path
        self.current_hex = image
        self._push_recent_path(path)
        self.settings.setValue("last_hex_dir", os.path.dirname(path))
        self._append_log(
            f"Selected HEX: {path} | range=0x{image.start_address:08X}-0x{(image.start_address + len(image.data) - 1):08X}"
        )

    def _ensure_hex_loaded(self):
        path = self.file_combo.currentText().strip()
        if not path:
            raise DfuError("No HEX file selected")
        if self.current_hex is None or os.path.normpath(path) != os.path.normpath(self.current_file):
            image = self._load_hex_image(path)
            self.current_file = path
            self.current_hex = image
            self._push_recent_path(path)

    def _parse_hex_line(self, line: str) -> tuple[int, int, int, bytes]:
        if not line.startswith(":"):
            raise DfuError("Invalid HEX line: missing ':'")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5:
            raise DfuError("Invalid HEX line: too short")

        byte_count = raw[0]
        if len(raw) != (5 + byte_count):
            raise DfuError("Invalid HEX line length")

        checksum = sum(raw) & 0xFF
        if checksum != 0:
            raise DfuError("HEX checksum mismatch")

        address = (raw[1] << 8) | raw[2]
        rectype = raw[3]
        payload = raw[4 : 4 + byte_count]
        return byte_count, address, rectype, payload

    def _load_hex_image(self, path: str) -> HexImage:
        if not path:
            raise DfuError("No HEX file selected")
        if not os.path.exists(path):
            raise DfuError("Firmware file does not exist")
        memory = {}
        upper_linear = 0
        upper_segment = 0

        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                row = line.strip()
                if not row:
                    continue
                try:
                    count, addr16, rectype, payload = self._parse_hex_line(row)
                except Exception as exc:
                    raise DfuError(f"HEX parse error at line {line_no}: {exc}") from exc

                if rectype == 0x00:
                    base = upper_linear + upper_segment + addr16
                    for offset, value in enumerate(payload):
                        memory[base + offset] = value
                elif rectype == 0x01:
                    break
                elif rectype == 0x02:
                    if count != 2:
                        raise DfuError(f"Invalid type 02 record at line {line_no}")
                    upper_segment = int.from_bytes(payload, "big") << 4
                    upper_linear = 0
                elif rectype == 0x04:
                    if count != 2:
                        raise DfuError(f"Invalid type 04 record at line {line_no}")
                    upper_linear = int.from_bytes(payload, "big") << 16
                    upper_segment = 0
                elif rectype == 0x05:
                    continue
                else:
                    raise DfuError(f"Unsupported HEX record type 0x{rectype:02X} at line {line_no}")

        if not memory:
            raise DfuError("HEX has no data records")

        start = min(memory.keys())
        end = max(memory.keys())
        length = end - start + 1
        data = bytearray([0xFF] * length)
        for address, value in memory.items():
            data[address - start] = value

        return HexImage(start_address=start, data=bytes(data))

    def _check_in_app_range(self, start_address: int, size: int):
        end_addr = start_address + size - 1
        if start_address < APP_START or end_addr > APP_END:
            raise DfuError(
                f"Image out of app range: start=0x{start_address:08X}, end=0x{end_addr:08X}, "
                f"allowed=0x{APP_START:08X}..0x{APP_END:08X}"
            )

    def on_erase_app(self):
        selected_addresses = self._get_checked_sector_addresses()

        def job():
            if not selected_addresses:
                raise DfuError("No sector selected. Tick at least one row in the sectors table.")

            self.signals.log.emit("Erasing selected sectors...")
            for i, sector_addr in enumerate(selected_addresses, start=1):
                self.dfu.erase_address(sector_addr)
                self.signals.log.emit(f"Erased sector at 0x{sector_addr:08X}")
                self.signals.progress.emit(int(i * 100 / len(selected_addresses)))
            self.signals.log.emit("Erase selected sectors done")

        self._run_task(job)

    def on_mass_erase(self):
        def job():
            self.signals.log.emit("Mass erase...")
            self.dfu.mass_erase()
            self.signals.progress.emit(100)
            self.signals.log.emit("Mass erase done")

        self._run_task(job)

    def on_flash(self):
        def job():
            self._ensure_hex_loaded()
            payload = self.current_hex.data
            start = self.current_hex.start_address
            self._check_in_app_range(start, len(payload))
            xfer = int(self.transfer_size.value())

            self.signals.log.emit(
                f"Flashing {len(payload)} bytes to 0x{start:08X} with transfer={xfer}..."
            )

            def progress(sent, total):
                pct = int((sent * 100) / total)
                self.signals.progress.emit(pct)

            self.dfu.write_memory(start, payload, transfer_size=xfer, progress=progress)
            self.signals.progress.emit(100)
            self.signals.log.emit("Flash done")

        self._run_task(job)

    def on_verify(self):
        def job():
            self._ensure_hex_loaded()
            payload = self.current_hex.data
            start = self.current_hex.start_address
            self._check_in_app_range(start, len(payload))
            xfer = int(self.transfer_size.value())

            self.signals.log.emit(
                f"Verifying {len(payload)} bytes from 0x{start:08X} with transfer={xfer}..."
            )

            def progress(done, total):
                pct = int((done * 100) / total)
                self.signals.progress.emit(pct)

            try:
                readback = self.dfu.read_memory(start, len(payload), transfer_size=xfer, progress=progress)
            except Exception as exc:
                if _is_pipe_error(exc) and xfer != 64:
                    self.signals.log.emit(
                        "Pipe error during verify; retrying with transfer size 64 for compatibility..."
                    )
                    readback = self.dfu.read_memory(start, len(payload), transfer_size=64, progress=progress)
                else:
                    raise

            if readback != payload:
                for i, (a, b) in enumerate(zip(payload, readback)):
                    if a != b:
                        raise DfuError(
                            f"Verify failed at offset 0x{i:X}: file=0x{a:02X}, target=0x{b:02X}"
                        )
                if len(readback) != len(payload):
                    raise DfuError(
                        f"Verify failed: size mismatch file={len(payload)} read={len(readback)}"
                    )
                raise DfuError("Verify failed: unknown mismatch")

            self.signals.progress.emit(100)
            self.signals.log.emit("Verify OK")

        self._run_task(job)

    def on_build(self):
        def job():
            self.signals.log.emit("Cleaning firmware before build...")
            self._run_make_target("clean")
            self.signals.log.emit("Building firmware...")
            self._run_make_target("all")
            self._archive_versioned_build()
            self.signals.progress.emit(100)
            self.signals.log.emit("Build done")

        self._run_task(job)

    def closeEvent(self, event):
        try:
            current_path = self.file_combo.currentText().strip()
            if current_path:
                self._push_recent_path(current_path)
            self.dfu.close()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
