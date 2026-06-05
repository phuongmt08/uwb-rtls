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

        self._error_re = re.compile(
            r"(^\s*ERROR:|^\s*\[ERROR\]|\berror\b|\bfail(?:ed|ure)?\b|\bfatal\b|\[FAIL\])",
            re.IGNORECASE,
        )
        self._warn_re = re.compile(
            r"(^\s*WARNING:|^\s*\[WARN\]|^\s*\[WARNING\]|\bwarning\b|\bwarn\b|\[WARN\])",
            re.IGNORECASE,
        )
        
        self.signals.log.connect(self.on_log)
        self.signals.progress.connect(self.view.progress.setValue)
        self.signals.done.connect(self.on_task_done)
        self.signals.connected.connect(self.view.tab_dfu.lbl_device_status.setText)
        
        self.build_ctrl = BuildController(self.view.tab_build, self.signals, self)
        self.dfu_ctrl = DfuController(self.view.tab_dfu, self.signals, self.config, self)
        self.fota_ctrl = FotaController(self.view.tab_fota, self.signals, self.config, self)


    def on_log(self, text: str):
        self.view.append_log(text)
        problem = self._extract_problem(text)
        if problem:
            severity, message = problem
            self.view.add_problem(severity, message)
        
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

    def _extract_problem(self, text: str):
        if not text:
            return None

        line = self._first_log_line(text)
        if self._error_re.search(line):
            severity = "Error"
        elif self._warn_re.search(line):
            severity = "Warning"
        else:
            return None

        message = self._strip_problem_prefix(line)
        return severity, message

    @staticmethod
    def _first_log_line(text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line
        return text.strip()

    @staticmethod
    def _strip_problem_prefix(text: str) -> str:
        msg = text.strip()
        prefixes = [
            "ERROR:", "WARNING:", "WARN:",
            "[ERROR]", "[WARN]", "[WARNING]", "[FAIL]",
        ]

        upper = msg.upper()
        for p in prefixes:
            if upper.startswith(p):
                msg = msg[len(p):].strip()
                break

        # Remove leading module tags like [FOTA], [DFU], [BUILD]
        while True:
            new_msg = re.sub(r"^\[[A-Z0-9 _-]+\]\s*", "", msg)
            if new_msg == msg:
                break
            msg = new_msg.strip()

        msg = re.sub(r"\s+", " ", msg).strip()
        return msg

    def run_task(self, task_func):
        self._set_busy(True)
        self.view.progress.setValue(0)
        # Assuming run_task from utils.workers executes task_func and handles errors.
        # I'll just restore the original line for now so it doesn't break the UI thread.
        from utils.workers import run_task
        run_task(self.signals, task_func)

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
        self.fota_ctrl.shutdown()
