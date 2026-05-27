from version import PROGRAMMER_VERSION
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout, QWidget,
    QTextEdit, QProgressBar, QLabel, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, QDateTime
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QShortcut, QKeySequence, QGuiApplication

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
        self.progress.setFixedHeight(16)

        # -- Bottom Section: Log and Memory --
        bottom_layout = QHBoxLayout()

        self.bottom_tabs = QTabWidget()

        # Log Tab
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(250)

        log_layout.addWidget(self.log)

        # Problems Tab
        problems_panel = QWidget()
        p_layout = QVBoxLayout(problems_panel)
        p_layout.setContentsMargins(0, 0, 0, 0)

        self.table_problems = QTableWidget(0, 4)
        self.table_problems.setHorizontalHeaderLabels(["Type", "Message", "Count", "Last Seen"])
        self.table_problems.verticalHeader().setVisible(False)
        self.table_problems.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_problems.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table_problems.setEditTriggers(QTableWidget.NoEditTriggers)
        p_header = self.table_problems.horizontalHeader()
        p_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        p_header.setSectionResizeMode(1, QHeaderView.Stretch)
        p_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        p_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_problems.setMinimumHeight(250)

        p_layout.addWidget(self.table_problems)

        self.bottom_tabs.addTab(log_panel, "Log")
        self.bottom_tabs.addTab(problems_panel, "Problems")

        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(6)
        self.btn_clear_log = QPushButton("Clear Log")
        self.btn_clear_log.setMaximumWidth(100)
        self.btn_clear_problems = QPushButton("Clear Problems")
        self.btn_clear_problems.setMaximumWidth(130)
        corner_layout.addWidget(self.btn_clear_log)
        corner_layout.addWidget(self.btn_clear_problems)
        self.bottom_tabs.setCornerWidget(corner, Qt.TopRightCorner)
        
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
        
        bottom_layout.addWidget(self.bottom_tabs, stretch=4)
        bottom_layout.addWidget(mem_group, stretch=1)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tabs, stretch=1)
        main_layout.addWidget(self.progress)
        main_layout.addLayout(bottom_layout, stretch=2)

        root = QWidget()
        root.setLayout(main_layout)
        self.setCentralWidget(root)
        
        self._problem_index = {}
        self.btn_clear_log.clicked.connect(self._clear_log)
        self.btn_clear_problems.clicked.connect(self.clear_problems)

        self._copy_problem_shortcut = QShortcut(QKeySequence.Copy, self.table_problems)
        self._copy_problem_shortcut.activated.connect(self.copy_selected_problems)

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

    def _clear_log(self):
        self.log.clear()

    def clear_problems(self):
        self.table_problems.setRowCount(0)
        self._problem_index = {}

    def copy_selected_problems(self):
        selection_model = self.table_problems.selectionModel()
        if selection_model is None:
            return
        rows = sorted({idx.row() for idx in selection_model.selectedRows()})
        if not rows:
            return

        lines = []
        for row in rows:
            type_item = self.table_problems.item(row, 0)
            msg_item = self.table_problems.item(row, 1)
            count_item = self.table_problems.item(row, 2)
            type_text = type_item.text() if type_item else ""
            msg_text = ""
            if msg_item is not None:
                raw_msg = msg_item.data(Qt.UserRole)
                msg_text = raw_msg if raw_msg else msg_item.text()
            count_text = count_item.text() if count_item else ""
            lines.append(f"{type_text}\t{msg_text}\t{count_text}")

        QGuiApplication.clipboard().setText("\n".join(lines))

    def add_problem(self, severity: str, message: str):
        msg = message.strip()
        if not msg:
            return

        key = (severity, msg)
        if key in self._problem_index:
            row = self._problem_index[key]
            count_item = self.table_problems.item(row, 2)
            last_item = self.table_problems.item(row, 3)
            try:
                count = int(count_item.text()) + 1
            except Exception:
                count = 1
            count_item.setText(str(count))
            last_item.setText(QDateTime.currentDateTime().toString("HH:mm:ss"))
            return

        row = self.table_problems.rowCount()
        self.table_problems.insertRow(row)

        type_item = QTableWidgetItem(severity)
        if severity.lower() == "error":
            type_item.setForeground(QColor("#ff6b6b"))
        elif severity.lower() == "warning":
            type_item.setForeground(QColor("#ffd166"))

        display_msg = self._shorten_message(msg)
        msg_item = QTableWidgetItem(display_msg)
        msg_item.setToolTip(msg)
        msg_item.setData(Qt.UserRole, msg)
        count_item = QTableWidgetItem("1")
        count_item.setTextAlignment(Qt.AlignCenter)
        last_item = QTableWidgetItem(QDateTime.currentDateTime().toString("HH:mm:ss"))

        self.table_problems.setItem(row, 0, type_item)
        self.table_problems.setItem(row, 1, msg_item)
        self.table_problems.setItem(row, 2, count_item)
        self.table_problems.setItem(row, 3, last_item)
        self._problem_index[key] = row

    @staticmethod
    def _shorten_message(msg: str, max_len: int = 140) -> str:
        clean = " ".join(msg.split())
        if len(clean) <= max_len:
            return clean
        head = clean[:70].rstrip()
        tail = clean[-50:].lstrip()
        return f"{head}...{tail}"
