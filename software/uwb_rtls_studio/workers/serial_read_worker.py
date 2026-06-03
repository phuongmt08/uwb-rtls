"""
===============================================================================
  UWB RTLS Studio — Serial Read Worker
===============================================================================
  File        : workers/serial_read_worker.py
  Description : (DEPRECATED — merged into SerialService._read_loop)
                SerialService đã tích hợp reader thread trực tiếp.
                File này giữ lại cho backward compatibility.
===============================================================================
"""
# Reader thread logic đã được tích hợp trong SerialService._read_loop().
# Không cần file worker riêng vì SerialService đã dùng threading.Thread
# nội bộ, emit signal trực tiếp → đơn giản hơn, ít indirection.
