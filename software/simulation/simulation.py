"""UKF simulation for UWB + IMU sensor fusion.

This module simulates a 10x10 meter room with three anchors in three corners.
It generates a reference trajectory, noisy IMU and UWB measurements, then
runs an Unscented Kalman Filter to fuse the data and compare results.
"""

from dataclasses import dataclass
from typing import Dict, Tuple

import matplotlib.pyplot as plt
import numpy as np

OUTPUT_FILE = r"D:\HOC\S\STM32\IDE\DATN\uwb-rtls\software\simulation\simulation.txt"

# ---------------------------------------------------------------------------
# Simulation configuration
# ---------------------------------------------------------------------------
ROOM_SIZE_M = 10.0  # Room dimension in meters (square room)
SIMULATION_TIME_S = 20.0  # Total simulation time in seconds
IMU_SAMPLE_RATE_HZ = 100  # IMU update rate
UWB_SAMPLE_RATE_HZ = 10  # UWB update rate
DT_IMU = 1.0 / IMU_SAMPLE_RATE_HZ
UWB_STEP = int(IMU_SAMPLE_RATE_HZ / UWB_SAMPLE_RATE_HZ)
RANDOM_SEED = 42  # Seed for repeatable simulation results

# IMU bias and noise configuration
IMU_BIAS_AX = 0.05  # Constant accelerometer bias in m/s^2
IMU_BIAS_AY = -0.03  # Constant accelerometer bias in m/s^2
IMU_BIAS_GZ = np.deg2rad(0.6)  # Constant gyro bias in rad/s (yaw drift)
IMU_NOISE_STD_AX = 0.15  # Accelerometer noise standard deviation in m/s^2
IMU_NOISE_STD_AY = 0.15  # Accelerometer noise standard deviation in m/s^2
IMU_NOISE_STD_GZ = np.deg2rad(0.8)  # Gyro noise standard deviation in rad/s

# UWB noise and packet loss configuration
UWB_NOISE_STD_M = 0.1  # UWB distance noise standard deviation in meters
UWB_COMM_LOSS_RATE = 0.1  # Simulated communication packet loss probability
UWB_OUTLIER_RATE = 0.5  # Simulated unreliable measurement probability
UWB_OUTLIER_MULTIPLIER = 4.0  # Outlier multiplier to make bad packets unreliable

# Anchor positions in the room (three corners)
ANCHOR_POSITIONS = np.array([
    [0.0, 0.0],  # Anchor 0 at bottom-left corner
    [ROOM_SIZE_M, 0.0],  # Anchor 1 at bottom-right corner
    [0.0, ROOM_SIZE_M],  # Anchor 2 at top-left corner
])

# UKF configuration
UKF_STATE_SIZE = 8
UKF_PROCESS_NOISE_SIZE = 3
UKF_MEASUREMENT_SIZE = 3
UKF_ALPHA = 1e-3
UKF_KAPPA = 0.0
UKF_BETA = 2.0

# Initial filter uncertainty
INITIAL_P = np.diag([
    0.1,  # px uncertainty
    0.1,  # py uncertainty
    0.1,  # vx uncertainty
    0.1,  # vy uncertainty
    0.1,  # theta uncertainty
    1e-4,  # bias ax uncertainty
    1e-4,  # bias ay uncertainty
    1e-4,  # bias gz uncertainty
])

# Motion noise covariance for process augmentation [ax_noise, ay_noise, gz_noise]
PROCESS_NOISE_COV = np.diag([
    0.20**2,  # process acceleration noise in x
    0.20**2,  # process acceleration noise in y
    np.deg2rad(2.0) ** 2,  # process yaw rate noise
])

# Measurement noise covariance for UWB distances
MEASUREMENT_NOISE_COV = np.diag([
    UWB_NOISE_STD_M**2,
    UWB_NOISE_STD_M**2,
    UWB_NOISE_STD_M**2,
])

# Derived UKF parameters
UKF_AUGMENTED_SIZE = UKF_STATE_SIZE + UKF_PROCESS_NOISE_SIZE
UKF_NUM_SIGMA = 2 * UKF_AUGMENTED_SIZE + 1
UKF_LAMBDA = UKF_ALPHA**2 * (UKF_AUGMENTED_SIZE + UKF_KAPPA) - UKF_AUGMENTED_SIZE
UKF_GAMMA = np.sqrt(UKF_AUGMENTED_SIZE + UKF_LAMBDA)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class IMUSample:
    ax: float
    ay: float
    gz: float


@dataclass
class UKFContext:
    x: np.ndarray
    P: np.ndarray
    Wm: np.ndarray
    Wc: np.ndarray
    is_first_frame: bool
    X_sigma_pred: np.ndarray


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def normalize_angle(angle: float) -> float:
    """Normalize angle to the range [-pi, +pi]."""
    wrapped = (angle + np.pi) % (2.0 * np.pi) - np.pi
    return float(wrapped)


