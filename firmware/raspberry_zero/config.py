"""Configuration module"""

class Config:
    # UART Configuration
    UART_PORT = '/dev/serial0'  # Raspberry Pi default UART (GPIO 14/15)
    UART_BAUDRATE = 115200
    UART_TIMEOUT = 1.0  # seconds
    
    # UWB Protocol
    UWB_SOF = 0xAA
    # Frame is now dynamic: [SOF(1)] [LEN(1)] [DATA(LEN)]
    # LEN = (3 + NUM_ANCHORS + 1) * 4
    
    # UDP Configuration
    UDP_HOST = '192.168.1.100'  # Server IP address
    UDP_PORT = 5000
    UDP_TIMEOUT = 0.5
    
    # Validation ranges (meters)
    POSITION_MIN = -100.0
    POSITION_MAX = 100.0
    ERROR_MIN = 0.0
    ERROR_MAX = 10.0
    
    # Statistics
    STATS_INTERVAL = 10.0  # seconds
    
    # Logging
    LOG_LEVEL = 'INFO'  # DEBUG, INFO, WARNING, ERROR
    LOG_FILE = 'uwb_gateway.log'
