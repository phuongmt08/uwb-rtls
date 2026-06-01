"""
===============================================================================
  UWB RTLS Studio — Ranging Model
===============================================================================
  File        : models/ranging_model.py
  Description : Data model cho ranging results và position data.
                Lưu trữ tọa độ tag, khoảng cách tới anchors,
                và history để vẽ trajectory trên map.

  MVVM Role   : MODEL — chỉ chứa data + buffer.

  Dữ liệu được quản lý:
    - Position hiện tại (x, y, z) của tag
    - Khoảng cách tới từng anchor
    - RMS error (đánh giá chất lượng position)
    - History buffer (N samples gần nhất cho trajectory)
    - Anchor layout (vị trí cố định của các anchor trên sơ đồ)
    - Ranging statistics (success rate, timeout count, ...)

  Được sử dụng bởi:
    - LiveTrackingViewModel → cập nhật position realtime
    - LiveTrackingTabView   → vẽ position lên canvas/map
    - HistoryTabView        → xem lại trajectory
    - StatusBarView         → hiển thị RMS error

  Protocol Messages liên quan:
    - ranging_start_t        (tag=16)  → Bắt đầu ranging
    - ranging_stop_t         (tag=17)  → Dừng ranging
    - ranging_result_t       (tag=18)  → Position + anchor distances
    - ranging_status_get_t   (tag=19)  → Lấy ranging statistics
    - ranging_status_resp_t  (tag=20)  → Response statistics
    - anchor_layout_get_t    (tag=43)  → Lấy vị trí anchors
    - anchor_layout_set_t    (tag=44)  → Set vị trí anchors
    - anchor_layout_resp_t   (tag=45)  → Response vị trí anchors

  Data fields:
    @dataclass
    class PositionSample:
        x_m: float                  # Tọa độ X (meter)
        y_m: float                  # Tọa độ Y (meter)
        z_m: float                  # Tọa độ Z (meter)
        rms_error_m: float          # RMS error (meter)
        timestamp_ms: int           # Timestamp từ device
        anchor_distances: list      # [{anchor_id, distance_mm, fp_amp}, ...]
        received_at: float          # time.time() lúc nhận

    @dataclass
    class AnchorPosition:
        anchor_id: int
        x_m: float
        y_m: float
        z_m: float

    @dataclass
    class RangingState:
        is_ranging: bool                        # Đang ranging hay không
        current_position: PositionSample | None # Sample mới nhất
        position_history: list[PositionSample]  # Buffer N samples
        anchor_layout: list[AnchorPosition]     # Vị trí các anchor
        stats: RangingStats                     # Thống kê
        max_history_size: int = 1000            # Giới hạn buffer

    @dataclass
    class RangingStats:
        total_count: int
        success_count: int
        failed_count: int
        timeout_count: int
        last_rms_error_m: float
        last_avg_rssi_dbm: int
        update_rate_hz: float               # Computed từ timestamps
===============================================================================
"""
pass
