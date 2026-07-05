from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, 
    QLabel, QPushButton, QComboBox, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt
from models.consts import ERASE_SECTORS

class FotaTab(QWidget):
    def __init__(self):
        super().__init__()
        main_layout = QHBoxLayout(self)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0,0,0,0)


        # 1. Device Box
        device_box = QGroupBox("Central Dongle & BLE Scanner")
        d_layout = QVBoxLayout(device_box)
        
        dongle_layout = QHBoxLayout()
        dongle_layout.addWidget(QLabel("Dongle Status:"))
        self.lbl_dongle_status = QLabel("Searching for Central Dongle...")
        self.lbl_dongle_status.setStyleSheet("color: #F59E0B; font-weight: bold;")
        dongle_layout.addWidget(self.lbl_dongle_status)
        dongle_layout.addStretch()

        ble_state_layout = QHBoxLayout()
        ble_state_layout.addWidget(QLabel("BLE State:"))
        self.lbl_ble_state = QLabel("UNKNOWN")
        self.lbl_ble_state.setStyleSheet("font-weight: bold;")
        ble_state_layout.addWidget(self.lbl_ble_state)
        ble_state_layout.addStretch()

        btn_layout = QHBoxLayout()
        self.btn_scan = QPushButton("Scan Nearby Devices")
        self.btn_connect = QPushButton("Connect Selected")
        self.lbl_device_status = QLabel("Not connected")
        
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addWidget(self.btn_connect)
        btn_layout.addWidget(self.lbl_device_status)
        btn_layout.addStretch()

        d_layout.addLayout(dongle_layout)
        d_layout.addLayout(ble_state_layout)

        self.table_ble = QTableWidget(0, 4)
        self.table_ble.setHorizontalHeaderLabels(["Device Name", "MAC Address / ID", "Serial Number", "RSSI"])
        self.table_ble.verticalHeader().setVisible(False)
        self.table_ble.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_ble.setSelectionMode(QTableWidget.SingleSelection)
        self.table_ble.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_ble.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_ble.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_ble.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_ble.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        
        d_layout.addLayout(btn_layout)
        d_layout.addWidget(self.table_ble)
        
        left_layout.addWidget(device_box, stretch=3)

        # 2. Firmware Box
        file_box = QGroupBox("Firmware Image")
        f_layout = QGridLayout(file_box)
        
        self.combo_file = QComboBox()
        self.combo_file.setEditable(True)
        self.combo_file.setInsertPolicy(QComboBox.NoInsert)
        self.btn_browse = QPushButton("Browse")
        
        self.spin_chunk = QSpinBox()
        self.spin_chunk.setRange(4, 200)
        self.spin_chunk.setSingleStep(4)
        self.spin_chunk.setValue(200)
        
        f_layout.addWidget(QLabel("File:"), 0, 0)
        f_layout.addWidget(self.combo_file, 0, 1, 1, 4)
        f_layout.addWidget(self.btn_browse, 0, 5)
        f_layout.addWidget(QLabel("Chunk size:"), 1, 0)
        f_layout.addWidget(self.spin_chunk, 1, 1)
        f_layout.addWidget(QLabel("bytes (Max 200)"), 1, 2)
        f_layout.setColumnStretch(1, 1)
        
        left_layout.addWidget(file_box, stretch=1)
        main_layout.addWidget(left_panel, stretch=6)

        # 3. Operations Box
        ops_box = QGroupBox("Operations")
        o_layout = QHBoxLayout(ops_box)
        
        action_panel = QWidget()
        action_panel_layout = QVBoxLayout(action_panel)
        action_panel_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_auto_fota = QPushButton("Auto OTA Flash")
        self.btn_verify = QPushButton("Verify")
        self.btn_erase_app = QPushButton("Erase App Sectors")
        
        self.btn_auto_fota.setMinimumHeight(45)

        self.btn_verify.setMinimumHeight(35)
        self.btn_erase_app.setMinimumHeight(35)
        
        action_panel_layout.addWidget(self.btn_auto_fota)
        action_panel_layout.addWidget(self.btn_verify)
        action_panel_layout.addWidget(self.btn_erase_app)
        action_panel_layout.addStretch()
        
        self.table_sectors = QTableWidget(len(ERASE_SECTORS), 4)
        self.table_sectors.setHorizontalHeaderLabels(["Erase", "Sector", "Type", "Address Range"])
        self.table_sectors.verticalHeader().setVisible(False)
        self.table_sectors.setSelectionMode(QTableWidget.NoSelection)
        self.table_sectors.setEditTriggers(QTableWidget.NoEditTriggers)
        
        # Smart resizing to keep it compact
        h = self.table_sectors.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        
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

        main_layout.addWidget(ops_box, stretch=4)


