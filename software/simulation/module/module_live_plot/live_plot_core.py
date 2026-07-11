import sys
import serial
from serial.tools import list_ports
import time
import os
import numpy as np
import threading
import queue

from PyQt5.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QLabel, QMainWindow
from PyQt5.QtCore import QThread, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtWidgets import QGraphicsRectItem
from PyQt5 import uic
import pyqtgraph as pg

from ..config import (
    UART_BAUDRATE, LIVE_FRAME_SIZE, UART_SOF,
    PRINT_DATA, MAX_SAMPLES, ROOM_SIZE_M, ANCHOR_POSITIONS, IMUSample, CSV_UKF_FILENAME_SUFFIX, CSV_UKF_FILENAME_PREFIX,
    GROUND_TRUTH_D1, GROUND_TRUTH_D2, GROUND_TRUTH_D3, GROUND_TRUTH_D4
)
from ..module_parse_frame import parse_live_frame
from ..module_csv import generate_timestamp_filename, create_csv_file, write_frame_to_csv, print_frame_data
from ..module_ukf import create_ukf_context, ukf_predict, ukf_update, normalize_angle
from ..module_kinematic import trilateration_2d
from ..config import DRAW_RECTANGLE, RECT_WIDTH, RECT_HEIGHT

DISTANCE_GRAPH_SLIDING_WINDOW = 100
GROUND_TRUTH_HORIZONTAL_M = 2.8
GROUND_TRUTH_VERTICAL_M = 6
GROUND_TRUTH_START_1 = "start_1"
GROUND_TRUTH_START_2 = "start_2"


