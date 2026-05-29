import sys
import os
import re
import signal
import numpy as np
from PyQt5 import QtWidgets, uic, QtCore

# Thêm thư mục hiện tại vào path để import module
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from module import config
from module.module_log import generate_log_from_csv
from simulation import run_simulation_with_params

class UKFSimulationGUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        # Load file UI
        uic.loadUi(os.path.join(current_dir, "simulation.ui"), self)
        
        self.init_ui_state()
        self.connect_signals()
        self.load_config_to_ui()

    def init_ui_state(self):
        """Khởi tạo trạng thái ban đầu của các widget"""
        # CSV Path logic - Đồng bộ với config.py
        is_csv_set = config.SOURCE_DATA_FILE is not None
        self.checkBox_csvPath.setChecked(is_csv_set)
        self.lineEdit_csvPath.setReadOnly(not is_csv_set)
        self.lineEdit_csvPath.setText(config.SOURCE_DATA_FILE if is_csv_set else "")
        
        self.checkBox_txtRaw.setChecked(True)
        self.checkBox_txtEMA.setChecked(True)
        self.checkBox_txtRaw_ZUPT.setChecked(True)
        self.checkBox_txtEMA_ZUPT.setChecked(True)

        # QR Test Params logic
        self.update_qr_visibility()

    def connect_signals(self):
        """Kết nối các tín hiệu từ widget tới các hàm xử lý"""
        # CheckBox signals
        self.checkBox_csvPath.toggled.connect(self.on_csv_path_toggled)
        self.lineEdit_csvPath.editingFinished.connect(self.on_csv_path_edited)
        self.checkBox_useQRTestParams.toggled.connect(self.on_qr_test_toggled)
        
        # LineEdit signals - Update config and SAVE TO FILE immediately on Enter/Focus lost
        # UKF Constants
        self.lineEdit_ukfAlpha.editingFinished.connect(lambda: self.update_config_var("UKF_ALPHA", self.lineEdit_ukfAlpha.text()))
        self.lineEdit_ukfKappa.editingFinished.connect(lambda: self.update_config_var("UKF_KAPPA", self.lineEdit_ukfKappa.text()))
        self.lineEdit_ukfBeta.editingFinished.connect(lambda: self.update_config_var("UKF_BETA", self.lineEdit_ukfBeta.text()))
        
        # Anchors
        self.lineEdit_A1_X.editingFinished.connect(lambda: self.update_config_var("ANCHOR_1_X", self.lineEdit_A1_X.text()))
        self.lineEdit_A1_Y.editingFinished.connect(lambda: self.update_config_var("ANCHOR_1_Y", self.lineEdit_A1_Y.text()))
        self.lineEdit_A2_X.editingFinished.connect(lambda: self.update_config_var("ANCHOR_2_X", self.lineEdit_A2_X.text()))
        self.lineEdit_A2_Y.editingFinished.connect(lambda: self.update_config_var("ANCHOR_2_Y", self.lineEdit_A2_Y.text()))
        self.lineEdit_A3_X.editingFinished.connect(lambda: self.update_config_var("ANCHOR_3_X", self.lineEdit_A3_X.text()))
        self.lineEdit_A3_Y.editingFinished.connect(lambda: self.update_config_var("ANCHOR_3_Y", self.lineEdit_A3_Y.text()))
        self.lineEdit_A4_X.editingFinished.connect(lambda: self.update_config_var("ANCHOR_4_X", self.lineEdit_A4_X.text()))
        self.lineEdit_A4_Y.editingFinished.connect(lambda: self.update_config_var("ANCHOR_4_Y", self.lineEdit_A4_Y.text()))
        
        # Rectangle
        self.lineEdit_recWidth.editingFinished.connect(lambda: self.update_config_var("RECT_WIDTH", self.lineEdit_recWidth.text()))
        self.lineEdit_recHeight.editingFinished.connect(lambda: self.update_config_var("RECT_HEIGHT", self.lineEdit_recHeight.text()))
        
        # EMA & ZUPT
        self.lineEdit_emaAlpha.editingFinished.connect(lambda: self.update_config_var("IMU_EMA_ALPHA", self.lineEdit_emaAlpha.text()))
        self.lineEdit_zuptThreshold.editingFinished.connect(lambda: self.update_config_var("IMU_ZUPT_THRESHOLD", self.lineEdit_zuptThreshold.text()))
        self.lineEdit_zuptFrames.editingFinished.connect(lambda: self.update_config_var("IMU_ZUPT_FRAMES", self.lineEdit_zuptFrames.text()))
        
        # Noise STD (Chỉ dùng để tính Q/R khi unchecked, hoặc hiển thị khi checked)
        self.lineEdit_imuSTDAx.editingFinished.connect(lambda: self.update_config_var("IMU_NOISE_STD_AX", self.lineEdit_imuSTDAx.text()))
        self.lineEdit_imuSTDAy.editingFinished.connect(lambda: self.update_config_var("IMU_NOISE_STD_AY", self.lineEdit_imuSTDAy.text()))
        self.lineEdit_imuSTDGz.editingFinished.connect(lambda: self.update_config_var("IMU_NOISE_STD_GZ", self.lineEdit_imuSTDGz.text()))
        self.lineEdit_STDRuwb.editingFinished.connect(lambda: self.update_config_var("UWB_NOISE_STD_M", self.lineEdit_STDRuwb.text()))
        
        # Q/R Values (Always map to MANUAL variables)
        self.lineEdit_ukfQa.editingFinished.connect(lambda: self.update_config_var("Q_A_MANUAL", self.lineEdit_ukfQa.text()))
        self.lineEdit_ukfQg.editingFinished.connect(lambda: self.update_config_var("Q_G_MANUAL", self.lineEdit_ukfQg.text()))
        self.lineEdit_ukfRuwb.editingFinished.connect(lambda: self.update_config_var("R_UWB_MANUAL", self.lineEdit_ukfRuwb.text()))
        
        # Initial P
        self.lineEdit_P_px.editingFinished.connect(lambda: self.update_config_var("P_PX", self.lineEdit_P_px.text()))
        self.lineEdit_P_py.editingFinished.connect(lambda: self.update_config_var("P_PY", self.lineEdit_P_py.text()))
        self.lineEdit_P_vx.editingFinished.connect(lambda: self.update_config_var("P_VX", self.lineEdit_P_vx.text()))
        self.lineEdit_P_vy.editingFinished.connect(lambda: self.update_config_var("P_VY", self.lineEdit_P_vy.text()))
        self.lineEdit_P_theta.editingFinished.connect(lambda: self.update_config_var("P_THETA", self.lineEdit_P_theta.text()))
        self.lineEdit_P_bax.editingFinished.connect(lambda: self.update_config_var("P_BAX", self.lineEdit_P_bax.text()))
        self.lineEdit_P_bay.editingFinished.connect(lambda: self.update_config_var("P_BAY", self.lineEdit_P_bay.text()))
        self.lineEdit_P_bgz.editingFinished.connect(lambda: self.update_config_var("P_BGZ", self.lineEdit_P_bgz.text()))
        
        # Run Button
        self.pushButton_run.clicked.connect(self.run_simulation)
        self.pushButton_genLogfile.clicked.connect(self.generate_log_file)

    def load_config_to_ui(self):
        """Đổ dữ liệu từ config.py lên giao diện"""
        self.lineEdit_ukfAlpha.setText(str(config.UKF_ALPHA))
        self.lineEdit_ukfKappa.setText(str(config.UKF_KAPPA))
        self.lineEdit_ukfBeta.setText(str(config.UKF_BETA))
        
        self.lineEdit_A1_X.setText(str(config.ANCHOR_1_X))
        self.lineEdit_A1_Y.setText(str(config.ANCHOR_1_Y))
        self.lineEdit_A2_X.setText(str(config.ANCHOR_2_X))
        self.lineEdit_A2_Y.setText(str(config.ANCHOR_2_Y))
        self.lineEdit_A3_X.setText(str(config.ANCHOR_3_X))
        self.lineEdit_A3_Y.setText(str(config.ANCHOR_3_Y))
        self.lineEdit_A4_X.setText(str(config.ANCHOR_4_X))
        self.lineEdit_A4_Y.setText(str(config.ANCHOR_4_Y))
        
        self.lineEdit_recWidth.setText(str(config.RECT_WIDTH))
        self.lineEdit_recHeight.setText(str(config.RECT_HEIGHT))
        
        self.lineEdit_emaAlpha.setText(str(config.IMU_EMA_ALPHA))
        self.lineEdit_zuptThreshold.setText(str(config.IMU_ZUPT_THRESHOLD))
        self.lineEdit_zuptFrames.setText(str(config.IMU_ZUPT_FRAMES))
        
        self.lineEdit_imuSTDAx.setText(str(getattr(config, 'IMU_NOISE_STD_AX', 0.15)))
        self.lineEdit_imuSTDAy.setText(str(getattr(config, 'IMU_NOISE_STD_AY', 0.15)))
        self.lineEdit_imuSTDGz.setText(str(getattr(config, 'IMU_NOISE_STD_GZ', 0.01396)))
        self.lineEdit_STDRuwb.setText(str(getattr(config, 'UWB_NOISE_STD_M', 0.1)))
        
        self.lineEdit_ukfQa.setText(str(config.Q_A_MANUAL))
        self.lineEdit_ukfQg.setText(str(config.Q_G_MANUAL))
        self.lineEdit_ukfRuwb.setText(str(config.R_UWB_MANUAL))
        
        self.lineEdit_P_px.setText(str(config.P_PX))
        self.lineEdit_P_py.setText(str(config.P_PY))
        self.lineEdit_P_vx.setText(str(config.P_VX))
        self.lineEdit_P_vy.setText(str(config.P_VY))
        self.lineEdit_P_theta.setText(str(config.P_THETA))
        self.lineEdit_P_bax.setText(str(config.P_BAX))
        self.lineEdit_P_bay.setText(str(config.P_BAY))
        self.lineEdit_P_bgz.setText(str(config.P_BGZ))
        
        self.checkBox_useQRTestParams.setChecked(config.TEST_UKF_Q_R_Params)
        self.checkBox_ukfTrapezoidal.setChecked(True) # Mặc định dùng Trapezoidal

    def update_config_var(self, name, value):
        """Cập nhật biến trong config module (in-memory)"""
        try:
            if value.strip() == "": return
            
            # Convert to appropriate type
            if name in ["IMU_ZUPT_FRAMES"]:
                val = int(value)
            else:
                val = float(value)
            
            setattr(config, name, val)
            
            # Nếu thay đổi Noise STD và đang bật QR Test Params thì tự tính lại Q/R
            if self.checkBox_useQRTestParams.isChecked() and name in ["IMU_NOISE_STD_AX", "IMU_NOISE_STD_GZ", "UWB_NOISE_STD_M"]:
                self.update_qr_visibility()
            
            # LƯU FILE NGAY LẬP TỨC
            self.save_to_config_file()
                
        except ValueError:
            pass # Bỏ qua nếu nhập sai định dạng số

    def on_csv_path_toggled(self, checked):
        """Xử lý khi checkbox đường dẫn CSV thay đổi"""
        self.lineEdit_csvPath.setReadOnly(not checked)
        if not checked:
            config.SOURCE_DATA_FILE = None
        else:
            config.SOURCE_DATA_FILE = self.lineEdit_csvPath.text()
        
        # Lưu file ngay lập tức
        self.save_to_config_file()

    def on_csv_path_edited(self):
        """Xử lý khi người dùng nhập xong đường dẫn CSV và ấn Enter"""
        if self.checkBox_csvPath.isChecked():
            config.SOURCE_DATA_FILE = self.lineEdit_csvPath.text()
            # Lưu file ngay lập tức
            self.save_to_config_file()

    def on_qr_test_toggled(self, checked):
        """Xử lý khi checkbox QR Test Params thay đổi"""
        config.TEST_UKF_Q_R_Params = checked
        self.update_qr_visibility()
        # Lưu file ngay lập tức
        self.save_to_config_file()

    def update_qr_visibility(self):
        """Cập nhật trạng thái hiển thị Q_A, Q_G, R_UWB dựa trên Test Mode"""
        is_test = self.checkBox_useQRTestParams.isChecked()
        
        # Khóa các ô khi ở chế độ Test
        self.lineEdit_ukfQa.setReadOnly(is_test)
        self.lineEdit_ukfQg.setReadOnly(is_test)
        self.lineEdit_ukfRuwb.setReadOnly(is_test)
        
        if is_test:
            # Hiển thị giá trị TEST (Hardcoded trong config)
            self.lineEdit_ukfQa.setText(f"{config.Q_A_TEST:.10f}")
            self.lineEdit_ukfQg.setText(f"{config.Q_G_TEST:.10f}")
            self.lineEdit_ukfRuwb.setText(f"{config.R_UWB_TEST:.10f}")
            
            # Cập nhật bộ nhớ để simulation dùng
            config.Q_A = config.Q_A_TEST
            config.Q_G = config.Q_G_TEST
            config.R_UWB = config.R_UWB_TEST
        else:
            # Hiển thị giá trị MANUAL
            self.lineEdit_ukfQa.setText(str(config.Q_A_MANUAL))
            self.lineEdit_ukfQg.setText(str(config.Q_G_MANUAL))
            self.lineEdit_ukfRuwb.setText(str(config.R_UWB_MANUAL))
            
            # Cập nhật bộ nhớ để simulation dùng
            config.Q_A = config.Q_A_MANUAL
            config.Q_G = config.Q_G_MANUAL
            config.R_UWB = config.R_UWB_MANUAL
            
        # Cập nhật các ô Noise STD để hiển thị (tùy chọn, chỉ để trực quan)
        if is_test:
            self.lineEdit_imuSTDAx.setText("0.2")
            self.lineEdit_imuSTDAy.setText("0.2")
            self.lineEdit_imuSTDGz.setText(f"{np.deg2rad(2):.6f}")
            self.lineEdit_STDRuwb.setText("0.1")
            self.lineEdit_imuSTDAx.setReadOnly(True)
            self.lineEdit_imuSTDAy.setReadOnly(True)
            self.lineEdit_imuSTDGz.setReadOnly(True)
            self.lineEdit_STDRuwb.setReadOnly(True)
        else:
            self.lineEdit_imuSTDAx.setReadOnly(False)
            self.lineEdit_imuSTDAy.setReadOnly(False)
            self.lineEdit_imuSTDGz.setReadOnly(False)
            self.lineEdit_STDRuwb.setReadOnly(False)

    def get_params_for_simulation(self):
        """Thu thập các tham số để truyền vào hàm run_simulation (nếu cần)"""
        # Tuy nhiên yêu cầu nói là simulation dùng trực tiếp biến trong config.py
        # Vậy ta cần đồng bộ những mảng/ma trận phái sinh trong config.py
        
        config.ANCHOR_POSITIONS = np.array([
            [config.ANCHOR_1_X, config.ANCHOR_1_Y],
            [config.ANCHOR_2_X, config.ANCHOR_2_Y],
            [config.ANCHOR_3_X, config.ANCHOR_3_Y],
            [config.ANCHOR_4_X, config.ANCHOR_4_Y]
        ])
        
        config.INITIAL_P = np.diag([
            config.P_PX, config.P_PY, config.P_VX, config.P_VY,
            config.P_THETA, config.P_BAX, config.P_BAY, config.P_BGZ
        ])
        
        config.PROCESS_NOISE_COV = np.diag([config.Q_A, config.Q_A, config.Q_G])
        config.MEASUREMENT_NOISE_COV = np.diag([config.R_UWB, config.R_UWB, config.R_UWB])
        
        # Cập nhật các biến dẫn xuất khác
        config.UKF_LAMBDA = config.UKF_ALPHA**2 * (config.UKF_AUGMENTED_SIZE + config.UKF_KAPPA) - config.UKF_AUGMENTED_SIZE
        config.UKF_GAMMA = np.sqrt(config.UKF_AUGMENTED_SIZE + config.UKF_LAMBDA)
        
        # TXT Export Logic
        params = {
            "run_raw": self.checkBox_rawIMU.isChecked(),
            "run_ema": self.checkBox_emaIMU.isChecked(),
            "run_raw_zupt": self.checkBox_rawIMU_ZUPT.isChecked(),
            "run_ema_zupt": self.checkBox_emaIMU_ZUPT.isChecked(),
            
            "txt_enabled": self.checkBox_txtEnable.isChecked(),
            "txt_raw": self.checkBox_rawIMU.isChecked() and self.checkBox_txtRaw.isChecked(),
            "txt_ema": self.checkBox_emaIMU.isChecked() and self.checkBox_txtEMA.isChecked(),
            "txt_raw_zupt": self.checkBox_rawIMU_ZUPT.isChecked() and self.checkBox_txtRaw_ZUPT.isChecked(),
            "txt_ema_zupt": self.checkBox_emaIMU_ZUPT.isChecked() and self.checkBox_txtEMA_ZUPT.isChecked(),
            
            "use_advanced_propagate": self.checkBox_ukfTrapezoidal.isChecked(),
            "q_a": config.Q_A,
            "q_g": config.Q_G,
            "r_uwb": config.R_UWB,
            "test_ukf_q_r_params": config.TEST_UKF_Q_R_Params
        }
        return params

    def save_to_config_file(self):
        """Lưu các biến hiện tại vào file config.py để persistence"""
        config_path = os.path.join(current_dir, "module", "config.py")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()

            def replace_var(name, val, is_str=False, is_bool=False, force_val=None):
                nonlocal content
                actual_val = force_val if force_val is not None else val
                if is_str:
                    if actual_val is None:
                        content = re.sub(rf"^(\s*{name}\s*=\s*).*?$", lambda m: m.group(1) + "None", content, flags=re.MULTILINE)
                    else:
                        content = re.sub(rf"^(\s*{name}\s*=\s*).*?$", lambda m: m.group(1) + f'r"{actual_val}"', content, flags=re.MULTILINE)
                elif is_bool:
                    content = re.sub(rf"^(\s*{name}\s*=\s*).*?$", lambda m: m.group(1) + str(actual_val), content, flags=re.MULTILINE)
                else:
                    # Fix formatting for floats
                    if isinstance(actual_val, float):
                        val_str = f"{actual_val}"
                    else:
                        val_str = str(actual_val)
                    content = re.sub(rf"^(\s*{name}\s*=\s*).*?$", lambda m: m.group(1) + val_str, content, flags=re.MULTILINE)

            # TXT
            replace_var("OUTPUT_TXT_ENABLED", self.checkBox_txtEnable.isChecked(), is_bool=True)
            if self.checkBox_csvPath.isChecked():
                replace_var("SOURCE_DATA_FILE", config.SOURCE_DATA_FILE, is_str=True)
            else:
                replace_var("SOURCE_DATA_FILE", None, is_str=True)

            # UKF Constants
            replace_var("UKF_ALPHA", config.UKF_ALPHA)
            replace_var("UKF_KAPPA", config.UKF_KAPPA)
            replace_var("UKF_BETA", config.UKF_BETA)

            # Anchors
            replace_var("ANCHOR_1_X", config.ANCHOR_1_X)
            replace_var("ANCHOR_1_Y", config.ANCHOR_1_Y)
            replace_var("ANCHOR_2_X", config.ANCHOR_2_X)
            replace_var("ANCHOR_2_Y", config.ANCHOR_2_Y)
            replace_var("ANCHOR_3_X", config.ANCHOR_3_X)
            replace_var("ANCHOR_3_Y", config.ANCHOR_3_Y)
            replace_var("ANCHOR_4_X", config.ANCHOR_4_X)
            replace_var("ANCHOR_4_Y", config.ANCHOR_4_Y)

            # Rectangle
            replace_var("RECT_WIDTH", config.RECT_WIDTH)
            replace_var("RECT_HEIGHT", config.RECT_HEIGHT)

            # EMA & ZUPT
            replace_var("IMU_EMA_ALPHA", config.IMU_EMA_ALPHA)
            replace_var("IMU_ZUPT_THRESHOLD", config.IMU_ZUPT_THRESHOLD)
            replace_var("IMU_ZUPT_FRAMES", config.IMU_ZUPT_FRAMES)

            # TEST Mode
            replace_var("TEST_UKF_Q_R_Params", config.TEST_UKF_Q_R_Params, is_bool=True)

            # Noise STD - KHÔNG LƯU RA FILE vì chúng nằm trong khối if cố định
            # replace_var("IMU_NOISE_STD_AX", ...) - BỊ LOẠI BỎ ĐỂ BẢO VỆ KHỐI IF
            

            # Q/R Manual Values
            replace_var("Q_A_MANUAL", config.Q_A_MANUAL)
            replace_var("Q_G_MANUAL", config.Q_G_MANUAL)
            replace_var("R_UWB_MANUAL", config.R_UWB_MANUAL)

            # Initial P
            replace_var("P_PX", config.P_PX)
            replace_var("P_PY", config.P_PY)
            replace_var("P_VX", config.P_VX)
            replace_var("P_VY", config.P_VY)
            replace_var("P_THETA", config.P_THETA)
            replace_var("P_BAX", config.P_BAX)
            replace_var("P_BAY", config.P_BAY)
            replace_var("P_BGZ", config.P_BGZ)

            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"Error saving config: {e}")

    def run_simulation(self):
        """Hàm chạy mô phỏng"""
        params = self.get_params_for_simulation()
        
        # Cập nhật biases từ file CSV (logic cũ)
        def handle_biases(ax, ay, gz):
            # Không cần cập nhật UI ở đây vì PyQt đã có binding rồi, 
            # nhưng nếu muốn hiển thị chính xác bias trích xuất được:
            # config.IMU_BIAS_AX = ax ...
            pass

        self.pushButton_run.setEnabled(False)
        self.pushButton_run.setText("Running...")
        QtWidgets.QApplication.processEvents()

        # Lưu lại file config trước khi chạy
        self.save_to_config_file()

        try:
            run_simulation_with_params(params, on_init_parsed=handle_biases)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Lỗi mô phỏng", str(e))
        finally:
            self.pushButton_run.setEnabled(True)
            self.pushButton_run.setText("Run")

    def generate_log_file(self):
        """Gen file .log tu file CSV dang chon hoac CSV moi nhat."""
        source_path = None
        if self.checkBox_csvPath.isChecked():
            source_path = self.lineEdit_csvPath.text().strip() or None
            config.SOURCE_DATA_FILE = source_path
            self.save_to_config_file()

        self.pushButton_genLogfile.setEnabled(False)
        self.pushButton_genLogfile.setText("Generating...")
        QtWidgets.QApplication.processEvents()

        try:
            log_path, html_path = generate_log_from_csv(source_path)
            QtWidgets.QMessageBox.information(
                self,
                "Gen log file",
                f"Da tao file log:\n{log_path}\n\nFile mau de xem tren browser:\n{html_path}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Loi gen log file", str(e))
        finally:
            self.pushButton_genLogfile.setEnabled(True)
            self.pushButton_genLogfile.setText("Gen log file")

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = UKFSimulationGUI()
    signal.signal(signal.SIGINT, lambda *_: (window.close(), app.quit()))
    sigint_timer = QtCore.QTimer()
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start(100)
    window.show()
    sys.exit(app.exec())
