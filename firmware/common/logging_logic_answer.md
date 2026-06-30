# Trả lời về logic ghi log của MCU

Tài liệu này tổng hợp lại logic log của MCU trong nhánh hiện tại của project, dựa trên các file:

- `firmware/uwb/sys/sys_logger.h`
- `firmware/uwb/sys/sys_logger.c`
- `firmware/common/network/network_cmd.c`
- `firmware/uwb/Core/Inc/config.h`
- `firmware/common/memorylayout.h`

## Kết luận ngắn

1. Trong build hiện tại, **log đang nằm trong RAM shared buffer**.
2. **Nhánh log ra FLASH có tồn tại trong code**, nhưng chỉ được bật khi `HAVE_FLASH_STORAGE` và `ENABLE_FLASH_LOG` cùng được define.
3. Ở config hiện tại, `firmware/uwb/Core/Inc/config.h` có:
   - `#define HAVE_FLASH_STORAGE`
   - `#undef ENABLE_FLASH_LOG`
4. Vì vậy, **luồng log FLASH không chạy trong build hiện tại**.
5. Khi host ACK, MCU sẽ **consume log** tương ứng, nghĩa là **trỏ tail/read pointer tiến lên để bỏ log đã gửi thành công**.
6. Nếu không có ACK, log **không bị consume**, nên nó vẫn nằm trong buffer cho tới khi:
   - host ACK,
   - log bị ghi đè bởi log mới khi buffer đầy,
   - hoặc có cơ chế timeout/resend từ `network_core`.

## 1. RAM và FLASH có cùng chứa log không?

Không phải theo kiểu “mỗi log vừa vào RAM vừa vào FLASH ngay lập tức” trong build hiện tại.

### Theo code:

- `sys_logger_write_record()` luôn ghi log vào **circular buffer trong RAM** (`g_logger.buffer`).
- Nếu build có bật `ENABLE_FLASH_LOG`, thì `sys_logger_task()` và `sys_logger_flash_persist()` mới đẩy log từ RAM sang FLASH theo chu kỳ.
- Nhưng trong config hiện tại, `ENABLE_FLASH_LOG` bị tắt, nên **không có bước persist log sang FLASH**.

### Vị trí RAM log:

Trong `firmware/uwb/sys/sys_logger.c`:

- `g_logger` nằm trong section `.shared_log`
- buffer này dùng shared noinit RAM
- kích thước được lấy từ `MEM_SHARED_LOG_RAM_SIZE` trong `firmware/common/memorylayout.h`

Tức là log giữ trong một vùng RAM chia sẻ, không phải ngay lập tức xuống FLASH.

## 2. Mỗi lần có log thì đẩy vào RAM trước rồi đẩy vào FLASH ngay lúc đó luôn hay sao?

Trong build hiện tại: **không**.

### Thực tế:

- `sys_logger_write_record()`:
  - format message
  - đóng gói record
  - ghi vào RAM circular buffer
- `sys_logger_task()`:
  - chỉ khi `HAVE_FLASH_STORAGE && ENABLE_FLASH_LOG`
  - mới gọi `sys_logger_flash_persist()`

Nghĩa là nếu bật flash log thì nó cũng là **flush theo task/chu kỳ**, không phải “mỗi log một phát ghi flash ngay lập tức”.

## 3. Khi host ACK gói log đó thì MCU consume nghĩa là gì?

`consume` ở đây nghĩa là:

- MCU coi log đó đã được host nhận xong
- sau đó **advance con trỏ đọc**
- mục đích là giải phóng chỗ của log đã gửi

### Với RAM path

Trong `network_cmd.c`:

- `network_send_log()` gửi packet log đi
- lưu `log_len`
- gọi `network_core_wait_ack(...)`
- khi ACK về, `log_tracker_callback()` gọi:
  - `sys_logger_ram_consume((uint16_t)tracker->log_len);`

Trong `sys_logger_ram_consume()`:

- chỉ gọi `logger_pop_data(len)`
- tức là **dịch tail của circular buffer lên**

=> Nói ngắn gọn: **đúng, consume ở RAM nghĩa là xóa phần log đã ACK khỏi buffer RAM**.

### Với FLASH path

Nếu build bật flash log thì callback sẽ gọi:

- `sys_logger_flash_consume(tracker->log_len);`

Lúc này consume nghĩa là:

- advance read cursor của log trong flash
- và cập nhật metadata để lần sau boot lên còn biết đã gửi tới đâu

## 4. Nếu MCU không nhận được ACK từ host thì sao?

Nếu không có ACK:

- log đang chờ sẽ **không bị consume**
- `s_log_tracker.waiting_ack` sẽ giữ trạng thái đang chờ
- `network_send_log()` sẽ không gửi gói tiếp theo trong lúc đó

### Hệ quả

Với nhánh RAM-only hiện tại:

- log mới vẫn có thể tiếp tục được sinh ra bởi các module khác
- chúng vẫn được `sys_logger_write_record()` đẩy vào circular buffer
- nếu buffer đầy, code sẽ:
  - cố drop log cũ nhất bằng `logger_drop_oldest_entry()`
  - nếu vẫn không đủ thì ghi thất bại

Điểm này nằm ngay trong `sys_logger_write_record()`:

- nếu không đủ chỗ, logger sẽ xóa log cũ để nhường chỗ log mới
- đây là cơ chế **overwrite oldest** chứ không phải giữ vô hạn

## 5. Vậy làm sao đủ không gian để lưu log mới khi đang wait ACK?

Có 2 tầng bảo vệ khác nhau:

### Tầng 1: Chờ ACK ở luồng gửi

`network_send_log()` có:

- `if (s_log_tracker.waiting_ack) return;`

=> MCU **không gửi thêm log mới ra host** trong lúc gói trước chưa ACK.

### Tầng 2: Buffer log sinh ra trong RAM

`sys_logger_write_record()` vẫn tiếp tục nhận log từ các module khác.

Nếu buffer gần đầy:

- nó sẽ xóa log cũ nhất để chừa chỗ cho log mới
- nếu không thể xóa hoặc log quá lớn, hàm trả `false`

### Nói cách khác

Không có cơ chế “đóng băng toàn bộ log cho tới khi ACK”.

Thiết kế hiện tại là:

- **gói đang gửi**: chờ ACK
- **log nội bộ mới**: vẫn có thể vào RAM
- **buffer đầy**: rớt log cũ nhất để giữ log mới

## 6. Có cơ chế lưu log mới trong lúc wait ACK như nào?

Có, nhưng là theo kiểu **buffer vòng**:

- log mới vẫn được ghi vào circular buffer
- nếu còn chỗ thì giữ lại
- nếu hết chỗ thì overwrite log cũ nhất

Điều này giúp hệ thống:

- không bị block hoàn toàn
- vẫn tiếp tục ghi nhận sự kiện mới

Nhưng đổi lại:

- log cũ chưa ACK có thể bị mất nếu buffer bị đầy và bị overwrite

## 7. Điểm cần lưu ý trong code hiện tại

### 7.1 Build hiện tại không bật FLASH log

File `firmware/uwb/Core/Inc/config.h` đang để:

- `#define HAVE_FLASH_STORAGE`
- `#undef ENABLE_FLASH_LOG`

Nên các hàm:

- `sys_logger_flash_persist()`
- `sys_logger_flash_peek_packet()`
- `sys_logger_flash_consume()`

không phải luồng active của build hiện tại.

### 7.2 `consume` không phải xoá toàn bộ buffer

`consume` chỉ bỏ đúng phần đã xác nhận.

Nó không reset toàn bộ log, trừ khi:

- buffer bị lỗi
- hoặc code chủ động gọi `sys_logger_clear()`

### 7.3 ACK timeout có thể làm log bị treo chờ

`network_core_wait_ack()` có timeout.

Nếu ACK không tới:

- callback không đi theo đường consume
- packet chờ ACK có thể được hủy/tái xử lý theo logic `network_core`
- log dữ liệu đã peek vẫn còn trong buffer cho tới khi bị consume hoặc bị overwrite

## 8. Tóm tắt theo đúng câu hỏi của bạn

- **RAM và FLASH đều chứa log không?**
  - Trong code có cả 2 nhánh.
  - Nhưng **build hiện tại chỉ dùng RAM log** vì `ENABLE_FLASH_LOG` đang tắt.

- **Mỗi lần có log thì đẩy vào RAM rồi đẩy FLASH ngay lúc đó luôn à?**
  - Không.
  - Log được ghi vào RAM trước.
  - Chỉ khi bật flash log thì task nền mới persist sang FLASH theo chu kỳ.

- **Host ACK gói log đó thì MCU consume nghĩa là gì?**
  - Là **xóa phần log đã gửi thành công khỏi buffer đang giữ**.
  - Với RAM path: dịch `tail` của circular buffer.
  - Với FLASH path: advance read cursor của flash log.

- **Nếu không có ACK thì không clear log, vậy có đủ chỗ cho log mới không?**
  - Có cơ chế buffer vòng.
  - Log mới vẫn vào RAM.
  - Khi đầy thì log cũ nhất bị drop để nhường chỗ.

- **Có chế lưu log mới trong lúc wait ACK không?**
  - Có.
  - Nhưng đây là kiểu **ưu tiên log mới hơn log cũ**, không phải lưu vô hạn.

## 9. Nếu bạn muốn, mình có thể làm tiếp 1 file sơ đồ luồng

Ví dụ:

- `logger write -> RAM buffer -> network_send_log -> wait ACK -> consume`
- và thêm nhánh `ENABLE_FLASH_LOG`

để bạn nhìn một phát là hiểu ngay flow.
