import numpy as np
from typing import Tuple
from . import config
from .config import UKFContext, IMUSample

def normalize_angle(angle: float) -> float:
    wrapped = (angle + np.pi) % (2.0 * np.pi) - np.pi
    return float(wrapped)

def compute_sigma_weights() -> Tuple[np.ndarray, np.ndarray]:
    Wm = np.full(config.UKF_NUM_SIGMA, 1.0 / (2.0 * (config.UKF_AUGMENTED_SIZE + config.UKF_LAMBDA)))
    Wc = Wm.copy()
    Wm[0] = config.UKF_LAMBDA / (config.UKF_AUGMENTED_SIZE + config.UKF_LAMBDA)
    Wc[0] = Wm[0] + (1.0 - config.UKF_ALPHA**2 + config.UKF_BETA)
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



def init_ukf_logging(ctx: UKFContext):
    from .config import UKF_KAPPA
    qa = config.PROCESS_NOISE_COV[0, 0]
    qg = config.PROCESS_NOISE_COV[2, 2]
    r_uwb = config.MEASUREMENT_NOISE_COV[0, 0]
    if ctx.logger:
        ctx.logger.log_define(
            num_state=config.UKF_STATE_SIZE,
            num_predict_noise=config.UKF_PROCESS_NOISE_SIZE,
            num_update_noise=config.UKF_MEASUREMENT_SIZE,
            n=config.UKF_AUGMENTED_SIZE,
            m=config.UKF_AUGMENTED_SIZE,
            num_predict_sigma=config.UKF_NUM_SIGMA,
            num_update_sigma=config.UKF_NUM_SIGMA,
            alpha=config.UKF_ALPHA,
            kappa=UKF_KAPPA,
            beta=config.UKF_BETA,
            lambda_n=config.UKF_LAMBDA,
            gamma_n=config.UKF_GAMMA,
            qa=qa,
            qg=qg,
            r_uwb=r_uwb
        )
        ctx.logger.log_init(ctx.x, ctx.P, config.PROCESS_NOISE_COV, config.MEASUREMENT_NOISE_COV, ctx.Wm, ctx.Wc)

def create_ukf_context(initial_state: np.ndarray) -> UKFContext:
    from .config import INITIAL_P
    Wm, Wc = compute_sigma_weights()
    X_sigma_pred = np.zeros((config.UKF_STATE_SIZE, config.UKF_NUM_SIGMA))
    return UKFContext(
        x=initial_state.copy(),
        P=INITIAL_P.copy(),
        Wm=Wm,
        Wc=Wc,
        is_first_frame=True,
        X_sigma_pred=X_sigma_pred,
    )

def generate_augmented_sigma_points(context: UKFContext, return_internals=False):
    x_aug = np.zeros(config.UKF_AUGMENTED_SIZE)
    x_aug[:config.UKF_STATE_SIZE] = context.x

    P_aug = np.zeros((config.UKF_AUGMENTED_SIZE, config.UKF_AUGMENTED_SIZE))
    P_aug[:config.UKF_STATE_SIZE, :config.UKF_STATE_SIZE] = context.P
    P_aug[config.UKF_STATE_SIZE :, config.UKF_STATE_SIZE :] = config.PROCESS_NOISE_COV

    try:
        sqrt_P_aug = np.linalg.cholesky(P_aug)
    except np.linalg.LinAlgError:
        # Đảm bảo ma trận đối xứng và thêm nhiễu nhỏ để tránh suy biến
        P_aug = 0.5 * (P_aug + P_aug.T)
        sqrt_P_aug = np.linalg.cholesky(P_aug + np.eye(config.UKF_AUGMENTED_SIZE) * 1e-6)

    sigma_points = np.zeros((config.UKF_AUGMENTED_SIZE, config.UKF_NUM_SIGMA))
    sigma_points[:, 0] = x_aug
    for i in range(config.UKF_AUGMENTED_SIZE):
        delta = config.UKF_GAMMA * sqrt_P_aug[:, i]
        sigma_points[:, i + 1] = x_aug + delta
        sigma_points[:, i + 1 + config.UKF_AUGMENTED_SIZE] = x_aug - delta

    if return_internals:
        L_aug = config.UKF_GAMMA * sqrt_P_aug
        return sigma_points, P_aug, L_aug, x_aug
    return sigma_points

