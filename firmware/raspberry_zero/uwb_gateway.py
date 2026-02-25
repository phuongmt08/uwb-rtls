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
    """UWB Position data structure"""
    x: float  # meters
    y: float  # meters
    z: float  # meters
    error: float  # meters
    timestamp: float  # unix timestamp
    
    def __str__(self):
        return f"Position(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f}, err={self.error:.3f})"
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'x': self.x,
            'y': self.y,
            'z': self.z,
            'error': self.error,
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
    
    def validate_frame(self, x: float, y: float, z: float, error: float) -> bool:
        """
        Validate UWB frame data
        
        Args:
            x, y, z: Position coordinates
            error: Error estimate
            
        Returns:
            True if valid, False otherwise
        """
        if not (Config.POSITION_MIN <= x <= Config.POSITION_MAX):
            return False
        if not (Config.POSITION_MIN <= y <= Config.POSITION_MAX):
            return False
        if not (Config.POSITION_MIN <= z <= Config.POSITION_MAX):
            return False
        if not (Config.ERROR_MIN <= error <= Config.ERROR_MAX):
            return False
        return True
    
    def parse_frame(self, frame_bytes: bytes) -> Optional[UwbPosition]:
        """
        Parse UWB frame
        
        Frame format (18 bytes):
            SOF(1) + X(4) + Y(4) + Z(4) + Error(4) + Length(1)
        
        Args:
            frame_bytes: Raw frame data (18 bytes)
            
        Returns:
            UwbPosition object if valid, None otherwise
        """
        try:
            # Unpack: '<' = little-endian, 'B' = uint8, 'f' = float
            sof, x, y, z, error, length = struct.unpack('<Bffffb', frame_bytes)
            
            # Validate SOF and length
            if sof != Config.UWB_SOF:
                self.logger.debug(f"Invalid SOF: 0x{sof:02X}")
                return None
            
            if length != Config.UWB_PAYLOAD_LEN:
                self.logger.debug(f"Invalid length: {length}")
                return None
            
            # Validate position data
            if not self.validate_frame(x, y, z, error):
                self.logger.debug(f"Invalid data: x={x}, y={y}, z={z}, err={error}")
                return None
            
            # Create position object
            position = UwbPosition(
                x=x,
                y=y,
                z=z,
                error=error,
                timestamp=time.time()
            )
            
            return position
            
        except struct.error as e:
            self.logger.error(f"Frame parse error: {e}")
            return None
    
    def send_udp(self, position: UwbPosition) -> bool:
        """
        Send position data via UDP
        
        Args:
            position: UwbPosition object
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Format: Binary format with only x, y, z (12 bytes)
            data = struct.pack('<fff',
                position.x,
                position.y,
                position.z
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
        Read and process UART data
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
            
            # State machine: Looking for Start-Of-Frame
            if self.parse_index == 0:
                if byte == Config.UWB_SOF:
                    self.parse_buffer = bytearray([byte])
                    self.parse_index = 1
                continue
            
            # Collecting frame bytes
            self.parse_buffer.append(byte)
            self.parse_index += 1
            
            # Complete frame received?
            if self.parse_index == Config.UWB_FRAME_SIZE:
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
            
            # Overflow protection
            if self.parse_index >= Config.UWB_FRAME_SIZE:
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