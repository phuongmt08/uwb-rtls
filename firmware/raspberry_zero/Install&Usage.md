"""
Installation on Raspberry Pi Zero:

1. Install dependencies:
   sudo apt update
   sudo apt install python3-serial python3-pip
   pip3 install pyserial

2. Enable UART on Raspberry Pi:
   - Edit /boot/config.txt:
     enable_uart=1
     dtoverlay=disable-bt
   - Reboot: sudo reboot

3. Check UART device:
   ls -l /dev/serial*
   # Should see /dev/serial0 -> ttyAMA0

4. Run the gateway:
   python3 main.py --host 192.168.1.100 --port 5000

5. Run with custom settings:
   python3 main.py --uart /dev/ttyUSB0 --host 10.0.0.50 --port 6000 --log-level DEBUG

6. Run as service (systemd):
   Create /etc/systemd/system/uwb-gateway.service:
   
   [Unit]
   Description=UWB Position Gateway
   After=network.target

   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/uwb_gateway
   ExecStart=/usr/bin/python3 /home/pi/uwb_gateway/main.py --host 192.168.1.100
   Restart=always

   [Install]
   WantedBy=multi-user.target
   
   Enable and start:
   sudo systemctl enable uwb-gateway
   sudo systemctl start uwb-gateway
   sudo systemctl status uwb-gateway
