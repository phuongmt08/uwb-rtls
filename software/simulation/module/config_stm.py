import numpy as np
from dataclasses import dataclass
from typing import Tuple

# TXT
OUTPUT_TXT_ENABLED = False

# OUTPUT_FILE = r"D:\HOC\S\STM32\IDE\DATN\uwb-rtls\software\simulation\simulation.txt"
SOURCE_DATA_FILE = r"D:\HOC\S\STM32\IDE\DATN\uwb-rtls\software\simulation\csv\21_05_26\20260521_18g31p_ukf_log_data.csv"
# SOURCE_DATA_FILE = None

# ---------------------------------------------------------------------------
# Simulation configuration
# ---------------------------------------------------------------------------
ROOM_SIZE_M = 10.0
SIMULATION_TIME_S = 20.0  # Total simulation time in seconds

# ==========================================
# User defined reference rectangle
# ==========================================
DRAW_RECTANGLE = True
RECT_WIDTH = -4.88
RECT_HEIGHT = 4.88
# ==========================================

IMU_EMA_ALPHA = 0.25

# ==========================================
# Zero Velocity Update (ZUPT) configuration
# ==========================================
IMU_ZUPT_THRESHOLD = 5.0
IMU_ZUPT_FRAMES = 15
# ==========================================


TEST_UKF_Q_R_Params = False

ANCHOR_1_X = 0.0
ANCHOR_1_Y = 0.0

ANCHOR_2_X = 9.76
ANCHOR_2_Y = 0.0

ANCHOR_3_X = 0.0
ANCHOR_3_Y = 9.76

ANCHOR_4_X = 9.76
ANCHOR_4_Y = 9.76

NUM_ANCHORS = 4

# Anchor positions in the room (three corners)
ANCHOR_POSITIONS = np.array([
    [ANCHOR_1_X, ANCHOR_1_Y],     
    [ANCHOR_2_X, ANCHOR_2_Y],     
    [ANCHOR_3_X, ANCHOR_3_Y],    
    [ANCHOR_4_X, ANCHOR_4_Y]     
])

# ==========================================
# UKF configuration — GIỐNG HỆT sys_sensor_fusion.c
# ==========================================
UKF_STATE_SIZE = 8           # NUM_STATE
UKF_PROCESS_NOISE_SIZE = 3   # NUM_PREDICT_NOISE
UKF_MEASUREMENT_SIZE = 3     # NUM_UPDATE_NOISE

# Augmented sizes — tách riêng cho Predict và Update (giống C)
N = UKF_STATE_SIZE + UKF_PROCESS_NOISE_SIZE   # 11 (predict augmented)
M = UKF_STATE_SIZE + UKF_MEASUREMENT_SIZE     # 11 (update augmented)

NUM_PREDICT_SIGMA = 2 * N + 1   # 23
NUM_UPDATE_SIGMA  = 2 * M + 1   # 23

# UKF constants — HARDCODED giống C
UKF_ALPHA = 1.0
UKF_KAPPA = 0.0
UKF_BETA  = 2.0

# Derived parameters — riêng biệt cho N và M
N_PLUS_LAMBDA_N = UKF_ALPHA**2 * (N + UKF_KAPPA)
UKF_LAMBDA_N    = N_PLUS_LAMBDA_N - N
GAMMA_N         = np.sqrt(N_PLUS_LAMBDA_N, dtype=np.float32)

M_PLUS_LAMBDA_M = UKF_ALPHA**2 * (M + UKF_KAPPA)
UKF_LAMBDA_M    = M_PLUS_LAMBDA_M - M
GAMMA_M         = np.sqrt(M_PLUS_LAMBDA_M, dtype=np.float32)

# ==========================================
# Giá trị P, Q, R ban đầu — GIỐNG sys_sensor_fusion_init()
# Cho phép sửa từ GUI
# ==========================================
P_PX    = 0.1
P_PY    = 0.1
P_VX    = 0.1
P_VY    = 0.1
P_THETA = 1e-10
P_BAX   = 0.1
P_BAY   = 0.1
P_BGZ   = 1e-10

INITIAL_P = np.diag(np.array([
    P_PX, P_PY, P_VX, P_VY, P_THETA, P_BAX, P_BAY, P_BGZ
], dtype=np.float32))

# Q/R Test Params toggle
TEST_UKF_Q_R_Params = False

# Hardcoded TEST values — giống hệt #define trong C
Q_A_TEST = 4.066e-5   # Qa trong C
Q_G_TEST = 2.388e-7   # Qg trong C
R_UWB_TEST = 0.1      # R_uwb trong C

# MANUAL values (Editable from GUI else block)
Q_A_MANUAL = 0.04
Q_G_MANUAL = 4.78e-07
R_UWB_MANUAL = 0.01

