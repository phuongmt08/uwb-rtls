"""
==============================================================================
  UWB RTLS Studio — Config Tab View
==============================================================================
  File        : config_tab.py
  Description : View for config configurations (Tab 3), loaded from config_tab.ui.
                Handles form submissions and binds input fields to ConfigViewModel.

  MVVM Role   : VIEW — Pure presentation.

  Thread Model:
    - Main GUI Thread: Renders widgets and listens to user input events strictly
      on this thread.
==============================================================================
"""
import os
import math
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QScrollArea, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QCheckBox,
    QStackedWidget, QTabWidget, QAbstractItemView
)
from PyQt6.QtCore import Qt
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'config_tab.ui')

from utils.helpers import format_coord
from utils.constants import DEVICE_TYPE_LABELS_SHORT
from views.tabs.anchor_visual_widget import AnchorVisualWidget

# --- Factory OTP constants (mirrors provision_otp.py) ---
OTP_CONFIRM_MAGIC    = 0x4F545057  # "OTPW"
OTP_TYPE_DEVICE_INFO   = 0x01
OTP_TYPE_ANTENNA_DELAY = 0x02
# Mirrors protocol.proto device_type_t enum values
DEVICE_TYPE_TAG        = 1
DEVICE_TYPE_ANCHOR     = 2
DEVICE_TYPE_GATEWAY    = 3
DEVICE_TYPE_DEBUG_TOOL = 4





