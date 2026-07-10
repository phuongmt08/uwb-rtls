import numpy as np
from typing import Dict, Tuple
from .config import SensorEvent
from .module_ukf import normalize_angle

def calculate_imu_dead_reckoning_path(
    events: list[SensorEvent],
    initial_pose: Tuple[float, float, float],
    imu_bias_ax: float,
    imu_bias_ay: float,
    imu_bias_gz: float
) -> Dict[str, np.ndarray]:
    n = len(events)
    x = np.zeros(n)
    y = np.zeros(n)
    vx = np.zeros(n)
    vy = np.zeros(n)
    theta = np.zeros(n)
    times = np.zeros(n)

    x[0], y[0], theta[0] = initial_pose
    current_time = 0.0
    
    last_gz = imu_bias_gz # Mặc định coi bias là giá trị ban đầu
    last_ax = imu_bias_ax
    last_ay = imu_bias_ay
    
    for i in range(1, n):
        event = events[i]
        dt = event.dt
        current_time += dt
        times[i] = current_time
        
        # Cập nhật dữ liệu IMU mới nhất nếu có
        if event.type in ["Predict", "Init"]:
            last_gz = event.gz
            last_ax = event.ax
            last_ay = event.ay
        
        # Tính toán góc Yaw (theta)
        gz_corrected = last_gz - imu_bias_gz
        theta[i] = normalize_angle(theta[i - 1] + gz_corrected * dt)
        
        # Tính toán vị trí (x, y)
        cos_theta = np.cos(theta[i]) # Dùng góc mới để chiếu gia tốc (hoặc theta[i-1])
        sin_theta = np.sin(theta[i])
        
        ax_body = last_ax - imu_bias_ax
        ay_body = last_ay - imu_bias_ay
        
        ax_world = ax_body * cos_theta - ay_body * sin_theta
        ay_world = ax_body * sin_theta + ay_body * cos_theta
        
        vx[i] = vx[i - 1] + ax_world * dt
        vy[i] = vy[i - 1] + ay_world * dt
        x[i] = x[i - 1] + vx[i - 1] * dt + 0.5 * ax_world * dt**2
        y[i] = y[i - 1] + vy[i - 1] * dt + 0.5 * ay_world * dt**2
    
    return {"timestamps": times, "x": x, "y": y, "vx": vx, "vy": vy, "theta": theta}

def trilateration_2d(
    distances: np.ndarray,
    anchors: np.ndarray,
    previous_position: Tuple[float, float],
) -> Tuple[float, float]:
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
            d0**2 - di**2 + pi[0]**2 + pi[1]**2 - p0[0]**2 - p0[1]**2
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

def calculate_uwb_only_path(
    events: list[SensorEvent],
    anchors: np.ndarray,
    initial_pose: Tuple[float, float],
) -> Dict[str, np.ndarray]:
    n = len(events)
    x = np.zeros(n)
    y = np.zeros(n)
    theta = np.zeros(n)
    
    previous_position = initial_pose

    for i in range(n):
        event = events[i]
        d_meas_all = event.distances[:len(anchors)]
        active_indices = [idx for idx, d in enumerate(d_meas_all) if d > 1e-6]
        
        if event.type == "Update" and len(active_indices) >= 3:
            active_d_meas = d_meas_all[active_indices][:3]
            active_anchors = anchors[active_indices][:3]
            position = trilateration_2d(active_d_meas, active_anchors, previous_position)
            x[i], y[i] = position
            previous_position = position
        else:
            x[i], y[i] = previous_position

    return {"x": x, "y": y, "theta": theta}
