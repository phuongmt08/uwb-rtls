"""
===============================================================================
  UWB RTLS Studio — Protocol Service
===============================================================================
  File        : services/protocol_service.py
  Description : Xử lý encode/decode protobuf packets qua HDLC framing.
                Wraps common/transport.py (VvProtocol, HdlcCodec)
                và common/commands.py (CommandFactory).

  MVVM Role   : SERVICE — protocol layer.

  Chức năng:
    - Nhận raw bytes từ SerialService → decode ra packet_t
    - Đóng vai trò TRUNG TÂM ĐIỀU PHỐI (Router/Dispatcher) cho toàn app.
    - Phân loại gói tin theo WhichOneof("params") và GỬI ĐÚNG TAB:
        + Nếu là ranging_result_t -> Chỉ gửi cho LiveTrackingViewModel
        + Nếu là log_data_t       -> Chỉ gửi cho LogViewModel
        + Nếu là sys_config_*     -> Chỉ gửi cho ConfigViewModel
      -> Việc này tránh tình trạng spam dữ liệu sang các Tab không liên quan,
         giải phóng RAM và chống đơ app khi 1 Dongle gánh nhiều tính năng.
    - Build command packets (sử dụng CommandFactory)
    - Encode packet_t → HDLC frame → gửi qua SerialService
    - Sequence number management
    - ACK/NACK handling

  Architecture:
    SerialService                ProtocolService                ViewModels
    ┌──────────┐   raw bytes   ┌──────────────┐   packet_t    ┌──────────┐
    │  RX data ├──────────────►│ HDLC decode  ├──────────────►│ dispatch │
    └──────────┘               │ Proto decode │               └──────────┘
    ┌──────────┐   HDLC frame  │              │   packet_t    ┌──────────┐
    │  TX data │◄──────────────┤ HDLC encode  │◄──────────────┤ commands │
    └──────────┘               │ Proto encode │               └──────────┘
                               └──────────────┘

  Signals:
    - packet_received(param_name: str, packet: packet_t)
    - ack_received(seq: int, response: int)
    - decode_error(msg: str)

  Sử dụng common modules:
    - common.transport.VvProtocol   → HDLC + protobuf
    - common.commands.CommandFactory → build packets
===============================================================================
"""
pass