class ConfigTab(QWidget):
    def __init__(self, parent=None, is_developer=False):
        super().__init__(parent)
        self._is_developer = is_developer
        self._vm = None
        # Initialize state before UI wiring can trigger callbacks.
        self._current_role = 1  # Default: Tag
        self._current_device_id = 0
        self._last_anchor_layout = []
        self._scan_devices = []

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_dev_widgets()
        if self._has_widget("tx_power_spin"):
            if hasattr(self.tx_power_spin, "setDecimals"):
                self.tx_power_spin.setDecimals(0)
            self.tx_power_spin.setRange(0, 0x7FFFFFFF)
            self.tx_power_spin.setValue(max(0, self.tx_power_spin.value()))
        self._setup_factory_otp_ui()
        self._setup_view_toggle()
        self._setup_target_selector()
        self._merge_ranging_into_uwb_config()

        # Build container for Col 2 if not loaded from UI
        if not hasattr(self, "col2_container") or self.col2_container is None:
            self.col2_container = QWidget()
            self.col2_layout = QVBoxLayout(self.col2_container)
            self.col2_layout.setContentsMargins(0, 0, 0, 0)
            self.col2_layout.setSpacing(16)
            if hasattr(self, "uwb_config_group") and self.uwb_config_group is not None:
                self.col2_layout.addWidget(self.uwb_config_group)
            if hasattr(self, "dev_type_group") and self.dev_type_group is not None:
                self.col2_layout.addWidget(self.dev_type_group, 0, Qt.AlignmentFlag.AlignTop)

        # Setup Host Transport Group Box if not present in UI
        if not hasattr(self, "host_group") or self.host_group is None:
            self._setup_host_transport_group()
        else:
            if hasattr(self, "combo_usb_port"):
                self._populate_serial_ports(self.combo_usb_port)
            if hasattr(self, "combo_uart_port"):
                self._populate_serial_ports(self.combo_uart_port)
            if hasattr(self, "combo_host_transport"):
                self.combo_host_transport.currentIndexChanged.connect(self._on_host_transport_changed)
            if hasattr(self, "combo_usb_port"):
                self.combo_usb_port.currentIndexChanged.connect(self._on_usb_port_changed)
            if hasattr(self, "combo_uart_port"):
                self.combo_uart_port.currentIndexChanged.connect(self._on_uart_port_changed)
            if hasattr(self, "combo_host_transport"):
                self._on_host_transport_changed(self.combo_host_transport.currentIndex())
            if hasattr(self, "combo_usb_port"):
                self._on_usb_port_changed(self.combo_usb_port.currentIndex())

        # Setup BLE Configuration Group Box if not present in UI
        if not hasattr(self, "ble_group") or self.ble_group is None:
            self.ble_group = QGroupBox("📶 BLE Configuration")
            self.ble_grid = QGridLayout(self.ble_group)
            self.ble_grid.setSpacing(10)

            self.chk_enable_ble = QCheckBox("Enable BLE Advertising")
            self.chk_enable_ble.setChecked(True)
            self.ble_grid.addWidget(self.chk_enable_ble, 0, 0, 1, 2)

            self.lbl_ble_name = QLabel("🏷️ Device Name:")
            self.txt_ble_name = QLineEdit("Mock Device")
            self.ble_grid.addWidget(self.lbl_ble_name, 1, 0)
            self.ble_grid.addWidget(self.txt_ble_name, 1, 1)

            self.lbl_ble_min_int = QLabel("⏱️ Min Interval (ms):")
            self.spin_ble_min_int = QSpinBox()
            self.spin_ble_min_int.setRange(20, 5000)
            self.spin_ble_min_int.setValue(20)
            self.ble_grid.addWidget(self.lbl_ble_min_int, 2, 0)
            self.ble_grid.addWidget(self.spin_ble_min_int, 2, 1)

            self.lbl_ble_max_int = QLabel("⏱️ Max Interval (ms):")
            self.spin_ble_max_int = QSpinBox()
            self.spin_ble_max_int.setRange(20, 5000)
            self.spin_ble_max_int.setValue(40)
            self.ble_grid.addWidget(self.lbl_ble_max_int, 3, 0)
            self.ble_grid.addWidget(self.spin_ble_max_int, 3, 1)

            self.lbl_ble_latency = QLabel("⏱️ Slave Latency:")
            self.spin_ble_latency = QSpinBox()
            self.spin_ble_latency.setRange(0, 100)
            self.spin_ble_latency.setValue(0)
            self.ble_grid.addWidget(self.lbl_ble_latency, 4, 0)
            self.ble_grid.addWidget(self.spin_ble_latency, 4, 1)

            self.lbl_ble_timeout = QLabel("⏱️ Sup. Timeout (ms):")
            self.spin_ble_timeout = QSpinBox()
            self.spin_ble_timeout.setRange(100, 30000)
            self.spin_ble_timeout.setValue(3000)
            self.ble_grid.addWidget(self.lbl_ble_timeout, 5, 0)
            self.ble_grid.addWidget(self.spin_ble_timeout, 5, 1)

            self.spin_ble_min_int.valueChanged.connect(self._on_ble_min_interval_changed)

            self.btn_set_ble = QPushButton("Set BLE Config")
            self.btn_set_ble.setStyleSheet(
                "QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE; border-radius: 6px; font-weight: bold; padding: 5px; }"
                "QPushButton:hover { background: #22D3EE; color: #0F172A; }"
            )
            self.ble_grid.addWidget(self.btn_set_ble, 6, 0, 1, 2)
        else:
            if hasattr(self, "spin_ble_min_int"):
                self.spin_ble_min_int.valueChanged.connect(self._on_ble_min_interval_changed)

        if hasattr(self, "page_table_layout") and hasattr(self, "anchor_btns") and self.page_table_layout is not None:
            self.page_table_layout.removeItem(self.anchor_btns)
            if hasattr(self, "anchor_layout"):
                self.anchor_layout.addLayout(self.anchor_btns)
            if hasattr(self, "anchor_table") and self.anchor_table is not None:
                self.anchor_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # Apply initial mode layout
        self.set_developer_mode(self._is_developer)
        self._reset_display_fields()

    def _setup_host_transport_group(self):
        self.host_group = QGroupBox("Host Transport Interface")
        self.host_layout = QVBoxLayout(self.host_group)
        self.host_layout.setSpacing(8)

        self.lbl_host_transport = QLabel("Select Interface:")
        self.combo_host_transport = QComboBox()
        self.combo_host_transport.addItems(["USB", "UART"])

        self.host_detail_stack = QStackedWidget()

        self.usb_detail_widget = QWidget()
        self.usb_detail_layout = QGridLayout(self.usb_detail_widget)
        self.usb_detail_layout.setContentsMargins(0, 6, 0, 0)
        self.usb_detail_layout.setHorizontalSpacing(8)
        self.usb_detail_layout.setVerticalSpacing(8)
        self.lbl_usb_device = QLabel("Device Name:")
        self.txt_usb_device = QLineEdit("STM32 Virtual COM Port")
        self.txt_usb_device.setReadOnly(True)
        self.lbl_usb_port = QLabel("COM Port:")
        self.combo_usb_port = QComboBox()
        self._populate_serial_ports(self.combo_usb_port)
        self.lbl_usb_baud = QLabel("Baud Rate:")
        self.combo_usb_baud = QComboBox()
        self.combo_usb_baud.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self.combo_usb_baud.setCurrentText("115200")
        self.usb_detail_layout.addWidget(self.lbl_usb_device, 0, 0)
        self.usb_detail_layout.addWidget(self.txt_usb_device, 0, 1)
        self.usb_detail_layout.addWidget(self.lbl_usb_port, 1, 0)
        self.usb_detail_layout.addWidget(self.combo_usb_port, 1, 1)
        self.usb_detail_layout.addWidget(self.lbl_usb_baud, 2, 0)
        self.usb_detail_layout.addWidget(self.combo_usb_baud, 2, 1)

        self.uart_detail_widget = QWidget()
        self.uart_detail_layout = QGridLayout(self.uart_detail_widget)
        self.uart_detail_layout.setContentsMargins(0, 6, 0, 0)
        self.uart_detail_layout.setHorizontalSpacing(8)
        self.uart_detail_layout.setVerticalSpacing(8)
        self.lbl_uart_port = QLabel("COM Port:")
        self.combo_uart_port = QComboBox()
        self._populate_serial_ports(self.combo_uart_port)
        self.lbl_uart_baud = QLabel("Baud Rate:")
        self.combo_uart_baud = QComboBox()
        self.combo_uart_baud.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self.combo_uart_baud.setCurrentText("115200")
        self.lbl_uart_databits = QLabel("Data Bits:")
        self.combo_uart_databits = QComboBox()
        self.combo_uart_databits.addItems(["8", "7", "6", "5"])
        self.lbl_uart_parity = QLabel("Parity:")
        self.combo_uart_parity = QComboBox()
        self.combo_uart_parity.addItems(["None", "Even", "Odd", "Mark", "Space"])
        self.lbl_uart_stopbits = QLabel("Stop Bits:")
        self.combo_uart_stopbits = QComboBox()
        self.combo_uart_stopbits.addItems(["1", "1.5", "2"])
        self.lbl_uart_flow = QLabel("Flow Control:")
        self.combo_uart_flow = QComboBox()
        self.combo_uart_flow.addItems(["None", "RTS/CTS", "XON/XOFF"])
        self.uart_detail_layout.addWidget(self.lbl_uart_port, 0, 0)
        self.uart_detail_layout.addWidget(self.combo_uart_port, 0, 1)
        self.uart_detail_layout.addWidget(self.lbl_uart_baud, 1, 0)
        self.uart_detail_layout.addWidget(self.combo_uart_baud, 1, 1)
        self.uart_detail_layout.addWidget(self.lbl_uart_databits, 2, 0)
        self.uart_detail_layout.addWidget(self.combo_uart_databits, 2, 1)
        self.uart_detail_layout.addWidget(self.lbl_uart_parity, 3, 0)
        self.uart_detail_layout.addWidget(self.combo_uart_parity, 3, 1)
        self.uart_detail_layout.addWidget(self.lbl_uart_stopbits, 4, 0)
        self.uart_detail_layout.addWidget(self.combo_uart_stopbits, 4, 1)
        self.uart_detail_layout.addWidget(self.lbl_uart_flow, 5, 0)
        self.uart_detail_layout.addWidget(self.combo_uart_flow, 5, 1)

        self.host_detail_stack.addWidget(self.usb_detail_widget)
        self.host_detail_stack.addWidget(self.uart_detail_widget)

        self.btn_apply_host_transport = QPushButton("Apply Transport")
        self.btn_apply_host_transport.setStyleSheet(
            "QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE; border-radius: 6px; font-weight: bold; padding: 5px; }"
            "QPushButton:hover { background: #22D3EE; color: #0F172A; }"
        )

        self.host_layout.addWidget(self.lbl_host_transport)
        self.host_layout.addWidget(self.combo_host_transport)
        self.host_layout.addWidget(self.host_detail_stack)
        self.host_layout.addWidget(self.btn_apply_host_transport)
        self.host_layout.addStretch()

        self.combo_host_transport.currentIndexChanged.connect(self._on_host_transport_changed)
        self.combo_usb_port.currentIndexChanged.connect(self._on_usb_port_changed)
        self.combo_uart_port.currentIndexChanged.connect(self._on_uart_port_changed)
        self._on_host_transport_changed(self.combo_host_transport.currentIndex())
        self._on_usb_port_changed(self.combo_usb_port.currentIndex())

    def _setup_target_selector(self):
        """Add a compact target picker fed by BLE scan results."""
        self.lbl_target_device = QLabel("Target:")
        self.lbl_target_device.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.target_device_combo = QComboBox()
        self.target_device_combo.setMinimumWidth(190)
        self.target_device_combo.currentIndexChanged.connect(self._on_target_device_changed)
        self.device_ops_layout.insertWidget(0, self.lbl_target_device)
        self.device_ops_layout.insertWidget(1, self.target_device_combo)
        self._refresh_target_devices([])

    def _merge_ranging_into_uwb_config(self):
        """Move ranging controls under the shared UWB Configuration group."""
        self.uwb_config_group.setTitle("UWB Configuration")
        if not self._has_widget("ranging_group"):
            return
        self.uwb_config_form.insertRow(5, self.lbl_rng_period, self.rng_period_spin)
        self.uwb_config_form.insertRow(6, self.lbl_rx_timeout, self.rx_timeout_spin)
        self.ranging_group.setVisible(False)

    def _setup_dev_widgets(self):
        """Collect developer-only widgets for visibility toggling."""
        self._dev_widgets = [
            widget for widget in (
                getattr(self, "lbl_tx_delay", None),
                getattr(self, "tx_delay_spin", None),
                getattr(self, "lbl_rx_delay", None),
                getattr(self, "rx_delay_spin", None),
                getattr(self, "lbl_tx_power", None),
                getattr(self, "tx_power_spin", None),
                getattr(self, "lbl_preamble", None),
                getattr(self, "preamble_spin", None),
                getattr(self, "lbl_preamble_len", None),
                getattr(self, "val_preamble_len", None),
                getattr(self, "lbl_rx_pac", None),
                getattr(self, "val_rx_pac", None),
                getattr(self, "lbl_ns_sfd", None),
                getattr(self, "val_ns_sfd", None),
                getattr(self, "lbl_phr_mode", None),
                getattr(self, "val_phr_mode", None),
                getattr(self, "lbl_smart_tx_power", None),
                getattr(self, "chk_smart_tx_power", None),
                getattr(self, "lbl_pg_delay", None),
                getattr(self, "val_pg_delay", None),
                getattr(self, "uwb_adv_separator", None),
                getattr(self, "fusion_group", None),
                getattr(self, "pos_calib_group", None),
            )
            if widget is not None
        ]

    def _has_widget(self, name: str) -> bool:
        return getattr(self, name, None) is not None

    def _spin_value(self, name: str, default=0):
        widget = getattr(self, name, None)
        if widget is None or not hasattr(widget, "value"):
            return default
        return widget.value()

    def _set_value_if_present(self, name: str, value) -> None:
        widget = getattr(self, name, None)
        if widget is not None and hasattr(widget, "setValue"):
            widget.setValue(value)

    def _set_checked_if_present(self, name: str, checked: bool) -> None:
        widget = getattr(self, name, None)
        if widget is not None and hasattr(widget, "setChecked"):
            widget.setChecked(checked)

    def _setup_factory_otp_ui(self):
        # Change title
        if hasattr(self, "anchor_group"):
            self.anchor_group.setTitle("🏭 Factory OTP Configuration")

        if hasattr(self, "otp_type_combo") and self.otp_type_combo is not None:
            self.otp_type_combo.currentIndexChanged.connect(self.otp_stacked_widget.setCurrentIndex)
            return

        # Hide original widgets to replace them with OTP UI
        if hasattr(self, "btn_view_table") and self.btn_view_table is not None:
            self.btn_view_table.hide()
        if hasattr(self, "btn_view_visual") and self.btn_view_visual is not None:
            self.btn_view_visual.hide()
        if hasattr(self, "anchor_stack") and self.anchor_stack is not None:
            self.anchor_stack.hide()
        if hasattr(self, "btn_add_anchor") and self.btn_add_anchor is not None:
            self.btn_add_anchor.hide()
        if hasattr(self, "btn_remove_anchor") and self.btn_remove_anchor is not None:
            self.btn_remove_anchor.hide()

        # Remove header layout and stack from main anchor_layout
        for i in reversed(range(self.anchor_layout.count())):
            self.anchor_layout.takeAt(i)

        # Setup Form Layout for OTP fields
        from PyQt6.QtWidgets import QFormLayout, QStackedWidget, QComboBox, QLineEdit, QSpinBox, QCheckBox
        
        otp_form = QFormLayout()
        otp_form.setContentsMargins(12, 12, 12, 12)
        otp_form.setSpacing(12)
        
        self.otp_type_combo = QComboBox()
        self.otp_type_combo.addItems(["Device Info (0x01)", "Antenna Delay (0x02)"])
        otp_form.addRow("OTP Field Type:", self.otp_type_combo)
        
        self.otp_stacked_widget = QStackedWidget()
        
        # Page 0: Device Info
        page_device_info = QWidget()
        layout_device_info = QFormLayout(page_device_info)
        layout_device_info.setContentsMargins(0, 0, 0, 0)
        layout_device_info.setSpacing(10)
        
        self.otp_dev_type_combo = QComboBox()
        self.otp_dev_type_combo.addItems(["Tag", "Anchor", "Gateway", "Debug Tool"])
        self.otp_dev_type_combo.setCurrentText("Anchor")
        
        self.otp_mfg_date_input = QLineEdit()
        self.otp_mfg_date_input.setPlaceholderText("DDMMYYYY (e.g. 03072026)")
        self.otp_mfg_date_input.setInputMask("99999999")
        
        self.otp_hw_rev_spin = QSpinBox()
        self.otp_hw_rev_spin.setRange(0, 255)
        self.otp_hw_rev_spin.setValue(1)
        
        layout_device_info.addRow("Device Type:", self.otp_dev_type_combo)
        layout_device_info.addRow("Mfg Date:", self.otp_mfg_date_input)
        layout_device_info.addRow("HW Revision:", self.otp_hw_rev_spin)
        
        # Page 1: Antenna Delay
        page_antenna_delay = QWidget()
        layout_antenna_delay = QFormLayout(page_antenna_delay)
        layout_antenna_delay.setContentsMargins(0, 0, 0, 0)
        layout_antenna_delay.setSpacing(10)
        
        self.otp_tx_delay_spin = QSpinBox()
        self.otp_tx_delay_spin.setRange(0, 65535)
        self.otp_tx_delay_spin.setValue(16436)
        
        self.otp_rx_delay_spin = QSpinBox()
        self.otp_rx_delay_spin.setRange(0, 65535)
        self.otp_rx_delay_spin.setValue(16436)
        
        layout_antenna_delay.addRow("TX Antenna Delay:", self.otp_tx_delay_spin)
        layout_antenna_delay.addRow("RX Antenna Delay:", self.otp_rx_delay_spin)
        
        self.otp_stacked_widget.addWidget(page_device_info)
        self.otp_stacked_widget.addWidget(page_antenna_delay)
        
        otp_form.addRow(self.otp_stacked_widget)
        
        self.otp_confirm_checkbox = QCheckBox("Confirm irreversible OTP write")
        self.otp_confirm_checkbox.setStyleSheet("QCheckBox { color: #F87171; font-weight: bold; }")
        otp_form.addRow(self.otp_confirm_checkbox)
        
        self.anchor_layout.addLayout(otp_form)
        
        # Connect currentIndex changes
        self.otp_type_combo.currentIndexChanged.connect(self.otp_stacked_widget.setCurrentIndex)

    @staticmethod
    def _coord_text(value):
        if value is None:
            return "-"
        try:
            return format_coord(float(value))
        except (TypeError, ValueError):
            return "-"


    def _clear_main_layout(self):
        # Safely remove all widgets from the grid layout without deleting them
        widgets = [
            getattr(self, "anchor_group", None),
            getattr(self, "uwb_config_group", None),
            getattr(self, "fusion_group", None),
            getattr(self, "host_group", None),
            getattr(self, "ble_group", None),
            getattr(self, "dev_type_group", None),
            getattr(self, "ranging_group", None),
            getattr(self, "device_operations_group", None),
            getattr(self, "sys_group", None),
            getattr(self, "col2_container", None)
        ]
        for w in widgets:
            if w is not None:
                self.main_layout.removeWidget(w)

    def _unmerge_ranging_from_uwb_config(self):
        """No-op: we always keep them merged now."""
        pass

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled

        # Clear existing layout bindings
        self._clear_main_layout()

        # Update visibility of inner developer-only widgets in UWB Group
        for w in self._dev_widgets:
            if w not in (getattr(self, "fusion_group", None), getattr(self, "pos_calib_group", None)):
                w.setVisible(enabled)

        # Show or hide connection parameters in BLE Group - always show in both modes
        ble_advanced_widgets = [
            getattr(self, "lbl_ble_min_int", None),
            getattr(self, "spin_ble_min_int", None),
            getattr(self, "lbl_ble_max_int", None),
            getattr(self, "spin_ble_max_int", None),
            getattr(self, "lbl_ble_latency", None),
            getattr(self, "spin_ble_latency", None),
            getattr(self, "lbl_ble_timeout", None),
            getattr(self, "spin_ble_timeout", None),
            getattr(self, "btn_set_ble", None),
        ]
        for w in ble_advanced_widgets:
            if w is not None:
                w.setVisible(True)

        # Always keep ranging config merged into UWB configuration
        self._merge_ranging_into_uwb_config()

        if enabled:
            # 1. Developer Mode Layout (Grid 4-columns)
            # Row 0: Anchor Layout (Col 0-1)
            self.main_layout.addWidget(self.anchor_group, 0, 0, 1, 2)
            # Column 2 Container (spanning row 0 and 1)
            self.main_layout.addWidget(self.col2_container, 0, 2, 2, 1, Qt.AlignmentFlag.AlignTop)
            # Sensor Fusion (Col 3, spanning row 0 and 1)
            self.main_layout.addWidget(self.fusion_group, 0, 3, 2, 1)

            # Row 1: Host Transport (Col 0), BLE Config (Col 1)
            self.main_layout.addWidget(self.host_group, 1, 0, 1, 1)
            self.main_layout.addWidget(self.ble_group, 1, 1, 1, 1)

            # Row 2 (Footer): Bottom Control buttons
            self.main_layout.addWidget(self.device_operations_group, 2, 0, 1, 3, Qt.AlignmentFlag.AlignBottom)
            self.main_layout.addWidget(self.sys_group, 2, 3, 1, 1, Qt.AlignmentFlag.AlignBottom)

            # Column Stretch
            self.main_layout.setColumnStretch(0, 1)
            self.main_layout.setColumnStretch(1, 1)
            self.main_layout.setColumnStretch(2, 1)
            self.main_layout.setColumnStretch(3, 1)

            # Visibility
            self.fusion_group.setVisible(True)
            self.host_group.setVisible(True)
            if hasattr(self, "ranging_group") and self.ranging_group is not None:
                self.ranging_group.setVisible(False)
            self.ble_group.setVisible(True)

            # Table Edit Triggers (deprecated)
            pass

        else:
            # 2. User Mode: 3 Column Layout (Anchor Layout, Host, BLE on left/middle, UWB on right)
            # Row 0: Anchor Layout (Col 0-1)
            self.main_layout.addWidget(self.anchor_group, 0, 0, 1, 2)
            # Column 2 Container (spanning row 0 and 1)
            self.main_layout.addWidget(self.col2_container, 0, 2, 2, 1, Qt.AlignmentFlag.AlignTop)

            # Row 1: Host Transport (Col 0), BLE Config (Col 1)
            self.main_layout.addWidget(self.host_group, 1, 0, 1, 1)
            self.main_layout.addWidget(self.ble_group, 1, 1, 1, 1)

            # Row 2 (Footer): Bottom Control buttons
            self.main_layout.addWidget(self.device_operations_group, 2, 0, 1, 2, Qt.AlignmentFlag.AlignBottom)
            self.main_layout.addWidget(self.sys_group, 2, 2, 1, 1, Qt.AlignmentFlag.AlignBottom)

            # Column Stretch (Column 3 stretch to 0)
            self.main_layout.setColumnStretch(0, 1)
            self.main_layout.setColumnStretch(1, 1)
            self.main_layout.setColumnStretch(2, 1)
            self.main_layout.setColumnStretch(3, 0)

            # Visibility
            self.fusion_group.setVisible(False)
            self.host_group.setVisible(True)
            if hasattr(self, "ranging_group") and self.ranging_group is not None:
                self.ranging_group.setVisible(False)
            self.ble_group.setVisible(True)
            self.dev_type_group.setVisible(True)

            # Table Edit Triggers (deprecated)
            pass

        # Row Stretch - give Row 0 (Anchor Layout) more height than Row 1 to prevent table clipping
        self.main_layout.setRowStretch(0, 3)
        self.main_layout.setRowStretch(1, 1)
        self.main_layout.setRowStretch(2, 0)

    def set_viewmodel(self, vm):
        self._vm = vm

        # Connect signals from viewmodel to UI update slots
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_loaded)
        self._vm.sys_config_updated.connect(self._on_sys_config_loaded)
        self._vm.sys_ranging_cfg_updated.connect(self._on_sys_ranging_cfg_loaded)
        self._vm.sensor_fusion_cfg_updated.connect(self._on_sensor_fusion_cfg_loaded)
        self._vm.pos_calib_cfg_updated.connect(self._on_pos_calib_cfg_loaded)
        if hasattr(self._vm, "ble_conn_params_updated"):
            self._vm.ble_conn_params_updated.connect(self._on_ble_conn_params_loaded)
        if hasattr(self._vm, "device_type_updated"):
            self._vm.device_type_updated.connect(self._on_device_type_loaded)
        if hasattr(self._vm, "scan_devices_updated"):
            self._vm.scan_devices_updated.connect(self._refresh_target_devices)

        # Connect UI buttons to viewmodel actions
        self.btn_read_device.clicked.connect(self._read_device_config)
        self.btn_write_device.clicked.connect(self._write_device_config)
        self.btn_write_all.clicked.connect(self._write_all_devices)
        self.btn_device_reset.clicked.connect(self._vm.device_reset)
        self.btn_bootloader.clicked.connect(self._vm.enter_bootloader)
        self.btn_set_ble.clicked.connect(self._on_set_ble_clicked)
        self.btn_apply_host_transport.clicked.connect(self._on_apply_host_transport_clicked)
        self.btn_get_device_type.clicked.connect(self._on_get_device_type)
        self.btn_set_device_type.clicked.connect(self._on_set_device_type)
        if hasattr(self._vm.model, "connection_state_changed"):
            self._vm.model.connection_state_changed.connect(self._on_connection_state_changed)
        self._vm.emit_current_state()

    def _on_connection_state_changed(self, info: dict):
        if info.get("status") in ("Disconnected", "Connecting", "Connected"):
            self._reset_display_fields()

    def _reset_display_fields(self):
        """Show placeholder '-' for all fields initially or when disconnected."""
        from utils.helpers import set_widget_placeholder
        
        # Reset UWB Configuration widgets
        set_widget_placeholder(self.val_channel)
        set_widget_placeholder(self.val_role)
        set_widget_placeholder(self.val_datarate)
        set_widget_placeholder(self.val_prf)
        set_widget_placeholder(self.val_deviceid)
        set_widget_placeholder(self.tx_delay_spin)
        set_widget_placeholder(self.rx_delay_spin)
        set_widget_placeholder(self.tx_power_spin)
        set_widget_placeholder(self.preamble_spin)
        
        if self._has_widget("val_preamble_len"):
            set_widget_placeholder(self.val_preamble_len)
        if self._has_widget("val_rx_pac"):
            set_widget_placeholder(self.val_rx_pac)
        if self._has_widget("val_ns_sfd"):
            set_widget_placeholder(self.val_ns_sfd)
        if self._has_widget("val_phr_mode"):
            set_widget_placeholder(self.val_phr_mode)
        if hasattr(self, "chk_smart_tx_power") and self.chk_smart_tx_power is not None:
            set_widget_placeholder(self.chk_smart_tx_power)
        if hasattr(self, "val_pg_delay") and self.val_pg_delay is not None:
            set_widget_placeholder(self.val_pg_delay)
        set_widget_placeholder(self.combo_device_type)
            
        # Reset Ranging widgets
        set_widget_placeholder(self.rng_period_spin)
        set_widget_placeholder(self.rx_timeout_spin)
        
        # Reset Sensor Fusion (UKF) Configuration widgets
        set_widget_placeholder(self.alpha_spin)
        set_widget_placeholder(self.beta_spin)
        set_widget_placeholder(self.kappa_spin)
        set_widget_placeholder(self.q_accel_spin)
        set_widget_placeholder(self.q_gyro_spin)
        set_widget_placeholder(self.r_uwb_spin)
        if self._has_widget("init_p_px_spin"):
            set_widget_placeholder(self.init_p_px_spin)
        if self._has_widget("init_p_py_spin"):
            set_widget_placeholder(self.init_p_py_spin)
        if self._has_widget("init_p_vx_spin"):
            set_widget_placeholder(self.init_p_vx_spin)
        if self._has_widget("init_p_vy_spin"):
            set_widget_placeholder(self.init_p_vy_spin)
        if self._has_widget("init_p_theta_spin"):
            set_widget_placeholder(self.init_p_theta_spin)
        if self._has_widget("init_p_bias_ax_spin"):
            set_widget_placeholder(self.init_p_bias_ax_spin)
        if self._has_widget("init_p_bias_ay_spin"):
            set_widget_placeholder(self.init_p_bias_ay_spin)
        if self._has_widget("init_p_bias_gz_spin"):
            set_widget_placeholder(self.init_p_bias_gz_spin)
            
        # Reset Position Auto-Calibration widgets
        if self._has_widget("chk_enable_anchor_calib"):
            set_widget_placeholder(self.chk_enable_anchor_calib)
        if self._has_widget("chk_enable_tag_calib"):
            set_widget_placeholder(self.chk_enable_tag_calib)
        if self._has_widget("pos_ref_dist_spin"):
            set_widget_placeholder(self.pos_ref_dist_spin)
        if self._has_widget("pos_tag_height_spin"):
            set_widget_placeholder(self.pos_tag_height_spin)
        if self._has_widget("pos_anchor_height_spin"):
            set_widget_placeholder(self.pos_anchor_height_spin)
        if self._has_widget("pos_calib_anchor_spin"):
            set_widget_placeholder(self.pos_calib_anchor_spin)
        if self._has_widget("pos_samples_spin"):
            set_widget_placeholder(self.pos_samples_spin)
        if self._has_widget("pos_err_thresh_spin"):
            set_widget_placeholder(self.pos_err_thresh_spin)
        if self._has_widget("pos_min_delta_spin"):
            set_widget_placeholder(self.pos_min_delta_spin)
        if self._has_widget("pos_max_rounds_spin"):
            set_widget_placeholder(self.pos_max_rounds_spin)
        if self._has_widget("pos_max_std_spin"):
            set_widget_placeholder(self.pos_max_std_spin)
        if self._has_widget("pos_damping_spin"):
            set_widget_placeholder(self.pos_damping_spin)
        if self._has_widget("pos_iterations_spin"):
            set_widget_placeholder(self.pos_iterations_spin)
            
        # Reset BLE widgets
        set_widget_placeholder(self.chk_enable_ble)
        set_widget_placeholder(self.txt_ble_name)
        set_widget_placeholder(self.spin_ble_min_int)
        set_widget_placeholder(self.spin_ble_max_int)
        set_widget_placeholder(self.spin_ble_latency)
        set_widget_placeholder(self.spin_ble_timeout)
        
        # Reset Anchor Layout table placeholders (deprecated)
        pass

    def _setup_view_toggle(self):
        pass

    def _show_table_view(self):
        pass

    def _show_visual_view(self):
        pass

    def _update_segmented_style(self):
        pass

    def _on_table_item_changed(self, item):
        pass

    def _sync_table_to_shared_state(self):
        pass

    def _refresh_target_devices(self, devices: list):
        self._scan_devices = [dict(d) for d in devices]
        selected_key = None
        current_data = self.target_device_combo.currentData()
        if isinstance(current_data, dict):
            selected_key = current_data.get("key")

        self.target_device_combo.blockSignals(True)
        try:
            self.target_device_combo.clear()
            if not self._scan_devices:
                fallback = {
                    "key": "manual",
                    "label": "Manual target",
                    "device_id": self._parse_device_id_from_ui(default=1),
                    "role": self._role_from_ui(),
                    "device_type": self._role_from_ui(),
                }
                self.target_device_combo.addItem(fallback["label"], fallback)
            else:
                for idx, dev in enumerate(self._scan_devices):
                    target = self._target_from_scan_device(dev, idx)
                    self.target_device_combo.addItem(target["label"], target)
                    if selected_key and selected_key == target["key"]:
                        self.target_device_combo.setCurrentIndex(self.target_device_combo.count() - 1)
        finally:
            self.target_device_combo.blockSignals(False)
        self._on_target_device_changed(self.target_device_combo.currentIndex())

    def _target_from_scan_device(self, dev: dict, idx: int) -> dict:
        device_type = int(dev.get("device_type") or 0)
        role = device_type if device_type in (1, 2, 3) else 1
        device_id = int(dev.get("device_id") or dev.get("serial_number") or idx + 1)
        type_label = DEVICE_TYPE_LABELS_SHORT.get(device_type, str(device_type))
        if type_label == "-":
            type_label = "DEVICE"
        label = f"{type_label} 0x{device_id:08X}"
        mac = dev.get("mac", "")
        if mac:
            label = f"{label} - {mac}"
        return {
            "key": f"{device_type}:{device_id}:{mac}",
            "label": label,
            "role": role,
            "device_type": device_type,
            "device_id": device_id,
            "mac": mac,
        }

    def _selected_target(self) -> dict:
        data = self.target_device_combo.currentData()
        if isinstance(data, dict):
            return data
        return {
            "role": self._role_from_ui(),
            "device_type": self._role_from_ui(),
            "device_id": self._parse_device_id_from_ui(default=1),
        }

    def _on_target_device_changed(self, index: int):
        target = self._selected_target()
        self._apply_target_to_ui(target)

    def _apply_target_to_ui(self, target: dict):
        if not hasattr(self, "_last_anchor_layout"):
            self._last_anchor_layout = []
        role = int(target.get("role") or 1)
        device_id = int(target.get("device_id") or 1)
        role_map = {1: "Tag", 2: "Anchor", 3: "Gateway"}
        self._current_role = role
        self._current_device_id = device_id
        self.val_role.setCurrentText(role_map.get(role, "Tag"))
        self.val_deviceid.setCurrentText(f"0x{device_id:04X}")
        # if self._last_anchor_layout:
        #     self._apply_anchor_layout_to_table()

    def _role_from_ui(self) -> int:
        role_map = {"Tag": 1, "Anchor": 2, "Gateway": 3}
        return role_map.get(self.val_role.currentText(), 1)

    def _parse_device_id_from_ui(self, default=1) -> int:
        dev_id_str = self.val_deviceid.currentText().strip()
        try:
            if dev_id_str.lower().startswith("0x"):
                return int(dev_id_str, 16)
            return int(dev_id_str)
        except ValueError:
            return default

    def _get_anchors_from_table(self):
        anchors = []
        for row in range(self.anchor_table.rowCount()):
            id_item = self.anchor_table.item(row, 0)
            x_item = self.anchor_table.item(row, 1)
            y_item = self.anchor_table.item(row, 2)
            z_item = self.anchor_table.item(row, 3)

            if not id_item or not x_item or not y_item or not z_item:
                continue

            try:
                anchor_id_str = id_item.text().strip()
                if anchor_id_str.startswith('A') or anchor_id_str.startswith('a'):
                    anchor_id = int(anchor_id_str[1:])
                else:
                    anchor_id = int(anchor_id_str)

                x_m = float(x_item.text().strip())
                y_m = float(y_item.text().strip())
                z_m = float(z_item.text().strip())

                anchors.append({
                    "anchor_id": anchor_id,
                    "x_m": x_m,
                    "y_m": y_m,
                    "z_m": z_m
                })
            except ValueError:
                pass
        return anchors

    def _read_device_config(self):
        if self._vm:
            target = self._selected_target()
            self._apply_target_to_ui(target)
            self._vm.read_device_config(target)

    def _on_ble_min_interval_changed(self, value: int):
        if self.spin_ble_max_int.value() < value:
            self.spin_ble_max_int.setValue(value)

    def _on_apply_host_transport_clicked(self):
        if not self._vm:
            return
        transport_map = {"USB": 1, "UART": 2}
        self._vm.set_host_transport(transport_map.get(self.combo_host_transport.currentText(), 1))

    def _on_ble_conn_params_loaded(self, cfg: dict):
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            set_widget_placeholder(self.chk_enable_ble)
            set_widget_placeholder(self.txt_ble_name)
            set_widget_placeholder(self.spin_ble_min_int)
            set_widget_placeholder(self.spin_ble_max_int)
            set_widget_placeholder(self.spin_ble_latency)
            set_widget_placeholder(self.spin_ble_timeout)
            return
        set_widget_value(self.chk_enable_ble, cfg.get("advertising_enabled", True))
        set_widget_value(self.txt_ble_name, cfg.get("device_name", ""))
        set_widget_value(self.spin_ble_min_int, cfg.get("min_interval_ms", 20))
        set_widget_value(self.spin_ble_max_int, cfg.get("max_interval_ms", 40))
        set_widget_value(self.spin_ble_latency, cfg.get("slave_latency", 0))
        set_widget_value(self.spin_ble_timeout, cfg.get("sup_timeout_ms", 3000))

    def _on_set_ble_clicked(self):
        if not self._vm:
            return
        min_int = self.spin_ble_min_int.value()
        max_int = max(self.spin_ble_max_int.value(), min_int)
        if max_int != self.spin_ble_max_int.value():
            self.spin_ble_max_int.setValue(max_int)
        latency = self.spin_ble_latency.value()
        timeout = self.spin_ble_timeout.value()
        serial_number = self._parse_device_id_from_ui(default=0)
        self._vm.write_ble_adv_config(
            enable=self.chk_enable_ble.isChecked(),
            serial_number=serial_number,
            device_name=self.txt_ble_name.text().strip(),
        )
        self._vm.write_ble_conn_params(
            min_interval_ms=min_int,
            max_interval_ms=max_int,
            slave_latency=latency,
            sup_timeout_ms=timeout
        )

    def _write_device_config(self):
        if not self._vm:
            return
        target = self._selected_target()
        self._apply_target_to_ui(target)

        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Configuration to Write")
        dialog.setMinimumWidth(320)
        
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setSpacing(12)
        dialog_layout.setContentsMargins(16, 16, 16, 16)
        
        label = QLabel("Tích chọn các loại cấu hình muốn ghi xuống thiết bị:")
        label.setStyleSheet("font-weight: bold;")
        dialog_layout.addWidget(label)
        
        # Checkboxes
        chk_sys = QCheckBox("UWB Configuration (Sys Config)")
        chk_sys.setChecked(True)
        dialog_layout.addWidget(chk_sys)
        
        chk_ranging = QCheckBox("Ranging Configuration")
        chk_ranging.setChecked(True)
        dialog_layout.addWidget(chk_ranging)
        
        chk_fusion = QCheckBox("Sensor Fusion (UKF) Configuration")
        chk_fusion.setChecked(True)
        dialog_layout.addWidget(chk_fusion)
        
        chk_calib = QCheckBox("Position Calibration Configuration")
        chk_calib.setChecked(True)
        dialog_layout.addWidget(chk_calib)
        
        chk_otp = QCheckBox("Factory OTP Configuration")
        chk_otp.setChecked(False) # Unchecked by default for safety
        dialog_layout.addWidget(chk_otp)
        
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        dialog_layout.addWidget(button_box)
        
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Prepare configurations to send
        sys_config = None
        if chk_sys.isChecked():
            role = self._role_from_ui()
            device_id = self._parse_device_id_from_ui(default=1)

            try:
                uwb_channel = int(self.val_channel.currentText())
            except ValueError:
                uwb_channel = 5

            rate_str = self.val_datarate.currentText()
            rate_map = {"110 kbps": 110, "850 kbps": 850, "6.8 Mbps": 6800}
            uwb_data_rate = rate_map.get(rate_str, 6800)

            prf_str = self.val_prf.currentText()
            prf_map = {"16 MHz": 16, "64 MHz": 64}
            uwb_prf = prf_map.get(prf_str, 64)

            # 6 developer-mode UWB fields
            preamble_len_map = {
                "64 symbols": 0x04,
                "128 symbols": 0x08,
                "256 symbols": 0x18,
                "512 symbols": 0x28,
                "1024 symbols": 0x14,
                "1536 symbols": 0x0C,
                "2048 symbols": 0x24,
                "4096 symbols": 0x34
            }
            preamble_len_text = self.val_preamble_len.currentText() if self._has_widget("val_preamble_len") else "4096 symbols"
            uwb_preamble_len = preamble_len_map.get(preamble_len_text, 0x34)

            pac_map = {"8": 0, "16": 1, "32": 2, "64": 3}
            rx_pac_text = self.val_rx_pac.currentText() if self._has_widget("val_rx_pac") else "8"
            uwb_rx_pac = pac_map.get(rx_pac_text, 0)

            sfd_map = {"Standard": 0, "Non-standard": 1}
            ns_sfd_text = self.val_ns_sfd.currentText() if self._has_widget("val_ns_sfd") else "Standard"
            uwb_ns_sfd = sfd_map.get(ns_sfd_text, 0)

            phr_map = {"Standard": 0, "Extended": 1}
            phr_mode_text = self.val_phr_mode.currentText() if self._has_widget("val_phr_mode") else "Standard"
            uwb_phr_mode = phr_map.get(phr_mode_text, 0)

            smart_tx_power = self.chk_smart_tx_power.isChecked() if self._has_widget("chk_smart_tx_power") else False
            pg_delay = self._spin_value("val_pg_delay", 193)

            sys_config = dict(
                role=role,
                device_id=device_id,
                uwb_channel=uwb_channel,
                uwb_data_rate=uwb_data_rate,
                uwb_prf=uwb_prf,
                tx_antenna_delay=self.tx_delay_spin.value(),
                rx_antenna_delay=self.rx_delay_spin.value(),
                tx_power=max(0, int(self.tx_power_spin.value())),
                uwb_preamble_code=self.preamble_spin.value(),
                ranging_period_ms=self.rng_period_spin.value(),
                rx_timeout_ms=self.rx_timeout_spin.value(),
                uwb_preamble_len=uwb_preamble_len,
                uwb_rx_pac=uwb_rx_pac,
                uwb_ns_sfd=uwb_ns_sfd,
                uwb_phr_mode=uwb_phr_mode,
                smart_tx_power=smart_tx_power,
                pg_delay=pg_delay
            )

        ranging_config = None
        if chk_ranging.isChecked():
            ranging_config = {
                "period_ms": self.rng_period_spin.value(),
                "timeout_ms": self.rx_timeout_spin.value(),
            }

        sensor_fusion_config = None
        if chk_fusion.isChecked():
            sensor_fusion_config = dict(
                alpha=self.alpha_spin.value(),
                beta=self.beta_spin.value(),
                kappa=self.kappa_spin.value(),
                q_a=self.q_accel_spin.value(),
                q_g=self.q_gyro_spin.value(),
                r_uwb=self.r_uwb_spin.value(),
                init_p_px=self._spin_value("init_p_px_spin", 1.0),
                init_p_py=self._spin_value("init_p_py_spin", 1.0),
                init_p_vx=self._spin_value("init_p_vx_spin", 0.1),
                init_p_vy=self._spin_value("init_p_vy_spin", 0.1),
                init_p_theta=self._spin_value("init_p_theta_spin", 0.1),
                init_p_bias_ax=self._spin_value("init_p_bias_ax_spin", 0.01),
                init_p_bias_ay=self._spin_value("init_p_bias_ay_spin", 0.01),
                init_p_bias_gz=self._spin_value("init_p_bias_gz_spin", 0.01)
            )

        pos_calib_config = None
        if chk_calib.isChecked() and self._has_widget("chk_enable_anchor_calib"):
            pos_calib_config = dict(
                enable_anchor_auto_calib=self.chk_enable_anchor_calib.isChecked(),
                enable_tag_auto_calib=getattr(self, "chk_enable_tag_calib", self.chk_enable_anchor_calib).isChecked(),
                ref_distance_xy_m=self._spin_value("pos_ref_dist_spin", 2.0),
                tag_height_m=self._spin_value("pos_tag_height_spin", 1.0),
                anchor_height_m=self._spin_value("pos_anchor_height_spin", 2.5),
                calib_anchor_id=self._spin_value("pos_calib_anchor_spin", 1),
                samples=self._spin_value("pos_samples_spin", 10),
                error_threshold_m=self._spin_value("pos_err_thresh_spin", 0.3),
                min_delta_step=self._spin_value("pos_min_delta_spin", 1),
                max_rounds=self._spin_value("pos_max_rounds_spin", 10),
                max_std_m=self._spin_value("pos_max_std_spin", 0.2),
                damping=self._spin_value("pos_damping_spin", 0.1),
                iterations=self._spin_value("pos_iterations_spin", 100)
            )

        factory_otp_config = None
        if chk_otp.isChecked():
            if not self.otp_confirm_checkbox.isChecked():
                QMessageBox.warning(self, "Cảnh báo OTP",
                    "Vui lòng tích chọn 'Confirm irreversible OTP write' trước khi ghi OTP!")
                return

            otp_type_idx = self.otp_type_combo.currentIndex()
            # Map combo index → OTP_TYPE constant (matches provision_otp.py)
            otp_type = OTP_TYPE_DEVICE_INFO if otp_type_idx == 0 else OTP_TYPE_ANTENNA_DELAY

            device_type    = DEVICE_TYPE_ANCHOR  # default
            value_u32      = 0
            value_u8       = 0
            tx_antenna_delay = 0
            rx_antenna_delay = 0

            if otp_type == OTP_TYPE_DEVICE_INFO:
                dev_type_map = {
                    "Tag":       DEVICE_TYPE_TAG,
                    "Anchor":    DEVICE_TYPE_ANCHOR,
                    "Gateway":   DEVICE_TYPE_GATEWAY,
                    "Debug Tool": DEVICE_TYPE_DEBUG_TOOL,
                }
                device_type = dev_type_map.get(self.otp_dev_type_combo.currentText(), DEVICE_TYPE_ANCHOR)

                mfg_date_str = self.otp_mfg_date_input.text().strip()
                try:
                    value_u32 = int(mfg_date_str)
                except ValueError:
                    QMessageBox.warning(self, "Cảnh báo OTP",
                        "Ngày sản xuất phải là số nguyên định dạng DDMMYYYY (ví dụ: 03072026)!")
                    return

                # device_info requires a non-zero mfg_date (mirrors provision_otp.py line 118)
                if value_u32 == 0:
                    QMessageBox.warning(self, "Cảnh báo OTP",
                        "device_info yêu cầu ngày sản xuất hợp lệ, ví dụ: 03072026.")
                    return

                # Validate DDMMYYYY ranges (mirrors _valid_mfg_date in provision_otp.py)
                day   = value_u32 // 1_000_000
                month = (value_u32 // 10_000) % 100
                year  = value_u32 % 10_000
                if not (1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2255):
                    QMessageBox.warning(self, "Cảnh báo OTP",
                        "Ngày sản xuất không hợp lệ! Định dạng DDMMYYYY, "
                        "ngày 1..31, tháng 1..12, năm 2000..2255 (ví dụ: 03072026).")
                    return

                value_u8 = self.otp_hw_rev_spin.value()

            elif otp_type == OTP_TYPE_ANTENNA_DELAY:
                tx_antenna_delay = self.otp_tx_delay_spin.value()
                rx_antenna_delay = self.otp_rx_delay_spin.value()
                # Guard against uint16 overflow (mirrors provision_otp.py line 115)
                if tx_antenna_delay > 0xFFFF or rx_antenna_delay > 0xFFFF:
                    QMessageBox.warning(self, "Cảnh báo OTP",
                        "TX/RX antenna delay phải nằm trong khoảng uint16 (0..65535).")
                    return

            factory_otp_config = {
                "confirm_magic":    OTP_CONFIRM_MAGIC,
                "otp_type":         otp_type,
                "device_type":      device_type,
                "tx_antenna_delay": tx_antenna_delay,
                "rx_antenna_delay": rx_antenna_delay,
                "value_u32":        value_u32,
                "value_u8":         value_u8,
            }

        self._vm.write_device_config(
            target=target,
            ranging_config=ranging_config,
            sys_config=sys_config,
            sensor_fusion_config=sensor_fusion_config,
            pos_calib_config=pos_calib_config,
            factory_otp_config=factory_otp_config,
        )

    def _write_all_devices(self):
        if not self._vm:
            return
        snapshot = self._collect_write_snapshot()
        targets = [self._target_from_scan_device(dev, idx) for idx, dev in enumerate(self._scan_devices)]
        if not targets:
            targets = [self._selected_target()]
        self._vm.write_all_device_configs(targets, snapshot)

    def _collect_write_snapshot(self) -> dict:
        target = self._selected_target()
        anchors = self._get_anchors_from_table()
        role = self._role_from_ui()
        device_id = self._parse_device_id_from_ui(default=1)
        try:
            uwb_channel = int(self.val_channel.currentText())
        except ValueError:
            uwb_channel = 5
        rate_map = {"110 kbps": 110, "850 kbps": 850, "6.8 Mbps": 6800}
        prf_map = {"16 MHz": 16, "64 MHz": 64}
        preamble_len_map = {
            "64 symbols": 0x04,
            "128 symbols": 0x08,
            "256 symbols": 0x18,
            "512 symbols": 0x28,
            "1024 symbols": 0x14,
            "1536 symbols": 0x0C,
            "2048 symbols": 0x24,
            "4096 symbols": 0x34,
        }
        pac_map = {"8": 0, "16": 1, "32": 2, "64": 3}
        sfd_map = {"Standard": 0, "Non-standard": 1}
        phr_map = {"Standard": 0, "Extended": 1}
        sys_config = {
            "role": role,
            "device_id": device_id,
            "uwb_channel": uwb_channel,
            "uwb_data_rate": rate_map.get(self.val_datarate.currentText(), 6800),
            "uwb_prf": prf_map.get(self.val_prf.currentText(), 64),
            "tx_antenna_delay": self.tx_delay_spin.value(),
            "rx_antenna_delay": self.rx_delay_spin.value(),
            "tx_power": max(0, int(self.tx_power_spin.value())),
            "uwb_preamble_code": self.preamble_spin.value(),
            "ranging_period_ms": self.rng_period_spin.value(),
            "rx_timeout_ms": self.rx_timeout_spin.value(),
            "uwb_preamble_len": preamble_len_map.get(self.val_preamble_len.currentText() if self._has_widget("val_preamble_len") else "4096 symbols", 0x34),
            "uwb_rx_pac": pac_map.get(self.val_rx_pac.currentText() if self._has_widget("val_rx_pac") else "8", 0),
            "uwb_ns_sfd": sfd_map.get(self.val_ns_sfd.currentText() if self._has_widget("val_ns_sfd") else "Standard", 0),
            "uwb_phr_mode": phr_map.get(self.val_phr_mode.currentText() if self._has_widget("val_phr_mode") else "Standard", 0),
            "smart_tx_power": self.chk_smart_tx_power.isChecked() if self._has_widget("chk_smart_tx_power") else False,
            "pg_delay": self._spin_value("val_pg_delay", 193),
        }
        sensor_fusion_config = {
            "alpha": self.alpha_spin.value(),
            "beta": self.beta_spin.value(),
            "kappa": self.kappa_spin.value(),
            "q_a": self.q_accel_spin.value(),
            "q_g": self.q_gyro_spin.value(),
            "r_uwb": self.r_uwb_spin.value(),
            "init_p_px": self._spin_value("init_p_px_spin", 1.0),
            "init_p_py": self._spin_value("init_p_py_spin", 1.0),
            "init_p_vx": self._spin_value("init_p_vx_spin", 0.1),
            "init_p_vy": self._spin_value("init_p_vy_spin", 0.1),
            "init_p_theta": self._spin_value("init_p_theta_spin", 0.1),
            "init_p_bias_ax": self._spin_value("init_p_bias_ax_spin", 0.01),
            "init_p_bias_ay": self._spin_value("init_p_bias_ay_spin", 0.01),
            "init_p_bias_gz": self._spin_value("init_p_bias_gz_spin", 0.01),
        }
        pos_calib_config = {}
        if self._has_widget("chk_enable_anchor_calib"):
            pos_calib_config = {
                "enable_anchor_auto_calib": self.chk_enable_anchor_calib.isChecked(),
                "enable_tag_auto_calib": getattr(self, "chk_enable_tag_calib", self.chk_enable_anchor_calib).isChecked(),
                "ref_distance_xy_m": self._spin_value("pos_ref_dist_spin", 2.0),
                "tag_height_m": self._spin_value("pos_tag_height_spin", 1.0),
                "anchor_height_m": self._spin_value("pos_anchor_height_spin", 2.5),
                "calib_anchor_id": self._spin_value("pos_calib_anchor_spin", 1),
                "samples": self._spin_value("pos_samples_spin", 10),
                "error_threshold_m": self._spin_value("pos_err_thresh_spin", 0.3),
                "min_delta_step": self._spin_value("pos_min_delta_spin", 1),
                "max_rounds": self._spin_value("pos_max_rounds_spin", 10),
                "max_std_m": self._spin_value("pos_max_std_spin", 0.2),
                "damping": self._spin_value("pos_damping_spin", 0.1),
                "iterations": self._spin_value("pos_iterations_spin", 100),
            }
        return {
            "target": target,
            "anchors": anchors,
            "ranging_config": {"period_ms": self.rng_period_spin.value(), "timeout_ms": self.rx_timeout_spin.value()},
            "sys_config": sys_config,
            "sensor_fusion_config": sensor_fusion_config,
            "pos_calib_config": pos_calib_config,
        }

    def _on_anchor_layout_loaded(self, anchors):
        pass

    def _update_single_anchor_in_table(self, anchor_id, x_m, y_m, z_m):
        target_row = -1
        for row in range(self.anchor_table.rowCount()):
            item = self.anchor_table.item(row, 0)
            if item:
                text = item.text().strip()
                if text == f"A{anchor_id}" or text == f"a{anchor_id}" or text == str(anchor_id):
                    target_row = row
                    break

        if target_row == -1:
            target_row = self.anchor_table.rowCount()
            self.anchor_table.insertRow(target_row)
            self.anchor_table.setItem(target_row, 0, QTableWidgetItem(f"A{anchor_id}"))

        self.anchor_table.setItem(target_row, 1, QTableWidgetItem(self._coord_text(x_m)))
        self.anchor_table.setItem(target_row, 2, QTableWidgetItem(self._coord_text(y_m)))
        self.anchor_table.setItem(target_row, 3, QTableWidgetItem(self._coord_text(z_m)))
    def _apply_anchor_layout_to_table(self):
        pass

    def _on_sys_config_loaded(self, cfg):
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            set_widget_placeholder(self.val_channel)
            set_widget_placeholder(self.val_role)
            set_widget_placeholder(self.val_datarate)
            set_widget_placeholder(self.val_prf)
            set_widget_placeholder(self.val_deviceid)
            set_widget_placeholder(self.tx_delay_spin)
            set_widget_placeholder(self.rx_delay_spin)
            set_widget_placeholder(self.tx_power_spin)
            set_widget_placeholder(self.preamble_spin)
            if self._has_widget("val_preamble_len"):
                set_widget_placeholder(self.val_preamble_len)
            if self._has_widget("val_rx_pac"):
                set_widget_placeholder(self.val_rx_pac)
            if self._has_widget("val_ns_sfd"):
                set_widget_placeholder(self.val_ns_sfd)
            if self._has_widget("val_phr_mode"):
                set_widget_placeholder(self.val_phr_mode)
            if self._has_widget("chk_smart_tx_power"):
                set_widget_placeholder(self.chk_smart_tx_power)
            if self._has_widget("val_pg_delay"):
                set_widget_placeholder(self.val_pg_delay)
            return

        # Save active device role and ID
        self._current_role = cfg.get("role", 1)
        self._current_device_id = cfg.get("device_id", 0)

        # Map channel
        chan = str(cfg.get("uwb_channel", 5))
        set_widget_value(self.val_channel, chan)

        # Map role (1 = Tag, 2 = Anchor, 3 = Gateway)
        role_map = {1: "Tag", 2: "Anchor", 3: "Gateway"}
        role = role_map.get(self._current_role, "Tag")
        set_widget_value(self.val_role, role)

        # Map data rate (1 = 110kbps, 2 = 850kbps, 3 = 6.8Mbps)
        rate_val = cfg.get("uwb_data_rate", 3)
        rate_map = {1: "110 kbps", 2: "850 kbps", 3: "6.8 Mbps", 110: "110 kbps", 850: "850 kbps", 6800: "6.8 Mbps"}
        rate = rate_map.get(rate_val, "6.8 Mbps")
        set_widget_value(self.val_datarate, rate)

        # Map PRF (1 = 16MHz, 2 = 64MHz)
        prf_val = cfg.get("uwb_prf", 2)
        prf_map = {1: "16 MHz", 2: "64 MHz", 16: "16 MHz", 64: "64 MHz"}
        prf = prf_map.get(prf_val, "64 MHz")
        set_widget_value(self.val_prf, prf)

        # Map Device ID
        dev_id = f"0x{cfg.get('device_id', 1):04X}"
        set_widget_value(self.val_deviceid, dev_id)

        # Map Advanced delay & power
        set_widget_value(self.tx_delay_spin, cfg.get("tx_antenna_delay", 16436))
        set_widget_value(self.rx_delay_spin, cfg.get("rx_antenna_delay", 16436))
        set_widget_value(self.tx_power_spin, cfg.get("tx_power", 0))
        set_widget_value(self.preamble_spin, cfg.get("uwb_preamble_code", 10))

        # Map the 6 developer-mode UWB fields
        preamble_len_rev = {
            0x04: "64 symbols",
            0x08: "128 symbols",
            0x18: "256 symbols",
            0x28: "512 symbols",
            0x14: "1024 symbols",
            0x0C: "1536 symbols",
            0x24: "2048 symbols",
            0x34: "4096 symbols"
        }
        preamble_len_val = cfg.get("uwb_preamble_len", 0x34)
        if self._has_widget("val_preamble_len"):
            set_widget_value(self.val_preamble_len, preamble_len_rev.get(preamble_len_val, "4096 symbols"))

        pac_rev = {0: "8", 1: "16", 2: "32", 3: "64"}
        pac_val = cfg.get("uwb_rx_pac", 0)
        if self._has_widget("val_rx_pac"):
            set_widget_value(self.val_rx_pac, pac_rev.get(pac_val, "8"))

        sfd_rev = {0: "Standard", 1: "Non-standard"}
        sfd_val = cfg.get("uwb_ns_sfd", 0)
        if self._has_widget("val_ns_sfd"):
            set_widget_value(self.val_ns_sfd, sfd_rev.get(sfd_val, "Standard"))

        phr_rev = {0: "Standard", 1: "Extended"}
        phr_val = cfg.get("uwb_phr_mode", 0)
        if self._has_widget("val_phr_mode"):
            set_widget_value(self.val_phr_mode, phr_rev.get(phr_val, "Standard"))
        
        if self._has_widget("chk_smart_tx_power"):
            set_widget_value(self.chk_smart_tx_power, cfg.get("smart_tx_power", False))
        if self._has_widget("val_pg_delay"):
            set_widget_value(self.val_pg_delay, cfg.get("pg_delay", 193))
            
        # if self._last_anchor_layout:
        #     self._apply_anchor_layout_to_table()

    def _on_sys_ranging_cfg_loaded(self, cfg):
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            set_widget_placeholder(self.rng_period_spin)
            set_widget_placeholder(self.rx_timeout_spin)
            return
        set_widget_value(self.rng_period_spin, cfg.get("ranging_period_ms", 100))
        set_widget_value(self.rx_timeout_spin, cfg.get("rx_timeout_ms", 70))

    def _on_sensor_fusion_cfg_loaded(self, cfg):
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            set_widget_placeholder(self.alpha_spin)
            set_widget_placeholder(self.beta_spin)
            set_widget_placeholder(self.kappa_spin)
            set_widget_placeholder(self.q_accel_spin)
            set_widget_placeholder(self.q_gyro_spin)
            set_widget_placeholder(self.r_uwb_spin)
            for widget_name in ("init_p_px_spin", "init_p_py_spin", "init_p_vx_spin", "init_p_vy_spin", 
                                "init_p_theta_spin", "init_p_bias_ax_spin", "init_p_bias_ay_spin", "init_p_bias_gz_spin"):
                if self._has_widget(widget_name):
                    set_widget_placeholder(getattr(self, widget_name))
            return
        set_widget_value(self.alpha_spin, cfg.get("alpha", 0.001))
        set_widget_value(self.beta_spin, cfg.get("beta", 2.0))
        set_widget_value(self.kappa_spin, cfg.get("kappa", 0.0))
        set_widget_value(self.q_accel_spin, cfg.get("q_a", 0.1))
        set_widget_value(self.q_gyro_spin, cfg.get("q_g", 0.01))
        set_widget_value(self.r_uwb_spin, cfg.get("r_uwb", 0.05))
        for key in ("init_p_px", "init_p_py", "init_p_vx", "init_p_vy", "init_p_theta", "init_p_bias_ax", "init_p_bias_ay", "init_p_bias_gz"):
            widget_name = f"{key}_spin"
            if self._has_widget(widget_name):
                set_widget_value(getattr(self, widget_name), cfg.get(key))

    def _on_pos_calib_cfg_loaded(self, cfg):
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            for widget_name in ("chk_enable_anchor_calib", "chk_enable_tag_calib", "pos_ref_dist_spin",
                                "pos_tag_height_spin", "pos_anchor_height_spin", "pos_calib_anchor_spin",
                                "pos_samples_spin", "pos_err_thresh_spin", "pos_min_delta_spin",
                                "pos_max_rounds_spin", "pos_max_std_spin", "pos_damping_spin", "pos_iterations_spin"):
                if self._has_widget(widget_name):
                    set_widget_placeholder(getattr(self, widget_name))
            return
        if self._has_widget("chk_enable_anchor_calib"):
            set_widget_value(self.chk_enable_anchor_calib, cfg.get("enable_anchor_auto_calib", True))
        if self._has_widget("chk_enable_tag_calib"):
            set_widget_value(self.chk_enable_tag_calib, cfg.get("enable_tag_auto_calib", True))
        if self._has_widget("pos_ref_dist_spin"):
            set_widget_value(self.pos_ref_dist_spin, cfg.get("ref_distance_xy_m", 2.0))
        if self._has_widget("pos_tag_height_spin"):
            set_widget_value(self.pos_tag_height_spin, cfg.get("tag_height_m", 1.0))
        if self._has_widget("pos_anchor_height_spin"):
            set_widget_value(self.pos_anchor_height_spin, cfg.get("anchor_height_m", 2.5))
        if self._has_widget("pos_calib_anchor_spin"):
            set_widget_value(self.pos_calib_anchor_spin, cfg.get("calib_anchor_id", 1))
        if self._has_widget("pos_samples_spin"):
            set_widget_value(self.pos_samples_spin, cfg.get("samples", 10))
        if self._has_widget("pos_err_thresh_spin"):
            set_widget_value(self.pos_err_thresh_spin, cfg.get("error_threshold_m", 0.3))
        if self._has_widget("pos_min_delta_spin"):
            set_widget_value(self.pos_min_delta_spin, cfg.get("min_delta_step", 1))
        if self._has_widget("pos_max_rounds_spin"):
            set_widget_value(self.pos_max_rounds_spin, cfg.get("max_rounds", 10))
        if self._has_widget("pos_max_std_spin"):
            set_widget_value(self.pos_max_std_spin, cfg.get("max_std_m", 0.2))
        if self._has_widget("pos_damping_spin"):
            set_widget_value(self.pos_damping_spin, cfg.get("damping", 0.1))
        if self._has_widget("pos_iterations_spin"):
            set_widget_value(self.pos_iterations_spin, cfg.get("iterations", 100))

    def _on_host_transport_changed(self, index):
        index = max(0, int(index))
        if hasattr(self, "host_detail_stack"):
            self.host_detail_stack.setCurrentIndex(index)
        if index == 0 and hasattr(self, "combo_usb_port"):
            self._on_usb_port_changed(self.combo_usb_port.currentIndex())
        elif hasattr(self, "combo_uart_port"):
            self._on_uart_port_changed(self.combo_uart_port.currentIndex())

    def _on_uart_port_changed(self, index):
        # UART details are represented directly by the visible controls.
        return None

    def _populate_serial_ports(self, combo):
        combo.clear()
        try:
            import serial.tools.list_ports
            ports = list(serial.tools.list_ports.comports())
            if ports:
                for p in ports:
                    combo.addItem(p.device, p.device)
            else:
                combo.addItem("No COM detected", "")
        except Exception as e:
            print("Error listing serial ports:", e)

    def _on_usb_port_changed(self, index):
        port_name = self.combo_usb_port.currentData()
        if not port_name:
            port_name = self.combo_usb_port.currentText()

        try:
            import serial.tools.list_ports
            ports = serial.tools.list_ports.comports()
            for p in ports:
                if p.device == port_name:
                    self.txt_usb_device.setText(p.description)
                    return
        except Exception:
            pass
        self.txt_usb_device.setText("STM32 Virtual COM Port" if "COM" in port_name else "Unknown Device")

    def _on_get_device_type(self):
        if self._vm:
            self._vm.read_device_type()

    def _on_set_device_type(self):
        if self._vm:
            text_map = {
                "Tag": 1,
                "Anchor": 2,
                "Gateway": 3,
                "Debug Tool": 4
            }
            dev_type = text_map.get(self.combo_device_type.currentText(), 1)
            self._vm.write_device_type(dev_type)

    def _on_device_type_loaded(self, device_type: int):
        from utils.helpers import set_widget_value
        type_map = {
            1: "Tag",
            2: "Anchor",
            3: "Gateway",
            4: "Debug Tool"
        }
        text = type_map.get(device_type, "Tag")
        set_widget_value(self.combo_device_type, text)
