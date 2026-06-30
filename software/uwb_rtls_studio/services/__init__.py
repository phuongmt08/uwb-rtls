"""
===============================================================================
  UWB RTLS Studio — Services Package
===============================================================================
  Package     : services/
  Description : Chứa các service classes xử lý I/O, communication,
                và business logic KHÔNG liên quan đến UI.
                Services được inject vào ViewModels.

  MVVM Role   : SERVICE LAYER (nằm giữa ViewModel và hardware).
                Services KHÔNG biết gì về View.

  Sub-modules:
    ├── serial_service.py        → Quản lý kết nối Serial/USB
    ├── protocol_service.py      → Encode/decode protobuf + HDLC
    ├── dongle_detect_service.py → Auto-detect dongle qua VID/PID
    └── data_export_service.py   → Manual export (CSV/JSON) cho ad-hoc needs
                                   (Session auto-save do SessionRepository xử lý)

  Lưu ý:
    - Session persistence (auto-save khi End Session) được xử lý bởi
      repository/session_repository.py, KHÔNG phải data_export_service.
    - data_export_service vẫn giữ cho manual export (user muốn export
      thêm dữ liệu ngoài session, VD: export chỉ positions riêng).
===============================================================================
"""
