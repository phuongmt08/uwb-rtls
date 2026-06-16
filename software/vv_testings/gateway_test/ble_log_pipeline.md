# Pipeline BLE Log: test_ble_log -> Dongle -> FS-BT630 -> MCU

Tai lieu nay mo ta chu trinh log BLE khi chay:

```powershell
python software/vv_testings/gateway_test/test_ble_log.py --port COMx
```

Muc tieu cua pipeline la dung dongle BLE Central tren PC de ket noi toi FS-BT630/BLE Peripheral tren thiet bi, sau do lay log cua MCU qua duong BLE va in ra terminal giong `test_pushing_log.py`.

## 1. Cac node trong pipeline

```text
PC / Python test_ble_log.py
        |
        | USB CDC serial, HDLC frame chua protobuf packet_t
        v
BLE Central Dongle
        |
        | BLE link
        v
FS-BT630 / BLE Peripheral bridge
        |
        | internal UART/SPI/transport giua module BLE va firmware
        v
STM32 MCU / log storage
```

Vai tro tung node:

- `test_ble_log.py`: tao command protobuf, boc HDLC, gui qua COM port, doc packet tra ve, parse record log va print.
- `BLE Central Dongle`: nhan command tu PC qua USB CDC, thuc hien scan/connect BLE, forward packet giua PC va peripheral.
- `FS-BT630`: dong vai tro BLE Peripheral tren target, nhan BLE packet tu dongle va chuyen vao firmware/MCU.
- `MCU`: xu ly command `none`, `time_sync_set`, `host_transport_set`, `log_data`, `log_clear`, `ack`, va day log tu flash/log buffer ve host.

## 2. Dia chi logic trong packet

Moi protobuf `packet_t` co header:

```text
hdr.addr.src
hdr.addr.dst
hdr.seq
```

Trong flow BLE log:

```text
HOST    = 5  # test_ble_log.py tren PC
CENTRAL = 3  # BLE Central Dongle
MCU     = 1  # STM32 MCU sau FS-BT630
```

Co hai nhom command chinh:

- Command dieu khien BLE gui toi `CENTRAL`: `ble_scan_start`, `ble_scan_stop`, `ble_connect`, `ble_disconnect`.
- Command lay log gui toi `MCU`: `none`, `time_sync_set`, `host_transport_set`, `log_data`, `log_clear`, `end_session`, `ack`.

## 3. Control plane: scan va connect BLE

Control plane chi lam viec voi dongle Central, chua cham vao log MCU.

### 3.1. Mo serial port

`test_ble_log.py` mo COM port cua dongle:

```text
PC opens COMx @ 115200
PC reset_input_buffer()
PC reset_output_buffer()
```

Neu khong truyen `--port`, script se auto-probe hoac fallback scan USB serial port.

### 3.2. Bat dau scan

PC tao packet:

```text
src = HOST
dst = CENTRAL
param = ble_scan_start
```

Duong di:

```text
test_ble_log.py
  -> HDLC/protobuf over USB CDC
  -> BLE Central Dongle
  -> dongle bat dau scan BLE advertising
```

Dongle tra ve nhieu packet:

```text
src = CENTRAL
dst = HOST
param = ble_scan_result
fields = mac_address, name, rssi_dbm, serial_number
```

Script loc device theo:

- `--mac` neu co truyen MAC cu the.
- `--name` neu co truyen filter ten.
- Mac dinh: ten co chua `UWB`, `TAG`, `ANCHOR`, `NUS`, hoac `RTLS`.

### 3.3. Stop scan

Sau khi tim duoc target hoac het timeout:

```text
src = HOST
dst = CENTRAL
param = ble_scan_stop
```

Dongle dung scan de tranh vua scan vua connect lam link kem on dinh.

### 3.4. Connect toi FS-BT630

PC tao packet:

```text
src = HOST
dst = CENTRAL
param = ble_connect
field = ble_connect.mac_address
```

Duong di:

```text
test_ble_log.py
  -> USB CDC
  -> BLE Central Dongle
  -> BLE connect request
  -> FS-BT630 Peripheral
```

Dongle bao trang thai bang:

```text
src = CENTRAL
dst = HOST
param = ble_status_resp
field = state
```

Trang thai quan trong:

```text
BLE_STATE_CONNECTING
BLE_STATE_CONNECTED
BLE_STATE_IDLE + disconnect_reason
```

Khi nhan `BLE_STATE_CONNECTED`, data plane moi bat dau.

## 4. Data plane: bootstrap log session voi MCU

