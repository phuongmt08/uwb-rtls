"""UWB Gateway module - handles UART parsing and UDP sending"""

import serial
import socket
import struct
import time
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from config import Config

@dataclass
class UwbPosition:
    """UWB Position data structure with distances"""
    x: float  # meters
    y: float  # meters
    vx: float # m/s
    vy: float # m/s
    yaw: float # rad
    error: float  # meters
    error_frame_cnt: int
    distances: list[float] # meters from each anchor
    timestamp: float  # unix timestamp
    
    def __str__(self):
        dist_str = ", ".join([f"{d:.2f}" for d in self.distances])
        return f"Position(x={self.x:.3f}, y={self.y:.3f}, vx={self.vx:.3f}, vy={self.vy:.3f}, yaw={self.yaw:.3f}, err={self.error:.3f}, err_cnt={self.error_frame_cnt}, dists=[{dist_str}])"
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'x': self.x,
            'y': self.y,
            'vx': self.vx,
            'vy': self.vy,
            'yaw': self.yaw,
            'error': self.error,
            'error_frame_cnt': self.error_frame_cnt,
            'distances': self.distances,
            'timestamp': self.timestamp
        }


class UwbGateway:
    """
    UWB Gateway - reads UART frames and sends via UDP
    Combines parsing and network transmission in one module
    """
    
    def __init__(self, uart_port=None, udp_host=None, udp_port=None):
        """
        Initialize UWB Gateway
        
        Args:
            uart_port: Serial port path (default from Config)
            udp_host: UDP server IP (default from Config)
            udp_port: UDP server port (default from Config)
        """
        self.logger = logging.getLogger('UwbGateway')
        
        # Configuration
        self.uart_port = uart_port or Config.UART_PORT
        self.udp_host = udp_host or Config.UDP_HOST
        self.udp_port = udp_port or Config.UDP_PORT
        
        # Serial port
        self.serial = None
        
        # UDP socket
        self.udp_socket = None
        
        # Parsing state
        self.parse_buffer = bytearray()
        self.parse_index = 0
        
        # Statistics
        self.frame_count = 0
        self.error_count = 0
        self.udp_send_count = 0
        self.udp_error_count = 0
        self.last_stats_time = time.time()
        
        # Running flag
        self.running = False
    
    def connect(self):
        """Open serial port and UDP socket"""
        try:
            # Open UART
            self.serial = serial.Serial(
                port=self.uart_port,
                baudrate=Config.UART_BAUDRATE,
                timeout=Config.UART_TIMEOUT,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )
            self.logger.info(f"UART opened: {self.uart_port} @ {Config.UART_BAUDRATE}")
            
            # Create UDP socket
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.settimeout(Config.UDP_TIMEOUT)
            self.logger.info(f"UDP socket created -> {self.udp_host}:{self.udp_port}")
            
            return True
            
        except serial.SerialException as e:
            self.logger.error(f"Failed to open UART: {e}")
            return False
        except socket.error as e:
            self.logger.error(f"Failed to create UDP socket: {e}")
            if self.serial:
                self.serial.close()
            return False
    
    def disconnect(self):
        """Close serial port and UDP socket"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.logger.info("UART closed")
        
        if self.udp_socket:
            self.udp_socket.close()
            self.logger.info("UDP socket closed")
    
    def validate_frame(self, x: float, y: float, error: float) -> bool:
        """
        Validate UWB frame data
        
        Args:
            x, y: Position coordinates
            error: Error estimate
            
        Returns:
            True if valid, False otherwise
        """
        if not (Config.POSITION_MIN <= x <= Config.POSITION_MAX):
            return False
        if not (Config.POSITION_MIN <= y <= Config.POSITION_MAX):
            return False
        if not (Config.ERROR_MIN <= error <= Config.ERROR_MAX):
            return False
        return True
    
    def parse_frame(self, frame_bytes: bytes) -> Optional[UwbPosition]:
        """
        Parse UWB frame
        
        Frame format (variable):
            SOF(1) + LEN(1) + X(4) + Y(4) + VX(4) + VY(4) + Yaw(4) + Error(4) + ErrorCnt(4) + Dists(4*N)
        
        Args:
            frame_bytes: Raw frame data
            
        Returns:
            UwbPosition object if valid, None otherwise
        """
        try:
            if len(frame_bytes) < 30: # SOF(1) + LEN(1) + PX(4) + PY(4) + VX(4) + VY(4) + YAW(4) + Error(4) + ErrorCnt(4)
                return None
                
            sof = frame_bytes[0]
            length = frame_bytes[1]
            
            # Validate SOF
            if sof != Config.UWB_SOF:
                self.logger.debug(f"Invalid SOF: 0x{sof:02X}")
                return None
            
            # Length validation - minimum is 28 bytes without distances 
            # 28 bytes = PX(4) + PY(4) + VX(4) + VY(4) + YAW(4) + Error(4) + ErrorCnt(4)
            if length % 4 != 0 or length < 28:
                self.logger.debug(f"Invalid length byte: {length}")
                return None
            
            # Calculate number of distances
            num_anchors = (length - 28) // 4
            
            # Unpack: '<' = little-endian, 'B' = uint8, 'I' = uint32, 'f' = float
            fmt = f'<BBffffffI{num_anchors}f'
            unpacked = struct.unpack(fmt, frame_bytes)
            
            # unpacked indices
            sof, length, x, y, vx, vy, yaw, error, error_frame_cnt = unpacked[0:9]
            distances = list(unpacked[9:9+num_anchors])
            
            # Validate position data
            if not self.validate_frame(x, y, error):
                self.logger.debug(f"Invalid data: x={x}, y={y}, err={error}")
                return None
            
            # Create position object
            position = UwbPosition(
                x=x,
                y=y,
                vx=vx,
                vy=vy,
                yaw=yaw,
                error=error,
                error_frame_cnt=error_frame_cnt,
                distances=distances,
                timestamp=time.time()
            )
            
            return position
            
        except (struct.error, IndexError) as e:
            self.logger.error(f"Frame parse error: {e}")
            return None
    
    def send_udp(self, position: UwbPosition) -> bool:
        """
        Send position and distance data via UDP
        
        Args:
            position: UwbPosition object
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Format: Binary format with fields matching UwbPosition
            # X(4) + Y(4) + VX(4) + VY(4) + Yaw(4) + Error(4) + ErrorCnt(4) + Dists(4*N)
            num_dists = len(position.distances)
            fmt = f'<ffffffI{num_dists}f'
            
            data = struct.pack(fmt,
                position.x,
                position.y,
                position.vx,
                position.vy,
                position.yaw,
                position.error,
                position.error_frame_cnt,
                *position.distances,
            )
            
            self.udp_socket.sendto(data, (self.udp_host, self.udp_port))
            self.udp_send_count += 1
            return True
            
        except socket.error as e:
            self.logger.error(f"UDP send error: {e}")
            self.udp_error_count += 1
            return False
    
    def process_uart_data(self):
        """
        Read and process UART data with dynamic length detection
        Returns number of bytes processed
        """
        if not self.serial or not self.serial.is_open:
            return 0
        
        bytes_available = self.serial.in_waiting
        if bytes_available == 0:
            return 0
        
        # Read available bytes
        data = self.serial.read(bytes_available)
        bytes_processed = 0
        
        for byte in data:
            bytes_processed += 1
            
            # State 0: Looking for Start-Of-Frame
            if self.parse_index == 0:
                if byte == Config.UWB_SOF:
                    self.parse_buffer = bytearray([byte])
                    self.parse_index = 1
                continue
            
            # State 1: Reading Length byte
            if self.parse_index == 1:
                self.parse_buffer.append(byte)
                self.parse_index = 2
                self.expected_len = byte + 2 # Total frame is SOF + LEN + Payload
                continue

            # Collecting payload bytes
            self.parse_buffer.append(byte)
            self.parse_index += 1
            
            # Complete frame received?
            if self.parse_index == self.expected_len:
                position = self.parse_frame(bytes(self.parse_buffer))
                
                if position:
                    self.frame_count += 1
                    self.logger.debug(f"Parsed: {position}")
                    
                    # Send via UDP
                    if self.send_udp(position):
                        self.logger.debug(f"Sent UDP: {position}")
                    
                else:
                    self.error_count += 1
                
                # Reset parser
                self.parse_buffer = bytearray()
                self.parse_index = 0
            
            # Overflow protection (max size say 256 bytes)
            if self.parse_index > 256:
                self.logger.warning("Parse buffer overflow, resetting")
                self.parse_buffer = bytearray()
                self.parse_index = 0
                self.error_count += 1
        
        return bytes_processed
    
    def print_statistics(self):
        """Print statistics"""
        now = time.time()
        elapsed = now - self.last_stats_time
        
        if elapsed >= Config.STATS_INTERVAL:
            self.logger.info("=" * 60)
            self.logger.info(f"Statistics (last {elapsed:.1f}s):")
            self.logger.info(f"  Valid frames: {self.frame_count}")
            self.logger.info(f"  Parse errors: {self.error_count}")
            self.logger.info(f"  UDP sent: {self.udp_send_count}")
            self.logger.info(f"  UDP errors: {self.udp_error_count}")
            
            if self.frame_count > 0:
                success_rate = (self.udp_send_count / self.frame_count) * 100
                self.logger.info(f"  Success rate: {success_rate:.1f}%")
            
            self.logger.info("=" * 60)
            self.last_stats_time = now
    
    def run(self):
        """Main loop - process UART data continuously"""
        self.running = True
        self.logger.info("Gateway started")
        
        try:
            while self.running:
                # Process UART data
                self.process_uart_data()
                
                # Print statistics periodically
                self.print_statistics()
                
                # Small delay to prevent CPU hogging
                time.sleep(0.001)  # 1ms
                
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        except Exception as e:
            self.logger.error(f"Runtime error: {e}", exc_info=True)
        finally:
            self.running = False
            self.logger.info("Gateway stopped")
    
    def stop(self):
        """Stop the gateway"""
        self.running = False