def compute_sigma_weights() -> Tuple[np.ndarray, np.ndarray]:
    """Compute mean and covariance weights for the UKF sigma points."""
    Wm = np.full(UKF_NUM_SIGMA, 1.0 / (2.0 * (UKF_AUGMENTED_SIZE + UKF_LAMBDA)))
    Wc = Wm.copy()
    Wm[0] = UKF_LAMBDA / (UKF_AUGMENTED_SIZE + UKF_LAMBDA)
    Wc[0] = Wm[0] + (1.0 - UKF_ALPHA**2 + UKF_BETA)
    return Wm, Wc

def format_matrix(name, mat):
    s = f"{name} =[\n"
    for row in mat:
        s += "[" + ", ".join(f"{v: .6f}" for v in row) + "]\n"
    s += "]\n\n"
    return s

def format_vector(name, vec):
    s = f"{name} = ["
    s += ", ".join(f"{v: .6f}" for v in vec)
    s += "]\n\n"
    return s

def dump_ukf_info(ctx: UKFContext):
    with open(OUTPUT_FILE, "w") as f:
        # --- DEFINE ---
        f.write("===== UKF DEFINE =====\n\n")
        f.write(f"UKF_STATE_SIZE = {UKF_STATE_SIZE}\n")
        f.write(f"UKF_PROCESS_NOISE_SIZE = {UKF_PROCESS_NOISE_SIZE}\n")
        f.write(f"UKF_MEASUREMENT_SIZE = {UKF_MEASUREMENT_SIZE}\n")
        f.write(f"UKF_ALPHA = {UKF_ALPHA}\n")
        f.write(f"UKF_KAPPA = {UKF_KAPPA}\n")
        f.write(f"UKF_BETA = {UKF_BETA}\n")
        f.write(f"UKF_LAMBDA = {UKF_LAMBDA}\n")
        f.write(f"UKF_GAMMA = {UKF_GAMMA}\n\n")

        f.write(format_matrix("INITIAL_P", INITIAL_P))
        f.write(format_matrix("PROCESS_NOISE_COV", PROCESS_NOISE_COV))
        f.write(format_matrix("MEASUREMENT_NOISE_COV", MEASUREMENT_NOISE_COV))

        # --- INIT ---
        f.write("===== UKF INIT =====\n\n")
        f.write(format_vector("x_init", ctx.x))
        f.write(format_matrix("P_init", ctx.P))
        f.write(format_vector("Wm", ctx.Wm))
        f.write(format_vector("Wc", ctx.Wc))

# ---------------------------------------------------------------------------
# Reference trajectory generation
# ---------------------------------------------------------------------------

def generate_reference_trajectory(
    total_time_s: float,
    sample_rate_hz: float,
) -> Dict[str, np.ndarray]:
    """Generate a realistic reference trajectory inside a 10x10 room.

    The trajectory runs near three anchor corners without traveling exactly to the
    anchor positions.
    """
    timestamps = np.arange(0.0, total_time_s, 1.0 / sample_rate_hz)
    cyclic_speed = 0.3

    # Closed path that remains inside the room and passes near three anchor corners.
    phi = 2.0 * np.pi * timestamps / total_time_s
    x = 4.5 + 3.2 * np.cos(phi) + 0.5 * np.sin(2.0 * phi)
    y = 4.5 + 3.2 * np.sin(phi) + 0.5 * np.cos(2.0 * phi)

    vx = np.gradient(x, timestamps)
    vy = np.gradient(y, timestamps)
    ax = np.gradient(vx, timestamps)
    ay = np.gradient(vy, timestamps)

    theta = np.arctan2(vy, vx)
    gz = np.gradient(theta, timestamps)
    gz = np.array([normalize_angle(v) for v in gz])

    return {
        "timestamps": timestamps,
        "x": x,
        "y": y,
        "theta": theta,
        "vx": vx,
        "vy": vy,
        "ax": ax,
        "ay": ay,
        "gz": gz,
    }


# ---------------------------------------------------------------------------
# Sensor data simulation
# ---------------------------------------------------------------------------