Sau khi BLE connected, script gui mot chuoi command toi `MCU`. Cac packet nay di xuyen qua dongle va FS-BT630.

### 4.1. none: danh thuc/keep-alive protocol

Packet:

```text
src = HOST
dst = MCU
param = none
```

Duong di:

```text
PC -> Dongle -> BLE -> FS-BT630 -> MCU
```

Muc dich:

- Kich hoat path route HOST -> MCU.
- Lam packet nhe de firmware xac nhan host dang song.
- Dung lai dinh ky lam keep-alive neu can.

### 4.2. time_sync_set: dong bo thoi gian log

Packet:

```text
src = HOST
dst = MCU
param = time_sync_set
fields:
  unix_time_ms
  timezone_offset
```

Muc dich:

- MCU cap nhat moc thoi gian.
- Log sau do co timestamp dung thay vi mac dinh `2000-01-01`.

### 4.3. host_transport_set: chon duong tra log

Packet:

```text
src = HOST
dst = MCU
param = host_transport_set
field:
  transport = USB
```

Y nghia trong flow BLE:

- Day la setting logic cua firmware ve host I/O/route.
- Packet van di qua BLE bridge, nhung firmware biet host dang yeu cau duong tra ve phu hop voi host session.
- Neu route sai, log co the bi day ve interface khac hoac khong ve terminal.

### 4.4. log_clear all neu chay voi --clear-first

Neu muon bo backlog log cu:

```text
src = HOST
dst = MCU
param = log_clear
fields:
  type = LOG_TYPE_DEVICE_LOG
  offset = 0
  length = 0xFFFFFFFF
```

Nen dung khi:

- Vua reset MCU va chi muon xem log moi.
- Flash log con backlog lon lam terminal in cham.

Khong nen dung khi:

- Dang can dieu tra boot log cu.
- Can giu lai log truoc do de doi chieu.

### 4.5. log_data get: yeu cau MCU gui chunk log

Packet:

```text
src = HOST
dst = MCU
param = log_data
field:
  type = LOG_TYPE_DEVICE_LOG
```

Day la lenh poll log. MCU doc log storage/buffer va tra ve mot chunk `log_data.data`.

## 5. Vong lap runtime lay log

Sau bootstrap, script vao loop doc COM port va xu ly packet.

Pipeline runtime chuan:

```text
repeat:
  PC gui log_data get moi LOG_POLL_PERIOD_S
  Dongle forward qua BLE
  FS-BT630 forward vao MCU
  MCU tra log_data chunk
  FS-BT630 forward ra BLE
  Dongle forward ra USB CDC
  PC parse + print
  PC gui ack cho seq vua nhan
  PC gui log_clear(length da nhan) de xoa phan da consume
```

Dang sequence:

```text
PC/test_ble_log       Dongle Central       FS-BT630        MCU
      |                    |                  |             |
      | log_data get       |                  |             |
      |------------------->|                  |             |
      |                    | BLE payload      |             |
      |                    |----------------->|             |
      |                    |                  | to firmware |
      |                    |                  |------------>|
      |                    |                  |             | read flash/log buffer
      |                    |                  | log_data    |
      |                    |                  |<------------|
      |                    | BLE notify/data  |             |
      |                    |<-----------------|             |
      | USB CDC packet     |                  |             |
      |<-------------------|                  |             |
      | parse + print      |                  |             |
      | ack(seq)           |                  |             |
      |------------------->|----------------->|------------>|
      | log_clear(length)  |                  |             |
      |------------------->|----------------->|------------>|
```

## 6. Format payload log_data.data

`log_data.data` khong phai text raw truc tiep. No la stream record co framing rieng:

```text
[len_lo][len_hi][raw_record(len)][pad to 4-byte]
```

Trong `raw_record`:

```text
[log_type][obj_code][timestamp(6 bytes little-endian)][msg_len][msg]
```

Parser trong `test_ble_log.py` gom byte vao buffer, sau do tach tung record:

```text
rec_len = len_lo | (len_hi << 8)
entry_len = align4(2 + rec_len)
```

Sau khi decode:

```text
log_type = 0xFE -> INFO
log_type = 0xFF -> DEBUG
log_type = 0xFD -> WARN
other           -> ERROR
```

Terminal in dang:

```text
[timestamp] [LEVEL] [0xOBJ] message
```

Vi du:

```text
[2026-06-09 22:47:59.406] [INFO ] [0x15] Host transport set to: USB
```

## 7. ACK va clear de tranh in lap

Khi PC nhan packet:

```text
src = MCU
dst = HOST
param = log_data
seq = N
```

