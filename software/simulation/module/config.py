import numpy as np
from dataclasses import dataclass
from typing import Tuple

# TXT
OUTPUT_TXT_ENABLED = False

# OUTPUT_FILE = r"D:\HOC\S\STM32\IDE\DATN\uwb-rtls\software\simulation\simulation.txt"
SOURCE_DATA_FILE = r"D:/HOC/S/STM32/IDE/DATN/uwb-rtls/software/data/scripts/18_07_26/20260718_17g39p_ukf_log_data.csv"
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

ANCHOR_1_X = 0.7
ANCHOR_1_Y = 0.03

ANCHOR_2_X = 2.7
ANCHOR_2_Y = 8.37

ANCHOR_3_X = 7.5
ANCHOR_3_Y = 8.37

ANCHOR_4_X = 7.5
ANCHOR_4_Y = 0.03

ANCHOR_5_X = 4.3
ANCHOR_5_Y = 0.8

ANCHOR_6_X = 4.3
ANCHOR_6_Y = 7.88

# Keep this layout aligned with Zone 1 in firmware/uwb/sys/positioning_config.h.
ANCHOR_POSITIONS = np.array([
    [ANCHOR_1_X, ANCHOR_1_Y],     
    [ANCHOR_2_X, ANCHOR_2_Y],     
    [ANCHOR_3_X, ANCHOR_3_Y],    
    [ANCHOR_4_X, ANCHOR_4_Y],
    [ANCHOR_5_X, ANCHOR_5_Y],
    [ANCHOR_6_X, ANCHOR_6_Y],
])
NUM_ANCHORS = len(ANCHOR_POSITIONS)

# UKF configuration
UKF_STATE_SIZE = 8
UKF_PROCESS_NOISE_SIZE = 3
UKF_MEASUREMENT_SIZE = 3
UKF_ALPHA = 1.0
UKF_KAPPA = 0.0
UKF_BETA = 2.0

# Initial filter uncertainty variables
P_PX = 0.1
P_PY = 0.1
P_VX = 0.1
P_VY = 0.1
P_THETA = 1e-10
P_BAX = 0.1
P_BAY = 0.1
P_BGZ = 1e-10

INITIAL_P = np.diag([
    P_PX, P_PY, P_VX, P_VY, P_THETA, P_BAX, P_BAY, P_BGZ
])

# Q/R Test Params toggle
TEST_UKF_Q_R_Params = False

# Hardcoded TEST values (Always constant)
Q_A_TEST = 0.2**2
Q_G_TEST = np.deg2rad(2)**2
R_UWB_TEST = 0.1**2

# MANUAL values (Editable from GUI else block)
Q_A_MANUAL = 0.04
Q_G_MANUAL = 1e-10
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

PROCESS_NOISE_COV = np.diag([Q_A, Q_A, Q_G])
MEASUREMENT_NOISE_COV = np.diag([R_UWB, R_UWB, R_UWB])

# Derived UKF parameters
UKF_AUGMENTED_SIZE = UKF_STATE_SIZE + UKF_PROCESS_NOISE_SIZE
UKF_NUM_SIGMA = 2 * UKF_AUGMENTED_SIZE + 1
UKF_LAMBDA = UKF_ALPHA**2 * (UKF_AUGMENTED_SIZE + UKF_KAPPA) - UKF_AUGMENTED_SIZE
UKF_GAMMA = np.sqrt(UKF_AUGMENTED_SIZE + UKF_LAMBDA)

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
class UKFContext:
    x: np.ndarray
    P: np.ndarray
    Wm: np.ndarray
    Wc: np.ndarray
    is_first_frame: bool
    X_sigma_pred: np.ndarray
    use_advanced_propagate: bool = False
    prev_imu_sample: IMUSample = None
    logger: 'UKFLogger' = None

# ==================== SERIAL CONFIGURATION ====================
# UART Configuration
UART_BAUDRATE = 115200
UART_TIMEOUT = 1.0
TARGET_PORT = "COM15"

# Protocol Definitions
UART_SOF = 0xAA

# ==================== LIVE PLOT CONFIGURATION ====================
GROUND_TRUTH_D1 = 5.357
GROUND_TRUTH_D2 = 4.370
GROUND_TRUTH_D3 = 5.357
GROUND_TRUTH_D4 = 5.357
# Geometric distances from the existing reference point (~4.1, 4.2 m)
# to the newly configured A5 and A6 positions.
GROUND_TRUTH_D5 = 3.406
GROUND_TRUTH_D6 = 3.685
GROUND_TRUTH_DISTANCES = np.array([
    GROUND_TRUTH_D1,
    GROUND_TRUTH_D2,
    GROUND_TRUTH_D3,
    GROUND_TRUTH_D4,
    GROUND_TRUTH_D5,
    GROUND_TRUTH_D6,
])