def propagate_sigma_point(
    sigma_point: np.ndarray,
    imu_sample: IMUSample,
    dt: float,
) -> np.ndarray:
    x = sigma_point[:config.UKF_STATE_SIZE].copy()
    noise = sigma_point[config.UKF_STATE_SIZE : config.UKF_STATE_SIZE + config.UKF_PROCESS_NOISE_SIZE]

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

def propagate_sigma_point_advanced(
    sigma_point: np.ndarray,
    imu_sample_k: IMUSample,
    imu_sample_k_minus_1: IMUSample,
    dt: float,
) -> np.ndarray:
    x = sigma_point[:config.UKF_STATE_SIZE].copy()
    noise = sigma_point[config.UKF_STATE_SIZE : config.UKF_STATE_SIZE + config.UKF_PROCESS_NOISE_SIZE]

    px, py, vx, vy, theta, bax, bay, bgz = x
    n_ax, n_ay, n_gz = noise

    # --- Yaw Integration (Trapezoidal) ---
    omega_k_minus_1 = imu_sample_k_minus_1.gz
    omega_k = imu_sample_k.gz
    
    delta_theta = (0.5 * (omega_k_minus_1 + omega_k) - bgz + n_gz) * dt
    theta_new = normalize_angle(theta + delta_theta)
    
    # --- Acceleration at k-1 ---
    cos_theta = np.cos(theta)
    sin_theta = np.sin(theta)
    
    a_x_k_minus_1_G = (imu_sample_k_minus_1.ax - bax + n_ax) * cos_theta - (imu_sample_k_minus_1.ay - bay + n_ay) * sin_theta
    a_y_k_minus_1_G = (imu_sample_k_minus_1.ax - bax + n_ax) * sin_theta + (imu_sample_k_minus_1.ay - bay + n_ay) * cos_theta
    
    # --- Acceleration at k ---
    cos_theta_new = np.cos(theta_new)
    sin_theta_new = np.sin(theta_new)
    
    a_x_k_G = (imu_sample_k.ax - bax + n_ax) * cos_theta_new - (imu_sample_k.ay - bay + n_ay) * sin_theta_new
    a_y_k_G = (imu_sample_k.ax - bax + n_ax) * sin_theta_new + (imu_sample_k.ay - bay + n_ay) * cos_theta_new
    
    # --- Average World Acceleration ---
    a_mx_G = 0.5 * (a_x_k_minus_1_G + a_x_k_G)
    a_my_G = 0.5 * (a_y_k_minus_1_G + a_y_k_G)
    
    # --- Velocity Update ---
    vx_new = vx + a_mx_G * dt
    vy_new = vy + a_my_G * dt
    
    # --- Position Update ---
    px_new = px + vx * dt + 0.5 * a_mx_G * (dt ** 2)
    py_new = py + vy * dt + 0.5 * a_my_G * (dt ** 2)

    x_pred = np.array(
        [px_new, py_new, vx_new, vy_new, theta_new, bax, bay, bgz], dtype=float
    )
    return x_pred

