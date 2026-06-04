"""
module_ukf_stm.py — UKF Engine Float32, port trung thực từ sys_sensor_fusion.c

Logic giống hệt sys_sensor_fusion.c:
- Predict: giữ sigma points qua nhiều lần predict (không regenerate trừ khi is_first_frame)
- Predict: 2 phiên bản (normal Euler / advanced Trapezoidal), toggle CIRCULAR_MEAN
- Update: Augmented UKF [P;R], noise term trong D_sigma
- Update: P -= K @ Pxd.T
- Tất cả tính toán dùng np.float32
"""

import numpy as np
from . import config_stm as config
from .config_stm import UKFContext_STM, IMUSample

# ==========================================
# TOGGLE FLAGS — bật/tắt các tính năng
# ==========================================
CIRCULAR_MEAN = False      # False = linear mean + normalize (giống C)
                           # True  = circular mean arctan2
P_REGULARIZATION = False   # False = không symmetrize/epsilon (giống C)
                           # True  = symmetrize + epsilon

# ==========================================
# Helper: float32 wrapper
# ==========================================
def f32(val):
    """Chuyển sang np.float32"""
    return np.float32(val)

def f32_arr(arr):
    """Chuyển array sang float32"""
    return np.array(arr, dtype=np.float32)

# ==========================================
# normalize_angle — giống C: fmodf + clamp
# ==========================================
def normalize_angle(angle):
    """Giống normalize_angle() trong sys_sensor_fusion.c"""
    angle = f32(angle)
    PI = config.SYS_SENSOR_FUSION_PI
    TWO_PI = config.SYS_SENSOR_FUSION_2PI
    angle = np.float32(np.fmod(angle, TWO_PI))
    if angle > PI:
        angle -= TWO_PI
    if angle < -PI:
        angle += TWO_PI
    return angle

# ==========================================
# Format helpers cho logging
# ==========================================
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

# ==========================================
# init_ukf_logging — log các hằng số ban đầu
# ==========================================
def init_ukf_logging(ctx: UKFContext_STM):
    if ctx.logger:
        qa = float(ctx.Q[0, 0])
        qg = float(ctx.Q[2, 2])
        r_uwb = float(ctx.R[0, 0])
        ctx.logger.log_define(
            num_state=config.UKF_STATE_SIZE,
            num_predict_noise=config.UKF_PROCESS_NOISE_SIZE,
            num_update_noise=config.UKF_MEASUREMENT_SIZE,
            n=config.N,
            m=config.M,
            num_predict_sigma=config.NUM_PREDICT_SIGMA,
            num_update_sigma=config.NUM_UPDATE_SIGMA,
            alpha=config.UKF_ALPHA,
            kappa=config.UKF_KAPPA,
            beta=config.UKF_BETA,
            lambda_n=config.UKF_LAMBDA_N,
            gamma_n=float(config.GAMMA_N),
            qa=qa,
            qg=qg,
            r_uwb=r_uwb
        )
        ctx.logger.log_init(
            ctx.x, ctx.P, ctx.Q, ctx.R, ctx.Wm_N, ctx.Wc_N
        )