# Frame Structure Format (little-endian)
# - B: unsigned char (1 byte) for sof
# - B: unsigned char (1 byte) for length  
# - B: unsigned char (1 byte) for anchor_mask
# - I: unsigned int (4 bytes) for tx_frame_cnt
# - f: float (4 bytes) for ax
# - f: float (4 bytes) for ay
# - f: float (4 bytes) for gz
# - f: float (4 bytes) for px
# - f: float (4 bytes) for py
# - NUM_ANCHORS floats for distance array
# - NUM_ANCHORS doubles for fp_amp_norm array
# - NUM_ANCHORS doubles for fp_snr array
# - I: unsigned int (4 bytes) for error_frame_cnt
# - f: float (4 bytes) for dt
LIVE_FRAME_FORMAT = f'<BBBI5f{NUM_ANCHORS}f{NUM_ANCHORS}d{NUM_ANCHORS}dIf'
import struct
LIVE_FRAME_SIZE = struct.calcsize(LIVE_FRAME_FORMAT)

# ==================== FUSION FRAME CONFIGURATION ====================
# Matches firmware uart_fusion_frame_t:
# sof, length, anchor_mask, tx_frame_cnt,
# ukf_x, ukf_y, ukf_yaw, tril_x, tril_y, yaw, ukf_step, error_frame_cnt
# Position and yaw fields are int16 fixed-point values scaled by 100.
FUSION_FRAME_FORMAT = '<BBBIhhhhhhBI'
FUSION_FRAME_SIZE = struct.calcsize(FUSION_FRAME_FORMAT)
FUSION_FRAME_PAYLOAD_LEN = FUSION_FRAME_SIZE - 2
FUSION_FRAME_NO_STEP_FORMAT = '<BBBIhhhhhhI'
FUSION_FRAME_NO_STEP_SIZE = struct.calcsize(FUSION_FRAME_NO_STEP_FORMAT)
FUSION_FRAME_NO_STEP_PAYLOAD_LEN = FUSION_FRAME_NO_STEP_SIZE - 2
FUSION_FRAME_LEGACY_FORMAT = '<BBIhhhhhhI'
FUSION_FRAME_LEGACY_SIZE = struct.calcsize(FUSION_FRAME_LEGACY_FORMAT)
FUSION_FRAME_LEGACY_PAYLOAD_LEN = FUSION_FRAME_LEGACY_SIZE - 2
FUSION_FRAME_MIN_SIZE = min(FUSION_FRAME_SIZE, FUSION_FRAME_NO_STEP_SIZE, FUSION_FRAME_LEGACY_SIZE)
FUSION_FRAME_LENGTH_TO_SIZE = {
    FUSION_FRAME_LEGACY_PAYLOAD_LEN: FUSION_FRAME_LEGACY_SIZE,
    FUSION_FRAME_NO_STEP_PAYLOAD_LEN: FUSION_FRAME_NO_STEP_SIZE,
    FUSION_FRAME_NO_STEP_SIZE: FUSION_FRAME_NO_STEP_SIZE,
    FUSION_FRAME_PAYLOAD_LEN: FUSION_FRAME_SIZE,
    FUSION_FRAME_SIZE: FUSION_FRAME_SIZE,
}

# ==================== IMU Q Process CONFIGURATION ====================
# Frame Structure Format (little-endian)
# - B: unsigned char (1 byte) for sof
# - B: unsigned char (1 byte) for length  
# - I: unsigned int (4 bytes) for tx_frame_cnt
# - f: float (4 bytes) for ax
# - f: float (4 bytes) for ay
# - f: float (4 bytes) for gz
IMU_FRAME_FORMAT = '<BBI3f'
import struct
IMU_FRAME_SIZE = struct.calcsize(IMU_FRAME_FORMAT)

# ==================== CSV CONFIGURATION ====================
# File naming
CSV_UKF_FILENAME_PREFIX = "ukf_log_data"
CSV_UKF_FILENAME_SUFFIX = ".csv"

CSV_UKF_FUSION_FILENAME_PREFIX = "fusion_frame_log_data"
CSV_UKF_FUSION_FILENAME_SUFFIX = ".csv"

# File naming
CSV_IMU_FILENAME_PREFIX = "imu_log_data"
CSV_IMU_FILENAME_SUFFIX = ".csv"

# File naming
CSV_UWB_FILENAME_PREFIX = "uwb_log_data"
CSV_UWB_FILENAME_SUFFIX = ".csv"

# ==================== UWB CONFIGURATION ====================
# Frame Structure Format (little-endian)
# - B: unsigned char (1 byte) for sof
# - B: unsigned char (1 byte) for length  
# - I: unsigned int (4 bytes) for tx_frame_cnt
# - 4f: 4 floats (16 bytes) for distance array
UWB_FRAME_FORMAT = '<BBI4f'
UWB_FRAME_SIZE = struct.calcsize(UWB_FRAME_FORMAT)

# Logging Control
PRINT_DATA = True  # Set to False to disable console printing

# Predict/Update detection
PREDICT_THRESHOLD = 0.001

# Graph configuration
MAX_SAMPLES = 5000  # Define number of samples to keep in graph
