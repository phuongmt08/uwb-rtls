"""UWB Gateway module - handles UART parsing and UDP sending"""

import serial
import socket
import struct
import time
import logging
import json
import threading
import asyncio
import websockets
from dataclasses import dataclass
from typing import Optional
from config import Config

UART_FUSION_FORMAT = '<BBIhhhhhhI'
UART_FUSION_FRAME_LEN = struct.calcsize(UART_FUSION_FORMAT)
UART_FUSION_PAYLOAD_LEN = UART_FUSION_FRAME_LEN - 2
UART_FUSION_LEGACY_PAYLOAD_LEN = UART_FUSION_FRAME_LEN


@dataclass
class UwbFusionFrame:
    """UKF/UWB fusion data frame from STM uart_fusion_frame_t"""
    sof: int
    length: int
    tx_frame_cnt: int
    ukf_x: float
    ukf_y: float
    ukf_yaw: float
    tril_x: float
    tril_y: float
    yaw: float
    error_frame_cnt: int
    timestamp: float

    def __str__(self):
        return (
            f"Fusion(cnt={self.tx_frame_cnt}, "
            f"ukf=({self.ukf_x:.3f}, {self.ukf_y:.3f}, {self.ukf_yaw:.2f}deg), "
            f"tril=({self.tril_x:.3f}, {self.tril_y:.3f}), "
            f"yaw={self.yaw:.2f}deg, err_cnt={self.error_frame_cnt})"
        )

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'type': 'fusion',
            'sof': self.sof,
            'length': self.length,
            'tx_frame_cnt': self.tx_frame_cnt,
            'ukf_x': self.ukf_x,
            'ukf_y': self.ukf_y,
            'ukf_yaw': self.ukf_yaw,
            'tril_x': self.tril_x,
            'tril_y': self.tril_y,
            'yaw': self.yaw,
            'error_frame_cnt': self.error_frame_cnt,
            'timestamp': self.timestamp
        }


