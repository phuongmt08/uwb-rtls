# MCU UKF Streaming Workflow Plan

## Goal

Add a second, low-rate stream for UKF/fusion data from MCU to the app over BLE, without changing the existing vehicle output path.

Current vehicle path must stay intact:

```c
bsp_io_uart_send_fusion_data(...)
```

That path is the priority output for the vehicle. The BLE/app stream is a separate telemetry path and may run at a lower rate.

## Existing Flow Checked

### MCU transport

- MCU network stack is initialized in `firmware/uwb/Core/Src/main.c`.
- `serial_init()` registers:
  - `STREAM_SERIAL_RX/TX` through `debug_serial`
  - `STREAM_BLE_RX/TX` through `ble_bridge`
- `network_core_init(&g_network_core, protobuf_PACKET_ADDR_MCU, ...)` sets MCU as local address.
- `network_cmd_init(&g_network_core)` registers the packet command handler.
- `network_entry()` in `firmware/uwb/Core/Src/freertos.c` runs:

```c
network_core_process(&g_network_core);
network_cmd_process();
osDelay(5);
```

### MCU BLE route

`firmware/common/network/network_core.c` routes destination addresses:

- `PACKET_ADDR_MCU` -> `STREAM_SERIAL_TX`
- `PACKET_ADDR_CENTRAL`, `PACKET_ADDR_PERIPHERAL`, `PACKET_ADDR_HOST` -> `STREAM_BLE_TX`

So MCU can send an app packet with:

```c
network_core_send_packet(&g_network_core, protobuf_PACKET_ADDR_HOST, &pkt);
```

This goes to USART2/HDLC through `firmware/common/serial/ble_bridge.c`, then to the nRF bridge.

### BLE session activity

`firmware/common/network/network_cmd.c` sets:

- `serial_connection_active = true` when `device_information_get` comes from `PACKET_ADDR_DEBUG`
- `ble_connection_active = true` when `device_information_get` comes from `PACKET_ADDR_HOST`
- `ble_connection_active = false` on `end_session` from host in default case

For app streaming, the app should first send `device_information_get` as `src=PACKET_ADDR_HOST` and `dst=PACKET_ADDR_MCU`. This marks BLE host active on MCU.

### Nordic bridge

`firmware/ble_firmware/ble_common/ble_bridge/bb_router.c` decodes only the protobuf header enough to route:

- On peripheral firmware:
  - packets for `PACKET_ADDR_MCU` or `BCAST` are forwarded to serial/MCU
  - other destinations, including `PACKET_ADDR_HOST`, are forwarded to BLE
- On central firmware:
  - packets for `PACKET_ADDR_HOST`, `DEBUG`, or `BCAST` are forwarded to serial/host
  - others are forwarded to BLE

This means the first MCU implementation can rely on raw forwarding. Nordic does not need a command handler for the new UKF packet in phase 1, as long as the packet is addressed to `PACKET_ADDR_HOST`.

## Proposed Protobuf

Add a passive telemetry message near the Sensor Fusion section in `protocol/protos/protocol.proto`.

Recommended name:

```proto
message ukf_stream_t {
    float ukf_x_m        = 1;
    float ukf_y_m        = 2;
    float ukf_yaw_deg    = 3;
    float tril_x_m       = 4;
    float tril_y_m       = 5;
    float yaw_deg        = 6;
    uint32 error_count   = 7;
    uint32 timestamp_ms  = 8;
}
```

Add it to `packet_t.oneof params` using an unused tag:

```proto
ukf_stream_t ukf_stream = 67;
```

Reason: tag `66` is already `factory_otp_write`, and `69/70` are IMU fields. Tag `67` is currently free.

## Build Protobuf

Preferred:

```powershell
cd protocol
make
```

If `make` is unavailable on Windows:

```powershell
cd protocol
py -3 nanopb/generator/nanopb_generator.py -I protos -D protos protos/protocol.proto
py -3 -m grpc_tools.protoc -I protos --python_out=../software/common protos/protocol.proto
```

Generated files expected:

