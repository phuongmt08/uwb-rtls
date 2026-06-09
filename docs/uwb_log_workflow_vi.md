# Workflow log trong du an UWB RTLS

Tai lieu nay tom tat luong log hien tai trong repo `uwb-rtls`: log duoc tao o MCU nhu nao, nam trong RAM/Flash ra sao, duoc dong goi thanh packet nao, `test_pushing_log.py` parse thanh dong text ra sao, va ACK/consume hoat dong nhu nao.

## 1. Ket luan nhanh ve dong log mau

Vi du:

```text
[2000-01-01 07:00:00.851] [INFO ] [0x03] [BSP][CFG] CH=4 PRF=64MHz DR=2 PCode=17
[2000-01-01 07:00:00.000] [DEBUG] [0x00] =================================================
```

MCU khong gui nguyen chuoi da format nhu tren qua protobuf `log_data`.

MCU chi luu va gui record nhi phan:

```text
[LEN 2B][LOG_TYPE 1B][OBJ_CODE 1B][TIMESTAMP 6B][MSG_LEN 1B][MESSAGE bytes][PAD 0..3B]
```

Trong do `MESSAGE bytes` moi la chuoi do firmware tao bang `vsnprintf`, vi du:

```text
[BSP][CFG] CH=4 PRF=64MHz DR=2 PCode=17
=================================================
```

Phan tien to:

```text
[2000-01-01 07:00:00.851] [INFO ] [0x03]
```

la do `software/vv_testings/test_pushing_log.py` doc record nhi phan roi format lai trong `FlashLogStreamParser._decode_record()`. Nghia la dau `==` va tung chu trong message nam trong payload do MCU ghi; con dau ngoac timestamp/level/object la ben Python render.

## 2. Cac file quan trong

- `firmware/uwb/sys/sys_logger.h`, `firmware/uwb/sys/sys_logger.c`: tao record log, buffer RAM, peek/consume, optional persist Flash.
- `firmware/common/log_config.h`: index header, log type, object code.
- `firmware/common/network/network_cmd.c`: lenh `log_data`, `log_clear`, gui log va cho ACK.
- `firmware/common/network/network_core.c`: encode/decode protobuf, route packet, tracker ACK.
- `firmware/common/serial/hdlc.c`, `firmware/common/serial/serial.c`, `debug_serial.c`, `ble_bridge.c`: HDLC framing va UART/BLE serial transport.
- `protocol/protos/protocol.proto`: schema protobuf `packet_t`, `log_data_t`, `log_clear_t`, `ack_t`.
- `software/vv_testings/test_pushing_log.py`: host script poll log, parse record, in ra dong text.
- `firmware/uwb/bsp/bsp_uwb.c`, `firmware/uwb/Core/Src/stm32f4xx_it.c`, `firmware/uwb/Core/Src/freertos.c`: UWB IRQ va process ranging.

## 3. Cau truc packet gui tren UART/BLE/USB

Transport ngoai cung la HDLC tu `firmware/common/serial/hdlc.h` va `software/common/transport.py`:

```text
SOF      1B  = 0x55
TYPE     1B  = 0 voi protobuf
LEN      2B  little-endian, do dai protobuf payload, max 256
PAYLOAD  nB  protobuf packet_t serialized
CHECKSUM 1B  sum(SOF + TYPE + LEN + PAYLOAD) & 0xFF
```

Payload la protobuf `packet_t`:

```protobuf
message packet_t {
  hdr_t hdr = 1;
  oneof params {
    ack_t ack = 3;
    ...
    log_data_t log_data = 43;
    log_clear_t log_clear = 44;
  }
}
```

Header:

```protobuf
message hdr_t {
  addr_t addr = 1;      // src, dst
  uint32 seq = 2;       // sequence number
  uint32 timestamp = 3;
}
```

Dia chi hay gap:

```text
MCU   = 0x01
HOST  = 0x05
DEBUG = 0x07
BCAST = 0x0F
```

Log packet:

```protobuf
message log_data_t {
  log_type_t type = 1;  // LOG_TYPE_DEVICE_LOG = 1
  bytes data = 2;       // chunk byte log da frame [LEN][RECORD][PAD]
}

message log_clear_t {
  log_type_t type = 1;
  uint32 offset = 2;
  uint32 length = 3;
}
```

