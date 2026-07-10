import os
import queue
import threading
import time

import serial
from serial.tools import list_ports

import numpy as np
import pyqtgraph as pg
from PyQt5 import uic
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QComboBox, QLabel, QMainWindow

from ..config import (
    ANCHOR_POSITIONS,
    FUSION_FRAME_LENGTH_TO_SIZE,
    FUSION_FRAME_MIN_SIZE,
    MAX_SAMPLES,
    ROOM_SIZE_M,
    UART_BAUDRATE,
    UART_SOF,
    CSV_UKF_FUSION_FILENAME_PREFIX,
    CSV_UKF_FUSION_FILENAME_SUFFIX,
)
from ..module_parse_frame import parse_uart_fusion_frame
from ..module_csv import create_csv_file, generate_timestamp_filename, write_fusion_frame_to_csv


GROUND_TRUTH_HORIZONTAL_M = 2.8
GROUND_TRUTH_VERTICAL_M = 6
GROUND_TRUTH_START_1 = "start_1"
GROUND_TRUTH_START_2 = "start_2"
UKF_STEP_PREDICT = 0
UKF_STEP_UPDATE = 1


class FusionFrameThread(QThread):
    connected_signal = pyqtSignal(str)
    disconnected_signal = pyqtSignal()
    data_signal = pyqtSignal(dict)
    csv_created_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.running = True
        self.serial_port = None
        self.csv_file = None
        self.csv_writer = None
        self.create_csv_enabled = True
        self.new_csv_requested = False
        self._request_lock = threading.Lock()
        self._serial_reader = None
        self._rx_queue = queue.Queue()
        self._parsed_frames = 0
        self._bad_frames = 0
        self._last_tx_frame_cnt = None
        self._tx_gap_count = 0
        self._last_step_timestamp = {}
        self._stats_last_print = time.monotonic()

    def stop(self):
        self.running = False
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.cancel_read()
            except (AttributeError, serial.SerialException, OSError):
                pass
        if self._serial_reader and self._serial_reader.is_alive():
            self._serial_reader.join(timeout=1.0)
        self.wait()

    def request_new_csv(self):
        with self._request_lock:
            self.new_csv_requested = True

    def _take_new_csv_request(self):
        with self._request_lock:
            requested = self.new_csv_requested
            self.new_csv_requested = False
            return requested

    def _open_new_csv(self):
        self._close_csv()
        self._last_step_timestamp.clear()
        filename = generate_timestamp_filename(CSV_UKF_FUSION_FILENAME_PREFIX, CSV_UKF_FUSION_FILENAME_SUFFIX)
        self.csv_file, self.csv_writer = create_csv_file(filename)
        self.csv_created_signal.emit()
        print("[INFO] Started new fusion data csv...")

    def _close_csv(self):
        if self.csv_file:
            self.csv_file.flush()
            self.csv_file.close()
        self.csv_file = None
        self.csv_writer = None

    def _close_serial(self):
        if self.serial_port:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except serial.SerialException:
                pass
        self.serial_port = None

    def _serial_candidates(self):
        ignored_ports = {"COM1", "COM2"}
        candidates = []
        for port_info in list_ports.comports():
            device = (port_info.device or "").upper()
            if device in ignored_ports:
                continue
            text = " ".join(
                str(value or "")
                for value in (
                    port_info.device,
                    port_info.description,
                    port_info.manufacturer,
                    port_info.hwid,
                )
            ).upper()
            if "BLUETOOTH" in text:
                continue

            score = 0
            if "USB" in text or "VID:PID" in text:
                score += 100
            if port_info.vid is not None or port_info.pid is not None:
                score += 80
            if any(name in text for name in ("STM", "STLINK", "VCP", "CP210", "CH340", "CH910", "FTDI", "USB-SERIAL", "USB SERIAL")):
                score += 40
            candidates.append((score, port_info))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [port_info for _, port_info in candidates]

    def _open_serial_port(self, port_name):
        return serial.Serial(
            port=port_name,
            baudrate=UART_BAUDRATE,
            timeout=0.02,
            write_timeout=0,
        )

    def _probe_port_for_frame(self, serial_port, probe_seconds=0.8):
        deadline = time.monotonic() + probe_seconds
        probe_buffer = bytearray()
        while self.running and time.monotonic() < deadline:
            waiting = serial_port.in_waiting
            data = serial_port.read(waiting if waiting > 0 else 1)
            if data:
                probe_buffer.extend(data)
                frames = self._extract_frames(bytearray(probe_buffer))
                if frames:
                    return True, bytes(probe_buffer)
        return False, bytes(probe_buffer)

    def _connect_serial(self):
        candidates = self._serial_candidates()
        if not candidates:
            raise serial.SerialException("No USB serial COM port found")

        fallback_port_info = None
        for port_info in candidates:
            port_name = port_info.device
            print(f"[INFO] Probing {port_name}: {port_info.description}")
            try:
                serial_port = self._open_serial_port(port_name)
            except serial.SerialException as e:
                print(f"[WARNING] Cannot open {port_name}: {e}")
                continue

            if fallback_port_info is None:
                fallback_port_info = port_info

            matched, probe_data = self._probe_port_for_frame(serial_port)
            if matched:
                if probe_data:
                    self._rx_queue.put(probe_data)
                self.serial_port = serial_port
                self.connected_signal.emit(port_name)
                print(f"[INFO] Connected to fusion frame stream on {port_name} at {UART_BAUDRATE} baud")
                return

            serial_port.close()

        port_name = fallback_port_info.device
        self.serial_port = self._open_serial_port(port_name)
        self.connected_signal.emit(port_name)
        print(f"[INFO] Connected to best USB candidate {port_name} at {UART_BAUDRATE} baud")

    def _reader_loop(self):
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                waiting = self.serial_port.in_waiting
                data = self.serial_port.read(waiting if waiting > 0 else 1)
                if data:
                    self._rx_queue.put(data)
            except (serial.SerialException, OSError) as e:
                port_name = self.serial_port.port if self.serial_port else "USB serial"
                print(f"[WARNING] Serial reader stopped on {port_name}: {e}")
                break

    def _start_reader(self):
        self._serial_reader = threading.Thread(
            target=self._reader_loop,
            name="fusion-frame-serial-reader",
            daemon=True,
        )
        self._serial_reader.start()

    def _drain_rx_queue(self, buffer, max_chunks=512):
        drained = 0
        while drained < max_chunks:
            try:
                buffer.extend(self._rx_queue.get_nowait())
                drained += 1
            except queue.Empty:
                break
        return drained

    def _extract_frames(self, buffer):
        frames = []
        while len(buffer) >= FUSION_FRAME_MIN_SIZE:
            sof_idx = buffer.find(bytes((UART_SOF,)))
            if sof_idx < 0:
                del buffer[:]
                break
            if sof_idx > 0:
                del buffer[:sof_idx]
            if len(buffer) < 2:
                break

            frame_size = FUSION_FRAME_LENGTH_TO_SIZE.get(buffer[1])
            if frame_size is None:
                self._bad_frames += 1
                del buffer[0]
                continue
            if len(buffer) < frame_size:
                break

            frame_bytes = bytes(buffer[:frame_size])
            frame_data = parse_uart_fusion_frame(frame_bytes)
            if frame_data is None:
                self._bad_frames += 1
                del buffer[0]
                continue
            frames.append(frame_data)
            del buffer[:frame_size]
        return frames

    def _track_tx_gap(self, tx_frame_cnt):
        if self._last_tx_frame_cnt is None:
            self._last_tx_frame_cnt = tx_frame_cnt
            return
        expected = self._last_tx_frame_cnt + 1
        if tx_frame_cnt != expected and tx_frame_cnt > self._last_tx_frame_cnt:
            gap = tx_frame_cnt - expected
            self._tx_gap_count += gap
            print(f"[WARNING] STM frame counter gap: expected {expected}, got {tx_frame_cnt}, missing {gap}")
        self._last_tx_frame_cnt = tx_frame_cnt

    def _print_stats(self):
        now = time.monotonic()
        if now - self._stats_last_print < 2.0:
            return
        self._stats_last_print = now
        print(
            "[STATS] "
            f"parsed={self._parsed_frames} rx_queue={self._rx_queue.qsize()} "
            f"bad_frames={self._bad_frames} tx_gaps={self._tx_gap_count}"
        )

    def run(self):
        print("=" * 60)
        print("UART Fusion Frame Plot Receiver")
        print("=" * 60)

        buffer = bytearray()
        frame_count = 0
        while self.running:
            if self._take_new_csv_request() and self.create_csv_enabled:
                self._open_new_csv()
                frame_count = 0

            if (
                self.serial_port is None
                or not self.serial_port.is_open
                or self._serial_reader is None
                or not self._serial_reader.is_alive()
            ):
                self._close_serial()
                self.disconnected_signal.emit()
                try:
                    self._connect_serial()
                    if self.create_csv_enabled and self.csv_file is None:
                        self._open_new_csv()
                    self._start_reader()
                except serial.SerialException as e:
                    print(f"[WARNING] Failed to auto-connect USB serial: {e}")
                    time.sleep(1.0)
                    continue

            drained = self._drain_rx_queue(buffer)
            if drained == 0:
                self._print_stats()
                time.sleep(0.001)
                continue

            for frame_data in self._extract_frames(buffer):
                frame_count += 1
                self._parsed_frames += 1
                self._track_tx_gap(frame_data["tx_frame_cnt"])
                ukf_step = int(frame_data.get("ukf_step", -1))
                now = time.monotonic()
                previous_step_time = self._last_step_timestamp.get(ukf_step)
                frame_data["dt"] = 0.0 if previous_step_time is None else now - previous_step_time
                self._last_step_timestamp[ukf_step] = now
                if self.csv_writer is not None:
                    write_fusion_frame_to_csv(self.csv_writer, frame_data, frame_count)
                if self.csv_file is not None and frame_count % 25 == 0:
                    self.csv_file.flush()
                self.data_signal.emit(frame_data)

            self._print_stats()

        self._close_serial()
        self._close_csv()