def simulate_imu_data(
    reference: Dict[str, np.ndarray],
    bias_ax: float,
    bias_ay: float,
    bias_gz: float,
    noise_std_ax: float,
    noise_std_ay: float,
    noise_std_gz: float,
) -> Dict[str, np.ndarray]:
    """Simulate noisy IMU measurements from the reference trajectory."""
    n = len(reference["timestamps"])
    ax_meas = np.zeros(n)
    ay_meas = np.zeros(n)
    gz_meas = np.zeros(n)

    for i in range(n):
        cos_theta = np.cos(reference["theta"][i])
        sin_theta = np.sin(reference["theta"][i])

        # Convert world acceleration to body-frame acceleration.
        ax_body = (
            reference["ax"][i] * cos_theta
            + reference["ay"][i] * sin_theta
        )
        ay_body = (
            -reference["ax"][i] * sin_theta
            + reference["ay"][i] * cos_theta
        )

        ax_meas[i] = ax_body + bias_ax + np.random.normal(0.0, noise_std_ax)
        ay_meas[i] = ay_body + bias_ay + np.random.normal(0.0, noise_std_ay)
        gz_meas[i] = reference["gz"][i] + bias_gz + np.random.normal(0.0, noise_std_gz)

    return {
        "ax": ax_meas,
        "ay": ay_meas,
        "gz": gz_meas,
        "bias_ax": np.full(n, bias_ax),
        "bias_ay": np.full(n, bias_ay),
        "bias_gz": np.full(n, bias_gz),
    }


def simulate_uwb_data(
    x_real: np.ndarray,
    y_real: np.ndarray,
    anchor_positions: np.ndarray,
    sample_rate_hz: float,
    total_time_s: float,
    noise_std_m: float,
    packet_loss_rate: float,
    outlier_rate: float,
    outlier_multiplier: float,
) -> Dict[str, np.ndarray]:
    """Simulate noisy UWB distance readings with packet loss and outliers."""
    timestamps = np.arange(0.0, total_time_s, 1.0 / IMU_SAMPLE_RATE_HZ)
    n = len(timestamps)
    d_noise = np.full((len(anchor_positions), n), np.nan)
    d_true = np.zeros((len(anchor_positions), n))
    valid = np.zeros(n, dtype=bool)
    packet_type = np.zeros(n, dtype=int)

    for i in range(n):
        for anchor_idx, anchor in enumerate(anchor_positions):
            d_true[anchor_idx, i] = np.hypot(
                x_real[i] - anchor[0], y_real[i] - anchor[1]
            )

        if i % UWB_STEP != 0:
            continue

        if np.random.rand() < packet_loss_rate:
            packet_type[i] = 1  # communication loss
            continue

        if np.random.rand() < outlier_rate:
            packet_type[i] = 2  # unreliable measurement
            valid[i] = True
            for anchor_idx in range(len(anchor_positions)):
                d_noise[anchor_idx, i] = (
                    d_true[anchor_idx, i]
                    + outlier_multiplier * noise_std_m * np.random.randn()
                )
            continue

        valid[i] = True
        for anchor_idx in range(len(anchor_positions)):
            d_noise[anchor_idx, i] = (
                d_true[anchor_idx, i]
                + np.random.normal(0.0, noise_std_m)
            )

    return {
        "timestamps": timestamps,
        "d_true": d_true,
        "d_noisy": d_noise,
        "valid": valid,
        "packet_type": packet_type,
    }


# ---------------------------------------------------------------------------
# Dead reckoning and UWB-only position reconstruction
# ---------------------------------------------------------------------------

def integrate_imu_dead_reckoning(
    imu_data: Dict[str, np.ndarray],
    dt: float,
    initial_pose: Tuple[float, float, float],
) -> Dict[str, np.ndarray]:
    n = len(imu_data["ax"])
    x = np.zeros(n)
    y = np.zeros(n)
    vx = np.zeros(n)
    vy = np.zeros(n)
    theta = np.zeros(n)

    x[0], y[0], theta[0] = initial_pose
    
    for i in range(1, n):
        # Lấy giá trị đo tại thời điểm i-1
        gz_meas = imu_data["gz"][i - 1]
        ax_meas = imu_data["ax"][i - 1]
        ay_meas = imu_data["ay"][i - 1]
        
        # 1. Cập nhật góc (dùng góc cũ để tính)
        gz_corrected = gz_meas - IMU_BIAS_GZ
        theta[i] = normalize_angle(theta[i - 1] + gz_corrected * dt)
        
        # 2. QUAN TRỌNG: Dùng góc CŨ để xoay gia tốc
        cos_theta_old = np.cos(theta[i - 1])
        sin_theta_old = np.sin(theta[i - 1])
        
        # Trừ bias khỏi gia tốc đo
        ax_body = ax_meas - IMU_BIAS_AX
        ay_body = ay_meas - IMU_BIAS_AY
        
        # Chuyển sang world frame dùng góc cũ
        ax_world = ax_body * cos_theta_old - ay_body * sin_theta_old
        ay_world = ax_body * sin_theta_old + ay_body * cos_theta_old
        
        # 3. Cập nhật vận tốc và vị trí
        vx[i] = vx[i - 1] + ax_world * dt
        vy[i] = vy[i - 1] + ay_world * dt
        x[i] = x[i - 1] + vx[i - 1] * dt + 0.5 * ax_world * dt**2
        y[i] = y[i - 1] + vy[i - 1] * dt + 0.5 * ay_world * dt**2
    
    return {"x": x, "y": y, "vx": vx, "vy": vy, "theta": theta}