# Logic to select final values
if TEST_UKF_Q_R_Params:
    Q_A = Q_A_TEST
    Q_G = Q_G_TEST
    R_UWB = R_UWB_TEST
else:
    Q_A = Q_A_MANUAL
    Q_G = Q_G_MANUAL
    R_UWB = R_UWB_MANUAL

PROCESS_NOISE_COV = np.diag(np.array([Q_A, Q_A, Q_G], dtype=np.float32))
MEASUREMENT_NOISE_COV = np.diag(np.array([R_UWB, R_UWB, R_UWB], dtype=np.float32))

# ==========================================
# Dùng chung cho tương thích module_ukf.py cũ (UKF_AUGMENTED_SIZE, etc.)
# ==========================================
UKF_AUGMENTED_SIZE = N
UKF_NUM_SIGMA = NUM_PREDICT_SIGMA
UKF_LAMBDA = UKF_LAMBDA_N
UKF_GAMMA = GAMMA_N

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SensorEvent:
    type: str
    ax: float
    ay: float
    gz: float
    px: float
    py: float
    distances: np.ndarray
    dt: float
    mask: int = 0
    raw_line: str = ""
    ax_ema: float = 0.0
    ay_ema: float = 0.0
    gz_ema: float = 0.0

@dataclass
class IMUSample:
    ax: float
    ay: float
    gz: float

@dataclass
class UKFContext_STM:
    """UKF Context cho phiên bản STM32 — giống struct ukf_core_t trong C"""
    x: np.ndarray              # state [8] float32
    P: np.ndarray              # P [8x8] float32
    Q: np.ndarray              # Q [3x3] float32
    R: np.ndarray              # R [3x3] float32
    Wm_N: np.ndarray           # Predict weights mean [23]
    Wc_N: np.ndarray           # Predict weights cov  [23]
    Wm_M: np.ndarray           # Update weights mean  [23]
    Wc_M: np.ndarray           # Update weights cov   [23]
    is_first_frame: bool
    X_sigma_pred: np.ndarray   # [8][23] — state sigma points (tích phân đè lên)
    Noise_sigma_pred: np.ndarray  # [3][23] — noise sigma points
    imu_old: IMUSample = None
    imu_current: IMUSample = None
    use_advanced_propagate: bool = True   # True = Trapezoidal (mặc định giống C)
    prev_imu_sample: IMUSample = None     # Dùng cho propagate_advanced 
    logger: 'UKFLogger' = None
    cholesky_fail_count: int = 0          # Đếm số lần lỗi Cholesky

# ==================== SERIAL CONFIGURATION ====================
# UART Configuration
UART_BAUDRATE = 115200
UART_TIMEOUT = 1.0
TARGET_PORT = "COM8"

# Protocol Definitions
UART_SOF = 0xAA

# ==================== LIVE PLOT CONFIGURATION ====================
GROUND_TRUTH_D1 = 4.88 * np.sqrt(2)
GROUND_TRUTH_D2 = 4.88 * np.sqrt(2)
GROUND_TRUTH_D3 = 4.88 * np.sqrt(2)
GROUND_TRUTH_D4 = 3.45

# Frame Structure Format (little-endian)
LIVE_FRAME_FORMAT = '<BBBI5f4f4d4dIf'
import struct
LIVE_FRAME_SIZE = struct.calcsize(LIVE_FRAME_FORMAT)

# ==================== IMU Q Process CONFIGURATION ====================
IMU_FRAME_FORMAT = '<BBI3f'
import struct
IMU_FRAME_SIZE = struct.calcsize(IMU_FRAME_FORMAT)

# ==================== CSV CONFIGURATION ====================
CSV_UKF_FILENAME_PREFIX = "ukf_log_data"
CSV_UKF_FILENAME_SUFFIX = ".csv"

CSV_IMU_FILENAME_PREFIX = "imu_log_data"
CSV_IMU_FILENAME_SUFFIX = ".csv"

CSV_UWB_FILENAME_PREFIX = "uwb_log_data"
CSV_UWB_FILENAME_SUFFIX = ".csv"

# ==================== UWB CONFIGURATION ====================
UWB_FRAME_FORMAT = '<BBI4f'
UWB_FRAME_SIZE = struct.calcsize(UWB_FRAME_FORMAT)

# Logging Control
PRINT_DATA = True
PREDICT_THRESHOLD = 0.001
MAX_SAMPLES = 5000

# ==========================================
# SYS_SENSOR_FUSION constants — giống hệt #define trong C
# ==========================================
SYS_SENSOR_FUSION_PI  = np.float32(3.14159265358979323846)
SYS_SENSOR_FUSION_2PI = np.float32(2.0) * SYS_SENSOR_FUSION_PI