class FusionFrameWindow(QMainWindow):
    def __init__(self):
        super(FusionFrameWindow, self).__init__()

        ui_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ukf_log.ui",
        )
        uic.loadUi(ui_path, self)
        self.setWindowTitle("UART Fusion Frame Plot")

        self.graph_pos.setBackground("w")
        self.graph_pos.showGrid(x=True, y=True)
        self.graph_pos.setXRange(-1, ROOM_SIZE_M + 1)
        self.graph_pos.setYRange(-1, ROOM_SIZE_M + 1)
        self.graph_pos.setLabel("left", "Y (m)")
        self.graph_pos.setLabel("bottom", "X (m)")
        self.graph_pos.addLegend()

        self.graph_d.setBackground("w")
        self.graph_d.showGrid(x=True, y=True)
        self.graph_d.setLabel("left", "Yaw (deg)")
        self.graph_d.setLabel("bottom", "Frame")
        self.graph_d.addLegend()

        self.plot_ukf_predict = self.graph_pos.plot(
            pen=None,
            symbol="o",
            symbolSize=5,
            symbolPen=pg.mkPen((20, 90, 210)),
            symbolBrush=pg.mkBrush((20, 90, 210, 150)),
            name="UKF predict step=0",
        )
        self.plot_ukf_update = self.graph_pos.plot(
            pen=None,
            symbol="o",
            symbolSize=6,
            symbolPen=pg.mkPen((210, 70, 20)),
            symbolBrush=pg.mkBrush((210, 70, 20, 180)),
            name="UKF update step=1",
        )
        self.plot_tril = self.graph_pos.plot(
            pen=None,
            symbol="o",
            symbolSize=5,
            symbolPen=pg.mkPen((0, 150, 0, 110)),
            symbolBrush=pg.mkBrush((0, 180, 0, 70)),
            name="UWB Trilateration",
        )
        self.plot_ground_truth = self.graph_pos.plot(
            pen=pg.mkPen((230, 116, 37), width=2.0, style=Qt.DashLine),
            name="Ground truth",
        )
        self.ground_truth_start_markers = pg.ScatterPlotItem(
            size=16,
            pen=pg.mkPen((160, 70, 20), width=1.5),
            brush=pg.mkBrush((240, 112, 48)),
            symbol="o",
        )
        self.graph_pos.addItem(self.ground_truth_start_markers)
        self.ground_truth_labels = []
        self.plot_ukf_yaw = self.graph_d.plot(
            pen=None,
            symbol="o",
            symbolSize=5,
            symbolPen=pg.mkPen("b"),
            symbolBrush=pg.mkBrush("b"),
            name="ukf_yaw",
        )
        self.plot_yaw = self.graph_d.plot(
            pen=None,
            symbol="o",
            symbolSize=5,
            symbolPen=pg.mkPen("g"),
            symbolBrush=pg.mkBrush("g"),
            name="yaw",
        )

        for idx, anchor in enumerate(ANCHOR_POSITIONS, start=1):
            self.graph_pos.addItem(
                pg.ScatterPlotItem([anchor[0]], [anchor[1]], size=15, pen=pg.mkPen(None), brush=pg.mkBrush("k"), symbol="t")
            )
            text = pg.TextItem(f"A{idx}", color="k")
            text.setPos(anchor[0], anchor[1])
            self.graph_pos.addItem(text)

        self.ukf_xs, self.ukf_ys, self.ukf_steps = [], [], []
        self.ukf_predict_xs, self.ukf_predict_ys = [], []
        self.ukf_update_xs, self.ukf_update_ys = [], []
        self.tril_xs, self.tril_ys = [], []
        self.yaw_frame_idxs = []
        self.ukf_yaws, self.yaws = [], []
        self.frame_idx = 0
        self.ground_truth_start = None
        self.ground_truth_start_kind = GROUND_TRUTH_START_1
        self.latest_data = None

        self._setup_ground_truth_controls()
        self.pushButton_clearGraph.clicked.connect(self.clear_graph)
        if hasattr(self, "checkBox_createCsv"):
            self.checkBox_createCsv.stateChanged.connect(self.on_checkbox_csv_changed)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(30)

        self.thread = FusionFrameThread()
        if hasattr(self, "checkBox_createCsv"):
            self.thread.create_csv_enabled = self.checkBox_createCsv.isChecked()
        self.thread.connected_signal.connect(self.on_connected)
        self.thread.disconnected_signal.connect(self.on_disconnected)
        self.thread.data_signal.connect(self.on_data)
        self.thread.csv_created_signal.connect(self.on_csv_created)
        self.thread.start()

    def _setup_ground_truth_controls(self):
        self.groundTruthStartLabel = QLabel("Ground truth start")
        self.comboBox_groundTruthStart = QComboBox()
        self.comboBox_groundTruthStart.addItem("start 1", GROUND_TRUTH_START_1)
        self.comboBox_groundTruthStart.addItem("start 2", GROUND_TRUTH_START_2)
        self.comboBox_groundTruthStart.currentIndexChanged.connect(self.on_ground_truth_start_changed)

        if hasattr(self, "gridLayout_4"):
            row = self.gridLayout_4.rowCount()
            self.gridLayout_4.addWidget(self.groundTruthStartLabel, row, 0)
            self.gridLayout_4.addWidget(self.comboBox_groundTruthStart, row + 1, 0)

    def _ground_truth_points(self):
        if self.ground_truth_start is None:
            return [], []

        start_x, start_y = self.ground_truth_start
        if self.ground_truth_start_kind == GROUND_TRUTH_START_2:
            points = [
                (start_x, start_y),
                (start_x - GROUND_TRUTH_HORIZONTAL_M, start_y),
                (start_x - GROUND_TRUTH_HORIZONTAL_M, start_y - GROUND_TRUTH_VERTICAL_M),
                (start_x - 2.0 * GROUND_TRUTH_HORIZONTAL_M, start_y - GROUND_TRUTH_VERTICAL_M),
            ]
        else:
            points = [
                (start_x, start_y),
                (start_x + GROUND_TRUTH_HORIZONTAL_M, start_y),
                (start_x + GROUND_TRUTH_HORIZONTAL_M, start_y + GROUND_TRUTH_VERTICAL_M),
                (start_x + 2.0 * GROUND_TRUTH_HORIZONTAL_M, start_y + GROUND_TRUTH_VERTICAL_M),
            ]

        return [point[0] for point in points], [point[1] for point in points]

    def _clear_ground_truth_labels(self):
        for label in self.ground_truth_labels:
            self.graph_pos.removeItem(label)
        self.ground_truth_labels.clear()

    def _update_ground_truth_plot(self):
        xs, ys = self._ground_truth_points()
        self.plot_ground_truth.setData(xs, ys)
        self.ground_truth_start_markers.setData([], [])
        self._clear_ground_truth_labels()

        if not xs:
            return

        self.ground_truth_start_markers.setData([xs[0], xs[-1]], [ys[0], ys[-1]])
        start_labels = ("start 2", "start 1") if self.ground_truth_start_kind == GROUND_TRUTH_START_2 else ("start 1", "start 2")
        for text, x, y in ((start_labels[0], xs[0], ys[0]), (start_labels[1], xs[-1], ys[-1])):
            label = pg.TextItem(text, color=(120, 60, 20), anchor=(0.5, -0.2))
            label.setPos(x, y)
            self.graph_pos.addItem(label)
            self.ground_truth_labels.append(label)

    def on_ground_truth_start_changed(self, *_):
        self.ground_truth_start_kind = self.comboBox_groundTruthStart.currentData()
        self._update_ground_truth_plot()

    def on_checkbox_csv_changed(self, state):
        if hasattr(self, "thread") and self.thread is not None:
            self.thread.create_csv_enabled = (state == Qt.Checked)

    def on_csv_created(self):
        if hasattr(self, "checkBox_createCsv"):
            self.checkBox_createCsv.setChecked(False)

    def clear_graph(self):
        self.ukf_xs.clear()
        self.ukf_ys.clear()
        self.ukf_steps.clear()
        self.ukf_predict_xs.clear()
        self.ukf_predict_ys.clear()
        self.ukf_update_xs.clear()
        self.ukf_update_ys.clear()
        self.tril_xs.clear()
        self.tril_ys.clear()
        self.yaw_frame_idxs.clear()
        self.ukf_yaws.clear()
        self.yaws.clear()
        self.frame_idx = 0
        self.ground_truth_start = None
        self.latest_data = None
        self.plot_ukf_predict.setData([], [])
        self.plot_ukf_update.setData([], [])
        self.plot_tril.setData([], [])
        self.plot_ukf_yaw.setData([])
        self.plot_yaw.setData([])
        self._update_ground_truth_plot()
        if hasattr(self, "thread") and self.thread is not None:
            if hasattr(self, "checkBox_createCsv") and self.checkBox_createCsv.isChecked():
                self.thread.request_new_csv()

    @pyqtSlot(str)
    def on_connected(self, port):
        self.lineEdit_COM.setText(f"Connected: {port}")
        self.lineEdit_COM.setStyleSheet("background-color: lightgreen;")

    @pyqtSlot()
    def on_disconnected(self):
        self.lineEdit_COM.setText("Waiting for USB COM...")
        self.lineEdit_COM.setStyleSheet("background-color: yellow;")

    @pyqtSlot(dict)
    def on_data(self, data):
        self.latest_data = data
        self.frame_idx += 1
        if self.ground_truth_start is None:
            self.ground_truth_start = (data["tril_x"], data["tril_y"])
            self._update_ground_truth_plot()

        self.ukf_xs.append(data["ukf_x"])
        self.ukf_ys.append(data["ukf_y"])
        ukf_step = int(data.get("ukf_step", -1))
        self.ukf_steps.append(ukf_step)
        if ukf_step == UKF_STEP_PREDICT:
            self.ukf_predict_xs.append(data["ukf_x"])
            self.ukf_predict_ys.append(data["ukf_y"])
        elif ukf_step == UKF_STEP_UPDATE:
            self.ukf_update_xs.append(data["ukf_x"])
            self.ukf_update_ys.append(data["ukf_y"])
        self.tril_xs.append(data["tril_x"])
        self.tril_ys.append(data["tril_y"])
        if ukf_step != UKF_STEP_UPDATE:
            self.yaw_frame_idxs.append(self.frame_idx)
            self.ukf_yaws.append(data["ukf_yaw"])
            self.yaws.append(data["yaw"])

        if len(self.ukf_xs) > MAX_SAMPLES:
            old_ukf_step = self.ukf_steps.pop(0)
            self.ukf_xs.pop(0)
            self.ukf_ys.pop(0)
            if old_ukf_step == UKF_STEP_PREDICT and self.ukf_predict_xs:
                self.ukf_predict_xs.pop(0)
                self.ukf_predict_ys.pop(0)
            elif old_ukf_step == UKF_STEP_UPDATE and self.ukf_update_xs:
                self.ukf_update_xs.pop(0)
                self.ukf_update_ys.pop(0)
            self.tril_xs.pop(0)
            self.tril_ys.pop(0)
            if old_ukf_step != UKF_STEP_UPDATE and self.yaw_frame_idxs:
                self.yaw_frame_idxs.pop(0)
                self.ukf_yaws.pop(0)
                self.yaws.pop(0)

    def _update_ukf_position_plot(self):
        self.plot_ukf_predict.setData(self.ukf_predict_xs, self.ukf_predict_ys)
        self.plot_ukf_update.setData(self.ukf_update_xs, self.ukf_update_ys)

    def update_gui(self):
        if self.ukf_xs:
            self._update_ukf_position_plot()
            self.plot_tril.setData(self.tril_xs, self.tril_ys)
            self.plot_ukf_yaw.setData(self.yaw_frame_idxs, self.ukf_yaws)
            self.plot_yaw.setData(self.yaw_frame_idxs, self.yaws)

        if self.latest_data is None:
            return

        data = self.latest_data
        self.lineEdit_stm_px.setText(f"{data['tril_x']:.3f}")
        self.lineEdit_stm_py.setText(f"{data['tril_y']:.3f}")
        self.lineEdit_ukf_px.setText(f"{data['ukf_x']:.3f}")
        self.lineEdit_ukf_py.setText(f"{data['ukf_y']:.3f}")
        self.lineEdit_ukf_vx.setText("0.000")
        self.lineEdit_ukf_vy.setText("0.000")
        self.lineEdit_ukf_yaw.setText(f"{data['ukf_yaw']:.3f}")
        self.lineEdit_yaw.setText(f"{data['yaw']:.3f}")
        self.lineEdit_err.setText(str(data["err_cnt"]))
        if hasattr(self, "lineEdit_mask"):
            self.lineEdit_mask.setText(str(data.get("anchor_mask", 0)))

    def closeEvent(self, event):
        self.thread.stop()
        event.accept()