# ==========================================
# create_ukf_context_stm — giống sys_sensor_fusion_init()
# ==========================================
def create_ukf_context_stm(initial_state: np.ndarray) -> UKFContext_STM:
    """
    Tạo UKF context giống sys_sensor_fusion_init().
    P, Q, R lấy từ config_stm (cho phép user sửa từ GUI).
    Weights tính riêng cho predict (N) và update (M).
    """
    # State — float32
    x = f32_arr(initial_state)

    # P — lấy từ config (có thể đã được GUI cập nhật)
    P = config.INITIAL_P.copy().astype(np.float32)

    # Q — lấy từ config
    Q = config.PROCESS_NOISE_COV.copy().astype(np.float32)

    # R — lấy từ config
    R = config.MEASUREMENT_NOISE_COV.copy().astype(np.float32)

    # --- Weights cho Predict (N) ---
    # Giống sys_sensor_fusion_init() L133-L140
    Wm_N = np.zeros(config.NUM_PREDICT_SIGMA, dtype=np.float32)
    Wc_N = np.zeros(config.NUM_PREDICT_SIGMA, dtype=np.float32)

    Wm_N[0] = f32(config.UKF_LAMBDA_N / (config.N + config.UKF_LAMBDA_N))
    Wc_N[0] = Wm_N[0] + f32(1.0 - config.UKF_ALPHA**2 + config.UKF_BETA)
    w_rest_n = f32(1.0 / (2.0 * (config.N + config.UKF_LAMBDA_N)))
    for i in range(1, config.NUM_PREDICT_SIGMA):
        Wm_N[i] = w_rest_n
        Wc_N[i] = w_rest_n

    # --- Weights cho Update (M) ---
    # Giống sys_sensor_fusion_init() L142-L149
    Wm_M = np.zeros(config.NUM_UPDATE_SIGMA, dtype=np.float32)
    Wc_M = np.zeros(config.NUM_UPDATE_SIGMA, dtype=np.float32)

    Wm_M[0] = f32(config.UKF_LAMBDA_M / (config.M + config.UKF_LAMBDA_M))
    Wc_M[0] = Wm_M[0] + f32(1.0 - config.UKF_ALPHA**2 + config.UKF_BETA)
    w_rest_m = f32(1.0 / (2.0 * (config.M + config.UKF_LAMBDA_M)))
    for i in range(1, config.NUM_UPDATE_SIGMA):
        Wm_M[i] = w_rest_m
        Wc_M[i] = w_rest_m

    # Sigma point storage
    X_sigma_pred = np.zeros((config.UKF_STATE_SIZE, config.NUM_PREDICT_SIGMA), dtype=np.float32)
    Noise_sigma_pred = np.zeros((config.UKF_PROCESS_NOISE_SIZE, config.NUM_PREDICT_SIGMA), dtype=np.float32)

    return UKFContext_STM(
        x=x,
        P=P,
        Q=Q,
        R=R,
        Wm_N=Wm_N,
        Wc_N=Wc_N,
        Wm_M=Wm_M,
        Wc_M=Wc_M,
        is_first_frame=True,
        X_sigma_pred=X_sigma_pred,
        Noise_sigma_pred=Noise_sigma_pred,
        imu_old=None,
        imu_current=None,
    )

# ==========================================
# Cholesky float32
# ==========================================
def cholesky_f32(ctx, mat, step_name="Unknown Step"):
    """
    Cholesky decomposition trên float32.
    Trả về lower-triangular matrix L sao cho mat = L @ L.T
    Trả về None nếu fail (thậm chí sau khi đã thêm epsilon).
    """
    try:
        L = np.linalg.cholesky(mat.astype(np.float64)).astype(np.float32)
        return L
    except np.linalg.LinAlgError:
        ctx.cholesky_fail_count += 1
        print(f"\n[CẢNH BÁO] Ma trận KHÔNG XÁC ĐỊNH DƯƠNG tại {step_name} (Lần {ctx.cholesky_fail_count})!")
        # print(format_matrix("Ma_tran_Loi", mat))
        
        if ctx.cholesky_fail_count >= 2:
            print(f"[ERROR] Đã lỗi Cholesky 2 lần -> DỪNG chạy UKF cho biến thể này!")
            raise ValueError(f"Cholesky failed 2 times at {step_name}")

        try:
            # Fallback: Cộng một lượng epsilon siêu nhỏ vào đường chéo để tránh lỗi semi-definite
            # (Rất thường gặp khi cấu hình biến noise = 0, vd Qg = 0)
            eps = 1e-9
            mat_eps = mat.astype(np.float64) + eps * np.eye(mat.shape[0])
            return np.linalg.cholesky(mat_eps).astype(np.float32)
        except np.linalg.LinAlgError:
            return None

