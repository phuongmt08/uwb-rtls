import struct
from .config import (
    UART_SOF, 
    LIVE_FRAME_FORMAT, LIVE_FRAME_SIZE, 
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
        unpacked = struct.unpack(LIVE_FRAME_FORMAT, data_bytes[:LIVE_FRAME_SIZE])
        if unpacked[0] != UART_SOF:
            return None
        if unpacked[2] != 0:
            print(f"[PARSER] Detected non-zero mask: {unpacked[2]} | Frame hex: {data_bytes[:10].hex()}")
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
            'distances': list(unpacked[9:13]),
            'err_cnt': unpacked[13],
            'dt': unpacked[14]
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