Trong `protocol.options`, `log_data.data` max size la 192 byte. Code C lay kich thuoc that bang:

```c
sizeof(packet.params.log_data.data.bytes)
```

## 4. Cau truc mot log record

Header record thuan nam trong `firmware/common/log_config.h`:

```text
LOG_HEADER_IDX_LOG_TYPE  = 0
LOG_HEADER_IDX_OBJ_CODE  = 1
LOG_HEADER_IDX_TIMESTAMP = 2
LOG_HEADER_IDX_DATA_LEN  = 8
LOG_HEADER_IDX_DATA      = 9
LOG_HEADER_LEN           = 9
```

Record thuan:

```text
Byte 0       LOG_TYPE
Byte 1       OBJ_CODE
Byte 2..7    TIMESTAMP 6 byte little-endian
Byte 8       MSG_LEN
Byte 9..     MESSAGE UTF-8/ASCII, do dai MSG_LEN
```

Entry trong RAM/Flash them header do dai va padding:

```text
LEN_LO LEN_HI RAW_RECORD PAD...
```

`LEN` la do dai `RAW_RECORD`, khong tinh 2 byte LEN va khong tinh PAD. Tong entry duoc can 4 byte:

```c
entry_len  = 2 + record_len;
padded_len = (entry_len + 3) & ~3;
```

Log type:

```text
0xFE -> INFO
0xFF -> DEBUG
0xFD -> WARN
khac -> ERROR
```

Object code hay gap:

```text
0x00 BOOTLOADER
0x01 APPLICATION
0x03 UWB_DRIVER
0x04 RANGING
0x09 BLE
0x13 PM
0x14 FUSION
0x15 SYS_CFG
0x16 BATTERY
```

Luu y: `sys_logger_write_record()` OR them bit source theo role:

```c
#ifdef UWB_DEVICE_TAG
  obj_code |= LOG_ERR_SOURCE_TAG;
#else
  obj_code |= LOG_ERR_SOURCE_ANCHOR;
#endif
```

Nen object hien tren host co the da kem bit source tuy build.

## 5. Log duoc tao o MCU nhu nao

Firmware goi macro:

```c
RLOG_I(obj, "format", ...);
RLOG_D(obj, "format", ...);
RLOG_W(obj, "format", ...);
RLOG_E(obj, err_code, "format", ...);
```

Cac macro nay goi `sys_logger_write_record()`.

Trong `sys_logger_write_record()`:

1. Neu logger chua init thi `logger_init()`.
2. Lock `g_logger_mutexHandle`.
3. Format message bang `vsnprintf(msg, sizeof(msg), format, args)`.
4. Cat message toi `SYS_LOGGER_MAX_MSG_LEN` la 180 byte.
5. Lay timestamp:
   - Neu `HAVE_RTC`: `bsp_rtc_get_timestamp_ms()`.
   - Neu khong: dung sequence `log_seq_num++`.
6. Tao raw record `[type][obj][timestamp 6B][len][msg]`.
7. Boc entry `[LEN 2B][record][PAD]`.
8. Ghi entry vao circular buffer RAM `g_logger.buffer`.
9. Neu thieu cho thi drop oldest entry bang `logger_drop_oldest_entry()`.

Dung luong RAM log:

```text
MEM_SHARED_LOG_RAM_SIZE = 4 KB
SYS_LOGGER_SHARED_META_SIZE = 16 B
SYS_LOGGER_BUF_SIZE = 4 KB - 16 B
```

`g_logger` dat trong section `.shared_log`, aligned 4, de giu qua mot so reset/noinit tuy linker script.

## 6. RAM va Flash

Build hien tai trong `firmware/uwb/Core/Inc/config.h`:

```c
#define HAVE_FLASH_STORAGE
#undef  ENABLE_FLASH_LOG
#define HAVE_RTC
```

Nghia la co module Flash storage cho config/cac muc khac, nhung log persistence vao Flash dang tat. Luong log hien tai khi gui cho host la RAM-only:

```text
RLOG_* -> RAM circular buffer -> network_send_log() -> log_data.data -> host ACK -> sys_logger_ram_consume()
```

Neu sau nay bat `ENABLE_FLASH_LOG`, luong se thanh:

```text
RLOG_* -> RAM circular buffer
FlashStorage/sys_logger_task -> sys_logger_flash_persist() -> Flash log partition
network_send_log() -> sys_logger_flash_peek_packet() -> log_data.data
host ACK -> sys_logger_flash_consume()
```

Trong nhanh Flash:

- `sys_logger_flash_persist()` chi pop RAM sau khi ghi Flash thanh cong.
- `g_flash_log_write_pos` la vi tri da ghi toi.
- `g_flash_log_read_pos` la vi tri chua duoc host confirm.
- `sys_logger_flash_pending_bytes() = write_pos - read_pos`.
- `sys_logger_flash_consume(length)` tang read cursor va persist cursor vao flash metadata bang `sys_flash_log_update_read_pos()`.

Trong nhanh RAM-only:

- `sys_logger_ram_peek_packet()` copy cac entry da can record boundary, khong cat giua record.
- `sys_logger_ram_consume(len)` chi pop sau ACK.
- Mat nguon/reset co the mat log RAM tuy section/noinit va boot path; khong co Flash backlog khi `ENABLE_FLASH_LOG` tat.

## 7. Log duoc day ra host tu dau

Host script gui lenh `log_data` rong de xin log:

```python
pkt.log_data.type = pb.LOG_TYPE_DEVICE_LOG
```

MCU xu ly o `network_cmd_log_data_get()`:

```c
s_log_stream_enabled = true;
s_log_stream_dst = pkt->hdr.addr.src;
network_send_log(s_log_stream_dst, max_payload);
```

Sau do task network goi dinh ky trong `network_entry()`:

```c
network_core_process(&g_network_core);
network_cmd_process();
osDelay(2);
```

`network_cmd_process()`:

```c
network_cmd_retry_pending();
if (s_log_stream_enabled && network_cmd_host_active()) {
    network_send_log(s_log_stream_dst, 0xFFFFu);
}
```

`network_send_log()`:

- Neu dang `waiting_ack` thi khong gui goi moi.
- Neu khong co data thi return.
- Tao protobuf packet `log_data`.
- Fill `packet.params.log_data.data.bytes` bang `sys_logger_ram_peek_packet()` hoac `sys_logger_flash_peek_packet()`.
- Goi `network_core_send_packet()`.
- Dang ky ACK tracker bang `network_core_wait_ack()`.

## 8. Quy trinh ACK va consume

Khi MCU gui `log_data`, `network_core_send_packet()` gan:

```text
hdr.addr.src = local MCU
hdr.addr.dst = host/debug
hdr.seq      = tx_seq++
```

Sau do `network_send_log()` dang ky tracker:

```c
network_core_wait_ack(stream, packet.hdr.seq, WAIT_TIME_TO_RESEND_ACK_MS,
                      log_tracker_callback, &s_log_tracker);
```

Host nhan `log_data` trong `test_pushing_log.py`:

1. Lay `payload = bytes(pkt.log_data.data)`.
2. Feed vao `FlashLogStreamParser`.
3. In tung line da format.
4. Gui ACK:

```python
pkt.ack.ack_seq = seq cua goi log_data vua nhan
pkt.ack.response = PACKET_ACK_RESPONSE_ACK
```

MCU nhan ACK:

1. `network_core_process()` decode packet.
2. `network_core_update_ack_trackers()` so `packet.params.ack.ack_seq` voi `t->packet_header.seq`.
3. Neu ACK positive, state = `NETWORK_CORE_ACK_STATE_FOUND`.
4. Goi `log_tracker_callback()`.
5. Callback consume dung so byte vua gui:
   - Flash: `sys_logger_flash_consume(tracker->log_len)`.
   - RAM: `sys_logger_ram_consume(tracker->log_len)`.

Neu timeout hoac NACK:

- Callback van reset `waiting_ack = false`.
- Khong consume vi state khong phai `FOUND`.
- Lan poll sau se peek lai cung data va gui lai.

## 9. `test_pushing_log.py` lam gi

Bootstrap:

```text
send none
send time_sync_set
send host_transport_set
neu --clear-first: send log_clear length=0xFFFFFFFF
send log_data get
```

Loop:

```text
moi 5s gui none de giu host active
moi 1s gui log_data get
doc serial bytes
HDLC decode -> protobuf packet_t
neu packet la log_data -> parse, print, ACK
```

Parser trong script doc entry:

```text
[LEN 2B][RAW_RECORD][PAD]
```

Sau do decode raw record:

```python
log_type = rec[0]
obj_code = rec[1]
timestamp = int.from_bytes(rec[2:8], "little")
msg_len = rec[8]
msg = rec[9:9+msg_len].decode(...)
```

Timestamp:

- Neu >= `946684800000` ms, script format thanh datetime local.
- Neu nho hon, in so thuan.

Level:

- `0xFE` -> `INFO`
- `0xFF` -> `DEBUG`
- `0xFD` -> `WARN`
- khac -> `ERROR`

Vi vay dong hien thi la:

```text
[timestamp formatted] [level] [obj_code] message
```

## 10. Process nhan goi serial/BLE

Nhan UART debug:

- USART1 RX dung DMA circular.
- `USART1_IRQHandler()` trong `stm32f4xx_it.c` check IDLE flag roi goi `debug_serial_uart_rx_check()`.
- `debug_serial_uart_rx_check()` lay byte moi tu DMA, push vao ring buffer.
- `debug_serial_read()` drain ring buffer qua HDLC parser, tra ve protobuf payload.

Nhan BLE bridge:

- USART2 RX dung DMA circular.
- `USART2_IRQHandler()` goi `ble_bridge_uart_rx_check()`.
- `ble_bridge_read()` parse HDLC tu ring buffer.

Task network:

```c
network_core_process(&g_network_core);
network_cmd_process();
```

`network_core_process()`:

1. Goi `_read()` theo stream hien tai.
2. Decode protobuf `packet_t`.
3. Neu packet for local MCU thi goi `packet_handler`, tuc `network_cmd_packet_handler()`.
4. Update ACK trackers neu packet la ACK.
5. Neu packet can route sang interface khac thi forward.

`network_cmd_dispatch()` sau khi dispatch command se gui ACK cho packet nhan:

```c
network_core_send_ack(s_network_cmd.stream, pkt, PACKET_ACK_RESPONSE_ACK);
```

Rieng ACK packet thi duoc network core xu ly trong tracker, application layer khong lam gi them.

## 11. UWB IRQ va process khi nhan packet UWB

Day la luong khac voi log transport protobuf. UWB on-air packet la frame DW1000 cho ranging, khong phai `log_data`.

IRQ path:

1. DW1000 keo chan `UWB_IRQ` tren PA4.
2. STM32 vao `EXTI4_IRQHandler()` trong `stm32f4xx_it.c`.
3. Handler goi:

```c
HAL_GPIO_EXTI_IRQHandler(UWB_IRQ_Pin);
```

4. HAL goi `HAL_GPIO_EXTI_Callback()` trong `bsp_io.c`.
5. Khi callback nhan dung UWB pin, no goi `bsp_uwb_on_irq()` (ham nay nam trong `bsp_uwb.c`).
6. `bsp_uwb_on_irq()` khong doc SPI ngay trong ISR. No chi:

```c
osSemaphoreRelease(g_uwb_isr_semHandle);
```

7. `uwb_ranging_entry()` trong `freertos.c` dang cho semaphore `g_uwb_isr_semHandle`.
8. Khi duoc wake, task lock `g_spi1_mutexHandle`, roi goi:

```c
bsp_uwb_dwt_isr();
```

9. `bsp_uwb_dwt_isr()` goi `dwt_isr()` trong vong multi-pass de xu ly pending interrupt cua DW1000.
10. `dwt_isr()` goi callback da dang ky bang:

```c
dwt_setcallbacks(uwb_tx_cb, uwb_rx_cb);
```

11. `uwb_tx_cb()` queue event `BSP_UWB_EVENT_TX_DONE`.
12. `uwb_rx_cb()` neu `DWT_SIG_RX_OKAY` thi queue event `BSP_UWB_EVENT_RX_OK`, copy `rx_len`, `rx_data`, timestamp/quality.
13. Sau ISR dispatch, `uwb_ranging_entry()` goi `app_tag_process()` hoac `app_anchor_process()` tuy role.
14. State machine trong `sys_ranging.c` lay event bang `bsp_uwb_get_event()` va xu ly POLL/RESP/FINAL/RESULT.