class DataThread(QThread):
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
        
        # State variables
        self.ukf_ctx = None
        self.imu_bias_ax = 0.0
        self.imu_bias_ay = 0.0
        self.imu_bias_gz = 0.0
        
        self.imu_x = 0.0
        self.imu_y = 0.0
        self.imu_vx = 0.0
        self.imu_vy = 0.0
        self.imu_theta = 0.0
        self.initial_yaw_rad = 0.0
        
        self.ax_ema = 0.0
        self.ay_ema = 0.0
        
        self.zupt_counter = 0
        
        self.prev_uwb_pos = (0.0, 0.0)
        self.prev_tril_pos = (0.0, 0.0)
        self.prev_distances = None
        self.last_active_mask = 0
        self.new_csv_requested = False
        self.filter_reset_requested = False
        self.create_csv_enabled = True
        self._serial_reader = None
        self._rx_queue = queue.Queue()
        self._request_lock = threading.Lock()
        self._stats_last_print = time.monotonic()
        self._parsed_frames = 0
        self._bad_frames = 0
        self._dropped_chunks = 0
        self._last_tx_frame_cnt = None
        self._tx_gap_count = 0
        self._zero_distance_counts = [0, 0, 0, 0]
        self._last_gui_emit = 0.0

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

    def request_filter_reset(self):
        with self._request_lock:
            self.filter_reset_requested = True

    def _take_new_csv_request(self):
        with self._request_lock:
            requested = self.new_csv_requested
            self.new_csv_requested = False
            return requested

    def _take_filter_reset_request(self):
        with self._request_lock:
            requested = self.filter_reset_requested
            self.filter_reset_requested = False
            return requested

    def _reset_filter_state(self):
        self.ukf_ctx = None
        self.imu_x = 0.0
        self.imu_y = 0.0
        self.imu_vx = 0.0
        self.imu_vy = 0.0
        self.imu_theta = normalize_angle(self.initial_yaw_rad)
        self.ax_ema = 0.0
        self.ay_ema = 0.0
        self.zupt_counter = 0
        self.prev_uwb_pos = (0.0, 0.0)
        self.prev_tril_pos = (0.0, 0.0)
        self.last_active_mask = 0

    def _open_new_csv(self):
        if self.csv_file:
            self.csv_file.flush()
            self.csv_file.close()
        filename = generate_timestamp_filename(CSV_UKF_FILENAME_PREFIX, CSV_UKF_FILENAME_SUFFIX)
        self.csv_file, self.csv_writer = create_csv_file(filename)
        self.prev_distances = None
        self.csv_created_signal.emit()
        print("[INFO] Started new data collection csv...")

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

    def _probe_port_for_live_frame(self, serial_port, probe_seconds=0.8):
        deadline = time.monotonic() + probe_seconds
        probe_buffer = bytearray()
        while self.running and time.monotonic() < deadline:
            waiting = serial_port.in_waiting
            data = serial_port.read(waiting if waiting > 0 else 1)
            if data:
                probe_buffer.extend(data)
                frames = self._extract_live_frames(bytearray(probe_buffer))
                if frames:
                    return True, bytes(probe_buffer)
        return False, bytes(probe_buffer)

    def _open_serial_port(self, port_name):
        return serial.Serial(
            port=port_name,
            baudrate=UART_BAUDRATE,
            timeout=0.02,
            write_timeout=0
        )

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

            matched, probe_data = self._probe_port_for_live_frame(serial_port)
            if matched:
                if probe_data:
                    self._rx_queue.put(probe_data)
                self.serial_port = serial_port
                self.connected_signal.emit(port_name)
                print(f"[INFO] Connected to live UWB stream on {port_name} at {UART_BAUDRATE} baud")
                return

            serial_port.close()

        if fallback_port_info is None:
            raise serial.SerialException("No usable USB serial COM port found")

        port_name = fallback_port_info.device
        self.serial_port = self._open_serial_port(port_name)
        self.connected_signal.emit(port_name)
        print(f"[INFO] Connected to best USB candidate {port_name} at {UART_BAUDRATE} baud")

    def _reader_loop(self):
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                waiting = self.serial_port.in_waiting
                data = self.serial_port.read(waiting if waiting > 0 else 1)
                if not data:
                    continue
                try:
                    self._rx_queue.put(data, timeout=0.05)
                except queue.Full:
                    self._dropped_chunks += 1
                    print("[WARNING] RX queue full; host cannot keep up with STM stream")
            except (serial.SerialException, OSError) as e:
                port_name = self.serial_port.port if self.serial_port else "USB serial"
                print(f"[WARNING] Serial reader stopped on {port_name}: {e}")
                break

    def _start_reader(self):
        self._serial_reader = threading.Thread(
            target=self._reader_loop,
            name="ukf-serial-reader",
            daemon=True
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

    def _extract_live_frames(self, buffer):
        frames = []
        while len(buffer) >= LIVE_FRAME_SIZE:
            sof_idx = buffer.find(bytes((UART_SOF,)))
            if sof_idx < 0:
                del buffer[:]
                break
            if sof_idx > 0:
                del buffer[:sof_idx]
            if len(buffer) < LIVE_FRAME_SIZE:
                break

            frame_bytes = bytes(buffer[:LIVE_FRAME_SIZE])
            frame_data = parse_live_frame(frame_bytes)
            if frame_data is None:
                self._bad_frames += 1
                del buffer[0]
                continue
            frames.append(frame_data)
            del buffer[:LIVE_FRAME_SIZE]
        return frames

    def _classify_frame_status(self, frame_data):
        if frame_data['tx_frame_cnt'] == 1:
            self.prev_distances = frame_data['distances'].copy()
            return "Init"

        from ..module_csv import PREDICT_THRESHOLD

        status = "Predict"
        all_distances_zero = all(abs(d) < 1e-6 for d in frame_data['distances'])
        if not all_distances_zero and self.prev_distances is not None:
            for i in range(len(frame_data['distances'])):
                if abs(frame_data['distances'][i] - self.prev_distances[i]) > PREDICT_THRESHOLD:
                    status = "Update"
                    break
        self.prev_distances = frame_data['distances'].copy()
        return status

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

    def _track_zero_distances(self, distances):
        for idx, distance in enumerate(distances[:len(self._zero_distance_counts)]):
            if abs(distance) <= 1e-6:
                self._zero_distance_counts[idx] += 1

    def _print_stats(self, frame_count):
        now = time.monotonic()
        if now - self._stats_last_print < 2.0:
            return
        self._stats_last_print = now
        print(
            "[STATS] "
            f"csv_frames={frame_count} parsed={self._parsed_frames} "
            f"rx_queue={self._rx_queue.qsize()} bad_frames={self._bad_frames} "
            f"tx_gaps={self._tx_gap_count} dropped_chunks={self._dropped_chunks} "
            f"zero_distances={self._zero_distance_counts}"
        )

    def process_frame(self, frame_data, status):
        if status == "Init" or self.ukf_ctx is None:
            self.imu_bias_ax = frame_data['ax']
            self.imu_bias_ay = frame_data['ay']
            self.imu_bias_gz = frame_data['gz']
            
            self.ax_ema = 0.0
            self.ay_ema = 0.0
            self.zupt_counter = 0
            
            init_x = frame_data['px'] if frame_data['px'] else 0.0
            init_y = frame_data['py'] if frame_data['py'] else 0.0
            
            self.imu_x = init_x
            self.imu_y = init_y
            self.imu_vx = 0.0
            self.imu_vy = 0.0
            init_yaw = normalize_angle(self.initial_yaw_rad)
            self.imu_theta = init_yaw
            
            self.prev_uwb_pos = (init_x, init_y)
            self.prev_tril_pos = (init_x, init_y)
            
            ukf_initial_state = np.array([
                init_x, init_y, 0.0, 0.0, init_yaw,
                self.imu_bias_ax, self.imu_bias_ay, self.imu_bias_gz
            ])
            self.ukf_ctx = create_ukf_context(ukf_initial_state)
            return {
                'type': 'Init', 
                'x': init_x, 
                'y': init_y, 
                'yaw': init_yaw,
                'dt': 0.0,
                'mask': frame_data.get('anchor_mask', frame_data.get('mask', 0)),
                'zero_distance_counts': self._zero_distance_counts.copy()
            }
            
        if self.ukf_ctx is None:
            return None
            
        dt = frame_data['dt']
        
        ax_raw = frame_data['ax']
        ay_raw = frame_data['ay']
        
        ax_no_bias = ax_raw - self.imu_bias_ax
        ay_no_bias = ay_raw - self.imu_bias_ay
        
        from ..config import IMU_EMA_ALPHA, IMU_ZUPT_THRESHOLD, IMU_ZUPT_FRAMES
        alpha = IMU_EMA_ALPHA
        self.ax_ema = alpha * ax_no_bias + (1 - alpha) * self.ax_ema
        self.ay_ema = alpha * ay_no_bias + (1 - alpha) * self.ay_ema
        
        if abs(ax_no_bias) < IMU_ZUPT_THRESHOLD and abs(ay_no_bias) < IMU_ZUPT_THRESHOLD:
            self.zupt_counter += 1
        else:
            self.zupt_counter = 0
            
        ax_in = ax_no_bias
        ay_in = ay_no_bias
        
        if self.zupt_counter >= IMU_ZUPT_FRAMES:
            ax_in = 0.0
            ay_in = 0.0
            self.ax_ema = 0.0
            self.ay_ema = 0.0
            
        # 1. IMU Dead Reckoning Update
        gz_corrected = frame_data['gz'] - self.imu_bias_gz
        self.imu_theta = normalize_angle(self.imu_theta + gz_corrected * dt)
        cos_t = np.cos(self.imu_theta)
        sin_t = np.sin(self.imu_theta)
        
        ax_body = ax_in
        ay_body = ay_in
        
        ax_world = ax_body * cos_t - ay_body * sin_t
        ay_world = ax_body * sin_t + ay_body * cos_t
        
        self.imu_vx += ax_world * dt
        self.imu_vy += ay_world * dt
        self.imu_x += self.imu_vx * dt + 0.5 * ax_world * dt**2
        self.imu_y += self.imu_vy * dt + 0.5 * ay_world * dt**2
        
        # 2. MCU position received directly from STM px/py.
        mcu_pos = self.prev_uwb_pos
        stm_pos = (frame_data['px'], frame_data['py'])
        if abs(stm_pos[0]) > 1e-6 or abs(stm_pos[1]) > 1e-6:
            mcu_pos = stm_pos
            self.prev_uwb_pos = mcu_pos

        # 3. Host-side trilateration calculated independently from distances.
        d_meas_all = np.asarray(frame_data['distances'], dtype=float)
        active_indices = [
            idx for idx, distance in enumerate(d_meas_all)
            if np.isfinite(distance) and distance > 1e-6
        ]
        if len(active_indices) >= 3:
            tril_indices = active_indices[:3]
            self.prev_tril_pos = trilateration_2d(
                d_meas_all[tril_indices],
                ANCHOR_POSITIONS[tril_indices],
                self.prev_tril_pos,
            )
        tril_pos = self.prev_tril_pos
                
        # 4. UKF Predict & Update
        if status == "Predict":
            imu_sample = IMUSample(ax=frame_data['ax'], ay=frame_data['ay'], gz=frame_data['gz'])
            ukf_predict(self.ukf_ctx, imu_sample, dt)
        
        if status == "Update":
            active_indices = [idx for idx, d in enumerate(d_meas_all) if d > 1e-6]
            if len(active_indices) >= 3:
                active_d_meas = d_meas_all[active_indices][:3]
                active_anchors = ANCHOR_POSITIONS[active_indices][:3]
                ukf_update(self.ukf_ctx, active_d_meas, active_anchors)
                
        res = {
            'imu_x': self.imu_x,
            'imu_y': self.imu_y,
            'mcu_x': mcu_pos[0],
            'mcu_y': mcu_pos[1],
            'tril_x': tril_pos[0],
            'tril_y': tril_pos[1],
            'ukf_x': self.ukf_ctx.x[0],
            'ukf_y': self.ukf_ctx.x[1],
            'ukf_vx': self.ukf_ctx.x[2],
            'ukf_vy': self.ukf_ctx.x[3],
            'ukf_yaw': self.ukf_ctx.x[4],
            'stm_px': frame_data['px'],
            'stm_py': frame_data['py'],
            'yaw_raw': self.imu_theta,
            'err_cnt': frame_data.get('err_cnt', 0),
            'mask': frame_data.get('anchor_mask', frame_data.get('mask', 0)),
            'distances': frame_data.get('distances', [0.0, 0.0, 0.0, 0.0]),
            'dt': dt,
            'zero_distance_counts': self._zero_distance_counts.copy(),
            'ax': ax_in, # Used bias subtracted and ZUPT applied
            'ay': ay_in,
            'ax_ema': self.ax_ema,
            'ay_ema': self.ay_ema,
            'type': 'Data'
        }
        if res['mask'] != 0:
            if res['mask'] != self.last_active_mask:
                print(f"[DEBUG] Mask changed: {self.last_active_mask} -> {res['mask']}")
            self.last_active_mask = res['mask']
            
        res['last_active_mask'] = self.last_active_mask
        return res

    def run(self):
        print("=" * 60)
        print("UKF Live Plot Receiver")
        print("=" * 60)
        
        frame_count = 0
        buffer = bytearray()
        
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
                    self.disconnected_signal.emit()
                    time.sleep(1.0) # Wait and retry
                    continue
                    
            try:
                drained = self._drain_rx_queue(buffer)
                if drained == 0:
                    self._print_stats(frame_count)
                    time.sleep(0.001)
                    continue

                if self._take_filter_reset_request():
                    self._reset_filter_state()
                    print(f"[INFO] UKF filter reset with initial yaw {np.rad2deg(self.initial_yaw_rad):.3f} deg")

                for frame_data in self._extract_live_frames(buffer):
                    frame_count += 1
                    self._parsed_frames += 1
                    self._track_tx_gap(frame_data['tx_frame_cnt'])
                    self._track_zero_distances(frame_data.get('distances', []))

                    if self.csv_writer is not None:
                        status, self.prev_distances = write_frame_to_csv(
                            self.csv_writer, frame_data, frame_count, self.prev_distances
                        )
                    else:
                        status = self._classify_frame_status(frame_data)

                    if self.csv_file is not None and frame_count % 25 == 0:
                        self.csv_file.flush()

                    result = self.process_frame(frame_data, status)
                    if result is not None:
                        now = time.monotonic()
                        has_zero_distance = any(
                            abs(distance) <= 1e-6
                            for distance in frame_data.get('distances', [])
                        )
                        if (
                            result.get('type') == 'Init'
                            or has_zero_distance
                            or now - self._last_gui_emit >= 0.03
                        ):
                            self.data_signal.emit(result)
                            self._last_gui_emit = now

                    if PRINT_DATA and frame_count % 50 == 0:
                        print_frame_data(frame_data)

                self._print_stats(frame_count)
            except serial.SerialException as e:
                port_name = self.serial_port.port if self.serial_port else "USB serial"
                print(f"[WARNING] Disconnected from {port_name}: {e}")
                self._close_serial()
            except Exception as e:
                print(f"[ERROR] Unexpected error: {e}")
                
        # Cleanup
        self._close_serial()
        if self.csv_file:
            self.csv_file.flush()
            self.csv_file.close()

class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        
        # Load UI from parent directory
        ui_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ukf_log.ui")
        uic.loadUi(ui_path, self)
        
        # Setup PlotWidget (graph_pos)
        self.graph_pos.setBackground('w')
        self.graph_pos.showGrid(x=True, y=True)
        self.graph_pos.setXRange(-1, ROOM_SIZE_M + 1)
        self.graph_pos.setYRange(-1, ROOM_SIZE_M + 1)
        self.graph_pos.setLabel('left', 'Y (m)')
        self.graph_pos.setLabel('bottom', 'X (m)')
        self.graph_pos.addLegend()
        
        # Setup graph_d
        self.graph_d.setBackground('w')
        self.graph_d.showGrid(x=True, y=True)
        self.graph_d.setLabel('left', 'Distance (m)')
        self.graph_d.setLabel('bottom', 'Time (s)')
        self.graph_d.addLegend()
        
        # Reference rectangle
        self.ref_rect_item = None
        
        # Add plotting lines
        self.plot_mcu = self.graph_pos.plot(
            pen=pg.mkPen('r', width=1.5), name="MCU px/py"
        )
        self.plot_tril = self.graph_pos.plot(
            pen=pg.mkPen((0, 150, 0), width=1.5), name="Trilateration (distance)"
        )
        self.plot_ukf = self.graph_pos.plot(
            pen=pg.mkPen('b', width=1.5),
            name="UKF Filtered"
        )
        self.current_mcu_marker = pg.ScatterPlotItem(
            size=13, symbol='o', pen=pg.mkPen('r', width=2), brush=pg.mkBrush('r')
        )
        self.current_tril_marker = pg.ScatterPlotItem(
            size=15, symbol='t', pen=pg.mkPen((0, 120, 0), width=2), brush=pg.mkBrush((0, 190, 0))
        )
        self.current_ukf_marker = pg.ScatterPlotItem(
            size=15, symbol='s', pen=pg.mkPen('b', width=2), brush=pg.mkBrush('b')
        )
        for marker in (self.current_mcu_marker, self.current_tril_marker, self.current_ukf_marker):
            self.graph_pos.addItem(marker)
        self.plot_ground_truth = self.graph_pos.plot(
            pen=pg.mkPen((230, 116, 37), width=2.0, style=pg.QtCore.Qt.DashLine),
            name="Ground truth",
        )
        self.ground_truth_start_markers = pg.ScatterPlotItem(
            size=16,
            pen=pg.mkPen((160, 70, 20), width=1.5),
            brush=pg.mkBrush((240, 112, 48)),
            symbol='o',
        )
        self.graph_pos.addItem(self.ground_truth_start_markers)
        self.ground_truth_labels = []
        
        self.plot_d1 = self.graph_d.plot(
            pen=None, symbol='o', symbolSize=6,
            symbolPen=pg.mkPen('r'), symbolBrush=pg.mkBrush('r'), name="d1"
        )
        self.plot_d2 = self.graph_d.plot(
            pen=None, symbol='o', symbolSize=6,
            symbolPen=pg.mkPen('g'), symbolBrush=pg.mkBrush('g'), name="d2"
        )
        self.plot_d3 = self.graph_d.plot(
            pen=None, symbol='o', symbolSize=6,
            symbolPen=pg.mkPen('b'), symbolBrush=pg.mkBrush('b'), name="d3"
        )
        self.plot_d4 = self.graph_d.plot(
            pen=None, symbol='o', symbolSize=6,
            symbolPen=pg.mkPen('m'), symbolBrush=pg.mkBrush('m'), name="d4"
        )
        
        # Ground truth horizontal lines
        self.gt_line_d1 = pg.InfiniteLine(pos=GROUND_TRUTH_D1, angle=0, pen=pg.mkPen('r', width=3, style=pg.QtCore.Qt.DashLine))
        self.gt_line_d2 = pg.InfiniteLine(pos=GROUND_TRUTH_D2, angle=0, pen=pg.mkPen('g', width=3, style=pg.QtCore.Qt.DashLine))
        self.gt_line_d3 = pg.InfiniteLine(pos=GROUND_TRUTH_D3, angle=0, pen=pg.mkPen('b', width=3, style=pg.QtCore.Qt.DashLine))
        self.gt_line_d4 = pg.InfiniteLine(pos=GROUND_TRUTH_D4, angle=0, pen=pg.mkPen('m', width=3, style=pg.QtCore.Qt.DashLine))
        self.graph_d.addItem(self.gt_line_d1)
        self.graph_d.addItem(self.gt_line_d2)
        self.graph_d.addItem(self.gt_line_d3)
        self.graph_d.addItem(self.gt_line_d4)
        
        # Draw Anchors on plot
        for idx, anchor in enumerate(ANCHOR_POSITIONS):
            anchor_scatter = pg.ScatterPlotItem([anchor[0]], [anchor[1]], size=15, pen=pg.mkPen(None), brush=pg.mkBrush('k'), symbol='t')
            self.graph_pos.addItem(anchor_scatter)
            text = pg.TextItem(f"A{idx}", color='k')
            text.setPos(anchor[0], anchor[1])
            self.graph_pos.addItem(text)
            
        # Data storage for plots
        self.mcu_xs, self.mcu_ys = [], []
        self.tril_xs, self.tril_ys = [], []
        self.ukf_xs, self.ukf_ys = [], []
        self.d1_data, self.d2_data, self.d3_data, self.d4_data = [], [], [], []
        self.distance_xs = []
        self.distance_time_origin = time.monotonic()
        self.zero_distance_counts = [0, 0, 0, 0]
        self.ground_truth_start = None
        self.ground_truth_start_kind = GROUND_TRUTH_START_1
        self.latest_data = None
        self._update_zero_distance_title()
        
        # Setup UI connections
        self._setup_ground_truth_controls()
        self._setup_initial_yaw_controls()
        self.pushButton_clearGraph.clicked.connect(self.clear_graph)
        self.checkBox_createCsv.stateChanged.connect(self.on_checkbox_csv_changed)
        self.checkBox_graphDSliding.stateChanged.connect(self.on_graph_d_mode_changed)
        
        # Setup Timer for GUI updates (~30fps)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(30)
        
        # Start Data Thread
        self.thread = DataThread()
        self.thread.create_csv_enabled = self.checkBox_createCsv.isChecked()
        self.thread.initial_yaw_rad = self._initial_yaw_rad()
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

        if hasattr(self, 'gridLayout_4'):
            row = self.gridLayout_4.rowCount()
            self.gridLayout_4.addWidget(self.groundTruthStartLabel, row, 0)
            self.gridLayout_4.addWidget(self.comboBox_groundTruthStart, row + 1, 0)

    def _setup_initial_yaw_controls(self):
        self.initialYawLabel = QLabel("Initial yaw")
        self.doubleSpinBox_initialYaw = QDoubleSpinBox()
        self.doubleSpinBox_initialYaw.setRange(-360.0, 360.0)
        self.doubleSpinBox_initialYaw.setDecimals(3)
        self.doubleSpinBox_initialYaw.setSingleStep(1.0)
        self.doubleSpinBox_initialYaw.setSuffix(" deg")
        self.doubleSpinBox_initialYaw.setValue(0.0)
        self.doubleSpinBox_initialYaw.valueChanged.connect(self.on_initial_yaw_changed)

        if hasattr(self, 'gridLayout_4'):
            row = self.gridLayout_4.rowCount()
            self.gridLayout_4.addWidget(self.initialYawLabel, row, 0)
            self.gridLayout_4.addWidget(self.doubleSpinBox_initialYaw, row + 1, 0)

    def _initial_yaw_rad(self):
        if not hasattr(self, 'doubleSpinBox_initialYaw'):
            return 0.0
        return normalize_angle(np.deg2rad(self.doubleSpinBox_initialYaw.value()))

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
        if self.ground_truth_start_kind == GROUND_TRUTH_START_2:
            start_labels = ("start 2", "start 1")
        else:
            start_labels = ("start 1", "start 2")

        for text, x, y in ((start_labels[0], xs[0], ys[0]), (start_labels[1], xs[-1], ys[-1])):
            label = pg.TextItem(text, color=(120, 60, 20), anchor=(0.5, -0.2))
            label.setPos(x, y)
            self.graph_pos.addItem(label)
            self.ground_truth_labels.append(label)

    def _maybe_set_ground_truth_start(self, data):
        if self.ground_truth_start is not None:
            return

        if data.get('type') == 'Init':
            start_x = data.get('x')
            start_y = data.get('y')
        else:
            start_x = data.get('stm_px')
            start_y = data.get('stm_py')

        if start_x is None or start_y is None:
            return
        if not (np.isfinite(start_x) and np.isfinite(start_y)):
            return

        self.ground_truth_start = (float(start_x), float(start_y))
        self._update_ground_truth_plot()

    def on_ground_truth_start_changed(self, *_):
        self.ground_truth_start_kind = self.comboBox_groundTruthStart.currentData()
        self._update_ground_truth_plot()

    def on_initial_yaw_changed(self, *_):
        if hasattr(self, 'thread') and self.thread is not None:
            self.thread.initial_yaw_rad = self._initial_yaw_rad()
            self.thread.request_filter_reset()

    def on_checkbox_csv_changed(self, state):
        if hasattr(self, 'thread') and self.thread is not None:
            self.thread.create_csv_enabled = (state == pg.QtCore.Qt.Checked)

    def on_csv_created(self):
        self.checkBox_createCsv.setChecked(False)

    def on_graph_d_mode_changed(self, _state):
        self._update_distance_plot()

    def clear_graph(self):
        self.mcu_xs.clear()
        self.mcu_ys.clear()
        self.tril_xs.clear()
        self.tril_ys.clear()
        self.ukf_xs.clear()
        self.ukf_ys.clear()
        self.d1_data.clear()
        self.d2_data.clear()
        self.d3_data.clear()
        self.d4_data.clear()
        self.distance_xs.clear()
        self.distance_time_origin = time.monotonic()
        self.ground_truth_start = None
        self.latest_data = None
        
        if getattr(self, 'ref_rect_item', None) is not None:
            self.graph_pos.removeItem(self.ref_rect_item)
            self.ref_rect_item = None
            
        self.plot_mcu.setData([], [])
        self.plot_tril.setData([], [])
        self.plot_ukf.setData([], [])
        for marker in (self.current_mcu_marker, self.current_tril_marker, self.current_ukf_marker):
            marker.setData([], [])
        self.plot_d1.setData([])
        self.plot_d2.setData([])
        self.plot_d3.setData([])
        self.plot_d4.setData([])
        self._update_ground_truth_plot()
        self.graph_d.enableAutoRange(axis=pg.ViewBox.XAxis, enable=True)
        
        if hasattr(self, 'thread') and self.thread is not None:
            self.thread.initial_yaw_rad = self._initial_yaw_rad()
            self.thread.request_filter_reset()
            if self.checkBox_createCsv.isChecked():
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
        self._maybe_set_ground_truth_start(data)

        self.zero_distance_counts = data.get(
            'zero_distance_counts', self.zero_distance_counts
        )
        self._update_zero_distance_title()

        if data.get('type') == 'Init':
            if DRAW_RECTANGLE and self.ref_rect_item is None:
                from PyQt5.QtWidgets import QGraphicsRectItem
                init_x, init_y = data['x'], data['y']
                self.ref_rect_item = QGraphicsRectItem(
                    init_x,
                    init_y,
                    RECT_WIDTH,
                    RECT_HEIGHT
                )
                self.ref_rect_item.setPen(
                    pg.mkPen('r', width=1.5, style=pg.QtCore.Qt.DashLine)
                )
                self.graph_pos.addItem(self.ref_rect_item)
            if hasattr(self, 'lineEdit_mask'):
                self.lineEdit_mask.setText(str(data.get('mask', 0)))
            if 'yaw' in data:
                yaw_deg = np.rad2deg(data['yaw'])
                self.lineEdit_yaw.setText(f"{yaw_deg:.3f}")
                self.lineEdit_ukf_yaw.setText(f"{yaw_deg:.3f}")
            return

        self.latest_data = data
        
        self.mcu_xs.append(data['mcu_x'])
        self.mcu_ys.append(data['mcu_y'])
        self.tril_xs.append(data['tril_x'])
        self.tril_ys.append(data['tril_y'])
        self.ukf_xs.append(data['ukf_x'])
        self.ukf_ys.append(data['ukf_y'])
        
        d = data.get('distances', [0, 0, 0, 0])
        distance_series = (self.d1_data, self.d2_data, self.d3_data, self.d4_data)
        for idx, series in enumerate(distance_series):
            distance = d[idx] if idx < len(d) else 0.0
            series.append(distance if abs(distance) > 1e-6 else np.nan)
        self.distance_xs.append(time.monotonic() - self.distance_time_origin)
        
        # Keep maximum defined samples
        if len(self.mcu_xs) > MAX_SAMPLES:
            self.mcu_xs.pop(0)
            self.mcu_ys.pop(0)
            self.tril_xs.pop(0)
            self.tril_ys.pop(0)
            self.ukf_xs.pop(0)
            self.ukf_ys.pop(0)
            self.d1_data.pop(0)
            self.d2_data.pop(0)
            self.d3_data.pop(0)
            self.d4_data.pop(0)
            self.distance_xs.pop(0)

    def _update_zero_distance_title(self):
        counts = " | ".join(
            f"A{idx + 1}: {count}" for idx, count in enumerate(self.zero_distance_counts)
        )
        self.graph_d.setTitle(f"Distance = 0 count — {counts}")

    def _update_distance_plot(self):
        current_time = time.monotonic() - self.distance_time_origin

        if not self.distance_xs:
            self.plot_d1.setData([], [])
            self.plot_d2.setData([], [])
            self.plot_d3.setData([], [])
            self.plot_d4.setData([], [])
            if self.checkBox_graphDSliding.isChecked():
                self.graph_d.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
                x_max = current_time
                x_min = max(0.0, x_max - 1.0)
                self.graph_d.setXRange(x_min, x_max if x_max > x_min else x_min + 1, padding=0.0)
            else:
                self.graph_d.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
                self.graph_d.setXRange(0.0, current_time if current_time > 0.0 else 1.0, padding=0.0)
            return

        self.plot_d1.setData(self.distance_xs, self.d1_data)
        self.plot_d2.setData(self.distance_xs, self.d2_data)
        self.plot_d3.setData(self.distance_xs, self.d3_data)
        self.plot_d4.setData(self.distance_xs, self.d4_data)
        if self.checkBox_graphDSliding.isChecked():
            self.graph_d.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            x_max = current_time
            window_start_idx = max(0, len(self.distance_xs) - DISTANCE_GRAPH_SLIDING_WINDOW)
            if len(self.distance_xs) >= 2:
                window_span = max(1.0, self.distance_xs[-1] - self.distance_xs[window_start_idx])
            else:
                window_span = 1.0
            x_min = max(0.0, x_max - window_span)
            self.graph_d.setXRange(x_min, x_max if x_max > x_min else x_min + 1, padding=0.0)
        else:
            self.graph_d.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            self.graph_d.setXRange(0.0, current_time if current_time > 0.0 else 1.0, padding=0.0)

    def update_gui(self):
        # Update Plots
        if len(self.mcu_xs) > 0:
            self.plot_mcu.setData(self.mcu_xs, self.mcu_ys)
            self.plot_tril.setData(self.tril_xs, self.tril_ys)
            self.plot_ukf.setData(self.ukf_xs, self.ukf_ys)
            self.current_mcu_marker.setData([self.mcu_xs[-1]], [self.mcu_ys[-1]])
            self.current_tril_marker.setData([self.tril_xs[-1]], [self.tril_ys[-1]])
            self.current_ukf_marker.setData([self.ukf_xs[-1]], [self.ukf_ys[-1]])
            self._update_distance_plot()
            
        # Update Text Fields
        if self.latest_data is not None:
            data = self.latest_data
            
            # LineEdit for STM px, py (only update if != 0.0, matching csv Update logic)
            if abs(data['stm_px']) > 1e-6 or abs(data['stm_py']) > 1e-6:
                self.lineEdit_stm_px.setText(f"{data['stm_px']:.3f}")
                self.lineEdit_stm_py.setText(f"{data['stm_py']:.3f}")
                
            # LineEdit for UKF and other values
            self.lineEdit_ukf_px.setText(f"{data['ukf_x']:.3f}")
            self.lineEdit_ukf_py.setText(f"{data['ukf_y']:.3f}")
            self.lineEdit_ukf_vx.setText(f"{data['ukf_vx']:.3f}")
            self.lineEdit_ukf_vy.setText(f"{data['ukf_vy']:.3f}")
            self.lineEdit_ukf_yaw.setText(f"{np.rad2deg(data['ukf_yaw']):.3f}")
            self.lineEdit_yaw.setText(f"{np.rad2deg(data['yaw_raw']):.3f}")
            if data['err_cnt'] != 0:
                self.lineEdit_err.setText(str(data['err_cnt']))
                
            if hasattr(self, 'lineEdit_mask'):
                # Use last active mask to avoid flickering to 0 during Predict frames
                mask_val = data.get('last_active_mask', data.get('mask', 0))
                self.lineEdit_mask.setText(str(mask_val))

    def closeEvent(self, event):
        self.thread.stop()
        event.accept()