# ==========================================
# ukf_predict_stm — Normal (Euler, 1 IMU sample)
# Giống propagate_sigma_point nhưng dùng float32
# ==========================================
def _propagate_sigma_normal(X_sigma_pred, Noise_sigma_pred, m, imu_current, dt):
    """
    Propagate 1 sigma point bằng Euler integration (1 IMU sample).
    Giống propagate_sigma_point() trong module_ukf.py nhưng float32.
    """
    px    = X_sigma_pred[0][m]
    py    = X_sigma_pred[1][m]
    vx    = X_sigma_pred[2][m]
    vy    = X_sigma_pred[3][m]
    theta = X_sigma_pred[4][m]
    bax   = X_sigma_pred[5][m]
    bay   = X_sigma_pred[6][m]
    bgz   = X_sigma_pred[7][m]

    n_ax = Noise_sigma_pred[0][m]
    n_ay = Noise_sigma_pred[1][m]
    n_gz = Noise_sigma_pred[2][m]

    corrected_ax = f32(imu_current.ax) - bax + n_ax
    corrected_ay = f32(imu_current.ay) - bay + n_ay
    corrected_gz = f32(imu_current.gz) - bgz + n_gz

    theta_new = theta + corrected_gz * f32(dt)

    cos_theta = f32(np.cos(float(theta)))
    sin_theta = f32(np.sin(float(theta)))

    ax_world = corrected_ax * cos_theta - corrected_ay * sin_theta
    ay_world = corrected_ax * sin_theta + corrected_ay * cos_theta

    dt32 = f32(dt)
    X_sigma_pred[0][m] = px + vx * dt32 + f32(0.5) * ax_world * dt32 * dt32
    X_sigma_pred[1][m] = py + vy * dt32 + f32(0.5) * ay_world * dt32 * dt32
    X_sigma_pred[2][m] = vx + ax_world * dt32
    X_sigma_pred[3][m] = vy + ay_world * dt32
    X_sigma_pred[4][m] = theta_new
    # Bias không đổi


# ==========================================
# ukf_predict_stm — Advanced (Trapezoidal, giống C)
# Giống hệt sys_sensor_fusion_predict() L241-L269
# ==========================================
def _propagate_sigma_advanced(X_sigma_pred, Noise_sigma_pred, m, imu_old, imu_current, dt):
    """
    Propagate 1 sigma point bằng Trapezoidal integration.
    Giống hệt vòng for trong sys_sensor_fusion_predict().
    """
    px    = X_sigma_pred[0][m]
    py    = X_sigma_pred[1][m]
    vx    = X_sigma_pred[2][m]
    vy    = X_sigma_pred[3][m]
    theta = X_sigma_pred[4][m]
    bax   = X_sigma_pred[5][m]
    bay   = X_sigma_pred[6][m]
    bgz   = X_sigma_pred[7][m]

    n_ax = Noise_sigma_pred[0][m]
    n_ay = Noise_sigma_pred[1][m]
    n_gz = Noise_sigma_pred[2][m]

    dt32 = f32(dt)

    # w_avg = 0.5*(imu_old.gz + imu_current.gz) - bgz + n_gz
    w_avg = f32(0.5) * (f32(imu_old.gz) + f32(imu_current.gz)) - bgz + n_gz
    theta_new = theta + w_avg * dt32

    # Acceleration at k-1 (imu_old), rotated by theta
    cos_theta = f32(np.cos(float(theta)))
    sin_theta = f32(np.sin(float(theta)))

    ax_old = (f32(imu_old.ax) - bax + n_ax) * cos_theta - (f32(imu_old.ay) - bay + n_ay) * sin_theta
    ay_old = (f32(imu_old.ax) - bax + n_ax) * sin_theta + (f32(imu_old.ay) - bay + n_ay) * cos_theta

    # Acceleration at k (imu_current), rotated by theta_new
    cos_theta_new = f32(np.cos(float(theta_new)))
    sin_theta_new = f32(np.sin(float(theta_new)))

    ax_new = (f32(imu_current.ax) - bax + n_ax) * cos_theta_new - (f32(imu_current.ay) - bay + n_ay) * sin_theta_new
    ay_new = (f32(imu_current.ax) - bax + n_ax) * sin_theta_new + (f32(imu_current.ay) - bay + n_ay) * cos_theta_new

    # Average
    ax_avg = f32(0.5) * (ax_old + ax_new)
    ay_avg = f32(0.5) * (ay_old + ay_new)

    # Cập nhật đè lên chính nó
    X_sigma_pred[0][m] = px + vx * dt32 + f32(0.5) * ax_avg * dt32 * dt32
    X_sigma_pred[1][m] = py + vy * dt32 + f32(0.5) * ay_avg * dt32 * dt32
    X_sigma_pred[2][m] = vx + ax_avg * dt32
    X_sigma_pred[3][m] = vy + ay_avg * dt32
    X_sigma_pred[4][m] = theta_new
    # Bias không đổi


