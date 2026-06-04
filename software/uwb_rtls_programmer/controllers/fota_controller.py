from PySide6.QtCore import QObject, Signal, Qt, QTimer, QThread
from PySide6.QtWidgets import QFileDialog, QTableWidgetItem
import os
import sys

from views.fota_tab import FotaTab
from services.config_service import ConfigService
from services.fota_service import FotaService
from utils.workers import WorkerSignals

class DongleMonitorThread(QThread):
    dongle_connected = Signal(str, int)  # port, serial_number
    dongle_disconnected = Signal()
    ble_connection_status = Signal(str)  # "Connected" or "Disconnected"

    def __init__(self, fota_service):
        super().__init__()
        self.fota_service = fota_service
        self.running = True
        self.current_port = None
        self.suspended = False

    def run(self):
        failed_ports = set()
        while self.running:
            if self.suspended:
                self.msleep(100)
                continue

            if not self.current_port:
                from serial.tools import list_ports
                current_ports = {p.device for p in list_ports.comports()}
                
                # Remove any ports from failed_ports that are no longer physically present
                failed_ports = {p for p in failed_ports if p in current_ports}
                
                candidates = current_ports - failed_ports
                if candidates:
                    probe = self.fota_service.auto_probe_dongle(ignore_ports=failed_ports)
                    if probe:
                        self.current_port = probe.port
                        self.dongle_connected.emit(probe.port, probe.serial_number)
                    else:
                        # Since auto_probe returned None, all candidate ports that were probed failed.
                        # Blacklist them until they are unplugged.
                        failed_ports.update(candidates)
            else:
                if not self.fota_service.ping_dongle(self.current_port):
                    self.current_port = None
                    self.dongle_disconnected.emit()
                else:
                    status = self.fota_service.get_ble_status(self.current_port)
                    if status is not None:
                        self.ble_connection_status.emit(status)
            
            self.msleep(1000)

    def stop(self):
        self.running = False
        self.wait()

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
        
        self._setup_connections()
        self._load_preferences()
        
        self.monitor_thread = DongleMonitorThread(self.fota_service)
        self.monitor_thread.dongle_connected.connect(self._on_dongle_connected)
        self.monitor_thread.dongle_disconnected.connect(self._on_dongle_disconnected)
        self.monitor_thread.ble_connection_status.connect(self._on_ble_connection_status_monitored)
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
        self.signals.log.emit("[FOTA] Dongle Disconnected. Waiting for device...")
        
        # Revert connection state to idle/disconnected if dongle is unplugged
        self.is_connected = False
        self.connected_mac_str = None
        self.connected_mac_bytes = None
        self._on_connection_status("Disconnected")

    def _on_ble_connection_status_monitored(self, status: str):
        if status == "Connected":
            if not self.is_connected:
                mac_str = self.connected_mac_str or "Device"
                self.is_connected = True
                self._on_connection_status(f"Connected: {mac_str}")
                self.signals.log.emit(f"[FOTA] BLE status monitored: Connected ({mac_str})")
        else: # "Disconnected"
            if self.is_connected:
                self.is_connected = False
                self.connected_mac_str = None
                self.connected_mac_bytes = None
                self._on_connection_status("Disconnected")
                self.signals.log.emit("[FOTA] BLE status monitored: Disconnected (Connection lost)")

    def _on_task_done(self, ok: bool, msg: str):
        self.monitor_thread.suspended = False
        self.view.btn_scan.setEnabled(not self.is_connected)
        self.view.btn_connect.setEnabled(True)
        self.view.btn_auto_fota.setEnabled(True)

    def _setup_connections(self):
        self.view.btn_browse.clicked.connect(self.on_browse)
        self.view.btn_scan.clicked.connect(self.on_scan)
        self.view.btn_connect.clicked.connect(self.on_connect)
        self.view.btn_erase_app.clicked.connect(self.on_erase_app)
        self.view.btn_verify.clicked.connect(self.on_verify)
        self.view.btn_auto_fota.clicked.connect(self.on_auto_fota)
        
        self.scan_result_signal.connect(self._on_scan_result)
        self.connection_status_signal.connect(self._on_connection_status)
        self.signals.done.connect(self._on_task_done)

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
            
        self.monitor_thread.suspended = True
        self.view.table_ble.setRowCount(0)
        self.signals.log.emit(f"[FOTA] Scanning for BLE peripherals using {port}...")
        self.view.btn_scan.setEnabled(False)
        self.view.btn_connect.setEnabled(False)
        self.view.btn_auto_fota.setEnabled(False)
        def task():
            self.fota_service.scan_nearby_devices(
                port=port,
                log_cb=self.signals.log.emit,
                result_cb=self.scan_result_signal.emit
            )
        self.main_ctrl.run_task(task)

    def on_connect(self):
        port = self.current_dongle_port
        if not port:
            self.signals.log.emit("[FOTA] ERROR: Dongle not detected.")
            return
            
        if self.is_connected:
            self.monitor_thread.suspended = True
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
                    disconnected_cb=self.connection_status_signal.emit
                )
            self.main_ctrl.run_task(task_disconnect)
            return

        row = self.view.table_ble.currentRow()
        if row < 0:
            self.signals.log.emit("[FOTA] Please select a device to connect.")
            return
        mac_str = self.view.table_ble.item(row, 1).text()
        mac_bytes = bytes.fromhex(mac_str.replace(":", ""))
        self.monitor_thread.suspended = True
        
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
                connected_cb=self.connection_status_signal.emit
            )
        self.main_ctrl.run_task(task_connect)

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
        
        self.monitor_thread.suspended = True
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
                status_cb=self.connection_status_signal.emit
            )
        self.main_ctrl.run_task(task)