def trilateration_2d(
    distances: np.ndarray,
    anchors: np.ndarray,
    previous_position: Tuple[float, float],
) -> Tuple[float, float]:
    """Estimate 2D position from three UWB distances.

    Solves the linear system produced by subtracting the first anchor equation.
    """
    if np.any(np.isnan(distances)):
        return previous_position

    A = []
    b = []
    p0 = anchors[0]
    d0 = distances[0]

    for anchor_idx in range(1, len(anchors)):
        pi = anchors[anchor_idx]
        di = distances[anchor_idx]
        A.append([2.0 * (pi[0] - p0[0]), 2.0 * (pi[1] - p0[1])])
        b.append(
            d0**2
            - di**2
            + pi[0]**2
            + pi[1]**2
            - p0[0]**2
            - p0[1]**2
        )

    A = np.array(A)
    b = np.array(b)

    try:
        solution, residuals, rank, _ = np.linalg.lstsq(A, b, rcond=None)
        if rank < 2:
            return previous_position
        return float(solution[0]), float(solution[1])
    except np.linalg.LinAlgError:
        return previous_position


def compute_uwb_only_positions(
    uwb_data: Dict[str, np.ndarray],
    anchors: np.ndarray,
) -> Dict[str, np.ndarray]:
    """Compute an approximate path using only UWB measurements."""
    n = len(uwb_data["timestamps"])
    x = np.zeros(n)
    y = np.zeros(n)
    previous_position = (0.0, 0.0)

    for i in range(n):
        if uwb_data["valid"][i]:
            distances = uwb_data["d_noisy"][..., i]
            position = trilateration_2d(distances, anchors, previous_position)
            x[i], y[i] = position
            previous_position = position
        else:
            x[i], y[i] = previous_position

    return {"x": x, "y": y}


# ---------------------------------------------------------------------------
# UKF implementation
# ---------------------------------------------------------------------------

def create_ukf_context(initial_state: np.ndarray) -> UKFContext:
    """Create and initialize UKF context.

    The UKF stores its state, covariance, precomputed weights, and a buffer
    for predicted sigma points.
    """
    Wm, Wc = compute_sigma_weights()
    X_sigma_pred = np.zeros((UKF_STATE_SIZE, UKF_NUM_SIGMA))
    return UKFContext(
        x=initial_state.copy(),
        P=INITIAL_P.copy(),
        Wm=Wm,
        Wc=Wc,
        is_first_frame=True,
        X_sigma_pred=X_sigma_pred,
    )


def generate_augmented_sigma_points(context: UKFContext) -> np.ndarray:
    """Generate augmented sigma points from state and process noise."""
    x_aug = np.zeros(UKF_AUGMENTED_SIZE)
    x_aug[:UKF_STATE_SIZE] = context.x

    P_aug = np.zeros((UKF_AUGMENTED_SIZE, UKF_AUGMENTED_SIZE))
    P_aug[:UKF_STATE_SIZE, :UKF_STATE_SIZE] = context.P
    P_aug[UKF_STATE_SIZE :, UKF_STATE_SIZE :] = PROCESS_NOISE_COV

    try:
        sqrt_P_aug = np.linalg.cholesky(P_aug)
    except np.linalg.LinAlgError:
        sqrt_P_aug = np.linalg.cholesky(P_aug + np.eye(UKF_AUGMENTED_SIZE) * 1e-6)

    sigma_points = np.zeros((UKF_AUGMENTED_SIZE, UKF_NUM_SIGMA))
    sigma_points[:, 0] = x_aug
    for i in range(UKF_AUGMENTED_SIZE):
        delta = UKF_GAMMA * sqrt_P_aug[:, i]
        sigma_points[:, i + 1] = x_aug + delta
        sigma_points[:, i + 1 + UKF_AUGMENTED_SIZE] = x_aug - delta

    return sigma_points


def propagate_sigma_point(
    sigma_point: np.ndarray,
    imu_sample: IMUSample,
    dt: float,
) -> np.ndarray:
    """Propagate a single augmented sigma point through the motion model."""
    x = sigma_point[:UKF_STATE_SIZE].copy()
    noise = sigma_point[UKF_STATE_SIZE : UKF_STATE_SIZE + UKF_PROCESS_NOISE_SIZE]

    px, py, vx, vy, theta, bax, bay, bgz = x
    n_ax, n_ay, n_gz = noise

    corrected_ax = imu_sample.ax - bax + n_ax
    corrected_ay = imu_sample.ay - bay + n_ay
    corrected_gz = imu_sample.gz - bgz + n_gz

    theta_new = normalize_angle(theta + corrected_gz * dt)
    cos_theta = np.cos(theta)
    sin_theta = np.sin(theta)

    ax_world = corrected_ax * cos_theta - corrected_ay * sin_theta
    ay_world = corrected_ax * sin_theta + corrected_ay * cos_theta

    px_new = px + vx * dt + 0.5 * ax_world * dt**2
    py_new = py + vy * dt + 0.5 * ay_world * dt**2
    vx_new = vx + ax_world * dt
    vy_new = vy + ay_world * dt

    x_pred = np.array(
        [px_new, py_new, vx_new, vy_new, theta_new, bax, bay, bgz], dtype=float
    )
    return x_pred


