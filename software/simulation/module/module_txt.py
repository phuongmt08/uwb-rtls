import os
import numpy as np
from datetime import datetime

class UKFLogger:
    def __init__(self):
        self.file = None
        self.predict_count = 0
        self.update_count = 0
        self.nis_list = []
        
    def init_file(self, source_file="", suffix=""):
        now = datetime.now()
        date_folder = now.strftime("%d_%m_%y")
        timestamp = now.strftime("%d_%m_%y_%Hg_%Mp")
        
        base_dir = r"D:\HOC\S\STM32\IDE\DATN\uwb-rtls\software\simulation\txt"
        folder_path = os.path.join(base_dir, date_folder)
        os.makedirs(folder_path, exist_ok=True)
        
        suffix_str = f"_{suffix}" if suffix else ""
        file_path = os.path.join(folder_path, f"{timestamp}{suffix_str}.txt")
        self.file = open(file_path, "w", encoding="utf-8")
        if source_file:
            self.file.write(f"===== CONFIGURATION =====\n")
            self.file.write(f"Source CSV: {source_file}\n\n")
        print(f"[INFO] UKF Text Log file created: {file_path}")
        
    def close(self):
        if self.file:
            self.file.close()

    def write(self, text):
        if self.file:
            self.file.write(text)

    def format_vector(self, name, vec):
        s = f"{name} = [" + ", ".join(f"{v:10.6f}" for v in vec) + "]\n"
        return s

    def format_matrix(self, name, mat):
        s = f"{name} = [\n"
        for row in mat:
            s += " [" + ", ".join(f"{v:10.6f}" for v in row) + "]\n"
        s += "]\n"
        return s

    def log_define(self, num_state, num_predict_noise, num_update_noise, n, m, num_predict_sigma, num_update_sigma, alpha, kappa, beta, lambda_n, gamma_n, qa, qg, r_uwb):
        self.write("===== UKF DEFINE =====\n")
        self.write(f"NUM_STATE = {num_state}\n")
        self.write(f"NUM_PREDICT_NOISE = {num_predict_noise}\n")
        self.write(f"NUM_UPDATE_NOISE = {num_update_noise}\n")
        self.write(f"N = {n}, M = {m}\n")
        self.write(f"NUM_PREDICT_SIGMA = {num_predict_sigma}\n")
        self.write(f"NUM_UPDATE_SIGMA = {num_update_sigma}\n")
        self.write(f"UKF_ALPHA = {alpha:.6f}\n")
        self.write(f"UKF_KAPPA = {kappa:.6f}\n")
        self.write(f"UKF_BETA = {beta:.6f}\n")
        self.write(f"UKF_LAMBDA_N = {lambda_n:.6f}, GAMMA_N = {gamma_n:.6f}\n")
        self.write(f"UKF_LAMBDA_M = {lambda_n:.6f}, GAMMA_M = {gamma_n:.6f}\n")  # Assuming M=N logic or not implemented in python
        self.write(f"Qa = {qa:.6f}, Qg = {qg:.6f}, R_uwb = {r_uwb:.6f}\n")

    def log_init(self, x_init, p_init, q_data, r_data, wm_n, wc_n):
        self.write("===== UKF INIT =====\n")
        self.write(self.format_vector("ukf.state", x_init))
        self.write(self.format_matrix("ukf.P_data", p_init))
        self.write(self.format_matrix("ukf.Q_data", q_data))
        self.write(self.format_matrix("ukf.R_data", r_data))
        # Print first 5 elements for weights like C
        wmn_str = ", ".join(f"{v:.6f}" for v in wm_n[:5]) + ", ..."
        wcn_str = ", ".join(f"{v:.6f}" for v in wc_n[:5]) + ", ..."
        self.write(f"ukf.Wm_N = [{wmn_str}]\n")
        self.write(f"ukf.Wc_N = [{wcn_str}]\n")

    def log_predict(self, P_aug, L_aug, x_aug, sigma_points_aug, X_sigma_pred_kinematic, x_mean, P_data, event_line=""):
        self.predict_count += 1
        self.write(f"\n--- PREDICT {self.predict_count} ---\n")
        if event_line:
            self.write(f"CSV Line: {event_line}\n")
        
        if P_aug is not None:
            self.write(self.format_matrix("P_aug", P_aug))
        if L_aug is not None:
            self.write(self.format_matrix("L_aug", L_aug))
        if x_aug is not None:
            self.write(self.format_vector("x_aug", x_aug))
        
        if sigma_points_aug is not None:
            num_sigma = sigma_points_aug.shape[1]
            self.write(f"\n===== ALL SIGMA POINTS {self.predict_count} (NUM_PREDICT_SIGMA={num_sigma}) =====\n")
            for i in range(num_sigma):
                state = sigma_points_aug[:8, i]
                noise = sigma_points_aug[8:, i]
                state_str = ", ".join(f"{v: .6f}" for v in state)
                noise_str = ", ".join(f"{v: .6f}" for v in noise)
                self.write(f"Sigma {i:02d}: [{state_str}] | Noise: [{noise_str}]\n")

        self.write("===== AFTER KINEMATIC =====\n")
        self.write(self.format_matrix("X_sigma_pred", X_sigma_pred_kinematic))
        self.write(self.format_vector("x_mean", x_mean))
        self.write(self.format_matrix("P_data", P_data))

    def log_update(self, z_sigma, z_mean, P_dd, P_xd, K, y, D_real, update_val, trace_P, event_line=""):
        self.update_count += 1
        self.write(f"\n--- UPDATE {self.update_count} ---\n")
        if event_line:
            self.write(f"CSV Line: {event_line}\n")
        self.write(self.format_matrix("z_sigma", z_sigma))
        self.write(self.format_vector("z_mean", z_mean))
        self.write(self.format_matrix("P_dd (S matrix)", P_dd))
        self.write(self.format_matrix("P_xd (Tc matrix)", P_xd))
        self.write(self.format_matrix("K_data", K))
        self.write(self.format_vector("Innovation (v)", y))
        self.write(self.format_vector("update_val", update_val))
        
        try:
            P_dd_inv = np.linalg.inv(P_dd)
            # NIS = v^T * S^-1 * v
            nis = float(np.dot(y.T, np.dot(P_dd_inv, y)))
        except np.linalg.LinAlgError:
            nis = 0.0
            
        self.nis_list.append(nis)
        mean_nis = np.mean(self.nis_list)
        
        self.write(f"Trace(P) = {trace_P:.6f}\n")
        self.write(f"NIS = {nis:.6f}\n")
        self.write(f"Mean NIS = {mean_nis:.6f}\n")

# Global logger instance
logger = UKFLogger()