- `protocol/protos/protocol.pb.c`
- `protocol/protos/protocol.pb.h`
- `software/common/protocol_pb2.py`

## MCU Implementation Plan

Implement MCU first. Do not modify Nordic behavior yet.

### 1. Add sender helper in common network

Files:

- `firmware/common/network/network_cmd.h`
- `firmware/common/network/network_cmd.c`

Add a function:

```c
bool network_send_ukf_stream(network_core_t *stream,
                             uint8_t dst,
                             const protobuf_ukf_stream_t *data);
```

Implementation pattern:

```c
protobuf_packet_t pkt;
memset(&pkt, 0, sizeof(pkt));
pkt.which_params = protobuf_packet_t_ukf_stream_tag;
pkt.params.ukf_stream = *data;
return network_core_send_packet(stream, dst, &pkt);
```

Use `dst = protobuf_PACKET_ADDR_HOST`.

### 2. Disable ACK for stream packets

File:

- `firmware/common/network/network_core.c`

Add the new packet tag to `network_core_skip_ack_tb`:

```c
protobuf_packet_t_ukf_stream_tag,
```

Reason: UKF stream is periodic telemetry. ACKing every frame can overload BLE and create traffic in the opposite direction.

### 3. Add lightweight app streaming gate

Preferred location:

- `firmware/uwb/Core/Src/freertos.c`
- inside `sensor_fusion_entry()`
- after the existing latest fusion data is available
- near, but not replacing, this line:

```c
bsp_io_uart_send_fusion_data(ukf_data.px, ukf_data.py, ukf_yaw, tril_x, tril_y, yaw, err_count);
```

Do not edit or remove the vehicle send call.

Add a separate throttled call after it:

```c
static uint32_t s_last_ukf_ble_stream_ms = 0U;
uint32_t now_ms = HAL_GetTick();

if ((uint32_t)(now_ms - s_last_ukf_ble_stream_ms) >= UKF_BLE_STREAM_PERIOD_MS) {
    s_last_ukf_ble_stream_ms = now_ms;
    // build protobuf_ukf_stream_t and send to PACKET_ADDR_HOST
}
```

Recommended rate:

- vehicle output: unchanged
- BLE/app output: 5-10 Hz initially
- suggested default: `UKF_BLE_STREAM_PERIOD_MS = 200U` for 5 Hz

### 4. Guard conditions

The BLE stream should only send when:

- sensor fusion is enabled
- UKF has produced/latest fusion data
- host/app is considered active, if a public helper is added
- packet size remains below Nordic `MAX_PROTOBUF_PAYLOAD_SIZE = 256`

Potential helper to expose host state:

```c
bool network_cmd_is_ble_host_active(void);
```

Alternative for phase 1: send to `PACKET_ADDR_HOST` unconditionally at 5 Hz. `network_core_send_packet()` will route to BLE TX; if Nordic/app is not connected the Nordic layer may drop/fail forward. The cleaner version is to check BLE host state before sending.

### 5. Keep responsibilities separated

Do not put streaming into `bsp_io_uart_send_fusion_data()`.

Reason:

- that function is vehicle-facing output
- app stream needs protobuf, routing, BLE throttling, and possibly session state
- mixing the two would make vehicle timing depend on app/BLE behavior

## Streaming Point Decision

Do not stream directly inside `sys_sensor_fusion_predict()` by default.

Predict can run without new UWB measurement and may produce high-rate predicted states. That is useful for internal state propagation, but it is not the best app telemetry boundary.

Recommended stream point:

- after the fusion loop drains ranging messages
- after `app_tag_set_latest_fusion_data(...)`
- at the existing final output block where `tril_x`, `tril_y`, `ukf_yaw`, `yaw`, and `err_count` are already collected

This gives the app a coherent snapshot and keeps BLE rate independent from the UKF predict/update cadence.

## Nordic Implementation Plan

Do this after MCU streaming works over the existing forward path.

### Phase 1: no Nordic command handler

No new handler is required if:

- MCU sends `ukf_stream` to `PACKET_ADDR_HOST`
- `bb_router` forwards unknown/non-local packets by destination
- app receives raw protobuf via the existing BLE/NUS path