TEST_PREDICT = True
if not TEST_PREDICT:
    CIRCULAR_AVERAGE = True

    def ukf_predict(
        context: UKFContext,
        imu_sample: IMUSample,
        dt: float,
        event_line: str = ""
    ) -> None:
        P_aug, L_aug, x_aug = None, None, None
        sigma_points_aug_log = None
        if context.is_first_frame:
            sigma_points_aug, P_aug, L_aug, x_aug = generate_augmented_sigma_points(context, return_internals=True)
            sigma_points_aug_log = sigma_points_aug
            context.is_first_frame = False # note
        else:
            sigma_points_aug = np.vstack(
                [context.X_sigma_pred, np.zeros((config.UKF_PROCESS_NOISE_SIZE, config.UKF_NUM_SIGMA))]
            )

        if context.prev_imu_sample is None:
            context.prev_imu_sample = imu_sample

        for m in range(config.UKF_NUM_SIGMA):
            if context.use_advanced_propagate:
                context.X_sigma_pred[:, m] = propagate_sigma_point_advanced(
                    sigma_points_aug[:, m], imu_sample, context.prev_imu_sample, dt
                )
            else:
                context.X_sigma_pred[:, m] = propagate_sigma_point(
                    sigma_points_aug[:, m], imu_sample, dt
                )
                
        context.prev_imu_sample = imu_sample

        context.x = np.zeros(config.UKF_STATE_SIZE)

        if CIRCULAR_AVERAGE:
            # Circular mean for angle
            sum_sin = 0.0
            sum_cos = 0.0
            for m in range(config.UKF_NUM_SIGMA):
                sum_sin += context.Wm[m] * np.sin(context.X_sigma_pred[4, m])
                sum_cos += context.Wm[m] * np.cos(context.X_sigma_pred[4, m])
            context.x[4] = np.arctan2(sum_sin, sum_cos)

            for i in [0, 1, 2, 3, 5, 6, 7]:
                for m in range(config.UKF_NUM_SIGMA):
                    context.x[i] += context.Wm[m] * context.X_sigma_pred[i, m]

        else:
            for m in range(config.UKF_NUM_SIGMA):
                context.x += context.Wm[m] * context.X_sigma_pred[:, m]

            context.x[4] = normalize_angle(context.x[4])

        context.P = np.zeros((config.UKF_STATE_SIZE, config.UKF_STATE_SIZE))
        for m in range(config.UKF_NUM_SIGMA):
            diff = context.X_sigma_pred[:, m] - context.x
            diff[4] = normalize_angle(diff[4])
            context.P += context.Wc[m] * np.outer(diff, diff)
        if context.logger:
            context.logger.log_predict(P_aug, L_aug, x_aug, sigma_points_aug_log, context.X_sigma_pred, context.x, context.P, event_line=event_line)

else:
    def ukf_predict(
        context: UKFContext,
        imu_sample: IMUSample,
        dt: float,
        event_line: str = ""
    ) -> None:
        """
        UKF Prediction step.
        
        Args:
            context: UKF state context
            imu_sample: Current IMU measurement
            dt: Time step
            event_line: Optional event description for logging
        """
        # =====================================================
        # STEP 1: Generate augmented sigma points
        # =====================================================
        # Always regenerate from current state and covariance
        sigma_points_aug, P_aug, L_aug, x_aug = generate_augmented_sigma_points(
            context, 
            return_internals=True
        )
        
        # =====================================================
        # STEP 2: Propagate sigma points through dynamics
        # =====================================================
        # Initialize prev_imu if first call
        if context.prev_imu_sample is None:
            context.prev_imu_sample = imu_sample
        
        # Propagate each sigma point
        for m in range(config.UKF_NUM_SIGMA):
            if context.use_advanced_propagate:
                context.X_sigma_pred[:, m] = propagate_sigma_point_advanced(
                    sigma_points_aug[:, m], 
                    imu_sample, 
                    context.prev_imu_sample, 
                    dt
                )
            else:
                context.X_sigma_pred[:, m] = propagate_sigma_point(
                    sigma_points_aug[:, m], 
                    imu_sample, 
                    dt
                )
        
        # Update previous IMU sample
        context.prev_imu_sample = imu_sample
        
        # =====================================================
        # STEP 3: Compute predicted mean
        # =====================================================
        context.x = np.zeros(config.UKF_STATE_SIZE)
        
        # --- Circular mean for theta (state index 4) ---
        sum_sin = 0.0
        sum_cos = 0.0
        for m in range(config.UKF_NUM_SIGMA):
            theta = context.X_sigma_pred[4, m]
            sum_sin += context.Wm[m] * np.sin(theta)
            sum_cos += context.Wm[m] * np.cos(theta)
        
        context.x[4] = np.arctan2(sum_sin, sum_cos)
        
        # --- Linear mean for other states ---
        # States: [px, py, vx, vy, theta, bax, bay, bgz]
        #         [ 0,  1,  2,  3,     4,   5,   6,   7]
        for i in [0, 1, 2, 3, 5, 6, 7]:
            context.x[i] = np.sum(
                context.Wm * context.X_sigma_pred[i, :]
            )
        
        # =====================================================
        # STEP 4: Compute predicted covariance
        # =====================================================
        context.P = np.zeros((config.UKF_STATE_SIZE, config.UKF_STATE_SIZE))
        
        for m in range(config.UKF_NUM_SIGMA):
            # Compute difference
            diff = context.X_sigma_pred[:, m] - context.x
            
            # CRITICAL: Normalize angle difference
            diff[4] = normalize_angle(diff[4])
            
            # Accumulate weighted outer product
            context.P += context.Wc[m] * np.outer(diff, diff)
        
        # =====================================================
        # STEP 5: Ensure numerical stability
        # =====================================================
        # Symmetrize (should already be symmetric, but due to floating point...)
        context.P = 0.5 * (context.P + context.P.T)
        
        # Add small regularization to diagonal
        epsilon = 1e-9
        context.P += epsilon * np.eye(config.UKF_STATE_SIZE)
        
        # =====================================================
        # STEP 6: Check positive definiteness
        # =====================================================
        eigvals = np.linalg.eigvalsh(context.P)
        min_eigval = eigvals[0]
        
        if min_eigval <= 0:
            print(f"⚠️  WARNING: P not positive definite at predict!")
            print(f"  Min eigenvalue = {min_eigval:.6e}")
            print(f"  Trace(P) = {np.trace(context.P):.6f}")
            
            # Fix: Clip negative eigenvalues to small positive value
            eigvals_fixed = np.maximum(eigvals, epsilon)
            eigvecs = np.linalg.eigh(context.P)[1]
            context.P = eigvecs @ np.diag(eigvals_fixed) @ eigvecs.T
            
            print(f"  Fixed: Min eigenvalue = {eigvals_fixed[0]:.6e}")
        
        # =====================================================
        # STEP 7: Logging (optional)
        # =====================================================
        if context.logger:
            context.logger.log_predict(
                P_aug, 
                L_aug, 
                x_aug, 
                sigma_points_aug, 
                context.X_sigma_pred, 
                context.x, 
                context.P, 
                event_line=event_line
            )

