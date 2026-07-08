import struct
from .config import (
    UART_SOF,
    LIVE_FRAME_FORMAT, LIVE_FRAME_SIZE,
    LIVE_FRAME_LEGACY_FORMAT, LIVE_FRAME_LEGACY_SIZE,
    FUSION_FRAME_FORMAT, FUSION_FRAME_SIZE, FUSION_FRAME_PAYLOAD_LEN,
    IMU_FRAME_FORMAT, IMU_FRAME_SIZE,
    UWB_FRAME_FORMAT, UWB_FRAME_SIZE
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
        # New frames append rx_fp_delta_db[4]; the length byte tells them apart.
        is_new_format = (len(data_bytes) >= LIVE_FRAME_SIZE and
                         len(data_bytes) >= 2 and
                         data_bytes[1] == (LIVE_FRAME_SIZE - 2) & 0xFF)
        if is_new_format:
            unpacked = struct.unpack(LIVE_FRAME_FORMAT, data_bytes[:LIVE_FRAME_SIZE])
        else:
            unpacked = struct.unpack(LIVE_FRAME_LEGACY_FORMAT, data_bytes[:LIVE_FRAME_LEGACY_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        if unpacked[2] != 0:
            print(f"[PARSER] Detected non-zero mask: {unpacked[2]} | Frame hex: {data_bytes[:10].hex()}")
        frame = {
            'sof': unpacked[0],
            'length': unpacked[1],
            'anchor_mask': unpacked[2],
            'tx_frame_cnt': unpacked[3],
            'ax': unpacked[4],
            'ay': unpacked[5],
            'gz': unpacked[6],
            'px': unpacked[7],
            'py': unpacked[8],
            'distances': list(unpacked[9:13]),
            'fp_amp_norm': list(unpacked[13:17]),
            'fp_snr': list(unpacked[17:21]),
            'err_cnt': unpacked[21],
            'dt': unpacked[22],
            'rx_fp_delta_db': list(unpacked[23:27]) if is_new_format else [0.0, 0.0, 0.0, 0.0],
        }
        return frame
    except struct.error:
        return None

def parse_uart_fusion_frame(data_bytes):
    try:
        unpacked = struct.unpack(FUSION_FRAME_FORMAT, data_bytes[:FUSION_FRAME_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        # Firmware stores payload length as sizeof(uart_fusion_frame_t) - 2.
        # Some older senders used the full frame size, so keep that accepted
        # while parsing the new packed fusion frame layout.
        if unpacked[1] not in (FUSION_FRAME_PAYLOAD_LEN, FUSION_FRAME_SIZE):
            return None
        return {
            'sof': unpacked[0],
            'length': unpacked[1],
            'tx_frame_cnt': unpacked[2],
            'ukf_x': unpacked[3] / 100.0,
            'ukf_y': unpacked[4] / 100.0,
            'ukf_yaw': unpacked[5] / 100.0,
            'tril_x': unpacked[6] / 100.0,
            'tril_y': unpacked[7] / 100.0,
            'yaw': unpacked[8] / 100.0,
            'err_cnt': unpacked[9],
            'error_frame_cnt': unpacked[9],
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