def ukf_predict(
    context: UKFContext,
    imu_sample: IMUSample,
    dt: float,
) -> None:
    """Perform the UKF prediction step using the latest IMU sample."""
    if context.is_first_frame:
        sigma_points_aug = generate_augmented_sigma_points(context)
        context.is_first_frame = True # note
    else:
        sigma_points_aug = np.vstack(
            [context.X_sigma_pred, np.zeros((UKF_PROCESS_NOISE_SIZE, UKF_NUM_SIGMA))]
        )
        # The extra rows are not used after the first frame, but they preserve shape.

    for m in range(UKF_NUM_SIGMA):
        context.X_sigma_pred[:, m] = propagate_sigma_point(
            sigma_points_aug[:, m], imu_sample, dt
        )

    context.x = np.zeros(UKF_STATE_SIZE)
    for m in range(UKF_NUM_SIGMA):
        context.x += context.Wm[m] * context.X_sigma_pred[:, m]

    context.x[4] = normalize_angle(context.x[4])

    context.P = np.zeros((UKF_STATE_SIZE, UKF_STATE_SIZE))
    for m in range(UKF_NUM_SIGMA):
        diff = context.X_sigma_pred[:, m] - context.x
        diff[4] = normalize_angle(diff[4])
        context.P += context.Wc[m] * np.outer(diff, diff)


def ukf_update(
    context: UKFContext,
    d_meas: np.ndarray,
    anchors: np.ndarray,
) -> None:
    """Perform the UKF update step when UWB data is available."""
    if np.any(np.isnan(d_meas)):
        return

    z_sigma = np.zeros((UKF_MEASUREMENT_SIZE, UKF_NUM_SIGMA))
    for m in range(UKF_NUM_SIGMA):
        px, py = context.X_sigma_pred[0, m], context.X_sigma_pred[1, m]
        for anchor_idx, anchor in enumerate(anchors):
            z_sigma[anchor_idx, m] = np.hypot(px - anchor[0], py - anchor[1])

    z_mean = np.zeros(UKF_MEASUREMENT_SIZE)
    for m in range(UKF_NUM_SIGMA):
        z_mean += context.Wm[m] * z_sigma[:, m]

    S = np.zeros((UKF_MEASUREMENT_SIZE, UKF_MEASUREMENT_SIZE))
    Tc = np.zeros((UKF_STATE_SIZE, UKF_MEASUREMENT_SIZE))
    for m in range(UKF_NUM_SIGMA):
        z_diff = z_sigma[:, m] - z_mean
        x_diff = context.X_sigma_pred[:, m] - context.x
        x_diff[4] = normalize_angle(x_diff[4])
        S += context.Wc[m] * np.outer(z_diff, z_diff)
        Tc += context.Wc[m] * np.outer(x_diff, z_diff)

    S += MEASUREMENT_NOISE_COV

    try:
        K = Tc @ np.linalg.inv(S)
    except np.linalg.LinAlgError:
        return

    y = d_meas - z_mean
    context.x += K @ y
    context.x[4] = normalize_angle(context.x[4])
    context.P -= K @ S @ K.T
    context.P = 0.5 * (context.P + context.P.T)
    context.is_first_frame = True


# ---------------------------------------------------------------------------
# Visualization and diagnostics
# ---------------------------------------------------------------------------

