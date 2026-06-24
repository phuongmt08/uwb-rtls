# BLE Log Pipeline: Host -> Dongle -> FS-BT630 -> MCU

File này mô tả chu trình lấy log BLE từ host xuống MCU và chiều log từ MCU trả về host.

## Thành phần

- Host: script `software/vv_testings/gateway_test/test_ble_log.py`.
- Dongle: nhận command từ host PC, bridge command sang BLE central.
- FS-BT630/peripheral: nhận BLE NUS RX từ dongle/central, forward qua UART xuống MCU; nhận UART từ MCU rồi forward ngược qua BLE.
- MCU UWB: xử lý protobuf command trong `firmware/common/network/network_cmd.c`, đọc log từ RAM/FLASH logger và gửi `log_data`.

## Packet chính

- `log_data` tag 43:
  - Host gửi `log_data` với `type=LOG_TYPE_DEVICE_LOG` để yêu cầu MCU trả log.
  - MCU cũng dùng tag này để trả payload log về host.
- `log_clear` tag 44:
  - Host gửi sau khi đã nhận và xử lý xong một chunk `log_data`.
  - MCU dùng `length` để consume log khỏi RAM/FLASH.
- `ack`:
  - ACK transport cho một packet cụ thể, dựa trên `ack_seq`.
  - ACK không đồng nghĩa với consume log. ACK chỉ nói packet `log_data` đã tới host.

## Chu trình đúng cho một lần lấy log

1. Host gửi request:
   - Host tạo packet `log_data`.
   - `hdr.src = HOST`, `hdr.dst = MCU`, `hdr.seq = next_seq`.
   - `log_data.type = LOG_TYPE_DEVICE_LOG`.

2. Packet đi qua đường truyền:
   - Host PC -> dongle.
   - Dongle -> BLE central/peripheral link.
   - FS-BT630 nhận BLE NUS RX.
   - FS-BT630 forward frame qua UART xuống MCU.

3. MCU nhận `log_data_get`:
   - Handler: `network_cmd_log_data_get()`.
   - MCU đánh dấu log stream active.
   - `s_log_stream_dst = pkt->hdr.addr.src`.
   - Nếu đang `waiting_ack`, code hiện tại reset tracker cũ để tránh kẹt.
   - MCU gọi `network_send_log(dst, max_payload)`.

4. MCU gửi log:
   - Nếu `s_log_tracker.waiting_ack == true`: MCU không gửi chunk mới, vì chunk trước chưa được ACK.
   - Nếu `s_log_tracker.waiting_clear == true`: MCU chờ host gửi `log_clear`; chưa clear thì không gửi chunk mới trong khoảng `WAIT_TIME_TO_CLEAR_LOG_MS`.
   - Nếu logger không có data: return.
   - Nếu có data:
     - MCU peek một chunk từ RAM/FLASH logger.
     - Gửi packet `log_data` về host.
     - Set:
       - `waiting_ack = true`
       - `waiting_clear = false`
       - `log_len = read_len`
       - `sent_tick = now`
     - Đăng ký ACK tracker bằng `network_core_wait_ack(seq, ..., log_tracker_callback, ...)`.

5. MCU -> host:
   - MCU -> UART -> FS-BT630.
   - FS-BT630 forward qua BLE.
   - Dongle nhận BLE.
   - Host script nhận packet `log_data`.

6. Host xử lý `log_data`:
   - Host parse payload log và in ra terminal.
   - Host gửi `ack` cho packet `log_data` vừa nhận:
     - `ack.ack_seq = seq` của packet `log_data` từ MCU.
     - `ack.response = ACK`.

7. MCU nhận ACK:
   - ACK tracker gọi `log_tracker_callback()`.
   - Nếu ACK state là `FOUND` và `log_len > 0`:
     - `waiting_ack = false`
     - `waiting_clear = true`
     - `sent_tick = now`
     - `tracker_id = -1`
   - Lúc này MCU biết host đã nhận chunk, nhưng vẫn chưa được phép consume log nếu chưa có `log_clear`.