# ==========================================
# ukf_predict_stm — Main predict function
# Giống hệt sys_sensor_fusion_predict()
# ==========================================
def ukf_predict_stm(
    ctx: UKFContext_STM,
    imu_sample: IMUSample,
    dt: float,
    event_line: str = ""
) -> None:
    """
    UKF Predict step — giống hệt sys_sensor_fusion_predict().
    
    - Nếu is_first_frame: tạo sigma points từ [P; Q]
    - Luôn luôn: propagate sigma points qua mô hình động học
    - Tính x_mean, P từ sigma points
    """
    NUM_STATE = config.UKF_STATE_SIZE
    NUM_PREDICT_NOISE = config.UKF_PROCESS_NOISE_SIZE
    N = config.N
    NUM_PREDICT_SIGMA = config.NUM_PREDICT_SIGMA

    # Lưu IMU hiện tại
    ctx.imu_current = imu_sample

    # Nếu chưa có imu_old, lấy imu_current làm imu_old
    if ctx.imu_old is None:
        ctx.imu_old = imu_sample

    P_aug_log = None
    L_aug_log = None
    x_aug_log = None
    sigma_aug_log = None

    # =====================================================
    # STEP 1: Tạo sigma points (chỉ khi is_first_frame)
    # Giống sys_sensor_fusion_predict() L174-L237
    # =====================================================
    if ctx.is_first_frame:
        # P_aug [N×N] = [P 0; 0 Q]
        P_aug = np.zeros((N, N), dtype=np.float32)
        for i in range(NUM_STATE):
            for j in range(NUM_STATE):
                P_aug[i, j] = ctx.P[i, j]
        for i in range(NUM_PREDICT_NOISE):
            for j in range(NUM_PREDICT_NOISE):
                P_aug[NUM_STATE + i, NUM_STATE + j] = ctx.Q[i, j]

        # L_aug = cholesky(P_aug)
        L_aug = cholesky_f32(ctx, P_aug, f"Predict ({event_line.strip()})")
        if L_aug is None:
            return  # Cholesky failed

        # L_aug *= GAMMA_N
        L_aug = L_aug * config.GAMMA_N

        # x_aug = [state; 0, 0, 0]
        x_aug = np.zeros(N, dtype=np.float32)
        x_aug[:NUM_STATE] = ctx.x

        # Sinh sigma points
        # if(m==0): x_sigma = x_aug
        # else if(m<=N): x_sigma = x_aug + L_aug[:,m-1]
        # else: x_sigma = x_aug - L_aug[:,m-1-N]
        for m in range(NUM_PREDICT_SIGMA):
            if m == 0:
                x_sigma = x_aug.copy()
            elif m <= N:
                x_sigma = x_aug + L_aug[:, m - 1]
            else:
                x_sigma = x_aug - L_aug[:, m - 1 - N]

            # X_sigma_pred = first 8 states
            for i in range(NUM_STATE):
                ctx.X_sigma_pred[i][m] = x_sigma[i]
            # Noise_sigma_pred = last 3 states
            for i in range(NUM_PREDICT_NOISE):
                ctx.Noise_sigma_pred[i][m] = x_sigma[NUM_STATE + i]

        # Log data
        P_aug_log = P_aug
        L_aug_log = L_aug
        x_aug_log = x_aug

        # Build sigma_aug_log cho logging
        sigma_aug_log = np.zeros((N, NUM_PREDICT_SIGMA), dtype=np.float32)
        for m in range(NUM_PREDICT_SIGMA):
            for i in range(NUM_STATE):
                sigma_aug_log[i, m] = ctx.X_sigma_pred[i][m]
            for i in range(NUM_PREDICT_NOISE):
                sigma_aug_log[NUM_STATE + i, m] = ctx.Noise_sigma_pred[i][m]

        ctx.is_first_frame = False

    # =====================================================
    # STEP 2: Propagate sigma points (LUÔN LUÔN CHẠY)
    # Giống sys_sensor_fusion_predict() L241-L269
    # =====================================================
    for m in range(NUM_PREDICT_SIGMA):
        if ctx.use_advanced_propagate:
            _propagate_sigma_advanced(
                ctx.X_sigma_pred, ctx.Noise_sigma_pred, m,
                ctx.imu_old, ctx.imu_current, dt
            )
        else:
            _propagate_sigma_normal(
                ctx.X_sigma_pred, ctx.Noise_sigma_pred, m,
                ctx.imu_current, dt
            )

    # =====================================================
    # STEP 3: Tính predicted mean
    # Giống sys_sensor_fusion_predict() L272-L276
    # =====================================================
    x_mean = np.zeros(NUM_STATE, dtype=np.float32)

    if CIRCULAR_MEAN:
        # Circular mean cho theta (state index 4)
        sum_sin = f32(0.0)
        sum_cos = f32(0.0)
        for m in range(NUM_PREDICT_SIGMA):
            theta_m = float(ctx.X_sigma_pred[4][m])
            sum_sin += ctx.Wm_N[m] * f32(np.sin(theta_m))
            sum_cos += ctx.Wm_N[m] * f32(np.cos(theta_m))
        x_mean[4] = f32(np.arctan2(float(sum_sin), float(sum_cos)))

        # Linear mean cho các state còn lại
        for i in [0, 1, 2, 3, 5, 6, 7]:
            for m in range(NUM_PREDICT_SIGMA):
                x_mean[i] += ctx.Wm_N[m] * ctx.X_sigma_pred[i][m]
    else:
        # Linear mean cho TẤT CẢ states (giống C)
        for m in range(NUM_PREDICT_SIGMA):
            for i in range(NUM_STATE):
                x_mean[i] += ctx.Wm_N[m] * ctx.X_sigma_pred[i][m]

    # =====================================================
    # STEP 4: Tính predicted covariance P
    # Giống sys_sensor_fusion_predict() L281-L295
    # =====================================================
    P_new = np.zeros((NUM_STATE, NUM_STATE), dtype=np.float32)
    for m in range(NUM_PREDICT_SIGMA):
        diff = np.zeros(NUM_STATE, dtype=np.float32)
        for i in range(NUM_STATE):
            diff[i] = ctx.X_sigma_pred[i][m] - x_mean[i]
        diff[4] = normalize_angle(diff[4])

        for i in range(NUM_STATE):
            for j in range(NUM_STATE):
                P_new[i, j] += ctx.Wc_N[m] * diff[i] * diff[j]

    ctx.P = P_new

    # =====================================================
    # STEP 5: Regularization (tùy chọn — C không có)
    # =====================================================
    if P_REGULARIZATION:
        ctx.P = f32(0.5) * (ctx.P + ctx.P.T)
        ctx.P += f32(1e-9) * np.eye(NUM_STATE, dtype=np.float32)

    # =====================================================
    # STEP 6: Cập nhật state
    # Giống sys_sensor_fusion_predict() L298-L301
    # =====================================================
    ctx.x = x_mean.copy()
    ctx.x[4] = normalize_angle(ctx.x[4])

    # imu_old = imu_current
    ctx.imu_old = ctx.imu_current

    # =====================================================
    # STEP 7: Logging
    # =====================================================
    if ctx.logger:
        ctx.logger.log_predict(
            P_aug_log, L_aug_log, x_aug_log,
            sigma_aug_log,
            ctx.X_sigma_pred,
            ctx.x, ctx.P,
            event_line=event_line
        )