PC can gui ACK:

```text
src = HOST
dst = packet.hdr.addr.src
param = ack
ack_seq = N
response = PACKET_ACK_RESPONSE_ACK
```

ACK co muc dich bao voi firmware/transport rang host da nhan packet.

Sau ACK, PC nen gui `log_clear` theo do dai payload da consume:

```text
src = HOST
dst = MCU
param = log_clear
fields:
  type = LOG_TYPE_DEVICE_LOG
  offset = 0
  length = len(log_data.data)
```

Ly do:

- Neu chi ACK ma khong clear, flash log van con record cu.
- Lan poll sau co the doc lai cung vung log.
- Ket qua tren terminal la nhieu dong bi in hai lan hoac in lai theo tung chunk.

Day la diem `test_pushing_log.py` dang lam on:

```text
nhan log_data -> parse/print -> ack -> log_clear(len(payload)) -> poll tiep
```

## 8. Vi sao log BLE co the bi cham

Co ba nguyen nhan hay gap:

1. Chi gui `log_data get` mot lan luc bootstrap.

   Neu MCU chi tra mot chunk moi lan host poll, terminal se dung sau chunk dau tien. Muon realtime can poll dinh ky, vi du moi `LOG_POLL_PERIOD_S = 1.0s`.

2. Backlog flash log qua lon.

   Neu khong `--clear-first`, MCU co the day rat nhieu log cu sau reset. Terminal nhin nhu cham vi dang xa backlog, khong phai log realtime.

3. ACK/clear khong day du.

   Neu host khong clear phan da doc, lan sau co the doc lai log cu. Vua gay duplicate, vua lam backlog khong thoat nhanh.

## 9. Chu trinh chuan mong muon

Day la chu trinh nen co de BLE log on dinh nhu `test_pushing_log.py`:

```text
1. Open COM dongle.
2. Send ble_scan_start to CENTRAL.
3. Receive ble_scan_result.
4. Send ble_scan_stop.
5. Send ble_connect(target_mac) to CENTRAL.
6. Wait ble_status_resp == CONNECTED.
7. Send none to MCU.
8. Send time_sync_set to MCU.
9. Send host_transport_set to MCU.
10. Optional: send log_clear all if --clear-first.
11. Send log_data get to MCU.
12. Loop:
    - every 5s: send none keep-alive.
    - every 1s: send log_data get.
    - receive log_data.
    - parse FlashLogStreamParser.
    - print each decoded log line.
    - send ack for received seq.
    - send log_clear(len(payload)).
13. On Ctrl+C:
    - send end_session to MCU.
    - send ble_disconnect to CENTRAL.
    - close COM port.
```

## 10. Huong packet theo tung loai command

### BLE control command

```text
PC HOST(5)
  -> dst CENTRAL(3)
  -> Dongle xu ly truc tiep
```

Vi du:

```text
ble_scan_start
ble_scan_stop
ble_connect
ble_disconnect
```

### MCU log command

```text
PC HOST(5)
  -> dst MCU(1)
  -> Dongle route qua BLE
  -> FS-BT630
  -> MCU xu ly
```

Vi du:

```text
none
time_sync_set
host_transport_set
log_data
log_clear
ack
end_session
```

### Response tu MCU ve PC

```text
MCU(1)
  -> FS-BT630
  -> BLE
  -> Dongle
  -> USB CDC
  -> PC HOST(5)
```

Vi du:

```text
log_data
ack
device_information_resp
```

## 11. Checklist debug nhanh

Neu khong thay log:

- Kiem tra dung COM port cua dongle.
- Kiem tra target dang advertising va ten/MAC dung filter.
- Xem co nhan `ble_status_resp == CONNECTED` khong.
- Chay voi `--verbose` de xem packet ngoai log.
- Thu `--clear-first` neu nghi backlog qua lon.
- Kiem tra script co poll `log_data get` dinh ky khong.
- Kiem tra sau `log_data` co ACK va `log_clear(len(payload))` khong.

Neu log bi in lap:

- Kiem tra `log_clear(length)` sau moi `log_data`.
- Kiem tra MCU co clear dung so byte theo format flash log khong.
- Kiem tra co hai host/session cung poll log khong.
- Kiem tra reset MCU co tao lai cung boot log trong flash hay la host doc lai log cu.

Neu log bi cham:

- Giam backlog bang `--clear-first`.
- Poll `log_data get` dinh ky, mac dinh `1s`.
- Giam sleep bootstrap ve khoang `0.05s` neu link on.
- Dam bao COM port khong bi terminal khac giu.