def plot_input_data(
    timestamps: np.ndarray,
    reference: Dict[str, np.ndarray],
    imu_data: Dict[str, np.ndarray],
    uwb_data: Dict[str, np.ndarray],
) -> None:
    """Cụm đồ thị input: hiển thị dữ liệu thực tế và dữ liệu mô phỏng (nhiễu).

    Cửa sổ 1: IMU – ax, ay, gz (thực tế vs nhiễu)
    Cửa sổ 2: UWB – d0, d1, d2 (thực tế vs nhiễu)
    """
    # --- Cửa sổ 1: IMU input ---
    fig1, axs1 = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    fig1.suptitle("Cụm đồ thị INPUT – IMU: dữ liệu thực tế vs mô phỏng nhiễu", fontsize=13)

    imu_pairs = [
        ("ax", "ax_real", "ax_noise", "ax (m/s²)"),
        ("ay", "ay_real", "ay_noise", "ay (m/s²)"),
        ("gz", "gz_real", "gz_noise", "gz (rad/s)"),
    ]
    ref_keys = ["ax", "ay", "gz"]
    imu_keys = ["ax", "ay", "gz"]

    for idx, (_, label_real, label_noise, ylabel) in enumerate(imu_pairs):
        axs1[idx].plot(timestamps, reference[ref_keys[idx]],
                       label=label_real, linewidth=1.4, color="steelblue")
        axs1[idx].plot(timestamps, imu_data[imu_keys[idx]],
                       label=label_noise, alpha=0.65, color="tomato", linewidth=0.9)
        axs1[idx].set_ylabel(ylabel)
        axs1[idx].legend(loc="upper right")
        axs1[idx].grid(True, alpha=0.4)

    axs1[2].set_xlabel("Thời gian (s)")
    plt.tight_layout(rect=[0, 0.0, 1, 0.96])

    # --- Cửa sổ 2: UWB input ---
    fig2, axs2 = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    fig2.suptitle("Cụm đồ thị INPUT – UWB: khoảng cách thực tế vs mô phỏng nhiễu", fontsize=13)

    colors_real = ["steelblue", "seagreen", "mediumpurple"]
    colors_noise = ["tomato", "darkorange", "hotpink"]

    # UWB chỉ sample tại mỗi UWB_STEP – lấy mask các vị trí có data thật (không phải NaN)
    uwb_valid_mask = ~np.isnan(uwb_data["d_noisy"][0])

    for anchor_idx in range(3):
        axs2[anchor_idx].plot(
            timestamps,
            uwb_data["d_true"][anchor_idx],
            label=f"d{anchor_idx}_real",
            linewidth=1.4,
            color=colors_real[anchor_idx],
        )
        # Dùng scatter để hiển thị điểm rời rạc 10Hz thay vì plot liên tục với NaN
        axs2[anchor_idx].scatter(
            timestamps[uwb_valid_mask],
            uwb_data["d_noisy"][anchor_idx][uwb_valid_mask],
            label=f"d{anchor_idx}_noise",
            s=18,
            alpha=0.8,
            color=colors_noise[anchor_idx],
            zorder=4,
        )
        axs2[anchor_idx].set_ylabel("Khoảng cách (m)")
        axs2[anchor_idx].legend(loc="upper right")
        axs2[anchor_idx].grid(True, alpha=0.4)

    axs2[2].set_xlabel("Thời gian (s)")
    plt.tight_layout(rect=[0, 0.0, 1, 0.96])

    for fig in [fig1, fig2]:
        plt.figure(fig.number)
        plt.show(block=False)
        plt.waitforbuttonpress()
        plt.close()


def _draw_anchors(ax: plt.Axes) -> None:
    """Vẽ vị trí các anchor lên axes đã cho."""
    for idx, anchor in enumerate(ANCHOR_POSITIONS):
        ax.scatter(anchor[0], anchor[1], marker="^", s=120, zorder=5,
                   color="black", label=f"Anchor {idx}" if idx == 0 else f"Anchor {idx}")
        ax.annotate(f"A{idx}", xy=anchor, xytext=(anchor[0] + 0.2, anchor[1] + 0.2),
                    fontsize=8, color="black")