### Phase 2: optional Nordic improvements

Only add Nordic changes if testing shows drops or app routing needs explicit handling:

- Add debug log filtering so high-rate `ukf_stream` does not flood RTT logs.
- Add counters for forwarded stream packets.
- Add queue/backpressure around `ble_nus_data_send` if BLE returns busy.
- Confirm both peripheral and central bridge paths forward the new packet without trying to handle it as a local command.

## VVTesting Plan

Files:

- `software/common/commands.py`
- `software/vv_testings/test_command_matrix.py`
- add a new script in `software/vv_testings/`, for example `test_ukf_stream.py`

### 1. Regenerated Python protobuf

After running protocol build, confirm:

```python
pb.packet_t().WhichOneof("params")
```

can see the new `ukf_stream` field once populated/decoded.

### 2. CommandFactory support

Add a passive builder in `software/common/commands.py` only if tests need to construct sample packets:

```python
def ukf_stream(self, src: int, dst: int, seq: int) -> pb.packet_t:
    pkt = self._base(src, dst, seq)
    pkt.ukf_stream.timestamp_ms = 0
    return pkt
```

This is mainly useful for encode/decode/unit testing. The host normally receives this packet passively from MCU.

### 3. Command matrix

Add `ukf_stream` to `PASSIVE_REQUESTS` in `software/vv_testings/test_command_matrix.py`.

Reason: host should not send `ukf_stream` as a command request to MCU. It is telemetry from MCU to app.

### 4. Integration test script

Create `software/vv_testings/test_ukf_stream.py`.

Expected test flow:

1. Open `VvTestSession`.
2. Send `device_information_get`:
   - `src=PACKET_ADDR_HOST`
   - `dst=PACKET_ADDR_MCU`
3. Wait for `device_information_resp`.
4. Listen for `ukf_stream` packets for 3-5 seconds.
5. Validate:
   - at least one `ukf_stream` packet received when fusion is running
   - `ukf_x_m`, `ukf_y_m`, `timestamp_ms` decode correctly
   - timestamps are monotonic or non-decreasing
   - observed rate is approximately 5-10 Hz, not vehicle-rate
   - no ACK is expected for `ukf_stream`

Suggested CLI:

```powershell
cd software/vv_testings
python test_ukf_stream.py --port COMx --seconds 5
```

### 5. Negative test

Send `end_session` from `PACKET_ADDR_HOST`, then confirm the stream stops or is ignored depending on chosen MCU guard logic.

If phase 1 sends unconditionally, document that `end_session` is not yet a stream stop. Cleaner behavior is to gate on `ble_connection_active` and stop after `end_session`.

## Recommended Final Architecture

Use two independent output paths:

1. Vehicle path, high priority:
   - existing `bsp_io_uart_send_fusion_data(...)`
   - no changes
   - rate remains tied to current fusion output behavior

2. App path, low-rate telemetry:
   - `protobuf_ukf_stream_t`
   - `network_send_ukf_stream(&g_network_core, PACKET_ADDR_HOST, &msg)`
   - `network_core` routes to `STREAM_BLE_TX`
   - `ble_bridge` HDLC frames it over MCU USART2 to Nordic
   - Nordic `bb_router` forwards to BLE/app
   - VVTesting receives and decodes as passive telemetry

This keeps vehicle control deterministic and lets app streaming evolve independently.

## Implementation Order

1. Add protobuf `ukf_stream_t`.
2. Regenerate C/Python protobuf.
3. Add MCU sender helper in `firmware/common/network`.
4. Add `ukf_stream` to MCU no-ACK table.
5. Add throttled MCU stream call in `sensor_fusion_entry()` without touching vehicle send.
6. Build MCU firmware.
7. Add VVTesting decode/listen test.
8. Validate MCU -> existing Nordic forward path -> app/test.
9. Only then adjust Nordic if forwarding/backpressure/logging needs it.

# BLE Device Log Streaming Plan

## Mục tiêu

