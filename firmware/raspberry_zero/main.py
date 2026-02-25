"""Main entry point"""

import logging
import argparse
import sys
from config import Config
from uwb_gateway import UwbGateway

def setup_logging(level='INFO'):
    """Setup logging configuration"""
    logging.basicConfig(
        level=getattr(logging, level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(Config.LOG_FILE)
        ]
    )

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='UWB UART to UDP Gateway')
    parser.add_argument('--uart', default=Config.UART_PORT, help='UART port')
    parser.add_argument('--host', default=Config.UDP_HOST, help='UDP server IP')
    parser.add_argument('--port', type=int, default=Config.UDP_PORT, help='UDP server port')
    parser.add_argument('--log-level', default=Config.LOG_LEVEL, 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Logging level')
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger('Main')
    
    logger.info("=" * 60)
    logger.info("UWB Position Gateway for Raspberry Pi Zero")
    logger.info("=" * 60)
    logger.info(f"UART: {args.uart} @ {Config.UART_BAUDRATE}")
    logger.info(f"UDP: {args.host}:{args.port}")
    logger.info("=" * 60)
    
    # Create gateway
    gateway = UwbGateway(
        uart_port=args.uart,
        udp_host=args.host,
        udp_port=args.port
    )
    
    # Connect
    if not gateway.connect():
        logger.error("Failed to connect, exiting")
        return 1
    
    try:
        # Run main loop
        gateway.run()
    finally:
        # Cleanup
        gateway.disconnect()
        logger.info("Shutdown complete")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
