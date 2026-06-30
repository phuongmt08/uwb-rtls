# BLE RTLS Flow Draft

Tài liệu này là bản nháp để rà lại flow trước khi vẽ Mermaid.  
Mục tiêu là thay thế sơ đồ cũ trong ảnh bằng flow hiện tại theo code.

## Phạm vi

- `PC Host`
- `Gateway Central` trên nRF52 BLE Central
- `TAG`
- `ANCHOR 1..N`

## Luồng tổng quan hiện tại

### 1. Phase 1 - Scan and discovery

- `PC Host` gửi `ble_scan_start` đến `Gateway Central`.
- `Gateway Central` gọi `app_ble_central_scan_start(...)`.
- BLE Central bắt đầu scan.
- Mỗi thiết bị quảng bá phù hợp sẽ được:
  - ghi vào danh sách `known_device`
  - gửi `ble_scan_result` lên host ngay khi phát hiện
- Nếu là thiết bị quen thuộc thì cập nhật RSSI, tên, UUID, thời điểm thấy gần nhất.

### 2. Advertising status nền

- Khi đang ở trạng thái scan hoặc chờ kết nối, `Gateway Central` vẫn có thể phát `ble_adv_status` theo chu kỳ.
- Trong code hiện tại, status được đẩy qua `bb_cmd_notify_ble_status(...)`.
- `TAG` và `ANCHOR` không còn chỉ đứng yên theo flow cũ; phần trạng thái BLE được cập nhật động từ central/app.

### 3. Phase 2 - Gateway connects to TAG

- `PC Host` gửi `ble_connect` với MAC của `TAG`.
- `Gateway Central` gọi `app_ble_central_connect(...)`.
- Khi connect thành công:
  - BLE state chuyển sang `CONNECTED`
  - bắt đầu RSSI report
  - khởi động service discovery
  - scan được resume để vẫn theo dõi thiết bị khác
- Khi `TAG` vào connected state, dữ liệu phía UWB sẽ bắt đầu được cấu hình/trao đổi qua network commands.

### 4. Device info, time sync, and ack

- Sau khi có kết nối, host có thể gửi:
  - `device_information_get`
  - `time_sync_set`
- `Gateway Central` trả về `ack` hoặc các response tương ứng qua serial/network bridge.
- `device_information_get` hiện được map về response chứa:
  - serial number
  - device type
  - role
  - firmware version

### 5. Phase 3 - Data transfer and streaming

- Sau khi software connection ổn định, `TAG` bắt đầu ranging UWB.
- `freertos.c` giữ task `UwbRanging`, `SensorFusion`, `Network`, `IO`, `PM`, `Logger`.
- `TAG` nhận lệnh `ranging_start` / `ranging_stop` từ network layer.
- Khi ranging active:
  - `UwbRanging` xử lý ISR và đo khoảng cách
  - `SensorFusion` xử lý queue distance để suy ra vị trí
  - kết quả được stream ra host
- Dữ liệu chính trong giai đoạn này:
  - `tag_position`
  - `ranging_result`
  - log / telemetry liên quan pin, config, trạng thái hệ thống

### 6. Phase 4 - Disconnect, TAG returns to advertising

- `PC Host` hoặc hệ thống có thể gửi `ble_disconnect`.
- `Gateway Central` gọi `app_ble_central_disconnect()`.
- BLE state chuyển về `IDLE`.
- `TAG` quay lại advertising state.
- Ở trạng thái rảnh, các thiết bị vẫn có thể broadcast status nền theo chu kỳ.

## Điểm cần sửa so với ảnh cũ

- Flow cũ đang mô tả khá nặng theo kiểu "một số message cố định", còn code hiện tại tách rõ:
  - scan result
  - BLE status notification
  - connection management
  - UWB ranging / fusion
- `ble_scan_result` hiện được phát ngay khi phát hiện thiết bị, không chỉ đợi hết vòng scan.
- `ble_adv_status` hiện là status notification theo runtime state, không phải luồng tĩnh chỉ gắn với một phase.
- `Gateway Central` resume scan sau khi connect, nên flow scan và connect có thể tồn tại song song hơn ảnh cũ.
- `SensorFusion` là task riêng trong `freertos.c`, không còn là phần ẩn trong khối ranging.

## Gợi ý bố cục Mermaid sau khi xác nhận

- `sequenceDiagram`
- `participant PC`
- `participant GW as Gateway Central`
- `participant TAG`
- `participant A1 as ANCHOR 1`
- `participant AN as ANCHOR N`
- `rect` để chia 4 phase
- `loop` cho:
  - scan periodic
  - adv status periodic
  - streaming

## Ghi chú

- Đây mới là bản nháp chữ.
- Khi bạn xác nhận, mình sẽ chuyển nội dung này sang Mermaid Live Editor với theme sáng.