8. Host gửi `log_clear`:
   - Sau khi host đã xử lý xong payload, host nên gửi `log_clear`.
   - `log_clear.type = LOG_TYPE_DEVICE_LOG`.
   - `log_clear.offset = 0`.
   - `log_clear.length = số byte payload log_data đã xử lý`.

9. MCU nhận `log_clear`:
   - Handler: `network_cmd_log_clear()`.
   - Với RAM logger: gọi `sys_logger_ram_consume(length)`.
   - Với FLASH logger: gọi `sys_logger_flash_consume(length)`.
   - Nếu `length == 0` hoặc `length >= s_log_tracker.log_len`:
     - `waiting_ack = false`
     - `waiting_clear = false`
     - `log_len = 0`
     - `tracker_id = -1`
   - Nếu clear một phần:
     - `log_len -= length`
     - tiếp tục chờ clear phần còn lại.

10. Lặp chu trình:
    - Host tiếp tục poll `log_data_get`.
    - MCU chỉ gửi chunk mới khi không còn kẹt `waiting_ack` hoặc `waiting_clear`.

## ACK và clear có gửi cùng lúc không?

Không nên hiểu là một gói duy nhất.

Host cần gửi hai packet khác nhau:

1. `ack`: xác nhận đã nhận packet `log_data` theo `seq`.
2. `log_clear`: xác nhận đã xử lý xong payload và cho phép MCU consume log khỏi RAM/FLASH.

Hai packet này có thể được gửi rất sát nhau về mặt thời gian, nhưng về logic là hai bước khác nhau. MCU cần ACK trước để clear `waiting_ack`, sau đó cần `log_clear` để clear `waiting_clear` và consume log.

## Flow hiện tại trong `test_ble_log.py`

Script hiện tại:

- Có gửi `log_data_get` theo chu kỳ.
- Có gửi `ack` sau khi nhận `log_data`.
- Có `log_clear_all` lúc bootstrap nếu bật `--clear-first`.
- Chưa thấy gửi `log_clear(length=len(payload))` sau mỗi chunk `log_data`.

Vì vậy nếu MCU đang dùng cơ chế `waiting_clear`, host chỉ ACK thôi là chưa đủ. MCU có thể chuyển từ `waiting_ack` sang `waiting_clear`, rồi đứng chờ clear; các lần `log_data_get` tiếp theo sẽ bị block bởi `waiting_clear` cho đến timeout hoặc đến khi nhận `log_clear`.

## Điểm nghẽn cần debug

- Host có gửi `log_data_get` đều không:
  - Xem `log_get_tx` trong stats host.
- Peri có nhận đủ BLE RX không:
  - Xem counter BLE RX và UART TX bên FS-BT630.
- MCU có nhận `log_data_get` không:
  - Watch counter debug ở `network_cmd_log_data_get()`.
- MCU có gửi được `log_data` không:
  - Watch send success/fail trong `network_send_log()`.
- MCU có kẹt `waiting_ack` không:
  - Nếu kẹt ở đây, host ACK không về hoặc ACK seq không match.
- MCU có kẹt `waiting_clear` không:
  - Nếu kẹt ở đây, host đã ACK nhưng chưa gửi `log_clear`.
- MCU có consume log không:
  - Watch `network_cmd_log_clear()`, `log_clear_bytes`, và pending bytes trong logger.

## Kết luận flow chuẩn

Flow chuẩn nên là:

```text
host: log_data_get
  -> dongle
  -> FS-BT630
  -> MCU

MCU: network_cmd_log_data_get()
MCU: network_send_log()
MCU: log_data(seq=N, data=chunk)
  -> FS-BT630
  -> dongle
  -> host

host: parse/in log chunk
host: ack(ack_seq=N)
  -> MCU

MCU: log_tracker_callback(ACK_FOUND)
MCU: waiting_ack=false, waiting_clear=true

host: log_clear(length=len(chunk))
  -> MCU

MCU: consume RAM/FLASH log
MCU: waiting_clear=false

host: poll log_data_get tiếp theo
```

