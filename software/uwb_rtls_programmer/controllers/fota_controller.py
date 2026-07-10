from PySide6.QtCore import QObject, Signal, Qt, QThread
from PySide6.QtWidgets import QFileDialog, QTableWidgetItem
import os
import queue
import time
import traceback

from views.fota_tab import FotaTab
from services.config_service import ConfigService
from services.fota_service import FotaService
from utils.workers import WorkerSignals

class DongleMonitorThread(QThread):
    dongle_connected = Signal(str, int)  # port, serial_number
    dongle_disconnected = Signal()
    ble_status = Signal(dict)
    operation_done = Signal(str, bool, str)
    operation_error = Signal(str)

    def __init__(self, fota_service):
        super().__init__()
        self.fota_service = fota_service
        self.running = True
        self.current_port = None
        self._commands = queue.Queue()

    def submit(self, name, callback):
        self._commands.put((name, callback))

    def run(self):
        failed_ports = set()
        last_status_poll = 0.0
        last_port_check = 0.0
        last_probe = 0.0
        while self.running:
            try:
                name, callback = self._commands.get_nowait()
            except queue.Empty:
                name = callback = None

            if callback is not None:
                try:
                    callback()
                except Exception as exc:
                    self.operation_error.emit(
                        f"[FOTA] {name} failed: {exc}\n{traceback.format_exc()}"
                    )
                    self.operation_done.emit(name, False, str(exc))
                else:
                    self.operation_done.emit(name, True, "OK")
                continue

            if not self.current_port:
                if time.monotonic() - last_probe < 1.0:
                    self.msleep(100)
                    continue
                last_probe = time.monotonic()
                from serial.tools import list_ports
                current_ports = {p.device for p in list_ports.comports()}
                
                # Remove any ports from failed_ports that are no longer physically present
                failed_ports = {p for p in failed_ports if p in current_ports}
                
                candidates = current_ports - failed_ports
                if candidates:
                    probe = self.fota_service.auto_probe_dongle(ignore_ports=failed_ports)
                    if probe:
                        try:
                            self.fota_service.open_persistent_session(probe.port)
                        except Exception as exc:
                            failed_ports.add(probe.port)
                            self.operation_error.emit(
                                f"[FOTA] Could not keep {probe.port} open: {exc}"
                            )
                        else:
                            self.current_port = probe.port
                            last_status_poll = 0.0
                            last_port_check = 0.0
                            self.dongle_connected.emit(probe.port, probe.serial_number)
                    else:
                        # Since auto_probe returned None, all candidate ports that were probed failed.
                        # Blacklist them until they are unplugged.
                        failed_ports.update(candidates)
            else:
                now = time.monotonic()
                port_present = True
                if now - last_port_check >= 1.0:
                    port_present = self.fota_service.ping_dongle(self.current_port)
                    last_port_check = now

                if not port_present:
                    self.fota_service.close_persistent_session()
                    self.current_port = None
                    last_status_poll = 0.0
                    self.dongle_disconnected.emit()
                elif now - last_status_poll >= 5.0:
                    status = self.fota_service.get_ble_status(self.current_port)
                    last_status_poll = time.monotonic()
                    if status is not None:
                        self.ble_status.emit(status)

                if self.current_port is not None:
                    try:
                        statuses = self.fota_service.recv_unsolicited_ble_status(0.05)
                    except Exception as exc:
                        self.fota_service.close_persistent_session()
                        self.current_port = None
                        last_status_poll = 0.0
                        self.operation_error.emit(f"[FOTA] COM receive failed: {exc}")
                        self.dongle_disconnected.emit()
                    else:
                        for status in statuses:
                            self.ble_status.emit(status)
            
            self.msleep(100)

    def stop(self):
        self.running = False
        self.wait()
        self.fota_service.close_persistent_session()

