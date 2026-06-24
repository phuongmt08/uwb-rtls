"""
UWB RTLS Studio — Log & Session History Tab (UI loaded from .ui file)
Tab 5: Live log viewer + Session history browser.

FE: Loaded from views/ui/log_tab.ui (editable in Qt Designer)
BE: Log filtering + session management (this file)
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QTextEdit, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QFrame, QAbstractItemView, QButtonGroup, QFileDialog, QMessageBox,
    QFormLayout
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat, QDesktopServices
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'log_tab.ui')

OBJECT_CODE_FILTERS = [
    ("BOOTLOADER", 0x00),
    ("APPLICATION", 0x01),
    ("NETWORK", 0x02),
    ("UWB DRIVER", 0x03),
    ("RANGING", 0x04),
    ("POSITIONING", 0x05),
    ("SERIAL", 0x06),
    ("IO", 0x07),
    ("IMU", 0x08),
    ("BLE", 0x09),
    ("FLASH", 0x0D),
    ("TASK", 0x0F),
    ("ANCHOR", 0x10),
    ("TAG", 0x11),
    ("GATEWAY", 0x12),
    ("PM", 0x13),
    ("FUSION", 0x14),
    ("SYS CFG", 0x15),
    ("BATTERY", 0x16),
    ("SPECIAL", 0x7F),
]


class LogTab(QWidget):
    def __init__(self, parent=None, is_developer=False):
        super().__init__(parent)
        self._is_developer = is_developer
        self._log_entry_count = 0
        self._all_log_lines = []
        self._filter_active = False
        self._session_records = {}
        self._current_session_name = None
        self._detail_mode = "ranging"
        self._detail_rows = []

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # ── Post-load setup ──
        self._setup_splitter()
        self._setup_object_code_filter()
        self._setup_session_table()
        self._setup_detail_table()
        self._setup_dev_widgets()
        self._connect_signals()
        self._vm = None

        # Sẽ sinh virtual history sau 50ms nếu không có ViewModel nào được gán
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self._check_and_populate_virtual)

        # Apply initial mode
        self.set_developer_mode(self._is_developer)

    def _check_and_populate_virtual(self):
        return

    def _setup_dev_widgets(self):
        """Collect developer-only widgets for visibility toggling."""
        self._dev_widgets = []
        self._setup_host_packet_sender()

    def _setup_host_packet_sender(self):
        self.host_packet_group = QGroupBox("Host Packet Sender", self)
        self.host_packet_group.setStyleSheet(
            "QGroupBox { color: #22D3EE; border: 1px solid #334155; border-radius: 6px; "
            "margin-top: 8px; padding: 8px; font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )

        outer = QVBoxLayout(self.host_packet_group)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(6)

        packet_row = QHBoxLayout()
        packet_row.setSpacing(6)
        packet_row.addWidget(QLabel("Packet:", self.host_packet_group))

        self.host_packet_select = QComboBox(self.host_packet_group)
        self.host_packet_select.addItem("none keep-alive", "none")
        self.host_packet_select.addItem("host_ack", "ack")
        self.host_packet_select.addItem("time_sync_set", "time_sync_set")
        self.host_packet_select.addItem("host_transport_set", "host_transport_set")
        self.host_packet_select.addItem("log_data", "log_data")
        self.host_packet_select.addItem("log_clear", "log_clear")
        packet_row.addWidget(self.host_packet_select, 1)

        self.host_packet_dst = QComboBox(self.host_packet_group)
        self.host_packet_dst.addItem("MCU(1)", 1)
        self.host_packet_dst.addItem("CENTRAL(3)", 3)
        self.host_packet_dst.addItem("PERIPHERAL(4)", 4)
        packet_row.addWidget(QLabel("Dst:", self.host_packet_group))
        packet_row.addWidget(self.host_packet_dst)
        outer.addLayout(packet_row)

        self._host_packet_param_rows = {}
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        outer.addLayout(form)

        self.host_packet_log_type = QComboBox(self.host_packet_group)
        self.host_packet_log_type.addItem("DEVICE_LOG(1)", 1)
        self.host_packet_log_type.addItem("UNSPECIFIED(0)", 0)
        self._add_host_packet_field(form, "log_type", "Log type:", self.host_packet_log_type)

        self.host_packet_ack_seq = QLineEdit(self.host_packet_group)
        self.host_packet_ack_seq.setPlaceholderText("seq from MCU packet")
        self._add_host_packet_field(form, "ack_seq", "ACK seq:", self.host_packet_ack_seq)

        self.host_packet_ack_response = QComboBox(self.host_packet_group)
        self.host_packet_ack_response.addItem("ACK(1)", 1)
        self.host_packet_ack_response.addItem("NACK_BAD_CRC(2)", 2)
        self.host_packet_ack_response.addItem("NACK_UNIMPLEMENTED(3)", 3)
        self.host_packet_ack_response.addItem("NACK_TIMED_OUT(4)", 4)
        self.host_packet_ack_response.addItem("NACK_BUSY(5)", 5)
        self.host_packet_ack_response.addItem("NACK_CMD_FAILED(6)", 6)
        self.host_packet_ack_response.addItem("NACK_INVALID_TYPE(7)", 7)
        self._add_host_packet_field(form, "ack_response", "Response:", self.host_packet_ack_response)

        self.host_packet_data = QLineEdit(self.host_packet_group)
        self.host_packet_data.setPlaceholderText("hex: 01 02 03 or text:hello")
        self._add_host_packet_field(form, "data", "Data:", self.host_packet_data)

        self.host_packet_offset = QLineEdit(self.host_packet_group)
        self.host_packet_offset.setPlaceholderText("0")
        self._add_host_packet_field(form, "offset", "Offset:", self.host_packet_offset)

        self.host_packet_length = QLineEdit(self.host_packet_group)
        self.host_packet_length.setPlaceholderText("0")
        self._add_host_packet_field(form, "length", "Length:", self.host_packet_length)

        self.host_packet_unix_ms = QLineEdit(self.host_packet_group)
        self.host_packet_unix_ms.setPlaceholderText("blank = current host time")
        self._add_host_packet_field(form, "unix_time_ms", "Unix ms:", self.host_packet_unix_ms)

        self.host_packet_timezone = QLineEdit(self.host_packet_group)
        self.host_packet_timezone.setText("420")
        self._add_host_packet_field(form, "timezone_offset", "Timezone min:", self.host_packet_timezone)

        self.host_packet_transport = QComboBox(self.host_packet_group)
        self.host_packet_transport.addItem("USB(1)", 1)
        self.host_packet_transport.addItem("UART(2)", 2)
        self.host_packet_transport.addItem("UNSPECIFIED(0)", 0)
        self._add_host_packet_field(form, "transport", "Transport:", self.host_packet_transport)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.host_packet_status = QLabel("Ready", self.host_packet_group)
        self.host_packet_status.setStyleSheet("color: #94A3B8;")
        self.btn_send_host_packet = QPushButton("Send Packet", self.host_packet_group)
        action_row.addWidget(self.host_packet_status, 1)
        action_row.addWidget(self.btn_send_host_packet, 0)
        outer.addLayout(action_row)

        self.log_layout.insertWidget(2, self.host_packet_group)
        self._dev_widgets.append(self.host_packet_group)
        self.host_packet_select.currentIndexChanged.connect(self._update_host_packet_fields)
        self.btn_send_host_packet.clicked.connect(self._send_selected_host_packet)
        self._update_host_packet_fields()

    def _add_host_packet_field(self, form, key, label_text, widget):
        label = QLabel(label_text, self.host_packet_group)
        form.addRow(label, widget)
        self._host_packet_param_rows[key] = (label, widget)

    def _setup_splitter(self):
        """Keep live log and session history as equal left/right panes."""
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([650, 650])

    def _setup_object_code_filter(self):
        """Populate the live-log object-code filter from log_config.h values."""
        self.filter_source.clear()
        self.filter_source.addItem("All Objects", None)
        for name, code in OBJECT_CODE_FILTERS:
            self.filter_source.addItem(f"{name} (0x{code:02X})", code)

    def _setup_session_table(self):
        """Scale the history table so ten total sessions are visible comfortably."""
        self.session_table.verticalHeader().setDefaultSectionSize(32)
        self.session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.session_table.setMinimumHeight(260)
        self.session_table.setColumnCount(6)
        self.session_table.setColumnWidth(0, 240)
        self.session_table.setColumnWidth(1, 130)
        self.session_table.setColumnWidth(2, 112)
        self.session_table.setColumnWidth(3, 110)
        self.session_table.setColumnWidth(4, 86)
        self.session_table.setColumnWidth(5, 150)
        self.session_table.setHorizontalHeaderLabels(
            ["Session", "Started", "Elapsed", "Ranging Runs", "Session Files", "Browser"]
        )
        self.session_table.horizontalHeader().setStretchLastSection(True)

    def _setup_detail_table(self):
        """Set up the embedded detail table and footer actions."""
        self.detail_table.verticalHeader().setDefaultSectionSize(32)
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        self.detail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self._detail_mode_group = QButtonGroup(self)
        self._detail_mode_group.setExclusive(True)
        self._detail_mode_group.addButton(self.btn_detail_ranging)
        self._detail_mode_group.addButton(self.btn_detail_logs)
        self.btn_detail_ranging.setChecked(True)
        self.btn_detail_ranging.setMinimumWidth(85)
        self.btn_detail_logs.setMinimumWidth(85)
        self._set_detail_actions_enabled(False)

    def _connect_signals(self):
        """Connect UI signals."""
        self.filter_level.currentTextChanged.connect(self._apply_filter)
        self.filter_source.currentIndexChanged.connect(self._apply_filter)
        self.search_edit.textChanged.connect(self._apply_filter)
        if hasattr(self, "btn_clear_log"):
            self.btn_clear_log.clicked.connect(self._clear_log_session)
        if hasattr(self, "btn_start_log"):
            self.btn_start_log.clicked.connect(self._start_log_session)
        self.session_table.itemSelectionChanged.connect(self._on_session_selection_changed)
        self.btn_detail_ranging.toggled.connect(
            lambda checked: checked and self._set_detail_mode("ranging")
        )
        self.btn_detail_logs.toggled.connect(
            lambda checked: checked and self._set_detail_mode("logs")
        )
        self.detail_table.itemSelectionChanged.connect(self._update_detail_selection_label)
        self.btn_detail_open.clicked.connect(self._open_selected_detail)
        self.btn_detail_browse.clicked.connect(self._browse_selected_detail)
        self.btn_detail_remove.clicked.connect(self._remove_selected_detail)

    def _readonly_item(self, value) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _set_browser_cell(self, row: int, session_id: str, browser_path: str) -> None:
        container = QWidget(self.session_table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        path_label = QLabel(browser_path, container)
        path_label.setToolTip(browser_path)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        browse_btn = QPushButton("...", container)
        browse_btn.setFixedWidth(32)
        browse_btn.setToolTip("Copy this session to another folder")
        browse_btn.clicked.connect(lambda _=False, sid=session_id: self._export_session_to_custom_folder(sid))

        layout.addWidget(path_label, 1)
        layout.addWidget(browse_btn, 0)
        self.session_table.setCellWidget(row, 5, container)

    def _default_browser_root(self) -> str:
        browser_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "session_browser"))
        os.makedirs(os.path.join(browser_root, "ranging"), exist_ok=True)
        os.makedirs(os.path.join(browser_root, "log"), exist_ok=True)
        return browser_root

    def set_viewmodel(self, vm):
        self._vm = vm
        self._vm.log_entry_added.connect(self._append_log_entry)
        self._vm.live_logs_cleared.connect(self._clear_live_log_view)
        self._vm.session_list_updated.connect(self._on_session_list_updated)
        self._vm.session_details_loaded.connect(self._on_session_details_loaded)
        self._vm.session_deleted.connect(self._on_session_deleted)
        if hasattr(self._vm, "set_developer_mode"):
            self._vm.set_developer_mode(self._is_developer)
        self._vm.refresh_sessions()

    def _append_log_entry(self, entry: dict):
        raw_line = entry.get("raw_line")
        if raw_line:
            line = str(raw_line)
            self._all_log_lines.append(line)
            self._log_entry_count = len(self._all_log_lines)
            self._apply_filter()
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)
            return

        timestamp = entry.get("timestamp", "")
        level = entry.get("level", "")
        message = entry.get("message", "")
        object_code = entry.get("object_code")
        object_text = f"0x{int(object_code):02X}" if object_code is not None else "--"
        line = f"[{timestamp}] [{level:<5}] [{object_text}] {message}".strip()
        self._all_log_lines.append(line)
        self._log_entry_count = len(self._all_log_lines)
        self._apply_filter()
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)

    def _clear_live_log_view(self):
        self._all_log_lines.clear()
        self._log_entry_count = 0
        self._filter_active = False
        self.log_text.clear()
        self.log_count.setText("0 entries")

    def _clear_log_session(self):
        if self._vm:
            self._vm.clear_log_session()

    def _start_log_session(self):
        if self._vm:
            self._vm.start_log_stream()

    def _update_host_packet_fields(self):
        packet_name = self.host_packet_select.currentData()
        visible_fields = {
            "none": set(),
            "ack": {"ack_seq", "ack_response"},
            "time_sync_set": {"unix_time_ms", "timezone_offset"},
            "host_transport_set": {"transport"},
            "log_data": {"log_type", "data"},
            "log_clear": {"log_type", "offset", "length"},
        }.get(packet_name, set())

        for key, (label, widget) in self._host_packet_param_rows.items():
            is_visible = key in visible_fields
            label.setVisible(is_visible)
            widget.setVisible(is_visible)

    def _send_selected_host_packet(self):
        if not self._vm:
            self.host_packet_status.setText("No ViewModel")
            return

        packet_name = self.host_packet_select.currentData()
        try:
            params = self._collect_host_packet_params(packet_name)
        except ValueError as exc:
            self.host_packet_status.setText(str(exc))
            return

        result = self._vm.send_host_log_packet(packet_name, **params)
        if result.get("ok"):
            self.host_packet_status.setText(f"Sent {packet_name} seq={result.get('seq')}")
        else:
            self.host_packet_status.setText(result.get("error", "Send failed"))

    def _collect_host_packet_params(self, packet_name):
        params = {"dst_addr": int(self.host_packet_dst.currentData())}

        if packet_name == "time_sync_set":
            unix_text = self.host_packet_unix_ms.text().strip()
            params["unix_time_ms"] = self._parse_optional_int(unix_text, "unix_time_ms")
            params["timezone_offset"] = self._parse_int_field(
                self.host_packet_timezone.text().strip() or "420",
                "timezone_offset",
            )
        elif packet_name == "ack":
            ack_text = self.host_packet_ack_seq.text().strip()
            if not ack_text:
                raise ValueError("ack_seq is required")
            params["ack_seq"] = self._parse_int_field(ack_text, "ack_seq")
            params["response"] = int(self.host_packet_ack_response.currentData())
        elif packet_name == "host_transport_set":
            params["transport"] = int(self.host_packet_transport.currentData())
        elif packet_name == "log_data":
            params["log_type"] = int(self.host_packet_log_type.currentData())
            params["data"] = self._parse_packet_data(self.host_packet_data.text())
        elif packet_name == "log_clear":
            params["log_type"] = int(self.host_packet_log_type.currentData())
            params["offset"] = self._parse_int_field(self.host_packet_offset.text().strip() or "0", "offset")
            params["length"] = self._parse_int_field(self.host_packet_length.text().strip() or "0", "length")

        return params

    def _parse_optional_int(self, text, field_name):
        if not text:
            return None
        return self._parse_int_field(text, field_name)

    def _parse_int_field(self, text, field_name):
        try:
            value = int(text, 0)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a number") from exc
        if value < 0:
            raise ValueError(f"{field_name} must be >= 0")
        return value

    def _parse_packet_data(self, text):
        raw = (text or "").strip()
        if not raw:
            return b""
        if raw.lower().startswith("text:"):
            return raw[5:].encode("utf-8")

        normalized = raw.replace(" ", "").replace(",", "").replace("_", "")
        try:
            return bytes.fromhex(normalized)
        except ValueError:
            return raw.encode("utf-8")

    def _on_session_list_updated(self, sessions):
        self.session_table.blockSignals(True)
        self.session_table.setRowCount(0)
        self._session_records = {}

        for row, s in enumerate(sessions):
            self.session_table.insertRow(row)
            self.session_table.setItem(row, 0, self._readonly_item(s["session_id"]))
            self.session_table.setItem(row, 1, self._readonly_item(s["start_time"]))
            self.session_table.setItem(row, 2, self._readonly_item(s["duration"]))
            
            # Giữ tương thích số tệp tin chi tiết của UI
            ranging_count = str(s.get("ranging_count", 1 if s["type"] == "RANGING" else 0))
            session_file_count = str(s.get("session_file_count", 0))
            self.session_table.setItem(row, 3, self._readonly_item(ranging_count))
            self.session_table.setItem(row, 4, self._readonly_item(session_file_count))
            self._set_browser_cell(row, s["session_id"], s.get("browser_path", ""))

            self._session_records[s["session_id"]] = s

        self.session_table.blockSignals(False)
        if sessions:
            self.session_table.selectRow(0)
            self._current_session_name = sessions[0]["session_id"]
            self._vm.load_session_detail(self._current_session_name, self._detail_mode)

    def _on_session_details_loaded(self, session_id, detail_type, data):
        if session_id != self._current_session_name or detail_type != self._detail_mode:
            return

        self.detail_table.clear()
        self._detail_rows = []
        
        if detail_type == "ranging":
            headers = ["Timestamp (ms)", "X (m)", "Y (m)", "Z (m)", "RMS (m)", "Ranging Files"]
            self.detail_table.setColumnCount(len(headers))
            self.detail_table.setHorizontalHeaderLabels(headers)
            self.detail_table.setRowCount(len(data))
            
            for row, p in enumerate(data):
                self.detail_table.setItem(row, 0, self._readonly_item(str(p["timestamp_ms"])))
                self.detail_table.setItem(row, 1, self._readonly_item(f"{p['x_m']:.3f}"))
                self.detail_table.setItem(row, 2, self._readonly_item(f"{p['y_m']:.3f}"))
                self.detail_table.setItem(row, 3, self._readonly_item(f"{p['z_m']:.3f}"))
                self.detail_table.setItem(row, 4, self._readonly_item(f"{p['rms_error_m']:.3f}"))
                self.detail_table.setItem(row, 5, self._readonly_item("positions.csv"))
                self._detail_rows.append({"file": f"{session_id}:positions.csv", **p})
            self.detail_table.setColumnWidth(5, 150)
        else:  # logs
            headers = ["Timestamp", "Level", "Source", "Message", "Log Files"]
            self.detail_table.setColumnCount(len(headers))
            self.detail_table.setHorizontalHeaderLabels(headers)
            self.detail_table.setRowCount(len(data))
            
            for row, l in enumerate(data):
                self.detail_table.setItem(row, 0, self._readonly_item(l["timestamp"]))
                self.detail_table.setItem(row, 1, self._readonly_item(l["level"]))
                self.detail_table.setItem(row, 2, self._readonly_item(l["source"]))
                self.detail_table.setItem(row, 3, self._readonly_item(l["message"]))
                self.detail_table.setItem(row, 4, self._readonly_item("logs.txt"))
                self._detail_rows.append({"file": f"{session_id}:logs.txt", **l})
            self.detail_table.setColumnWidth(4, 150)

        self.detail_title.setText(f"Session Details · {session_id}")
        self.detail_selection_label.setText(f"Loaded {len(data)} items")
        self._set_detail_actions_enabled(self._current_session_name is not None)

    def _on_session_deleted(self, session_id):
        if self._current_session_name == session_id:
            self._current_session_name = None
            self.detail_table.clear()
            self.detail_title.setText("Session Details")
            self.detail_selection_label.setText("No file selected")
            self._set_detail_actions_enabled(False)

    def set_developer_mode(self, enabled: bool):
        self._is_developer = enabled
        for w in self._dev_widgets:
            w.setVisible(enabled)
        if self._vm and hasattr(self._vm, "set_developer_mode"):
            self._vm.set_developer_mode(enabled)

    def _apply_filter(self, *_):
        level = self.filter_level.currentText().strip().upper()
        object_code = self.filter_source.currentData()
        query = self.search_edit.text().strip().lower()
        lines = self._all_log_lines

        filtered_lines = [
            line for line in lines
            if self._line_matches_level(line, level)
            and self._line_matches_object_code(line, object_code)
            and (not query or query in line.lower())
        ]

        self._filter_active = level != "ALL" or object_code is not None or bool(query)
        self._render_log_lines(filtered_lines)
        if self._filter_active:
            self.log_count.setText(f"{len(filtered_lines)} / {self._log_entry_count} entries")
        else:
            self.log_count.setText(f"{self._log_entry_count} entries")

    def _render_log_lines(self, lines):
        self.log_text.clear()
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for idx, line in enumerate(lines):
            fmt = QTextCharFormat()
            fmt.setForeground(self._line_color(line))
            cursor.insertText(line, fmt)
            if idx != len(lines) - 1:
                cursor.insertText("\n")
        self.log_text.setTextCursor(cursor)

    def _line_color(self, line: str) -> QColor:
        normalized = line.upper()
        if normalized.startswith("[POLL]"):
            return QColor("#22D3EE")
        if normalized.startswith(("[RX]", "[TX]", "[ACK]", "[CLEAR]")):
            return QColor("#94A3B8")
        if "[ERROR" in normalized:
            return QColor("#EF4444")
        if "[WARN" in normalized:
            return QColor("#F59E0B")
        if "[DEBUG" in normalized:
            return QColor("#22D3EE")
        if "[INFO" in normalized:
            return QColor("#F8FAFC")
        return QColor("#CBD5E1")

    def _line_matches_level(self, line, level):
        if level == "ALL":
            return True
        normalized = line.upper()
        return f"[{level}" in normalized or f" {level} " in normalized

    def _line_matches_object_code(self, line, object_code):
        if object_code is None:
            return True
        return f"0X{object_code:02X}" in line.upper()

    def _populate_virtual_history(self):
        if self.session_table.rowCount() > 0:
            return

        session_specs = [
            ("20260610", "093012", "ab12", "09:30:12", "11:35:23", "2h 05m 11s", "Closed", 2, 3),
            ("20260610", "131844", "f03c", "13:18:44", "--", "Active · 18m 42s", "Active", 1, 2),
            ("20260609", "170502", "c9e4", "17:05:02", "17:48:19", "43m 17s", "Closed", 3, 1),
            ("20260609", "101915", "77a1", "10:19:15", "10:24:43", "5m 28s", "Closed", 1, 1),
            ("20260608", "154030", "9b06", "15:40:30", "17:02:08", "1h 21m 38s", "Closed", 4, 3),
            ("20260608", "091155", "20df", "09:11:55", "09:28:10", "16m 15s", "Closed", 2, 2),
            ("20260607", "200005", "4e8a", "20:00:05", "20:02:52", "2m 47s", "Closed", 1, 1),
            ("20260607", "143512", "aa39", "14:35:12", "16:00:09", "1h 24m 57s", "Closed", 5, 2),
            ("20260606", "113322", "6d71", "11:33:22", "12:05:41", "32m 19s", "Closed", 2, 3),
            ("20260606", "082018", "e52b", "08:20:18", "08:23:33", "3m 15s", "Closed", 1, 1),
        ]

        self.session_table.setRowCount(len(session_specs))
        for row, spec in enumerate(session_specs):
            record = self._build_virtual_total_session(*spec)
            self._session_records[record["session"]] = record

            values = [
                record["session"],
                record["started"],
                record["elapsed"],
                str(len(record["ranging"])),
                str(len(record["ranging"]) + len(record["logs"])),
            ]
            for col, value in enumerate(values):
                self.session_table.setItem(row, col, self._readonly_item(value))
            self._set_browser_cell(row, record["session"], self._default_browser_root())
        if session_specs:
            first_session = self.session_table.item(0, 0).text()
            self.session_table.selectRow(0)
            self._load_session_detail(first_session)

    def _build_virtual_total_session(
        self,
        date_text,
        time_text,
        sid,
        started_time,
        ended_time,
        elapsed,
        status,
        ranging_count,
        log_count,
    ):
        session_name = f"session_{date_text}_{time_text}_{sid}"
        session_path = self._virtual_session_dir(session_name)
        date_display = f"{date_text[0:4]}-{date_text[4:6]}-{date_text[6:8]}"
        started = f"{date_display} {started_time}"
        ended = "--" if ended_time == "--" else f"{date_display} {ended_time}"

        ranging = [
            self._build_virtual_ranging_run(sid, date_display, started_time, run_idx, session_path)
            for run_idx in range(1, ranging_count + 1)
        ]
        logs = [
            self._build_virtual_log_file(sid, date_display, started_time, log_idx, session_path)
            for log_idx in range(1, log_count + 1)
        ]

        return {
            "sid": sid,
            "session": session_name,
            "started": started,
            "ended": ended,
            "elapsed": elapsed,
            "status": status,
            "path": session_path,
            "ranging": ranging,
            "logs": logs,
        }

    def _build_virtual_ranging_run(self, sid, date_display, started_time, run_idx, session_path):
        file_name = f"ranging_{sid}_run_{run_idx:03d}.csv"
        started_min = 12 + (run_idx - 1) * 18
        ended_min = started_min + 11 + run_idx
        return {
            "run": f"run_{run_idx:03d}",
            "started": f"{date_display} {self._time_with_offset(started_time, started_min, 0)}",
            "ended": f"{date_display} {self._time_with_offset(started_time, ended_min, 24)}",
            "elapsed": f"{11 + run_idx}m 24s",
            "samples": str(180 + run_idx * 64),
            "file": file_name,
            "path": os.path.join(session_path, "ranging", file_name),
        }

    def _build_virtual_log_file(self, sid, date_display, started_time, log_idx, session_path):
        devices = [
            ("anchor", "03", "0852"),
            ("tag", "05", "1044"),
            ("gateway", "01", "2107"),
        ]
        device_type, device_id, mcu_id = devices[(log_idx - 1) % len(devices)]
        file_name = f"log_{sid}_{device_type}_{device_id}_mcu_{mcu_id}.txt"
        return {
            "device": f"{device_type}_{device_id}",
            "mcu_id": mcu_id,
            "first_connected": f"{date_display} {started_time}",
            "last_seen": f"{date_display} {self._time_with_offset(started_time, 20 + log_idx * 7, 30)}",
            "lines": str(420 + log_idx * 95),
            "file": file_name,
            "path": os.path.join(session_path, "logs", file_name),
        }

    def _time_with_offset(self, base_time, offset_minutes, seconds):
        hour, minute, _ = [int(part) for part in base_time.split(":")]
        total_minutes = hour * 60 + minute + offset_minutes
        return f"{(total_minutes // 60) % 24:02d}:{total_minutes % 60:02d}:{seconds:02d}"

    def _virtual_session_dir(self, session_name):
        return os.path.join(
            os.getcwd(),
            "software",
            "uwb_rtls_studio",
            "sessions",
            session_name,
        )

    def _on_session_selection_changed(self):
        session_name = self._selected_session_name()
        if session_name:
            self._current_session_name = session_name
            self._set_detail_actions_enabled(True)
            if self._vm:
                self._vm.load_session_detail(session_name, self._detail_mode)
            else:
                self._load_session_detail(session_name)
        else:
            self._current_session_name = None
            self._set_detail_actions_enabled(False)

    def _selected_session_name(self):
        row = self.session_table.currentRow()
        item = self.session_table.item(row, 0) if row >= 0 else None
        return item.text() if item else None

    def _set_detail_mode(self, mode):
        self._detail_mode = mode
        if self._current_session_name:
            if self._vm:
                self._vm.load_session_detail(self._current_session_name, self._detail_mode)
            else:
                self._load_session_detail(self._current_session_name)

    def _load_session_detail(self, session_name):
        record = self._session_records.get(session_name)
        self._current_session_name = session_name if record else None
        self.detail_title.setText(f"Session Details · {session_name}" if record else "Session Details")
        self._detail_rows = list(record[self._detail_mode]) if record else []
        self._render_detail_table()

    def _render_detail_table(self):
        headers = self._detail_headers()
        self.detail_table.clear()
        self.detail_table.setColumnCount(len(headers))
        self.detail_table.setHorizontalHeaderLabels(headers)
        self.detail_table.setRowCount(len(self._detail_rows))

        for row, item in enumerate(self._detail_rows):
            for col, value in enumerate(self._detail_values(item)):
                self.detail_table.setItem(row, col, self._readonly_item(value))

        self.detail_table.setColumnWidth(len(headers) - 1, 150)

        self._set_detail_actions_enabled(self._current_session_name is not None)
        self.detail_selection_label.setText("No file selected")

    def _detail_headers(self):
        if self._detail_mode == "logs":
            return ["Device", "MCU ID", "First Connected", "Last Seen", "Lines", "File", "Log Files"]
        return ["Run", "Started", "Ended", "Elapsed", "Samples", "File", "Ranging Files"]

    def _detail_values(self, item):
        if self._detail_mode == "logs":
            return [
                item["device"],
                item["mcu_id"],
                item["first_connected"],
                item["last_seen"],
                item["lines"],
                item["file"],
                "logs.txt",
            ]
        return [
            item["run"],
            item["started"],
            item["ended"],
            item["elapsed"],
            item["samples"],
            item["file"],
            "positions.csv",
        ]

    def _selected_detail_item(self):
        row = self.detail_table.currentRow()
        if row < 0 or row >= len(self._detail_rows):
            return None
        return self._detail_rows[row]

    def _update_detail_selection_label(self):
        item = self._selected_detail_item()
        self._set_detail_actions_enabled(self._current_session_name is not None)
        self.detail_selection_label.setText(item["file"] if item else "No file selected")

    def _set_detail_actions_enabled(self, enabled):
        self.btn_detail_open.setEnabled(enabled)
        self.btn_detail_browse.setEnabled(enabled)
        self.btn_detail_remove.setEnabled(enabled)

    def _open_selected_detail(self):
        item = self._selected_detail_item()
        if item:
            self.detail_selection_label.setText(f"Selected: {item['file']}")
            if self._current_session_name:
                import os
                from repository.session_repository import SESSIONS_DIR
                session_path = os.path.join(SESSIONS_DIR, self._current_session_name)
                
                # Determine file path
                if self._detail_mode == "logs":
                    file_path = os.path.join(session_path, "log", item.get("file", ""))
                    if not os.path.exists(file_path):
                        file_path = os.path.join(session_path, item.get("file", ""))
                else:
                    file_path = os.path.join(session_path, "ranging", item.get("file", ""))
                    if not os.path.exists(file_path):
                        file_path = os.path.join(session_path, item.get("file", ""))
                
                if os.path.exists(file_path):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(session_path))
        else:
            if self._current_session_name:
                import os
                from repository.session_repository import SESSIONS_DIR
                session_path = os.path.join(SESSIONS_DIR, self._current_session_name)
                QDesktopServices.openUrl(QUrl.fromLocalFile(session_path))

    def _export_session_to_custom_folder(self, session_id: str):
        if not self._vm:
            self.detail_selection_label.setText("Export is available after a real session is loaded")
            return

        target_dir = QFileDialog.getExistingDirectory(
            self,
            "Choose folder to save session",
            os.path.expanduser("~"),
        )
        if not target_dir:
            return

        exported_path = self._vm.export_session_to(session_id, target_dir)
        if exported_path:
            self.detail_selection_label.setText(f"Exported: {exported_path}")
            QMessageBox.information(self, "Session Exported", f"Session saved to:\n{exported_path}")
        else:
            QMessageBox.warning(self, "Export Failed", f"Could not export session '{session_id}'.")

    def _browse_selected_detail(self):
        if self._vm:
            if self._current_session_name:
                import os
                from repository.session_repository import SESSIONS_DIR
                session_path = os.path.join(SESSIONS_DIR, self._current_session_name)
                self._browse_session_folder(session_path)
        else:
            item = self._selected_detail_item()
            if item:
                self._browse_session_folder(item["path"])

    def _remove_selected_detail(self):
        if self._vm:
            if self._current_session_name:
                from PyQt6.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    self, "Delete Session",
                    f"Are you sure you want to permanently delete session '{self._current_session_name}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._vm.delete_session(self._current_session_name)
        else:
            item = self._selected_detail_item()
            if not item or not self._current_session_name:
                return

            record = self._session_records.get(self._current_session_name)
            if record and item in record[self._detail_mode]:
                record[self._detail_mode].remove(item)
                self._update_total_session_counts(self._current_session_name)
                self._load_session_detail(self._current_session_name)

    def _browse_session_folder(self, session_dir):
        browse_path = session_dir
        while browse_path and not os.path.isdir(browse_path):
            parent_path = os.path.dirname(browse_path)
            if parent_path == browse_path:
                break
            browse_path = parent_path

        if browse_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(browse_path))

    def _update_total_session_counts(self, session_id):
        record = self._session_records.get(session_id)
        row = self._find_session_row(session_id)
        if not record or row < 0:
            return

        self.session_table.setItem(row, 3, self._readonly_item(str(len(record["ranging"]))))
        self.session_table.setItem(row, 4, self._readonly_item(str(len(record["ranging"]) + len(record["logs"]))))

    def _find_session_row(self, session_id):
        for row in range(self.session_table.rowCount()):
            item = self.session_table.item(row, 0)
            if item and item.text() == session_id:
                return row
        return -1
