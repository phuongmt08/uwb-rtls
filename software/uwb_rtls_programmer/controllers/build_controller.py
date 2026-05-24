import json
from PySide6.QtCore import QSettings, QObject, QTimer, Qt, QMetaObject, Slot, Q_ARG, Signal
from PySide6.QtWidgets import QTableWidgetItem

from views.build_tab import BuildTab
from services.build_service import BuildService
from utils.workers import WorkerSignals, run_task
import os
import re
import datetime

class BuildController(QObject):
    history_ready = Signal(str, str, list)

    def __init__(self, view: BuildTab, signals: WorkerSignals, main_ctrl):

        super().__init__()
        self.view = view
        self.signals = signals
        self.main_ctrl = main_ctrl
        
        # Connect internal signals
        self.history_ready.connect(self.update_history_ui)
        
        self.view.btn_build.clicked.connect(self.on_build)
        self.view.btn_clean.clicked.connect(self.on_clean)
        self.view.btn_refresh.clicked.connect(self.refresh_history)
        self.view.btn_set_active.clicked.connect(self.set_active_firmware)
        self.view.table_archives.itemSelectionChanged.connect(self.on_history_select)
        self.view.btn_remove.clicked.connect(self.remove_archive)
        self.view.btn_remove_all.clicked.connect(self.remove_all_archives)

        self.settings = QSettings("UWB", "Programmer")
        # Load previous config
        self.view.chk_auto_increment.setChecked(self.settings.value("auto_inc", True, type=bool))
        self.view.chk_auto_flash.setChecked(self.settings.value("auto_flash", False, type=bool))
        opt_idx = self.settings.value("opt_level", 0, type=int)
        if 0 <= opt_idx < self.view.combo_opt.count():
            self.view.combo_opt.setCurrentIndex(opt_idx)
            
        # Hook up signals AFTER initialization
        self.view.combo_opt.currentIndexChanged.connect(self.on_opt_changed)
        self.view.chk_auto_increment.stateChanged.connect(lambda: self.settings.setValue("auto_inc", self.view.chk_auto_increment.isChecked()))
        self.view.chk_auto_flash.stateChanged.connect(lambda: self.settings.setValue("auto_flash", self.view.chk_auto_flash.isChecked()))

        QTimer.singleShot(100, self.refresh_history)

    def refresh_history(self):
        # Scan in background to keep UI responsive
        def scan_job():
            try:
                uwb_dir = BuildService.get_uwb_project_dir()
                version_dir = os.path.join(uwb_dir, "build_version")
                
                # Emit log safely
                self.signals.log.emit(f"[INFO] Scanning archive folder: {version_dir}")
                
                git_hash = BuildService.get_current_git_hash()
                
                # Parse version.h
                version_file = os.path.join(uwb_dir, "app", "version.h")
                version_build_file = os.path.join(uwb_dir, "app", "version_build.h")
                fw_ver = "Sync Error"
                try:
                    if os.path.exists(version_file):
                        with open(version_file, 'r') as f:
                            c = f.read()
                        m1 = re.search(r'#define\s+FW_VERSION_MAJOR\s+(\d+)', c).group(1)
                        m2 = re.search(r'#define\s+FW_VERSION_MINOR\s+(\d+)', c).group(1)
                        m3 = re.search(r'#define\s+FW_VERSION_PATCH\s+(\d+)', c).group(1)
                        build = "0"
                        if os.path.exists(version_build_file):
                            with open(version_build_file, 'r') as f:
                                c2 = f.read()
                            bm = re.search(r'#define\s+FW_VERSION_BUILD\s+(\d+)', c2)
                            if bm: build = bm.group(1)
                        fw_ver = f"{m1}.{m2}.{m3}.{build}"
                except:
                    pass

                archives_data = []
                if os.path.exists(version_dir):
                    for f in os.listdir(version_dir):
                        if f.endswith(".hex"):
                            full_path = os.path.join(version_dir, f)
                            try:
                                size = os.path.getsize(full_path) / 1024
                                mtime = os.path.getmtime(full_path)
                                archives_data.append((mtime, f, size, full_path))
                            except: continue
                    archives_data.sort(reverse=True, key=lambda x: x[0])

                # Emit signal safely (PySide6 converts list automatically)
                self.history_ready.emit(git_hash, fw_ver, archives_data)

            except Exception as e:
                self.signals.log.emit(f"[ERROR] History scan failed: {e}")

        self.main_ctrl.run_task(scan_job)

    @Slot(str, str, list)
    def update_history_ui(self, git_hash, fw_ver, archives_data):
        self.view.lbl_git.setText(f"Git Hash: {git_hash}")
        self.view.lbl_version.setText(f"Current Version: {fw_ver}")
        self.view.table_archives.setRowCount(0)
        self.view.table_archives.setRowCount(len(archives_data))
        
        for row, (mtime, name, size, full_path) in enumerate(archives_data):
            dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            i1 = QTableWidgetItem(name)
            i1.setData(Qt.UserRole, full_path)
            i2 = QTableWidgetItem(dt)
            i3 = QTableWidgetItem(f"{size:.1f} KB")
            
            self.view.table_archives.setItem(row, 0, i1)
            self.view.table_archives.setItem(row, 1, i2)
            self.view.table_archives.setItem(row, 2, i3)

        # Auto select the newly built firmware if the flag is set
        if getattr(self, 'select_new_build_after_refresh', False) and len(archives_data) > 0:
            self.select_new_build_after_refresh = False
            self.view.table_archives.selectRow(0)
            self.set_active_firmware()






    def on_history_select(self):
        row = self.view.table_archives.currentRow()
        if row < 0: return
        full_path = self.view.table_archives.item(row, 0).data(Qt.UserRole)
        hex_name = os.path.basename(full_path)
        
        # Try Parse from uwb_rtls_application_<version>_<time>_<gitsha>.hex
        parts = hex_name.split('_')
        # Format: uwb_rtls_application_1.1.0.45_021721_334fc76.hex
        # index: 0   1    2           3          4      5
        if len(parts) >= 6 and "application" in hex_name:
            git_hash = parts[5].split('.')[0]
            ver_str = parts[3]
        elif len(parts) >= 5: # Fallback for old style
            git_hash = parts[4].split('.')[0]
            ver_str = parts[2]
        else:
            git_hash = "Unknown"
            ver_str = "Unknown"
            
        self.view.lbl_git.setText(f"Git Hash: {git_hash}")
        self.view.lbl_version.setText(f"Archive Version: {ver_str}")
        
        meta_file = os.path.join(os.path.dirname(full_path), ".metadata", hex_name[:-4] + "_metadata.json")
        if os.path.exists(meta_file):

            try:
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                    


                if 'flash_used' in meta:
                    self.main_ctrl.view.bar_flash.setRange(0, meta['flash_total'])
                    self.main_ctrl.view.bar_flash.setValue(meta['flash_used'])
                    self.main_ctrl.view.lbl_flash.setText(f"Flash Usage: {meta['flash_used']//1024} / {meta['flash_total']//1024} KB")
                else:
                    self.main_ctrl.view.bar_flash.setValue(0)
                    self.main_ctrl.view.lbl_flash.setText(f"Flash Usage: N/A")
                    
                if 'ram_used' in meta:
                    self.main_ctrl.view.bar_ram.setRange(0, meta['ram_total'])
                    self.main_ctrl.view.bar_ram.setValue(meta['ram_used'])
                    self.main_ctrl.view.lbl_ram.setText(f"RAM Usage: {meta['ram_used']//1024} / {meta['ram_total']//1024} KB")
                else:
                    self.main_ctrl.view.bar_ram.setValue(0)
                    self.main_ctrl.view.lbl_ram.setText(f"RAM Usage: N/A")
                    
                if 'fota_len' in meta:
                    self.main_ctrl.view.lbl_fota.setText(f"Header Patched:\nLen: {meta['fota_len']}\nCRC: {meta['fota_crc']}")
                else:
                    self.main_ctrl.view.lbl_fota.setText("Header: N/A")
                    
            except Exception as e:
                pass
        else:
            # Clear UI if no meta
            self.main_ctrl.view.bar_flash.setValue(0)
            self.main_ctrl.view.lbl_flash.setText(f"Flash Usage: N/A")
            self.main_ctrl.view.bar_ram.setValue(0)
            self.main_ctrl.view.lbl_ram.setText(f"RAM Usage: N/A")
            self.main_ctrl.view.lbl_fota.setText(f"(No metadata recorded for old builds)")



    def remove_archive(self):
        row = self.view.table_archives.currentRow()
        if row < 0: return
        full_path = self.view.table_archives.item(row, 0).data(Qt.UserRole)
        hex_name = os.path.basename(full_path)
        # Delete hex, map, metadata
        base = full_path[:-4]
        for p in [base + ".hex", base + ".map", os.path.join(os.path.dirname(full_path), ".metadata", hex_name[:-4] + "_metadata.json")]:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass
        self.refresh_history()

    def remove_all_archives(self):
        uwb_dir = BuildService.get_uwb_project_dir()
        version_dir = os.path.join(uwb_dir, "build_version")
        if os.path.exists(version_dir):
            import shutil
            for f in os.listdir(version_dir):
                p = os.path.join(version_dir, f)
                try:
                    if os.path.isdir(p): shutil.rmtree(p)
                    else: os.remove(p)
                except: pass
        self.refresh_history()

    def on_opt_changed(self, index):
        self.settings.setValue("opt_level", index)
        self.view.btn_build.setEnabled(False)
        self.view.btn_build.setText("Must Clean First")
        self.signals.log.emit("[WARNING] Toolchain optimization changed. Please Make Clean.")

    def set_active_firmware(self):
        row = self.view.table_archives.currentRow()
        if row < 0: return
        full_path = self.view.table_archives.item(row, 0).data(Qt.UserRole)
        # Push to DFU and FOTA
        self.main_ctrl.dfu_ctrl.view.combo_file.setCurrentText(full_path)
        self.main_ctrl.fota_ctrl.view.combo_file.setCurrentText(full_path)
        self.signals.log.emit(f"[ARCHIVE] Selected firmware pushed to DFU/FOTA tabs:\\n{full_path}")

    def on_build(self):
        auto_inc = self.view.chk_auto_increment.isChecked()
        auto_flash = self.view.chk_auto_flash.isChecked()
        opt_flag = self.view.combo_opt.currentText().split(" ")[0]
        
        def job():
            self.signals.log.emit("=" * 60)
            self.signals.log.emit("[BUILD] Starting update version routine...")
            import time
            success, version_str = BuildService.update_version(auto_inc, self.signals.log.emit)
            if not hasattr(self.main_ctrl, 'current_build_meta'): self.main_ctrl.current_build_meta = {}
            
            self.main_ctrl.current_build_meta['version'] = version_str
            self.main_ctrl.current_build_meta['build_time'] = time.strftime("%Y-%m-%d %H:%M:%S")

            self.signals.log.emit("=" * 60)
            self.signals.log.emit("Building firmware incrementally...")
            try:
                BuildService.run_make_target("all", opt_flag, self.signals.log.emit)
                versioned_hex = BuildService.archive_versioned_build(self.signals.log.emit, version_str)
                if hasattr(self.main_ctrl, 'current_build_meta'):
                    hex_name = os.path.basename(versioned_hex)
                    meta_path = os.path.join(os.path.dirname(versioned_hex), ".metadata", hex_name[:-4] + "_metadata.json")
                    with open(meta_path, "w") as f:
                        json.dump(self.main_ctrl.current_build_meta, f)

                self.select_new_build_after_refresh = True
                
                # Update UI in main thread
                QMetaObject.invokeMethod(self, "refresh_history", Qt.QueuedConnection)
                self.signals.progress.emit(100)
                self.signals.log.emit("Build done")
                
                if auto_flash:
                    self.signals.log.emit("Auto-Flash is explicitly enabled. Preparing DFU...")
                    QMetaObject.invokeMethod(self, "invoke_trigger_auto_flash", Qt.QueuedConnection)
                    
            except Exception as e:
                self.signals.log.emit(f"ERROR: Build failed - {e}")
            
        self.main_ctrl.run_task(job)

    from PySide6.QtCore import Slot
    @Slot()
    def invoke_trigger_auto_flash(self):
        QTimer.singleShot(500, self.trigger_auto_flash)

    def trigger_auto_flash(self):
        # Trigger DFU Auto-Flash by pulling the top archive file and flashing
        if self.view.table_archives.rowCount() > 0:
            latest_path = self.view.table_archives.item(0, 0).data(Qt.UserRole)
            self.main_ctrl.dfu_ctrl.view.combo_file.setCurrentText(latest_path)
            self.main_ctrl.view.tabs.setCurrentIndex(1) # switch to DFU tab
            
            # Start flashing
            self.main_ctrl.dfu_ctrl.on_flash()

    def on_clean(self):
        def job():
            self.signals.log.emit("=" * 60)
            self.signals.log.emit("[BUILD] Running make clean...")
            opt_flag = self.view.combo_opt.currentText().split(" ")[0]
            try:
                BuildService.run_make_target("clean", opt_flag, self.signals.log.emit)
                QMetaObject.invokeMethod(self, "invoke_reset_build_btn", Qt.QueuedConnection)
                self.signals.progress.emit(100)
                self.signals.log.emit("Clean done.")
            except Exception as e:
                self.signals.log.emit(f"ERROR: Clean failed - {e}")
        self.main_ctrl.run_task(job)

    from PySide6.QtCore import Slot
    @Slot()
    def invoke_reset_build_btn(self):
        self.view.btn_build.setEnabled(True)
        self.view.btn_build.setText("Build && Archive Firmware")