class FotaController(QObject):
    scan_result_signal = Signal(dict)
    connection_status_signal = Signal(str)

    def __init__(self, view: FotaTab, signals: WorkerSignals, config: ConfigService, main_ctrl):
        super().__init__()
        self.view = view
        self.signals = signals
        self.config = config
        self.main_ctrl = main_ctrl
        self.fota_service = FotaService()
        self.current_dongle_port = None
        self.is_connected = False
        self.connected_mac_str = None
        self.connected_mac_bytes = None
        self._last_ble_status = None
        
        self._setup_connections()
        self._load_preferences()
        
        self.monitor_thread = DongleMonitorThread(self.fota_service)
        self.monitor_thread.dongle_connected.connect(self._on_dongle_connected)
        self.monitor_thread.dongle_disconnected.connect(self._on_dongle_disconnected)
        self.monitor_thread.ble_status.connect(self._on_ble_status_monitored)
        self.monitor_thread.operation_done.connect(self._on_operation_done)
        self.monitor_thread.operation_error.connect(self.signals.log.emit)
        self.monitor_thread.start()

    def shutdown(self):
        if hasattr(self, 'monitor_thread') and self.monitor_thread.isRunning():
            self.monitor_thread.stop()
            self.monitor_thread.wait()

    def _on_dongle_connected(self, port, serial_number):
        self.current_dongle_port = port
        self.view.lbl_dongle_status.setText(f"Connected: {port} (SN: {serial_number})")
        self.view.lbl_dongle_status.setStyleSheet("color: #10B981; font-weight: bold;") # Green
        self.signals.log.emit(f"[FOTA] Dongle Auto-Detected on {port} (SN: {serial_number})")

    def _on_dongle_disconnected(self):
        self.current_dongle_port = None
        self.view.lbl_dongle_status.setText("Searching for Central Dongle...")
        self.view.lbl_dongle_status.setStyleSheet("color: #F59E0B; font-weight: bold;") # Yellow/Orange
        self.signals.log.emit(
            "[DISCONNECTED] [FOTA] Dongle removed; reason=dongle removed"
        )
        
        # Revert connection state to idle/disconnected if dongle is unplugged
        self.is_connected = False
        self.connected_mac_str = None
        self.connected_mac_bytes = None
        self._on_connection_status("Disconnected")
        self._last_ble_status = None
        self.view.lbl_ble_state.setText("UNKNOWN")

    @staticmethod
    def _ble_state_name(state: int) -> str:
        names = {0: "UNSPECIFIED", 1: "IDLE", 2: "SCANNING", 3: "ADVERTISING", 4: "CONNECTING", 5: "CONNECTED"}
        return names.get(state, f"UNKNOWN({state})")

    @staticmethod
    def _disconnect_reason_text(reason: int) -> str:
        names = {0x00: "none", 0x08: "connection timeout", 0x13: "remote user terminated", 0x16: "local host terminated", 0x22: "LMP response timeout", 0x3B: "unacceptable connection parameters"}
        return f"{names.get(reason, 'unknown')} (0x{reason:02X})"

    def _on_ble_status_monitored(self, status: dict):
        state = status["state"]
        verification_failed = (
            status.get("central_state") == 5
            and not status.get("device_verified", False)
        )
        if state == 5 and not status.get("device_verified", False):
            # Never expose Central-only CONNECTED as an end-to-end connection.
            state = 4
        state_name = self._ble_state_name(state)
        reason = status["disconnect_reason"]
        rssi = status["rssi_dbm"]
        self.view.lbl_ble_state.setText(state_name)
        previous_state = self._last_ble_status[0] if self._last_ble_status else None
        status_key = (state, reason)
        state_changed = status_key != self._last_ble_status
        self._last_ble_status = status_key

        if verification_failed:
            if self.is_connected:
                self.is_connected = False
                self._on_connection_status("Disconnected")
            if state_changed:
                self.signals.log.emit(
                    "[DISCONNECTED] [FOTA] Central reports CONNECTED, but "
                    "device_information_get timed out; end-to-end link is unusable"
                )
        elif state == 5:
            if not self.is_connected:
                mac_str = self.connected_mac_str or "Device"
                self.is_connected = True
                self._on_connection_status(f"Connected: {mac_str}")
            if state_changed:
                info = status.get("device_info") or {}
                self.signals.log.emit(
                    f"[DEBUG] [FOTA] BLE state={state_name} raw={state} "
                    f"RSSI={rssi} dBm device_verified=yes "
                    f"SN={info.get('serial_number', '-')}"
                )
        else:
            if self.is_connected:
                self.is_connected = False
                self.connected_mac_str = None
                self.connected_mac_bytes = None
                self._on_connection_status("Disconnected")
            if state_changed:
                prefix = (
                    "[DISCONNECTED]"
                    if reason != 0 or previous_state == 5
                    else "[DEBUG]"
                )
                self.signals.log.emit(
                    f"{prefix} [FOTA] BLE state={state_name} raw={state}, "
                    f"disconnect_reason={self._disconnect_reason_text(reason)}"
                )

    def _on_operation_done(self, name: str, ok: bool, msg: str):
        self.view.btn_scan.setEnabled(not self.is_connected)
        self.view.btn_connect.setEnabled(True)
        self.view.btn_auto_fota.setEnabled(True)
        if not ok:
            self.signals.log.emit(f"[FOTA] ERROR: {name}: {msg}")

    def _setup_connections(self):
        self.view.btn_browse.clicked.connect(self.on_browse)
        self.view.btn_scan.clicked.connect(self.on_scan)
        self.view.btn_connect.clicked.connect(self.on_connect)
        self.view.btn_erase_app.clicked.connect(self.on_erase_app)
        self.view.btn_verify.clicked.connect(self.on_verify)
        self.view.btn_auto_fota.clicked.connect(self.on_auto_fota)
        
        self.scan_result_signal.connect(self._on_scan_result)
        self.connection_status_signal.connect(self._on_connection_status)

    def _on_connection_status(self, status: str):
        self.view.lbl_device_status.setText(status)
        if status.startswith("Connected"):
            self.is_connected = True
            self.view.btn_connect.setText("Disconnect")
            self.view.btn_connect.setStyleSheet("background-color: #EF4444; border: 1px solid #DC2626;") # Red
            self.view.btn_connect.setEnabled(True)
            self.view.table_ble.setEnabled(False)
            self.view.btn_scan.setEnabled(False)
        else:
            self.is_connected = False
            self.view.btn_connect.setText("Connect Selected")
            self.view.btn_connect.setStyleSheet("") # Default
            self.view.btn_connect.setEnabled(True)
            self.view.table_ble.setEnabled(True)
            self.view.btn_scan.setEnabled(True)

    def _load_preferences(self):
        try:
            from services.build_service import BuildService
            uwb_dir = BuildService.get_uwb_project_dir()
            version_dir = os.path.join(uwb_dir, "build_version")
            versioned_files = []
            if os.path.exists(version_dir):
                for f in os.listdir(version_dir):
                    if f.endswith(".hex"):
                        full_path = os.path.normpath(os.path.join(version_dir, f))
                        mtime = os.path.getmtime(full_path)
                        versioned_files.append((mtime, full_path))
                # Sort by mtime descending (newest first)
                versioned_files.sort(reverse=True, key=lambda x: x[0])
        except Exception:
            versioned_files = []

        final_paths = []
        for _, path in versioned_files:
            if path not in final_paths:
                final_paths.append(path)

        recent = self.config.get_recent_hex_paths()
        for p in recent:
            p_norm = os.path.normpath(p)
            if os.path.exists(p_norm) and p_norm not in final_paths:
                final_paths.append(p_norm)

        for p in final_paths:
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
        self._push_recent_path(path)
        self.config.set_last_hex_dir(os.path.dirname(path))
        self.signals.log.emit(f"[FOTA] Selected HEX: {path}")

    def _on_scan_result(self, result_dict: dict):
        for mac, info in result_dict.items():
            found = False
            for row in range(self.view.table_ble.rowCount()):
                if self.view.table_ble.item(row, 1).text() == mac:
                    self.view.table_ble.setItem(row, 0, QTableWidgetItem(info['name']))
                    self.view.table_ble.setItem(row, 2, QTableWidgetItem(str(info['sn'])))
                    self.view.table_ble.setItem(row, 3, QTableWidgetItem(str(info['rssi'])))
                    found = True
                    break
            if not found:
                row = self.view.table_ble.rowCount()
                self.view.table_ble.insertRow(row)
                self.view.table_ble.setItem(row, 0, QTableWidgetItem(info['name']))
                self.view.table_ble.setItem(row, 1, QTableWidgetItem(mac))
                self.view.table_ble.setItem(row, 2, QTableWidgetItem(str(info['sn'])))
                self.view.table_ble.setItem(row, 3, QTableWidgetItem(str(info['rssi'])))

    def on_scan(self):
        port = self.current_dongle_port
        if not port:
            self.signals.log.emit("[FOTA] ERROR: Dongle not detected. Please plug in the Dongle.")
            return
            
        self.view.table_ble.setRowCount(0)
        self.signals.log.emit(f"[FOTA] Scanning for BLE peripherals using {port}...")
        self.view.btn_scan.setEnabled(False)
        self.view.btn_connect.setEnabled(False)
        self.view.btn_auto_fota.setEnabled(False)
        def task():
            self.fota_service.scan_nearby_devices(
                port=port,
                log_cb=self.signals.log.emit,
                result_cb=self.scan_result_signal.emit,
                ble_status_cb=self.monitor_thread.ble_status.emit,
            )
        self.monitor_thread.submit("BLE scan", task)

    def on_connect(self):
        port = self.current_dongle_port
        if not port:
            self.signals.log.emit("[FOTA] ERROR: Dongle not detected.")
            return
            
        if self.is_connected:
            self.signals.log.emit("[FOTA] Disconnecting from device...")
            self.view.btn_connect.setEnabled(False)
            self.view.btn_scan.setEnabled(False)
            self.view.btn_auto_fota.setEnabled(False)
            self.connected_mac_str = None
            self.connected_mac_bytes = None
            def task_disconnect():
                self.fota_service.disconnect_device(
                    port=port,
                    log_cb=self.signals.log.emit,
                    disconnected_cb=self.connection_status_signal.emit,
                    ble_status_cb=self.monitor_thread.ble_status.emit,
                )
            self.monitor_thread.submit("BLE disconnect", task_disconnect)
            return

        row = self.view.table_ble.currentRow()
        if row < 0:
            self.signals.log.emit("[FOTA] Please select a device to connect.")
            return
        mac_str = self.view.table_ble.item(row, 1).text()
        mac_bytes = bytes.fromhex(mac_str.replace(":", ""))
        
        self.connected_mac_str = mac_str
        self.connected_mac_bytes = mac_bytes
        
        self.signals.log.emit(f"[FOTA] Connecting to {mac_str} via {port}...")
        self.view.btn_connect.setEnabled(False)
        self.view.btn_scan.setEnabled(False)
        self.view.btn_auto_fota.setEnabled(False)
        
        def task_connect():
            self.fota_service.connect_to_device(
                port=port,
                mac_bytes=mac_bytes,
                mac_str=mac_str,
                log_cb=self.signals.log.emit,
                connected_cb=self.connection_status_signal.emit,
                ble_status_cb=self.monitor_thread.ble_status.emit,
            )
        self.monitor_thread.submit("BLE connect", task_connect)

    def on_erase_app(self):
        if not self.is_connected:
            self.signals.log.emit("[FOTA] ERROR: Device is not connected.")
            return
        self.signals.log.emit("[FOTA] Erasing app sectors via OTA... (Not implemented yet)")

    def on_verify(self):
        if not self.is_connected:
            self.signals.log.emit("[FOTA] ERROR: Device is not connected.")
            return
        self.signals.log.emit("[FOTA] Verifying... (Not implemented yet)")

    def on_auto_fota(self):
        if not self.is_connected:
            self.signals.log.emit("[FOTA] ERROR: Device is not connected. Please connect to a BLE device first.")
            return
            
        port = self.current_dongle_port
        if not port:
            self.signals.log.emit("[FOTA] ERROR: Dongle not detected.")
            return
            
        hex_path = self.view.combo_file.currentText().strip()
        if not hex_path or not os.path.exists(hex_path):
            self.signals.log.emit("[FOTA] ERROR: Invalid HEX file selected.")
            return
            
        mac_str = self.connected_mac_str
        mac_bytes = self.connected_mac_bytes
        if not mac_str:
            row = self.view.table_ble.currentRow()
            if row >= 0:
                mac_str = self.view.table_ble.item(row, 1).text()
                mac_bytes = bytes.fromhex(mac_str.replace(":", ""))
                
        if not mac_str:
            self.signals.log.emit("[FOTA] ERROR: No target device selected or connected.")
            return

        chunk_size = self.view.spin_chunk.value()
        
        self.signals.log.emit(f"[FOTA] Starting Auto OTA Flash with chunk size {chunk_size} bytes via {port}")
        self.signals.progress.emit(0)
        self.view.btn_scan.setEnabled(False)
        self.view.btn_connect.setEnabled(False)
        self.view.btn_auto_fota.setEnabled(False)
        
        def task():
            self.fota_service.execute_ota_flash(
                port=port,
                hex_path=hex_path,
                chunk_size=chunk_size,
                mac_bytes=mac_bytes,
                mac_str=mac_str,
                log_cb=self.signals.log.emit,
                progress_cb=self.signals.progress.emit,
                status_cb=self.connection_status_signal.emit,
                ble_status_cb=self.monitor_thread.ble_status.emit,
            )
        self.monitor_thread.submit("Auto OTA flash", task)