# ==========================================
# ukf_update_stm — Augmented UKF Update
# Giống hệt sys_sensor_fusion_update()
# ==========================================
def ukf_update_stm(
    ctx: UKFContext_STM,
    d0: float, d1: float, d2: float,
    mask: int,
    event_line: str = ""
) -> None:
    """
    UKF Update step — giống hệt sys_sensor_fusion_update().
    
    Tạo augmented [P; R], sinh sigma mới, noise trong D_sigma,
    P -= K @ Pxd.T
    """
    NUM_STATE = config.UKF_STATE_SIZE
    NUM_UPDATE_NOISE = config.UKF_MEASUREMENT_SIZE
    M_val = config.M
    NUM_UPDATE_SIGMA = config.NUM_UPDATE_SIGMA
    NUM_ANCHORS = config.NUM_ANCHORS

    # =====================================================
    # STEP 1: P_aug [M×M] = [P, 0; 0, R]
    # Giống sys_sensor_fusion_update() L318-L333
    # =====================================================
    P_aug = np.zeros((M_val, M_val), dtype=np.float32)
    for i in range(NUM_STATE):
        for j in range(NUM_STATE):
            P_aug[i, j] = ctx.P[i, j]
    for i in range(NUM_UPDATE_NOISE):
        for j in range(NUM_UPDATE_NOISE):
            P_aug[NUM_STATE + i, NUM_STATE + j] = ctx.R[i, j]

    # =====================================================
    # STEP 2: L_aug = cholesky(P_aug) * GAMMA_M
    # Giống sys_sensor_fusion_update() L336-L345
    # =====================================================
    L_aug = cholesky_f32(ctx, P_aug, f"Update ({event_line.strip()})")
    if L_aug is None:
        return  # Cholesky failed

    L_aug = L_aug * config.GAMMA_M

    # =====================================================
    # STEP 3: Sigma points cho Update
    # Giống sys_sensor_fusion_update() L347-L397
    # =====================================================
    X_sigma = np.zeros((NUM_STATE, NUM_UPDATE_SIGMA), dtype=np.float32)
    D_sigma = np.zeros((NUM_UPDATE_NOISE, NUM_UPDATE_SIGMA), dtype=np.float32)

    # x_aug = [state; 0, 0, 0]
    x_aug = np.zeros(M_val, dtype=np.float32)
    x_aug[:NUM_STATE] = ctx.x

    # Anchor positions
    ANCHOR_POS_TABLE = [
        [f32(config.ANCHOR_1_X), f32(config.ANCHOR_1_Y)],
        [f32(config.ANCHOR_2_X), f32(config.ANCHOR_2_Y)],
        [f32(config.ANCHOR_3_X), f32(config.ANCHOR_3_Y)],
        [f32(config.ANCHOR_4_X), f32(config.ANCHOR_4_Y)],
    ]

    for m in range(NUM_UPDATE_SIGMA):
        # Sinh sigma point
        if m == 0:
            x_s = x_aug.copy()
        elif m <= M_val:
            x_s = x_aug + L_aug[:, m - 1]
        else:
            x_s = x_aug - L_aug[:, m - 1 - M_val]

        # X_sigma = state part
        for i in range(NUM_STATE):
            X_sigma[i][m] = x_s[i]

        # D_sigma = distance + noise term
        # Giống sys_sensor_fusion_update() L384-L396
        px_s = x_s[0]
        py_s = x_s[1]
        d_index = 0
        for anc in range(NUM_ANCHORS):
            if mask & (1 << anc):
                dx = px_s - ANCHOR_POS_TABLE[anc][0]
                dy = py_s - ANCHOR_POS_TABLE[anc][1]
                dist = f32(np.sqrt(float(dx * dx + dy * dy)))
                D_sigma[d_index][m] = dist + x_s[8 + d_index]  # noise term
                d_index += 1
                if d_index >= NUM_UPDATE_NOISE:
                    break

    # =====================================================
    # STEP 4: d_mean = Σ Wm_M * D_sigma
    # Giống sys_sensor_fusion_update() L400-L404
    # =====================================================
    d_mean = np.zeros(NUM_UPDATE_NOISE, dtype=np.float32)
    for m in range(NUM_UPDATE_SIGMA):
        for i in range(NUM_UPDATE_NOISE):
            d_mean[i] += ctx.Wm_M[m] * D_sigma[i][m]

    # =====================================================
    # STEP 5: P_dd, P_xd
    # Giống sys_sensor_fusion_update() L408-L427
    # =====================================================
    P_dd = np.zeros((NUM_UPDATE_NOISE, NUM_UPDATE_NOISE), dtype=np.float32)
    P_xd = np.zeros((NUM_STATE, NUM_UPDATE_NOISE), dtype=np.float32)

    for m in range(NUM_UPDATE_SIGMA):
        diff_d = np.zeros(NUM_UPDATE_NOISE, dtype=np.float32)
        diff_x = np.zeros(NUM_STATE, dtype=np.float32)

        for i in range(NUM_UPDATE_NOISE):
            diff_d[i] = D_sigma[i][m] - d_mean[i]
        for i in range(NUM_STATE):
            diff_x[i] = X_sigma[i][m] - x_aug[i]  # diff từ state hiện tại (x_aug)
        diff_x[4] = normalize_angle(diff_x[4])

        for i in range(NUM_UPDATE_NOISE):
            for j in range(NUM_UPDATE_NOISE):
                P_dd[i, j] += ctx.Wc_M[m] * diff_d[i] * diff_d[j]

        for i in range(NUM_STATE):
            for j in range(NUM_UPDATE_NOISE):
                P_xd[i, j] += ctx.Wc_M[m] * diff_x[i] * diff_d[j]

    # =====================================================
    # STEP 6: K = P_xd @ inv(P_dd)
    # Giống sys_sensor_fusion_update() L429-L443
    # =====================================================
    try:
        P_dd_inv = np.linalg.inv(P_dd.astype(np.float64)).astype(np.float32)
    except np.linalg.LinAlgError:
        return  # Inverse failed

    K = P_xd @ P_dd_inv

    # =====================================================
    # STEP 7: State update
    # Giống sys_sensor_fusion_update() L445-L462
    # =====================================================
    D_real = f32_arr([d0, d1, d2])
    for i in range(NUM_STATE):
        update_val = f32(0.0)
        for j in range(NUM_UPDATE_NOISE):
            update_val += K[i, j] * (D_real[j] - d_mean[j])

        if i == 0: ctx.x[0] += update_val   # px
        if i == 1: ctx.x[1] += update_val   # py
        if i == 2: ctx.x[2] += update_val   # vx
        if i == 3: ctx.x[3] += update_val   # vy
        if i == 4: ctx.x[4] = normalize_angle(ctx.x[4] + update_val)  # theta
        if i == 5: ctx.x[5] += update_val   # b_ax
        if i == 6: ctx.x[6] += update_val   # b_ay
        if i == 7: ctx.x[7] += update_val   # b_gz

    # =====================================================
    # STEP 8: P update — P -= K @ Pxd.T (giống C)
    # Giống sys_sensor_fusion_update() L464-L478
    # =====================================================
    Pxd_T = P_xd.T  # [3x8]
    K_Pxd_T = K @ Pxd_T  # [8x8]
    ctx.P = ctx.P - K_Pxd_T

    # Regularization (tùy chọn)
    if P_REGULARIZATION:
        ctx.P = f32(0.5) * (ctx.P + ctx.P.T)
        ctx.P += f32(1e-9) * np.eye(NUM_STATE, dtype=np.float32)

    # =====================================================
    # STEP 9: Reset flag — force sigma regeneration on next predict
    # Giống sys_sensor_fusion_update() L482
    # =====================================================
    ctx.is_first_frame = True

    # =====================================================
    # STEP 10: Logging
    # =====================================================
    if ctx.logger:
        innovation = D_real - d_mean
        update_vec = K @ innovation
        trace_P = f32(np.trace(ctx.P))
        ctx.logger.log_update(
            D_sigma, d_mean, P_dd, P_xd, K,
            innovation, D_real, update_vec, float(trace_P),
            event_line=event_line
        )