class UwbGateway:
    """
    UWB Gateway - reads UART frames and sends via UDP
    Combines parsing and network transmission in one module
    """
    
    def __init__(self, uart_port=None, udp_host=None, udp_port=None, ws_host=None, ws_port=None):
        """
        Initialize UWB Gateway
        
        Args:
            uart_port: Serial port path (default from Config)
            udp_host: UDP server IP (default from Config)
            udp_port: UDP server port (default from Config)
            ws_host: WebSocket host to bind (default from Config)
            ws_port: WebSocket port to bind (default from Config)
        """
        self.logger = logging.getLogger('UwbGateway')
        
        # Configuration
        self.uart_port = uart_port or Config.UART_PORT
        self.udp_host = udp_host or Config.UDP_HOST
        self.udp_port = udp_port or Config.UDP_PORT
        self.ws_host = ws_host or Config.WS_HOST
        self.ws_port = ws_port or Config.WS_PORT
        
        # Serial port
        self.serial = None
        
        # UDP socket
        self.udp_socket = None
        
        # WebSocket server states
        self.ws_clients = set()
        self.ws_loop = None
        self.ws_thread = None
        self.ws_server = None
        self.ws_lock = threading.Lock()
        
        # Parsing state
        self.parse_buffer = bytearray()
        self.parse_index = 0
        
        # Statistics
        self.frame_count = 0
        self.error_count = 0
        self.udp_send_count = 0
        self.udp_error_count = 0
        self.ws_send_count = 0
        self.ws_error_count = 0
        self.last_stats_time = time.time()
        
        # Running flag
        self.running = False
    
    def connect(self):
        """Open serial port and start network services (UDP and/or WebSocket)"""
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
            
            # Create UDP socket if enabled
            if Config.UDP_ENABLED:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_socket.settimeout(Config.UDP_TIMEOUT)
                self.logger.info(f"UDP socket created -> {self.udp_host}:{self.udp_port}")
            else:
                self.logger.info("UDP streaming is disabled")
            
            # Start WebSocket server if enabled
            if Config.WS_ENABLED:
                self._start_ws_server()
                
            return True
            
        except serial.SerialException as e:
            self.logger.error(f"Failed to open UART: {e}")
            return False
        except socket.error as e:
            self.logger.error(f"Failed to initialize network sockets: {e}")
            if self.serial:
                self.serial.close()
            return False

    def _start_ws_server(self):
        """Start the WebSocket server in a separate background thread"""
        self.ws_loop = asyncio.new_event_loop()
        self.ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self.ws_thread.start()
        self.logger.info("WebSocket server thread started")

    def _run_ws_loop(self):
        """Run the asyncio event loop for the WebSocket server"""
        asyncio.set_event_loop(self.ws_loop)
        
        # Start server
        try:
            start_server = websockets.serve(self._ws_handler, self.ws_host, self.ws_port)
            self.ws_server = self.ws_loop.run_until_complete(start_server)
            self.logger.info(f"WebSocket server listening on ws://{self.ws_host}:{self.ws_port}")
            self.ws_loop.run_forever()
        except Exception as e:
            self.logger.error(f"WebSocket loop error: {e}")
        finally:
            self.ws_loop.close()
            self.logger.info("WebSocket loop closed")

    async def _ws_handler(self, websocket):
        """Handle incoming WebSocket client connections"""
        client_address = websocket.remote_address
        self.logger.info(f"WebSocket client connected from {client_address}")
        with self.ws_lock:
            self.ws_clients.add(websocket)
            
        try:
            # Keep connection open and detect disconnection
            async for message in websocket:
                self.logger.debug(f"Received message from client {client_address}: {message}")
        except websockets.exceptions.ConnectionClosed:
            self.logger.info(f"WebSocket client disconnected from {client_address}")
        except Exception as e:
            self.logger.error(f"Error in WebSocket handler for client {client_address}: {e}")
        finally:
            with self.ws_lock:
                self.ws_clients.discard(websocket)
            self.logger.debug(f"Removed WebSocket client {client_address}")

    def broadcast_ws(self, frame: UwbFusionFrame):
        """Broadcast frame to all WebSocket clients (thread-safe)"""
        if not Config.WS_ENABLED or not self.ws_clients:
            return

        # Prepare JSON payload
        data_dict = frame.to_dict()
        message = json.dumps(data_dict)
        
        # Run send coroutine in the websocket event loop
        if self.ws_loop and self.ws_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_broadcast(message), self.ws_loop)

    async def _async_broadcast(self, message: str):
        """Async helper to broadcast message to all connected clients"""
        with self.ws_lock:
            active_clients = list(self.ws_clients)
            
        if active_clients:
            tasks = [self._send_to_client(client, message) for client in active_clients]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_to_client(self, client, message: str):
        """Send message to a single client with error handling"""
        try:
            await client.send(message)
            self.ws_send_count += 1
        except Exception as e:
            self.logger.error(f"WebSocket send error to client: {e}")
            self.ws_error_count += 1

    def disconnect(self):
        """Close serial port, UDP socket, and stop WebSocket server"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.logger.info("UART closed")
        
        if self.udp_socket:
            self.udp_socket.close()
            self.logger.info("UDP socket closed")
            
        if Config.WS_ENABLED and self.ws_server and self.ws_loop:
            self.logger.info("Stopping WebSocket server...")
            # Schedule closing of server
            self.ws_loop.call_soon_threadsafe(self.ws_server.close)
            
            # Wait for server to close
            future = asyncio.run_coroutine_threadsafe(self.ws_server.wait_closed(), self.ws_loop)
            try:
                future.result(timeout=2.0)
            except Exception as e:
                self.logger.error(f"Error waiting for WebSocket server to close: {e}")
                
            # Close all active clients
            with self.ws_lock:
                active_clients = list(self.ws_clients)
            
            if active_clients:
                close_tasks = []
                for client in active_clients:
                    close_tasks.append(asyncio.run_coroutine_threadsafe(client.close(), self.ws_loop))
                for task in close_tasks:
                    try:
                        task.result(timeout=1.0)
                    except Exception:
                        pass
                        
            # Stop the loop
            self.ws_loop.call_soon_threadsafe(self.ws_loop.stop)
            self.logger.info("WebSocket server stopped")

    @staticmethod
    def _decode_fixed2(value: int) -> float:
        """Decode int16 fixed-point values with 2 decimal places."""
        return value / 100.0
    
    def validate_fusion_frame(self, frame: UwbFusionFrame) -> bool:
        """Validate UKF/trilateration fusion frame data"""
        values = [
            frame.ukf_x,
            frame.ukf_y,
            frame.tril_x,
            frame.tril_y,
        ]

        for value in values:
            if not (Config.POSITION_MIN <= value <= Config.POSITION_MAX):
                return False

        return True
    
    def parse_frame(self, frame_bytes: bytes) -> Optional[UwbFusionFrame]:
        """
        Parse STM uart_fusion_frame_t only.
        
        Frame:
            SOF(1) + LEN(1) + TxCnt(4)
            + UKF_X(2) + UKF_Y(2) + UKF_Yaw(2)
            + Tril_X(2) + Tril_Y(2) + Yaw(2) + ErrorCnt(4)
        
        Args:
            frame_bytes: Raw frame data
            
        Returns:
            UwbFusionFrame object if valid, None otherwise
        """
        try:
            return self.parse_fusion_frame(frame_bytes)
            
        except (struct.error, IndexError) as e:
            self.logger.error(f"Frame parse error: {e}")
            return None

    def parse_fusion_frame(self, frame_bytes: bytes) -> Optional[UwbFusionFrame]:
        """Parse STM uart_fusion_frame_t"""
        try:
            if len(frame_bytes) != UART_FUSION_FRAME_LEN:
                self.logger.debug(f"Invalid fusion frame size: {len(frame_bytes)}")
                return None

            unpacked = struct.unpack(UART_FUSION_FORMAT, frame_bytes)
            (
                sof,
                length,
                tx_frame_cnt,
                ukf_x,
                ukf_y,
                ukf_yaw,
                tril_x,
                tril_y,
                yaw,
                error_frame_cnt,
            ) = unpacked

            if sof != Config.UWB_SOF or length not in (UART_FUSION_PAYLOAD_LEN, UART_FUSION_LEGACY_PAYLOAD_LEN):
                self.logger.debug(f"Invalid fusion header: sof=0x{sof:02X}, length={length}")
                return None

            frame = UwbFusionFrame(
                sof=sof,
                length=length,
                tx_frame_cnt=tx_frame_cnt,
                ukf_x=self._decode_fixed2(ukf_x),
                ukf_y=self._decode_fixed2(ukf_y),
                ukf_yaw=self._decode_fixed2(ukf_yaw),
                tril_x=self._decode_fixed2(tril_x),
                tril_y=self._decode_fixed2(tril_y),
                yaw=self._decode_fixed2(yaw),
                error_frame_cnt=error_frame_cnt,
                timestamp=time.time()
            )

            if not self.validate_fusion_frame(frame):
                self.logger.debug(f"Invalid fusion data: {frame}")
                return None

            return frame

        except (struct.error, IndexError) as e:
            self.logger.error(f"Fusion frame parse error: {e}")
            return None
    
    def send_udp(self, frame_bytes: bytes) -> bool:
        """
        Send the validated fusion frame via UDP without altering its bytes.
        
        Args:
            frame_bytes: Raw frame bytes
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            self.udp_socket.sendto(frame_bytes, (self.udp_host, self.udp_port))
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
                if byte not in (UART_FUSION_PAYLOAD_LEN, UART_FUSION_LEGACY_PAYLOAD_LEN):
                    self.logger.debug(f"Invalid fusion length byte: {byte}")
                    self.parse_buffer = bytearray()
                    self.parse_index = 0
                    self.error_count += 1
                    continue

                self.parse_buffer.append(byte)
                self.parse_index = 2
                self.expected_len = byte + 2 # Total frame is SOF + LEN + Payload
                continue

            # Collecting payload bytes
            self.parse_buffer.append(byte)
            self.parse_index += 1
            
            # Complete frame received?
            if self.parse_index == self.expected_len:
                frame = self.parse_frame(bytes(self.parse_buffer))
                
                if frame:
                    self.frame_count += 1
                    self.logger.debug(f"Parsed: {frame}")
                    
                    # Send via UDP
                    if Config.UDP_ENABLED:
                        if self.send_udp(bytes(self.parse_buffer)):
                            self.logger.debug(f"Sent UDP: {frame}")
                    
                    # Send via WebSocket
                    if Config.WS_ENABLED:
                        self.broadcast_ws(frame)
                        self.logger.debug(f"Broadcasted WS: {frame}")
                    
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
            if Config.UDP_ENABLED:
                self.logger.info(f"  UDP sent: {self.udp_send_count}")
                self.logger.info(f"  UDP errors: {self.udp_error_count}")
            if Config.WS_ENABLED:
                with self.ws_lock:
                    client_count = len(self.ws_clients)
                self.logger.info(f"  WS clients connected: {client_count}")
                self.logger.info(f"  WS messages sent: {self.ws_send_count}")
                self.logger.info(f"  WS errors: {self.ws_error_count}")
            
            if self.frame_count > 0:
                if Config.UDP_ENABLED:
                    success_rate = (self.udp_send_count / self.frame_count) * 100
                    self.logger.info(f"  UDP Success rate: {success_rate:.1f}%")
            
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