def ukf_update(
    context: UKFContext,
    d_meas: np.ndarray,
    anchors: np.ndarray,
    event_line: str = ""
) -> None:
    if np.any(np.isnan(d_meas)):
        return

    z_sigma = np.zeros((config.UKF_MEASUREMENT_SIZE, config.UKF_NUM_SIGMA))
    for m in range(config.UKF_NUM_SIGMA):
        px, py = context.X_sigma_pred[0, m], context.X_sigma_pred[1, m]
        for anchor_idx, anchor in enumerate(anchors):
            z_sigma[anchor_idx, m] = np.hypot(px - anchor[0], py - anchor[1])

    z_mean = np.zeros(config.UKF_MEASUREMENT_SIZE)
    for m in range(config.UKF_NUM_SIGMA):
        z_mean += context.Wm[m] * z_sigma[:, m]

    S = np.zeros((config.UKF_MEASUREMENT_SIZE, config.UKF_MEASUREMENT_SIZE))
    Tc = np.zeros((config.UKF_STATE_SIZE, config.UKF_MEASUREMENT_SIZE))
    for m in range(config.UKF_NUM_SIGMA):
        z_diff = z_sigma[:, m] - z_mean
        x_diff = context.X_sigma_pred[:, m] - context.x
        x_diff[4] = normalize_angle(x_diff[4])
        S += context.Wc[m] * np.outer(z_diff, z_diff)
        Tc += context.Wc[m] * np.outer(x_diff, z_diff)

    S += config.MEASUREMENT_NOISE_COV

    try:
        K = Tc @ np.linalg.inv(S)
    except np.linalg.LinAlgError:
        return

    y = d_meas - z_mean
    update_val = K @ y
    context.x += update_val
    context.x[4] = normalize_angle(context.x[4])
    context.P -= K @ S @ K.T
    context.P = 0.5 * (context.P + context.P.T)
    context.is_first_frame = True

    trace_P = np.trace(context.P)
    if context.logger:
        context.logger.log_update(z_sigma, z_mean, S, Tc, K, y, d_meas, update_val, trace_P, event_line=event_line)
