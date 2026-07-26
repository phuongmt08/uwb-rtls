import struct
from .config import (
    UART_SOF, 
    LIVE_FRAME_FORMAT, LIVE_FRAME_SIZE, 
    FUSION_FRAME_FORMAT, FUSION_FRAME_SIZE, FUSION_FRAME_PAYLOAD_LEN,
    FUSION_FRAME_NO_STEP_FORMAT, FUSION_FRAME_NO_STEP_SIZE, FUSION_FRAME_NO_STEP_PAYLOAD_LEN,
    FUSION_FRAME_LEGACY_FORMAT, FUSION_FRAME_LEGACY_SIZE, FUSION_FRAME_LEGACY_PAYLOAD_LEN,
    IMU_FRAME_FORMAT, IMU_FRAME_SIZE,
    UWB_FRAME_FORMAT, UWB_FRAME_SIZE,
    NUM_ANCHORS
)

def parse_uwb_frame(data_bytes):
    try:
        unpacked = struct.unpack(UWB_FRAME_FORMAT, data_bytes[:UWB_FRAME_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        return {
            'sof': unpacked[0],
            'length': unpacked[1],
            'tx_frame_cnt': unpacked[2],
            'distances': list(unpacked[3:7])
        }
    except struct.error:
        return None

def parse_live_frame(data_bytes):
    try:
        unpacked = struct.unpack(LIVE_FRAME_FORMAT, data_bytes[:LIVE_FRAME_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        if unpacked[2] != 0:
            print(f"[PARSER] Detected non-zero mask: {unpacked[2]} | Frame hex: {data_bytes[:10].hex()}")
        distance_start = 9
        fp_amp_start = distance_start + NUM_ANCHORS
        fp_snr_start = fp_amp_start + NUM_ANCHORS
        err_index = fp_snr_start + NUM_ANCHORS
        return {
            'sof': unpacked[0],
            'length': unpacked[1],
            'anchor_mask': unpacked[2],
            'tx_frame_cnt': unpacked[3],
            'ax': unpacked[4],
            'ay': unpacked[5],
            'gz': unpacked[6],
            'px': unpacked[7],
            'py': unpacked[8],
            'distances': list(unpacked[distance_start:fp_amp_start]),
            'fp_amp_norm': list(unpacked[fp_amp_start:fp_snr_start]),
            'fp_snr': list(unpacked[fp_snr_start:err_index]),
            'err_cnt': unpacked[err_index],
            'dt': unpacked[err_index + 1]
        }
    except struct.error:
        return None

def parse_uart_fusion_frame(data_bytes):
    try:
        if len(data_bytes) >= FUSION_FRAME_SIZE:
            unpacked = struct.unpack(FUSION_FRAME_FORMAT, data_bytes[:FUSION_FRAME_SIZE])
            if unpacked[0] == UART_SOF and unpacked[1] in (FUSION_FRAME_PAYLOAD_LEN, FUSION_FRAME_SIZE):
                return {
                    'sof': unpacked[0],
                    'length': unpacked[1],
                    'anchor_mask': unpacked[2],
                    'tx_frame_cnt': unpacked[3],
                    'ukf_x': unpacked[4] / 100.0,
                    'ukf_y': unpacked[5] / 100.0,
                    'ukf_yaw': unpacked[6] / 100.0,
                    'tril_x': unpacked[7] / 100.0,
                    'tril_y': unpacked[8] / 100.0,
                    'yaw': unpacked[9] / 100.0,
                    'ukf_step': unpacked[10],
                    'err_cnt': unpacked[11],
                    'error_frame_cnt': unpacked[11],
                    'error_count': unpacked[11],
                }

        if len(data_bytes) >= FUSION_FRAME_NO_STEP_SIZE:
            unpacked = struct.unpack(FUSION_FRAME_NO_STEP_FORMAT, data_bytes[:FUSION_FRAME_NO_STEP_SIZE])
            if unpacked[0] == UART_SOF and unpacked[1] in (FUSION_FRAME_NO_STEP_PAYLOAD_LEN, FUSION_FRAME_NO_STEP_SIZE):
                return {
                    'sof': unpacked[0],
                    'length': unpacked[1],
                    'anchor_mask': unpacked[2],
                    'tx_frame_cnt': unpacked[3],
                    'ukf_x': unpacked[4] / 100.0,
                    'ukf_y': unpacked[5] / 100.0,
                    'ukf_yaw': unpacked[6] / 100.0,
                    'tril_x': unpacked[7] / 100.0,
                    'tril_y': unpacked[8] / 100.0,
                    'yaw': unpacked[9] / 100.0,
                    'ukf_step': unpacked[10],
                    'err_cnt': unpacked[10],
                    'error_frame_cnt': unpacked[10],
                    'error_count': unpacked[10],
                }

        unpacked = struct.unpack(FUSION_FRAME_LEGACY_FORMAT, data_bytes[:FUSION_FRAME_LEGACY_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        # Firmware stores payload length as sizeof(uart_fusion_frame_t) - 2.
        # Some older senders used the full frame size, so keep that accepted
        # while parsing the new packed fusion frame layout.
        if unpacked[1] not in (FUSION_FRAME_LEGACY_PAYLOAD_LEN, FUSION_FRAME_LEGACY_SIZE):
            return None
        return {
            'sof': unpacked[0],
            'length': unpacked[1],
            'anchor_mask': 0,
            'tx_frame_cnt': unpacked[2],
            'ukf_x': unpacked[3] / 100.0,
            'ukf_y': unpacked[4] / 100.0,
            'ukf_yaw': unpacked[5] / 100.0,
            'tril_x': unpacked[6] / 100.0,
            'tril_y': unpacked[7] / 100.0,
            'yaw': unpacked[8] / 100.0,
            'ukf_step': unpacked[9],
            'err_cnt': unpacked[9],
            'error_frame_cnt': unpacked[9],
            'error_count': unpacked[9],
        }
    except struct.error:
        return None

def parse_imu_frame(data_bytes):
    try:
        unpacked = struct.unpack(IMU_FRAME_FORMAT, data_bytes[:IMU_FRAME_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        return {
            'sof': unpacked[0],
            'length': unpacked[1],
            'tx_frame_cnt': unpacked[2],
            'ax': unpacked[3],
            'ay': unpacked[4],
            'gz': unpacked[5],
        }
    except struct.error:
        return None
