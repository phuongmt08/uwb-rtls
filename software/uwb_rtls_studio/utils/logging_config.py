"""
===============================================================================
  UWB RTLS Studio — Logging Configuration
===============================================================================
  File        : utils/logging_config.py
  Description : Quản lý mức độ hiển thị log (log levels) trên terminal.
                Bạn có thể bật/tắt hoặc nâng mức log của các thư viện tại đây.
===============================================================================
"""
import logging

# Mức log mặc định cho toàn bộ ứng dụng (Root Logger)
DEFAULT_ROOT_LEVEL = logging.DEBUG

# Cấu hình mức log cho từng module cụ thể
# Định nghĩa ở đây sẽ ghi đè cấu hình mặc định phía trên.
LOGGER_LEVELS = {
    # PyQt6 uiparser in rất nhiều thông tin debug khi đọc file .ui -> Đặt WARNING để tắt
    "PyQt6.uic.uiparser": logging.WARNING,
    "PyQt6": logging.WARNING,
    
    # Matplotlib in rất nhiều thông tin debug font chữ -> Đặt WARNING để tắt
    "matplotlib": logging.WARNING,
    
    # Bạn có thể điều chỉnh mức log của các service/model nội bộ của app tại đây:
    # "services.serial_service": logging.INFO,
    # "services.dongle_detect_service": logging.INFO,
    # "models.device_model": logging.DEBUG,
}


def setup_logging():
    """Khởi tạo và cấu hình hệ thống ghi log của ứng dụng."""
    # 1. Cấu hình cơ bản cho Root Logger
    logging.basicConfig(
        level=DEFAULT_ROOT_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 2. Áp dụng mức cấu hình tùy chỉnh cho từng Logger
    for logger_name, level in LOGGER_LEVELS.items():
        logging.getLogger(logger_name).setLevel(level)
