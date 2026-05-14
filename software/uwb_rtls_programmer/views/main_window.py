from version import PROGRAMMER_VERSION
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout, QWidget, 
    QTextEdit, QProgressBar, QLabel, QPushButton, QGroupBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from models.consts import APP_START, APP_END

from views.build_tab import BuildTab
from views.dfu_tab import DfuTab
from views.fota_tab import FotaTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"UWB-RTLS Programmer v{PROGRAMMER_VERSION}")
        self.resize(1000, 760)
        
        self.tabs = QTabWidget()
        self.tab_build = BuildTab()
        self.tab_dfu = DfuTab()
        self.tab_fota = FotaTab()
        
        self.tabs.addTab(self.tab_build, "Firmware Builder")
        self.tabs.addTab(self.tab_dfu, "USB Flasher")
        self.tabs.addTab(self.tab_fota, "BLE OTA Flasher")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        # -- Bottom Section: Log and Memory --
        bottom_layout = QHBoxLayout()

        # Log Terminal
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        
        log_header = QHBoxLayout()
        info = QLabel(f"App range: 0x{APP_START:08X} - 0x{APP_END:08X}")
        info.setAlignment(Qt.AlignLeft)
        self.btn_clear_log = QPushButton("Clear Log")
        self.btn_clear_log.setMaximumWidth(100)
        
        log_header.addWidget(info)
        log_header.addStretch()
        log_header.addWidget(self.btn_clear_log)
        
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(250)
        
        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log)
        
        # Memory Gauges
        mem_group = QGroupBox("Target Memory")
        mem_group.setMaximumWidth(250)
        m_layout = QVBoxLayout(mem_group)
        
        self.lbl_flash = QLabel("Flash Usage: -- / 512 KB")
        self.bar_flash = QProgressBar()
        self.bar_flash.setRange(0, 512 * 1024)
        self.bar_flash.setTextVisible(True)
        
        self.lbl_ram = QLabel("RAM Usage: -- / 128 KB")
        self.bar_ram = QProgressBar()
        self.bar_ram.setRange(0, 128 * 1024)
        self.bar_ram.setTextVisible(True)
        
        self.lbl_fota = QLabel("Header: Not Built")
        self.lbl_fota.setWordWrap(True)
        
        m_layout.addStretch()
        m_layout.addWidget(self.lbl_flash)
        m_layout.addWidget(self.bar_flash)
        m_layout.addWidget(self.lbl_ram)
        m_layout.addWidget(self.bar_ram)
        m_layout.addStretch()
        m_layout.addStretch()
        m_layout.addWidget(self.lbl_fota)
        m_layout.addStretch()
        
        bottom_layout.addWidget(log_panel, stretch=4)
        bottom_layout.addWidget(mem_group, stretch=1)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tabs, stretch=1)
        main_layout.addWidget(self.progress)
        main_layout.addLayout(bottom_layout, stretch=2)

        root = QWidget()
        root.setLayout(main_layout)
        self.setCentralWidget(root)
        
        self.btn_clear_log.clicked.connect(self.log.clear)

    def append_log(self, text: str):
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
        # Fix \n escaping issue
        cursor.insertText(text + "\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()
