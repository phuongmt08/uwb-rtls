from PySide6.QtCore import QObject
from views.fota_tab import FotaTab
from services.config_service import ConfigService
from utils.workers import WorkerSignals
import os
from PySide6.QtWidgets import QFileDialog

class FotaController(QObject):
    def __init__(self, view: FotaTab, signals: WorkerSignals, config: ConfigService, main_ctrl):
        super().__init__()
        self.view = view
        self.signals = signals
        self.config = config
        self.main_ctrl = main_ctrl
        self._setup_connections()
        self._load_preferences()

    def _setup_connections(self):
        self.view.btn_browse.clicked.connect(self.on_browse)
        self.view.btn_scan.clicked.connect(self.on_scan)
        self.view.btn_connect.clicked.connect(self.on_connect)
        
        self.view.btn_erase_app.clicked.connect(self.on_erase_app)
        
        self.view.btn_verify.clicked.connect(self.on_verify)
        self.view.btn_auto_fota.clicked.connect(self.on_auto_fota)

    def _load_preferences(self):
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
        self._push_recent_path(path)
        self.config.set_last_hex_dir(os.path.dirname(path))
        self.signals.log.emit(f"[FOTA] Selected HEX: {path}")

    def on_scan(self):
        self.signals.log.emit("[FOTA] Scanning for BLE peripherals... (Not implemented yet)")

    def on_connect(self):
        self.signals.log.emit("[FOTA] Connecting to peripheral... (Not implemented yet)")

    def on_erase_app(self):
        self.signals.log.emit("[FOTA] Erasing app sectors via OTA... (Not implemented yet)")

    def on_verify(self):
        self.signals.log.emit("[FOTA] Verifying... (Not implemented yet)")

    def on_auto_fota(self):
        self.signals.log.emit("[FOTA] Starting 1-Click Auto FOTA... (Not implemented yet)")
        self.signals.progress.emit(0)