Triển khai truyền log hiện tại của firmware UWB qua BLE/app bằng đường protobuf `log_data/log_clear` đã có sẵn, ưu tiên tái dùng `sys_logger`, `network_core`, `network_cmd` và `sys_ble_peripheral` thay vì tạo một format log riêng.

## Logic log hiện tại

### 1. API ghi log

Các module firmware ghi log qua macro trong `firmware/uwb/sys/sys_logger.h`:

```c
RLOG_I(obj, fmt, ...)
RLOG_D(obj, fmt, ...)
RLOG_W(obj, fmt, ...)
RLOG_E(obj, err, fmt, ...)
```

Tất cả macro đi vào:

```c
sys_logger_write_record(log_type, obj_code, format, ...)
```

### 2. Format record trong RAM

`sys_logger_write_record()` format message bằng `vsnprintf()`, giới hạn message tối đa `SYS_LOGGER_MAX_MSG_LEN = 180`.

Raw record có header 9 byte:

```text
[LOG_TYPE 1B][OBJ_CODE 1B][TIMESTAMP 6B][DATA_LEN 1B][MESSAGE DATA_LEN B]
```

Trước khi đưa vào ring buffer, record được bọc thêm length 2 byte và pad 4 byte:

```text
[LEN_LO 1B][LEN_HI 1B][RAW_RECORD LEN B][PAD 0..3B]
```

Điểm quan trọng: `log_data_t.data` gửi lên host đang chứa nguyên các entry đã framed này, tức host cần parse theo `[len(2)][record][pad]`.

### 3. Buffer và bảo toàn qua reset

Logger dùng `g_logger` đặt trong linker section `.shared_log`:

```c
static sys_logger_t g_logger __attribute__((section(".shared_log"), aligned(4), used));
```

Vùng này là `NOLOAD`, nên có thể giữ log qua software reset nếu RAM không bị mất nguồn. Metadata gồm `magic`, `format`, `head`, `tail`; nếu không hợp lệ thì reset buffer.

### 4. Đồng bộ task

Khi ghi log, code lấy `g_logger_mutexHandle` nếu mutex đã được tạo. Điều này bảo vệ `vsnprintf()` và cập nhật ring buffer khi nhiều task cùng gọi `RLOG_*`.

`g_logger_semHandle` vẫn được tạo trong FreeRTOS nhưng hiện không còn được release khi có log mới. Comment trong `sys_logger.c` nói rõ USB CDC drain đã bị bỏ, log sẽ đi qua flash khi bật `ENABLE_FLASH_LOG`.

### 5. RAM-only vs flash log

Build hiện tại trong `firmware/uwb/Core/Inc/config.h`:

```c
#define HAVE_FLASH_STORAGE
#undef  ENABLE_FLASH_LOG
```

Vì `ENABLE_FLASH_LOG` đang tắt, logger hiện chạy theo nhánh RAM-only:

```c
sys_logger_ram_peek_packet(out, max_len)
sys_logger_ram_consume(len)
```

Nếu bật `ENABLE_FLASH_LOG`, RAM log sẽ được persist xuống flash bằng:

```c
sys_logger_flash_persist()
sys_logger_flash_peek_packet(out, max_len)
sys_logger_flash_consume(length)
```

Flash path đã có metadata read/write cursor, record-aligned read, và ACK/consume để tránh host cắt giữa record.

### 6. Task hiện tại

Trong `freertos.c`:

- `logger_entry()` mỗi 30 giây ghi memory stats và CPU stats bằng `RLOG_D`.
- `flash_storage_entry()` mỗi 2 giây gọi `sys_logger_flash_persist()` nhưng chỉ khi `ENABLE_FLASH_LOG` bật.
- `network_entry()` chạy `network_core_process()` và `network_cmd_process()`.

`sys_logger_task()` có flush flash mỗi 50 ms nhưng hiện không thấy được gọi thường xuyên trong task loop chính.

### 7. Đường protobuf log đã có

Protocol đã có:

```proto
message log_data_t {
  log_type_t type = 1;
  bytes data = 2;
}

message log_clear_t {
  log_type_t type = 1;
  uint32 offset = 2;
  uint32 length = 3;
}
```