def plot_position_estimates(
    reference: Dict[str, np.ndarray],
    imu_estimate: Dict[str, np.ndarray],
    uwb_estimate: Dict[str, np.ndarray],
) -> None:
    """Cụm đồ thị vị trí: 3 cửa sổ riêng + 1 cửa sổ so sánh.

    Cửa sổ 1: Vị trí thực tế (reference trajectory)
    Cửa sổ 2: Vị trí tính ngược từ IMU nhiễu (dead reckoning)
    Cửa sổ 3: Vị trí tính ngược từ UWB nhiễu (trilateration)
    Cửa sổ 4: So sánh cả 3 trên cùng đồ thị
    """
    common_kwargs = dict(xlim=(-1, ROOM_SIZE_M + 1), ylim=(-1, ROOM_SIZE_M + 1))

    # --- Cửa sổ 1: Vị trí thực tế ---
    fig1, ax1 = plt.subplots(figsize=(6, 6))
    fig1.suptitle("Cụm đồ thị VỊ TRÍ [1/4] – Vị trí thực tế (reference)", fontsize=12)
    ax1.plot(reference["x"], reference["y"],
             label="Vị trí thực tế", color="steelblue", linewidth=2.2)
    ax1.scatter(reference["x"][0], reference["y"][0], color="lime", s=80,
                zorder=5, label="Điểm đầu")
    ax1.scatter(reference["x"][-1], reference["y"][-1], color="red", s=80,
                zorder=5, label="Điểm cuối")
    _draw_anchors(ax1)
    ax1.set_xlabel("X (m)")
    ax1.set_ylabel("Y (m)")
    ax1.set_xlim(*common_kwargs["xlim"])
    ax1.set_ylim(*common_kwargs["ylim"])
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.4)
    plt.tight_layout()

    # --- Cửa sổ 2: Vị trí từ IMU nhiễu ---
    fig2, ax2 = plt.subplots(figsize=(6, 6))
    fig2.suptitle("Cụm đồ thị VỊ TRÍ [2/4] – Tính ngược từ IMU nhiễu (dead reckoning)", fontsize=12)
    ax2.plot(imu_estimate["x"], imu_estimate["y"],
             label="IMU dead reckoning", color="tomato", linewidth=1.8)
    ax2.scatter(imu_estimate["x"][0], imu_estimate["y"][0], color="lime", s=80, zorder=5,
                label="Điểm đầu")
    _draw_anchors(ax2)
    ax2.set_xlabel("X (m)")
    ax2.set_ylabel("Y (m)")
    ax2.set_xlim(*common_kwargs["xlim"])
    ax2.set_ylim(*common_kwargs["ylim"])
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.4)
    plt.tight_layout()

    # --- Cửa sổ 3: Vị trí từ UWB nhiễu ---
    fig3, ax3 = plt.subplots(figsize=(6, 6))
    fig3.suptitle("Cụm đồ thị VỊ TRÍ [3/4] – Tính ngược từ UWB nhiễu (trilateration)", fontsize=12)
    ax3.plot(uwb_estimate["x"], uwb_estimate["y"],
             label="UWB trilateration", color="seagreen", linewidth=1.8)
    ax3.scatter(uwb_estimate["x"][0], uwb_estimate["y"][0], color="lime", s=80, zorder=5,
                label="Điểm đầu")
    _draw_anchors(ax3)
    ax3.set_xlabel("X (m)")
    ax3.set_ylabel("Y (m)")
    ax3.set_xlim(*common_kwargs["xlim"])
    ax3.set_ylim(*common_kwargs["ylim"])
    ax3.legend(fontsize=8)
    ax3.grid(True, alpha=0.4)
    plt.tight_layout()

    # --- Cửa sổ 4: So sánh 3 vị trí ---
    fig4, ax4 = plt.subplots(figsize=(7, 7))
    fig4.suptitle("Cụm đồ thị VỊ TRÍ [4/4] – So sánh: thực tế / IMU / UWB", fontsize=12)
    ax4.plot(reference["x"], reference["y"],
             label="Thực tế (reference)", color="steelblue", linewidth=2.2)
    ax4.plot(imu_estimate["x"], imu_estimate["y"],
             label="IMU dead reckoning", color="tomato", linewidth=1.5, alpha=0.85)
    ax4.plot(uwb_estimate["x"], uwb_estimate["y"],
             label="UWB trilateration", color="seagreen", linewidth=1.5, alpha=0.85)
    _draw_anchors(ax4)
    ax4.set_xlabel("X (m)")
    ax4.set_ylabel("Y (m)")
    ax4.set_xlim(*common_kwargs["xlim"])
    ax4.set_ylim(*common_kwargs["ylim"])
    ax4.legend(fontsize=9)
    ax4.grid(True, alpha=0.4)
    plt.tight_layout()

    for fig in [fig1, fig2, fig3, fig4]:
        plt.figure(fig.number)
        plt.show(block=False)
        plt.waitforbuttonpress()
        plt.close()


def plot_position_results(
    reference: Dict[str, np.ndarray],
    ukf_estimate: Dict[str, np.ndarray],
) -> None:
    """Cụm đồ thị KẾT QUẢ UKF.

    Cửa sổ duy nhất gồm 2 subplot:
      - Trái: đồ thị XY – so sánh quỹ đạo thực và UKF dự đoán
      - Phải: đồ thị x(t), y(t) theo thời gian
    """
    fig, axs = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle("Cụm đồ thị KẾT QUẢ – UKF: vị trí thực tế vs UKF dự đoán", fontsize=13)

    # --- Subplot trái: XY trajectory ---
    axs[0].plot(reference["x"], reference["y"],
                label="Thực tế (reference)", color="steelblue", linewidth=2.2)
    axs[0].plot(ukf_estimate["x"], ukf_estimate["y"],
                label="UKF dự đoán", color="darkorange", linewidth=1.8, linestyle="--")
    axs[0].scatter(reference["x"][0], reference["y"][0],
                   color="lime", s=90, zorder=5, label="Điểm đầu")
    axs[0].scatter(reference["x"][-1], reference["y"][-1],
                   color="red", s=90, zorder=5, label="Điểm cuối")
    _draw_anchors(axs[0])
    axs[0].set_title("Đồ thị XY")
    axs[0].set_xlabel("X (m)")
    axs[0].set_ylabel("Y (m)")
    axs[0].set_xlim(-1, ROOM_SIZE_M + 1)
    axs[0].set_ylim(-1, ROOM_SIZE_M + 1)
    axs[0].legend(fontsize=8)
    axs[0].grid(True, alpha=0.4)

    # --- Subplot phải: x(t) và y(t) ---
    time_axis = reference["timestamps"]
    axs[1].plot(time_axis, reference["x"],
                label="x thực tế", linestyle="-", linewidth=1.6, color="steelblue")
    axs[1].plot(time_axis, ukf_estimate["x"],
                label="x UKF", linestyle="--", linewidth=1.4, color="dodgerblue")
    axs[1].plot(time_axis, reference["y"],
                label="y thực tế", linestyle="-", linewidth=1.6, color="seagreen")
    axs[1].plot(time_axis, ukf_estimate["y"],
                label="y UKF", linestyle="--", linewidth=1.4, color="limegreen")
    axs[1].set_title("Vị trí theo thời gian")
    axs[1].set_xlabel("Thời gian (s)")
    axs[1].set_ylabel("Vị trí (m)")
    axs[1].legend(fontsize=8)
    axs[1].grid(True, alpha=0.4)

    plt.tight_layout(rect=[0, 0.0, 1, 0.95])
    
    plt.figure(fig.number)
    plt.show(block=False)
    plt.waitforbuttonpress()
    plt.close()


