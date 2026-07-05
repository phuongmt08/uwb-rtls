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

def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='UWB UART to Network Gateway')
    parser.add_argument('--uart', default=Config.UART_PORT, help='UART port')
    
    # UDP Arguments
    parser.add_argument('--udp-enable', type=str2bool, default=Config.UDP_ENABLED, help='Enable UDP streaming')
    parser.add_argument('--host', default=Config.UDP_HOST, help='UDP server IP')
    parser.add_argument('--port', type=int, default=Config.UDP_PORT, help='UDP server port')
    
    # WebSocket Arguments
    parser.add_argument('--ws-enable', type=str2bool, default=Config.WS_ENABLED, help='Enable WebSocket server')
    parser.add_argument('--ws-host', default=Config.WS_HOST, help='WebSocket host to bind')
    parser.add_argument('--ws-port', type=int, default=Config.WS_PORT, help='WebSocket port to bind')
    
    parser.add_argument('--log-level', default=Config.LOG_LEVEL, 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Logging level')
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger('Main')
    
    # Apply enable/disable configs
    Config.UDP_ENABLED = args.udp_enable
    Config.WS_ENABLED = args.ws_enable
    
    logger.info("=" * 60)
    logger.info("UWB Position Gateway for Raspberry Pi Zero")
    logger.info("=" * 60)
    logger.info(f"UART: {args.uart} @ {Config.UART_BAUDRATE}")
    if Config.UDP_ENABLED:
        logger.info(f"UDP Target: {args.host}:{args.port}")
    if Config.WS_ENABLED:
        logger.info(f"WebSocket Server: ws://{args.ws_host}:{args.ws_port}")
    logger.info("=" * 60)
    
    # Create gateway
    gateway = UwbGateway(
        uart_port=args.uart,
        udp_host=args.host,
        udp_port=args.port,
        ws_host=args.ws_host,
        ws_port=args.ws_port
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
