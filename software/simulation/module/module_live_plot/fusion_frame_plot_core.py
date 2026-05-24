import os
import queue
import threading
import time

import serial
from serial.tools import list_ports

import numpy as np
import pyqtgraph as pg
from PyQt5 import uic
from PyQt5.QtCore import QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QMainWindow

from ..config import (
    ANCHOR_POSITIONS,
    FUSION_FRAME_SIZE,
    MAX_SAMPLES,
    ROOM_SIZE_M,
    UART_BAUDRATE,
    UART_SOF,
)
from ..module_parse_frame import parse_uart_fusion_frame


class FusionFrameThread(QThread):
    connected_signal = pyqtSignal(str)
    disconnected_signal = pyqtSignal()
    data_signal = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.running = True
        self.serial_port = None
        self._serial_reader = None
        self._rx_queue = queue.Queue()
        self._parsed_frames = 0
        self._bad_frames = 0
        self._last_tx_frame_cnt = None
        self._tx_gap_count = 0
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
        while len(buffer) >= FUSION_FRAME_SIZE:
            sof_idx = buffer.find(bytes((UART_SOF,)))
            if sof_idx < 0:
                del buffer[:]
                break
            if sof_idx > 0:
                del buffer[:sof_idx]
            if len(buffer) < FUSION_FRAME_SIZE:
                break

            frame_bytes = bytes(buffer[:FUSION_FRAME_SIZE])
            frame_data = parse_uart_fusion_frame(frame_bytes)
            if frame_data is None:
                self._bad_frames += 1
                del buffer[0]
                continue
            frames.append(frame_data)
            del buffer[:FUSION_FRAME_SIZE]
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
        while self.running:
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
                self._parsed_frames += 1
                self._track_tx_gap(frame_data["tx_frame_cnt"])
                self.data_signal.emit(frame_data)

            self._print_stats()

        self._close_serial()


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

        self.plot_ukf = self.graph_pos.plot(pen=pg.mkPen("b", width=2.5), name="UKF")
        self.plot_tril = self.graph_pos.plot(pen=pg.mkPen("g", width=2.0), name="Trilateration")
        self.plot_ukf_yaw = self.graph_d.plot(pen=pg.mkPen("b", width=1.5), name="ukf_yaw")
        self.plot_yaw = self.graph_d.plot(pen=pg.mkPen("g", width=1.5), name="yaw")

        for idx, anchor in enumerate(ANCHOR_POSITIONS, start=1):
            self.graph_pos.addItem(
                pg.ScatterPlotItem([anchor[0]], [anchor[1]], size=15, pen=pg.mkPen(None), brush=pg.mkBrush("k"), symbol="t")
            )
            text = pg.TextItem(f"A{idx}", color="k")
            text.setPos(anchor[0], anchor[1])
            self.graph_pos.addItem(text)

        self.ukf_xs, self.ukf_ys = [], []
        self.tril_xs, self.tril_ys = [], []
        self.ukf_yaws, self.yaws = [], []
        self.latest_data = None

        self.pushButton_clearGraph.clicked.connect(self.clear_graph)
        if hasattr(self, "checkBox_createCsv"):
            self.checkBox_createCsv.setChecked(False)
            self.checkBox_createCsv.setEnabled(False)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(30)

        self.thread = FusionFrameThread()
        self.thread.connected_signal.connect(self.on_connected)
        self.thread.disconnected_signal.connect(self.on_disconnected)
        self.thread.data_signal.connect(self.on_data)
        self.thread.start()

    def clear_graph(self):
        self.ukf_xs.clear()
        self.ukf_ys.clear()
        self.tril_xs.clear()
        self.tril_ys.clear()
        self.ukf_yaws.clear()
        self.yaws.clear()
        self.latest_data = None
        self.plot_ukf.setData([], [])
        self.plot_tril.setData([], [])
        self.plot_ukf_yaw.setData([])
        self.plot_yaw.setData([])

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
        self.ukf_xs.append(data["ukf_x"])
        self.ukf_ys.append(data["ukf_y"])
        self.tril_xs.append(data["tril_x"])
        self.tril_ys.append(data["tril_y"])
        self.ukf_yaws.append(data["ukf_yaw"])
        self.yaws.append(data["yaw"])

        if len(self.ukf_xs) > MAX_SAMPLES:
            self.ukf_xs.pop(0)
            self.ukf_ys.pop(0)
            self.tril_xs.pop(0)
            self.tril_ys.pop(0)
            self.ukf_yaws.pop(0)
            self.yaws.pop(0)

    def update_gui(self):
        if self.ukf_xs:
            self.plot_ukf.setData(self.ukf_xs, self.ukf_ys)
            self.plot_tril.setData(self.tril_xs, self.tril_ys)
            self.plot_ukf_yaw.setData(self.ukf_yaws)
            self.plot_yaw.setData(self.yaws)

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
