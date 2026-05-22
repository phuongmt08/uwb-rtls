from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, 
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox, QTableWidget, 
    QTableWidgetItem, QHeaderView
)
from models.consts import ERASE_SECTORS

class DfuTab(QWidget):
    def __init__(self):
        super().__init__()
        main_layout = QVBoxLayout(self)

        # Device Box
        device_box = QGroupBox("USB DFU Device")
        d_layout = QGridLayout(device_box)

        self.vid_edit = QLineEdit("0483")
        self.pid_edit = QLineEdit("DF11")
        self.btn_scan = QPushButton("Scan")
        self.btn_auto_connect = QPushButton("Auto Connect")
        self.btn_connect = QPushButton("Connect")
        self.combo_devices = QComboBox()
        self.combo_devices.addItem("No scanned DFU device")
        self.lbl_device_status = QLabel("Not connected")

        d_layout.addWidget(QLabel("VID (hex):"), 0, 0)
        d_layout.addWidget(self.vid_edit, 0, 1)
        d_layout.addWidget(QLabel("PID (hex):"), 0, 2)
        d_layout.addWidget(self.pid_edit, 0, 3)
        d_layout.addWidget(self.btn_connect, 0, 4)
        d_layout.addWidget(self.btn_scan, 0, 5)
        d_layout.addWidget(self.btn_auto_connect, 0, 6)
        d_layout.addWidget(QLabel("Scanned devices:"), 1, 0, 1, 2)
        d_layout.addWidget(self.combo_devices, 1, 2, 1, 5)
        d_layout.addWidget(self.lbl_device_status, 2, 0, 1, 7)

        # Firmware Box
        file_box = QGroupBox("Firmware Image")
        f_layout = QGridLayout(file_box)

        self.combo_file = QComboBox()
        self.combo_file.setEditable(True)
        self.combo_file.setInsertPolicy(QComboBox.NoInsert)
        self.btn_browse = QPushButton("Browse")

        self.spin_transfer = QSpinBox()
        self.spin_transfer.setRange(64, 2048)
        self.spin_transfer.setSingleStep(64)
        self.spin_transfer.setValue(1024)

        f_layout.addWidget(QLabel("File:"), 0, 0)
        f_layout.addWidget(self.combo_file, 0, 1, 1, 4)
        f_layout.addWidget(self.btn_browse, 0, 5)
        f_layout.addWidget(QLabel("Transfer size:"), 1, 0)
        f_layout.addWidget(self.spin_transfer, 1, 1)
        f_layout.addWidget(QLabel("bytes"), 1, 2)

        # Operations Box
        ops_box = QGroupBox("Operations")
        o_layout = QHBoxLayout(ops_box)

        action_panel = QWidget()
        a_layout = QVBoxLayout(action_panel)
        self.btn_flash = QPushButton("Flash")
        self.btn_verify = QPushButton("Verify")
        self.btn_erase_app = QPushButton("Erase App Sectors")
        self.btn_mass_erase = QPushButton("Mass Erase")
        
        a_layout.addWidget(self.btn_flash)
        a_layout.addWidget(self.btn_verify)
        a_layout.addWidget(self.btn_erase_app)
        a_layout.addWidget(self.btn_mass_erase)

        self.table_sectors = QTableWidget(len(ERASE_SECTORS), 4)
        self.table_sectors.setHorizontalHeaderLabels(["Erase", "Sector", "Type", "Address Range"])
        self.table_sectors.verticalHeader().setVisible(False)
        self.table_sectors.setSelectionMode(QTableWidget.NoSelection)
        self.table_sectors.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_sectors.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_sectors.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

        for row, (sname, stype, start, end) in enumerate(ERASE_SECTORS):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setData(Qt.UserRole, start)
            self.table_sectors.setItem(row, 0, check_item)
            self.table_sectors.setItem(row, 1, QTableWidgetItem(sname))
            self.table_sectors.setItem(row, 2, QTableWidgetItem(stype))
            self.table_sectors.setItem(row, 3, QTableWidgetItem(f"0x{start:08X} - 0x{end:08X}"))

        o_layout.addWidget(action_panel, 1)
        o_layout.addWidget(self.table_sectors, 3)

        main_layout.addWidget(device_box)
        main_layout.addWidget(file_box)
        main_layout.addWidget(ops_box)