Vi sao khong doc SPI trong ISR? Comment trong source noi ro: ISR chi signal semaphore; moi SPI transaction `dwt_isr`, doc status/rx_data/tx_ts lam trong task context duoi SPI mutex. Cach nay tranh tranh chap SPI va giam viec nang trong interrupt.

## 12. Quan he giua UWB process va log

UWB process sinh ra log bang `RLOG_*` tai nhieu diem:

- `bsp_uwb.c`: config, RX/TX warning/error, ISR recovery.
- `sys_ranging.c`: state machine, distance/result, timing diagnostics.
- `sys_pm.c`, `bsp_battery.c`, `bsp_imu.c`: PM/battery/IMU warning/error.
- `sys_ble_peripheral.c`: BLE init/status.
- `main.c`: app init, device id, role.

Tat ca cac log nay deu di vao cung `sys_logger_write_record()` va cung buffer RAM/Flash. UWB packet khong truc tiep la log packet. Log chi la side effect do code goi `RLOG_*`.

## 13. Clear log

Host co the gui:

```protobuf
log_clear {
  type = LOG_TYPE_DEVICE_LOG
  offset = ...
  length = ...
}
```

Trong code hien tai:

- Nhanh Flash: neu `length > 0` thi `sys_logger_flash_consume(length)`.
- Nhanh RAM: neu `length > 0` thi `sys_logger_ram_consume(length)`, neu `length == 0` thi `sys_logger_clear()`.

`test_pushing_log.py --clear-first` gui:

```text
offset = 0
length = 0xFFFFFFFF
```

Muc dich la clear backlog truoc khi stream realtime. Voi RAM-only, consume se pop toi khi het data. Voi Flash, consume theo entry boundary trong `sys_logger_flash_consume()`.

## 14. Timeline tong hop

### MCU boot

```text
main()
  sys_flash_storage_init()
  sys_logger_init()
  serial_init()
  network_core_init(local=MCU)
  network_cmd_init()
  BSP/app init goi RLOG_* lien tuc
  FreeRTOS start tasks
```

### Tao log

```text
RLOG_I/RLOG_W/RLOG_E
  -> sys_logger_write_record()
  -> vsnprintf message
  -> record binary
  -> [LEN][record][PAD]
  -> RAM circular buffer
  -> optional Flash persist neu ENABLE_FLASH_LOG
```

### Host lay log

```text
test_pushing_log.py
  -> HDLC protobuf log_data get
MCU network_cmd_log_data_get()
  -> network_send_log()
  -> peek RAM/Flash
  -> protobuf log_data.data
  -> HDLC send
Host
  -> parse [LEN][record][PAD]
  -> format thanh "[time] [level] [obj] msg"
  -> send ACK
MCU
  -> ACK tracker FOUND
  -> consume RAM/Flash
```

### Neu mat ACK

```text
MCU gui log_data
khong nhan ACK trong timeout
  -> tracker timeout
  -> khong consume
  -> lan sau gui lai cung chunk
```

## 15. Nhung diem can nho khi debug

- Dong text dep tren terminal khong phai byte raw MCU gui qua protobuf; no la host-rendered.
- Message ben trong dong log la MCU tao that bang `vsnprintf`.
- RAM/Flash deu luu binary entry, khong luu chuoi co prefix `[time][INFO][0x..]`.
- ACK cua `log_data` moi la dieu kien xoa/consume data da gui.
- `log_data` host gui len MCU la request/poll; `log_data` MCU gui ve host moi chua byte log.
- Build hien tai `ENABLE_FLASH_LOG` dang tat, nen doc/consume tu RAM, khong phai Flash log backlog.
- UWB IRQ khong xu ly SPI truc tiep trong EXTI ISR; ISR chi release semaphore, task ranging moi goi `dwt_isr()`.
- Neu thay log lap lai, thuong la host chua ACK, ACK khong route dung dia chi, hoac tracker timeout truoc khi ACK duoc process.
- Neu thay timestamp nam 2000-01-01, do RTC/time_sync chua dung hoac host vua set epoch/local timezone; script format timestamp theo local time neu gia tri >= epoch 2000.

