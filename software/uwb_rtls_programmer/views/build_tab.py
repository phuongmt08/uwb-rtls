from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QGroupBox, QCheckBox, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
)

class BuildTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)

        # Left Column - Control
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        control_group = QGroupBox("Build Configuration")
        c_layout = QVBoxLayout(control_group)

        # Optimization
        opt_layout = QHBoxLayout()
        opt_layout.addWidget(QLabel("Optimization Level:"))
        self.combo_opt = QComboBox()
        self.combo_opt.addItems(["-Og (Debug/Default)", "-O0 (None)", "-O1 (Low)", "-O2 (High)", "-O3 (Highest)", "-Os (Size)"])
        opt_layout.addWidget(self.combo_opt)
        c_layout.addLayout(opt_layout)

        self.chk_auto_increment = QCheckBox("Auto-increment Build Version")
        self.chk_auto_increment.setChecked(True)
        c_layout.addWidget(self.chk_auto_increment)
        
        self.chk_auto_flash = QCheckBox("Auto-Flash DFU after successful build")
        self.chk_auto_flash.setChecked(False)
        c_layout.addWidget(self.chk_auto_flash)

        self.btn_clean = QPushButton("Make Clean")
        self.btn_clean.setMinimumHeight(35)
        
        self.btn_build = QPushButton("Build && Archive Firmware")
        self.btn_build.setMinimumHeight(45)
        self.btn_build.setStyleSheet("font-weight: bold; font-size: 13px;")
        
        c_layout.addWidget(self.btn_clean)
        c_layout.addWidget(self.btn_build)
        c_layout.addStretch()

        left_layout.addWidget(control_group)

        # Basic Info
        info_group = QGroupBox("Version Information")
        i_layout = QVBoxLayout(info_group)
        self.lbl_version = QLabel("Current Version: N/A")
        self.lbl_git = QLabel("Git Hash: N/A")
        i_layout.addWidget(self.lbl_version)
        i_layout.addWidget(self.lbl_git)
        i_layout.addStretch()
        
        left_layout.addWidget(info_group)

        # Right Column - Archive
        archive_group = QGroupBox("Build History Archives")
        a_layout = QVBoxLayout(archive_group)

        self.table_archives = QTableWidget(0, 3)
        self.table_archives.setHorizontalHeaderLabels(["Name", "Date Modified", "Size"])
        self.table_archives.verticalHeader().setVisible(False)
        self.table_archives.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_archives.setSelectionMode(QTableWidget.SingleSelection)
        self.table_archives.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_archives.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_archives.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_archives.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        
        a_layout.addWidget(self.table_archives)
        
        btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh List")
        self.btn_refresh.setMinimumHeight(35)
        
        self.btn_remove = QPushButton("Delete")
        self.btn_remove.setMinimumHeight(35)
        
        self.btn_remove_all = QPushButton("Delete All")
        self.btn_remove_all.setMinimumHeight(35)
        
        self.btn_set_active = QPushButton("Set as Active Firmware")
        self.btn_set_active.setMinimumHeight(35)
        self.btn_set_active.setStyleSheet("font-weight: bold;")
        
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_remove)
        btn_layout.addWidget(self.btn_remove_all)
        btn_layout.addWidget(self.btn_set_active)
        
        a_layout.addLayout(btn_layout)

        layout.addWidget(left_panel, 1)
        layout.addWidget(archive_group, 2)