`network_cmd.c` đã có handler:

- Host gửi command lấy log, handler bật `s_log_stream_enabled`.
- `network_send_log()` peek log packet từ RAM hoặc flash.
- Gửi `protobuf_packet_t_log_data_tag` bằng `network_core_send_packet()`.
- Chờ ACK của packet bằng `network_core_wait_ack()`.
- Host gửi `log_clear`; firmware consume số byte tương ứng.
- `network_cmd_process()` tiếp tục gửi log khi stream enabled và host còn active.

Nói ngắn gọn: phần log-to-protobuf gần như đã có, việc còn lại là nối chắc đường BLE, init BLE peripheral đúng chỗ, gate host session, và test ACK/clear.

## BLE hiện có

### MCU UART/BLE

`USART2` được cấu hình là BLE UART:

```c
huart2.Init.BaudRate = 460800;
```

Pin:

```c
PA2 = UART2_BLE_TX
PA3 = UART2_BLE_RX
```

### Network route

`network_core` route theo destination:

- `PACKET_ADDR_MCU` -> `STREAM_SERIAL_TX`
- `PACKET_ADDR_CENTRAL`, `PACKET_ADDR_PERIPHERAL`, `PACKET_ADDR_HOST` -> `STREAM_BLE_TX`

Do đó packet log gửi tới host/app nên dùng `dst = protobuf_PACKET_ADDR_HOST`.

### BLE peripheral module

`firmware/common/ble/sys_ble_peripheral.c` đã có state manager cho BLE peripheral. File này cũng có `test_send_log_data_to_host()` dựng thử `protobuf_packet_t_log_data_tag`, cho thấy hướng thiết kế ban đầu cũng là gửi log qua protobuf/network layer.

## Plan triển khai log bằng BLE

### Phase 1: Kiểm tra init hiện có

1. Xác nhận `main.c` đã gọi đầy đủ:
   - `serial_init()` để đăng ký `STREAM_BLE_RX/TX` qua `ble_bridge`.
   - `network_core_init(&g_network_core, protobuf_PACKET_ADDR_MCU, ...)`.
   - `network_cmd_init(&g_network_core)`.
   - Nếu dùng BLE peripheral mode, gọi `sys_ble_peripheral_init(&g_network_core)`, `sys_ble_peripheral_set_config()`, `sys_ble_peripheral_enable(true)`.
2. Xác nhận USART2 RX path đẩy dữ liệu vào `ble_bridge_rx_push()` qua DMA/IRQ.
3. Xác nhận `network_entry()` delay đủ thấp. Hiện đang `osDelay(2)`, phù hợp để pump log stream.

### Phase 2: Chọn storage mode

Khuyến nghị triển khai theo 2 mức:

1. MVP RAM-only:
   - Giữ `#undef ENABLE_FLASH_LOG`.
   - Dùng sẵn `sys_logger_ram_peek_packet()` và `sys_logger_ram_consume()`.
   - Log mất khi mất nguồn hoặc buffer bị overwrite, nhưng đủ để chứng minh BLE log stream.
2. Bền vững flash-backed:
   - Bật `#define ENABLE_FLASH_LOG`.
   - Đảm bảo `sys_flash_storage_init()` chạy trước khi logger cần persist.
   - Gọi `sys_logger_flash_persist()` định kỳ đủ nhanh, nên chuyển từ 2 giây xuống 50-200 ms hoặc gọi `sys_logger_task()` trong `flash_storage_entry()`.
   - Test erase/swap sector và read cursor qua reset.

### Phase 3: Hoàn thiện command log flow

1. Chuẩn hóa command phía host/app:
   - Host gửi packet request log tới `dst=PACKET_ADDR_MCU`, `src=PACKET_ADDR_HOST`.
   - MCU bật `s_log_stream_enabled` và trả `log_data`.
   - Host ACK packet `log_data`.
   - Host parse payload, rồi gửi `log_clear.length = số byte đã xử lý`.
