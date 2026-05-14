import re
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox
from views.main_window import MainWindow
from controllers.build_controller import BuildController
from controllers.dfu_controller import DfuController
from controllers.fota_controller import FotaController
from services.config_service import ConfigService
from utils.workers import WorkerSignals, run_task

class MainController(QObject):
    def __init__(self, view: MainWindow):
        super().__init__()
        self.view = view
        self.signals = WorkerSignals()
        self.config = ConfigService()
        
        self.signals.log.connect(self.on_log)
        self.signals.progress.connect(self.view.progress.setValue)
        self.signals.done.connect(self.on_task_done)
        self.signals.connected.connect(self.view.tab_dfu.lbl_device_status.setText)
        
        self.build_ctrl = BuildController(self.view.tab_build, self.signals, self)
        self.dfu_ctrl = DfuController(self.view.tab_dfu, self.signals, self.config, self)
        self.fota_ctrl = FotaController(self.view.tab_fota, self.signals, self.config, self)


    def on_log(self, text: str):
        self.view.append_log(text)
        
        # Regex for size output
        # text    data     bss     dec     hex filename
        # 99824     764   19496  120084   1d514 build/uwb-rtls.elf
        if not hasattr(self, 'current_build_meta'):
            self.current_build_meta = {}
            
        m_region = re.search(r'^\s*(FLASH|RAM):\s+(\d+)\s+B\s+(\d+)\s+KB', text)
        if m_region:
            reg = m_region.group(1)
            used_b = int(m_region.group(2))
            total_b = int(m_region.group(3)) * 1024
            if reg == "FLASH":
                self.current_build_meta['flash_used'] = used_b
                self.current_build_meta['flash_total'] = total_b
                self.view.bar_flash.setRange(0, total_b)
                self.view.bar_flash.setValue(used_b)
                self.view.lbl_flash.setText(f"Flash Usage: {used_b//1024} / {total_b//1024} KB")
            elif reg == "RAM":
                self.current_build_meta['ram_used'] = used_b
                self.current_build_meta['ram_total'] = total_b
                self.view.bar_ram.setRange(0, total_b)
                self.view.bar_ram.setValue(used_b)
                self.view.lbl_ram.setText(f"RAM Usage: {used_b//1024} / {total_b//1024} KB")
                
        m_fota = re.search(r'\[patch_fota_header\] len=(\d+) crc=(0x[0-9a-fA-F]+)', text)
        if m_fota:
            l = int(m_fota.group(1))
            c = m_fota.group(2)
            self.current_build_meta['fota_len'] = l
            self.current_build_meta['fota_crc'] = c
            self.view.lbl_fota.setText(f"Header Patched:\nLen: {l}\nCRC: {c}")

    def run_task(self, fn):
        self._set_busy(True)
        self.view.progress.setValue(0)
        run_task(self.signals, fn)

    def on_task_done(self, ok: bool, msg: str):
        self._set_busy(False)
        if not ok:
            self.view.append_log(f"ERROR: {msg}")
            QMessageBox.critical(self.view, "Operation failed", msg)

    def _set_busy(self, busy: bool):
        self.view.tab_build.btn_build.setEnabled(not busy)
        self.view.tab_dfu.btn_scan.setEnabled(not busy)
        self.view.tab_dfu.btn_auto_connect.setEnabled(not busy)
        self.view.tab_dfu.btn_connect.setEnabled(not busy)
        self.view.tab_dfu.btn_browse.setEnabled(not busy)
        self.view.tab_dfu.btn_erase_app.setEnabled(not busy)
        self.view.tab_dfu.table_sectors.setEnabled(not busy)
        self.view.tab_dfu.btn_mass_erase.setEnabled(not busy)
        self.view.tab_dfu.btn_flash.setEnabled(not busy)
        self.view.tab_dfu.btn_verify.setEnabled(not busy)

    def shutdown(self):
        self.dfu_ctrl.shutdown()