# ---------------------------------------------------------------------------
# Top-level simulation and control flow
# ---------------------------------------------------------------------------

def run_simulation() -> None:
    """Run the complete UWB + IMU UKF simulation."""
    np.random.seed(None)

    reference = generate_reference_trajectory(SIMULATION_TIME_S, IMU_SAMPLE_RATE_HZ)

    imu_data = simulate_imu_data(
        reference,
        bias_ax=IMU_BIAS_AX,
        bias_ay=IMU_BIAS_AY,
        bias_gz=IMU_BIAS_GZ,
        noise_std_ax=IMU_NOISE_STD_AX,
        noise_std_ay=IMU_NOISE_STD_AY,
        noise_std_gz=IMU_NOISE_STD_GZ,
    )

    uwb_data = simulate_uwb_data(
        reference["x"],
        reference["y"],
        ANCHOR_POSITIONS,
        UWB_SAMPLE_RATE_HZ,
        SIMULATION_TIME_S,
        UWB_NOISE_STD_M,
        UWB_COMM_LOSS_RATE,
        UWB_OUTLIER_RATE,
        UWB_OUTLIER_MULTIPLIER,
    )

    imu_dead_reckoning = integrate_imu_dead_reckoning(
        imu_data, DT_IMU, (reference["x"][0], reference["y"][0], reference["theta"][0])
    )

    uwb_only_estimate = compute_uwb_only_positions(uwb_data, ANCHOR_POSITIONS)

    ukf_initial_state = np.array(
        [
            reference["x"][0],
            reference["y"][0],
            reference["vx"][0],
            reference["vy"][0],
            reference["theta"][0],
            IMU_BIAS_AX,
            IMU_BIAS_AY,
            IMU_BIAS_GZ,
        ]
    )
    ukf_context = create_ukf_context(ukf_initial_state)

    dump_ukf_info(ukf_context)

    ukf_positions_x = np.zeros_like(reference["x"])
    ukf_positions_y = np.zeros_like(reference["y"])

    for i in range(len(reference["timestamps"])):
        imu_sample = IMUSample(
            ax=imu_data["ax"][i],
            ay=imu_data["ay"][i],
            gz=imu_data["gz"][i],
        )

        ukf_predict(ukf_context, imu_sample, DT_IMU)

        if i % UWB_STEP == 0:
            if uwb_data["valid"][i]:
                d_meas = uwb_data["d_noisy"][:, i]
                ukf_update(ukf_context, d_meas, ANCHOR_POSITIONS)
            else:
                # Skip update for lost or unreliable UWB packet
                pass

        ukf_positions_x[i] = ukf_context.x[0]
        ukf_positions_y[i] = ukf_context.x[1]

    ukf_estimate = {"x": ukf_positions_x, "y": ukf_positions_y}

    print("Simulation summary:")
    print(f"  Total time: {SIMULATION_TIME_S:.1f} s")
    print(f"  IMU rate: {IMU_SAMPLE_RATE_HZ} Hz")
    print(f"  UWB rate: {UWB_SAMPLE_RATE_HZ} Hz")
    print(f"  UWB packets lost: {np.sum(uwb_data['packet_type'] == 1)}")
    print(f"  UWB outliers: {np.sum(uwb_data['packet_type'] == 2)}")

    plot_input_data(reference["timestamps"], reference, imu_data, uwb_data)
    plot_position_estimates(reference, imu_dead_reckoning, uwb_only_estimate)
    plot_position_results(reference, ukf_estimate)


if __name__ == "__main__":
    run_simulation()