2. Sửa/kiểm tra `network_cmd_log_clear()`:
   - Với flash path, cân nhắc kiểm `offset == sys_logger_flash_read_pos()` trước khi consume để tránh clear lệch.
   - Với RAM-only, `offset` có thể bỏ qua nhưng nên document rõ.
3. Kiểm tra ACK behavior:
   - Không thêm `log_data` vào `network_core_skip_ack_tb`, vì log cần ACK để retry và tránh mất record.
   - Nếu host không ACK, `log_tracker_callback` phải cho phép resend.

### Phase 4: BLE transport hardening

1. Thêm hoặc kiểm tra TX busy/backpressure ở `ble_bridge_write()`:
   - Không block lâu trong task network.
   - Khi BLE/Nordic UART busy, trả fail để `network_cmd` retry.
2. Giới hạn kích thước `log_data.data`:
   - Nanopb hiện là `bytes[192]`.
   - `network_send_log()` đã dùng `sizeof(packet.params.log_data.data.bytes)`, giữ nguyên giới hạn này.
3. Throttle log stream:
   - Nếu pending log nhiều, vẫn gửi từng packet record-aligned.
   - Có thể thêm minimum interval 10-20 ms giữa các `log_data` để tránh chiếm BLE khi ranging/fusion chạy.

### Phase 5: Host/app parser

Host cần parse `log_data.data` như sau:

1. Đọc `len = data[i] | (data[i + 1] << 8)`.
2. Raw record bắt đầu tại `i + 2`.
3. Parse:
   - `log_type = record[0]`
   - `obj_code = record[1]`
   - `timestamp_48 = record[2..7]`
   - `msg_len = record[8]`
   - `message = record[9:9 + msg_len]`
4. Bỏ qua padding:
   - `entry_len = 2 + len`
   - `padded_len = (entry_len + 3) & ~3`
5. Sau khi xử lý hết payload, gửi `log_clear.length = tổng padded_len đã xử lý`.

### Phase 6: Test checklist

1. Boot device, app gửi `device_information_get` từ `PACKET_ADDR_HOST` để đánh dấu BLE host active.
2. App gửi request log.
3. Tạo log thử bằng:
   - boot log `System Starting...`
   - click/double click IO
   - memory stats sau 30 giây
4. Xác nhận app nhận `log_data`.
5. Xác nhận payload decode được message text.
6. Gửi `log_clear.length`.
7. Xác nhận packet tiếp theo không lặp lại đoạn đã clear.
8. Test mất ACK:
   - Không ACK `log_data`.
   - MCU phải resend hoặc giữ dữ liệu, không consume.
9. Nếu bật flash:
   - Reset MCU sau khi có log.
   - Request log lại và xác nhận log chưa ACK vẫn còn.

## Thứ tự implementation đề xuất

1. Kiểm tra/init `sys_ble_peripheral` trong `main.c` nếu chưa có.
2. Kiểm tra `serial_init()` và `ble_bridge` cho USART2 RX/TX.
3. Dùng RAM-only để test nhanh `log_data/log_clear` qua BLE.
4. Viết script/app parser cho framed log payload.
5. Test ACK, retry, clear.
6. Sau khi BLE path ổn, bật `ENABLE_FLASH_LOG`.
7. Giảm chu kỳ persist flash hoặc gọi `sys_logger_task()` định kỳ.
8. Test reset/power cycle và sector swap.
9. Chốt tài liệu host protocol: request log, parse, ACK, clear.

## Rủi ro cần chú ý

- `ENABLE_FLASH_LOG` đang tắt, nên kỳ vọng "log lưu flash" hiện chưa đúng với build này.
- `sys_logger_task()` có logic persist 50 ms nhưng chưa được gọi trong loop hiện tại.
- `log_data.data` tối đa 192 byte, trong khi một record tối đa có thể gần `2 + 189 + pad`; cần giữ record-aligned read như hiện tại.
- Log nhiều trong ranging path có thể làm trễ hệ thống nếu BLE/log flush bị block.
- `log_clear.offset` đang không được dùng để validate trong `network_cmd_log_clear()`, dễ clear nhầm nếu host gửi lệch.
