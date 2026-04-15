import serial
import time

HDLC_SOF = 0x55
HDLC_HEADER_LEN = 4

def calc_hdlc_checksum(data):
    """Giả lập hàm hdlc_checksum trong hdlc.c bằng cách cộng dồn các byte"""
    sum_val = sum(data)
    return sum_val & 0xFF

def build_hdlc_frame(frame_type: int, payload: bytes) -> bytearray:
    length = len(payload)
    
    # Header: SOF(1) | TYPE(1) | LEN_LOW(1) | LEN_HIGH(1)
    buf = bytearray()
    buf.append(HDLC_SOF)
    buf.append(frame_type)
    buf.append(length & 0xFF)
    buf.append((length >> 8) & 0xFF)
    
    # Payload
    if length > 0:
        buf.extend(payload)
        
    # Checksum (Tính tổng các bytes của header và payload)
    checksum = calc_hdlc_checksum(buf)
    buf.append(checksum)
    
    return buf

def main():
    # Thay cổng "COM" ứng với thiết bị nRF52/UART bridge của bạn
    # Ví dụ: trên windows là COM3, trên linux là /dev/ttyUSB0
    com_port = "COM3"
    baud_rate = 115200

    try:
        ser = serial.Serial(com_port, baud_rate, timeout=1)
        print(f"[*] Opened {com_port} at {baud_rate} baud")
    except Exception as e:
        print(f"[!] Failed to open port: {e}")
        return

    # Payload giả lập protobuf encode
    dummy_payload = bytes([0x01, 0x02, 0x03, 0x04])
    
    # Đóng gói qua HDLC (Loại packet Type giả định là 0x00)
    frame = build_hdlc_frame(frame_type=0x00, payload=dummy_payload)
    
    print(f"[*] Sending HDLC Frame ({len(frame)} bytes): {frame.hex(' ').upper()}")
    ser.write(frame)
    
    time.sleep(0.5)

    # Đợi response nếu MCU có In ra debug log hoặc phản hồi
    if ser.in_waiting > 0:
        rx_data = ser.read(ser.in_waiting)
        print(f"[*] Received Response: {rx_data.hex(' ')}")
    else:
        print("[*] No response data received yet.")

    ser.close()

if __name__ == "__main__":
    main()