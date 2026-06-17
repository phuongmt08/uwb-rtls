# UWB RTLS Studio - Data/Repository Refactor Plan

Tai lieu nay chot ke hoach refactor truoc khi code de dua `software/uwb_rtls_studio`
ve dung mo hinh du kien:

- `data` chi chua raw bytes/raw packet/raw frame.
- `repository` chiu trach nhiem decode/parse/map/cache.
- `model` chiu business logic va quyet dinh can query loai data nao.
- `viewmodel` validate command/input-output, format field cho UI.
- `view` chi trigger va render.
- Khong su dung SQL/SQLite trong luong chinh va trong session persistence.

## 1. Muc Tieu

Refactor theo flow:

```text
View
  -> ViewModel
  -> Model
  -> Repository
  -> Data(raw)
```

Va flow RX:

```text
Serial bytes
  -> Data raw store
  -> Protocol decode
  -> ProtocolPacketRepository
  -> Domain repositories
  -> Models
  -> ViewModels
  -> UI
```

Muc tieu cu the:

- Da bo hoan toan `SQLite`/`db_manager.py` khoi session architecture.
- Giu `data` la "kho raw byte" dung nghia.
- De `repository` thanh noi parse packet duy nhat.
- Tach ro "realtime cache" va "session persistence".
- De flow de giai thich cho nguoi firmware/hardware, khong bi software-hoa qua muc.

## 2. Hien Trang

Phan dang di dung huong:

- [raw_packet.py](/C:/ALL_FILE/DoAnTotNghiep/uwb-rtls/software/uwb_rtls_studio/data/raw_packet.py)
- [raw_packet_store.py](/C:/ALL_FILE/DoAnTotNghiep/uwb-rtls/software/uwb_rtls_studio/data/raw_packet_store.py)
- [protocol_packet_repository.py](/C:/ALL_FILE/DoAnTotNghiep/uwb-rtls/software/uwb_rtls_studio/repository/protocol_packet_repository.py)
- Cac repository domain: telemetry, ranging, config, diagnostics

Phan da duoc chinh theo muc tieu moi:

- [session_repository.py](/C:/ALL_FILE/DoAnTotNghiep/uwb-rtls/software/uwb_rtls_studio/repository/session_repository.py)
- Session metadata da duoc dua ve file-based persistence

Van de chinh:

- `data` can duoc giu raw-oriented, tranh de lan sang parser/business.
- `session_repository` van can duoc giu gon de khong bi om qua nhieu logic khac.
- Persistence da thuần file-based, nhung docs va naming can tiep tuc duoc don dep.

## 3. Kien Truc Dich

### 3.1. Layer ownership

`data/`

- Raw serial chunks
- Raw HDLC/protobuf packets
- Bounded memory stores
- Optional file appenders cho raw debug

`repository/`

- Parse protobuf packet thanh dict/domain object
- Cap nhat cache noi bo theo domain
- Publish signal/domain event
- Doc/ghi file session persistence

`models/`

- Quyet dinh query command nao
- Giu state business/domain
- Hop nhat data tu repository

`viewmodels/`

- Validate du lieu user nhap
- Goi model method theo intent
- Format field cho UI

`views/`

- Trigger signal
- Render widget/table/chart/log

### 3.2. Session persistence moi

Khong dung DB. Moi session la mot folder:

```text
software/uwb_rtls_studio/data/sessions/
  index.json
  SES_20260612_101530_ranging/
    session_meta.json
    config_snapshot.json
    anchors.json
    positions.csv
    logs.txt
    logs.jsonl
    raw_packets.jsonl
```

`index.json` la danh sach session nhe de browse/filter:

```json
[
  {
    "session_id": "SES_20260612_101530_ranging",
    "session_type": "RANGING",
    "start_time_iso": "2026-06-12T10:15:30",
    "end_time_iso": "2026-06-12T10:21:08",
    "device_name": "TAG-01",
    "device_mac": "AA:BB:CC:DD:EE:FF",
    "path": "SES_20260612_101530_ranging"
  }
]
```

## 4. Dinh Dang Du Lieu De Xuat

### 4.1. Raw debug data

- `RawSerialChunk`: giu bytes nhan tu serial truoc decode
- `RawPacket`: giu packet da decode va metadata `src/dst/seq/param_name`
- `raw_packets.jsonl`: optional, append de debug/replay

### 4.2. Domain persistence data

- `session_meta.json`: metadata tong quan cua session
- `config_snapshot.json`: snapshot config tai thoi diem session
- `anchors.json`: anchor layout
- `positions.csv`: timeline vi tri/ranging result
- `logs.txt`: human-readable logs
- `logs.jsonl`: structured logs cho parsing/filter neu can

## 5. Cac Huong Giai Quyet Co The Chon

### Phuong an A - JSON + CSV thuần

Mo ta:

- Metadata bang JSON
- Timeline bang CSV
- Logs bang TXT/CSV

Uu diem:

- De doc nhat
- De explain nhat
- Khong can engine/driver

Nhuoc diem:

- Filter/query lon se cham hon
- Khong tot bang JSONL cho replay event

### Phuong an B - JSONL event store

Mo ta:

- Moi packet/event la 1 dong JSON
- Build session view tu event log

Uu diem:

- Append de
- Replay/inspect tot
- Sat voi protocol flow

Nhuoc diem:

- Can layer tong hop lai metadata/session summary

### Phuong an C - Hybrid khuyen nghi

Mo ta:

- Realtime: RAM raw store
- Persistence:
  - `index.json`
  - `session_meta.json`
  - `config_snapshot.json`
  - `anchors.json`
  - `positions.csv`
  - `logs.txt`
  - optional `raw_packets.jsonl`

Uu diem:

- De giai thich
- Van giu duoc raw data de debug
- Khong over-engineering

Nhuoc diem:

- Can quy dinh ro file nao la source of truth cua tung loai data

Phuong an khuyen nghi cho repo nay: `Phuong an C`.

## 6. Refactor Scope

### 6.1. Data layer

Can giu:

- `RawSerialChunk`
- `RawPacket`
- `RawPacketStore`

Can bo:

- Moi DB-specific helper con sot lai trong doc/flow chinh

Can them:

- Optional `raw_packet_file_store.py` neu muon append `jsonl`

### 6.2. Repository layer

Can giu:

- `ProtocolPacketRepository`
- `TelemetryRepository`
- `RangingRepository`
- `ConfigRepository`
- `DiagnosticsRepository`
- `LogRepository`

Can refactor:

- `SessionRepository` thanh file-based repository 100%
- `SessionBrowser` neu dang dua vao query/filter SQL

### 6.3. Model layer

Model can goi repository/session API theo intent, vi du:

- `save_active_session()`
- `load_session_summary()`
- `load_session_ranging_trace()`
- `load_session_logs()`

Model khong nen biet den file format chi tiet.

## 7. Cong Viec Theo Phase

### Phase 1 - Chot persistence moi

Muc tieu:

- Xac nhan file structure va source of truth

Cong viec:

- Chot `index.json`
- Chot `session_meta.json` schema
- Chot `positions.csv` schema
- Chot `logs.txt` va/hoac `logs.jsonl`
- Chot co luu `raw_packets.jsonl` hay khong

Deliverable:

- Tai lieu schema ngan trong docs

### Phase 2 - Bo SQL khoi session flow

Muc tieu:

- Session luu/doc/xoa hoan toan bang file

Cong viec:

- Bo import `get_connection()` khoi `session_repository.py`
- Xoa logic create/insert/update/delete SQLite
- Rewrite `save_session()`
- Rewrite `list_sessions()`
- Rewrite `get_session_meta()`
- Rewrite `delete_session()`

Deliverable:

- `SessionRepository` moi, file-based only

### Phase 3 - Them session index

Muc tieu:

- Browse session khong can scan full folder moi lan

Cong viec:

- Tao helper doc/ghi `index.json`
- Khi save session: update index
- Khi delete session: remove index item
- Khi load list: doc index va apply filter tai memory

Deliverable:

- `index.json` la source chinh cho danh sach session

### Phase 4 - Chuan hoa data ownership

Muc tieu:

- Tach ro raw/debug data va parsed domain data

Cong viec:

- Review `ProtocolService` va `ProtocolPacketRepository`
- Dam bao raw serial chunk duoc luu truoc decode
- Dam bao raw packet duoc luu sau decode
- Dam bao repository parse mot lan duy nhat

Deliverable:

- Flow raw -> parsed ro rang, khong lap parser o model

### Phase 5 - Session raw packet persistence tuy chon

Muc tieu:

- Co kha nang replay/debug packet theo session khi can

Cong viec:

- Can nhac them `raw_packets.jsonl`
- Append packet quan trong vao file session
- Khong bat buoc cho MVP

Deliverable:

- Optional debug/replay channel

## 8. De Xuat Thuc Thi Cu The

Thu tu lam de tranh vo domino:

1. Tao file plan va chot architecture
2. Refactor `SessionRepository` bo SQL, dua ve file-based
3. Tao `index.json` va helper load/save index
4. Sua `SessionBrowser` va cac caller dang phu thuoc SQL
5. Dan dep tai lieu va legacy artifact khoi app flow
6. Review lai `data/raw_packet_store.py` va protocol raw ownership
7. Them docs schema/file format

## 9. Rui Ro Va Luu Y

- Repo hien co dirty worktree, can edit scope hep
- Neu co code khac dang doc truc tiep SQLite, can thay the cung luc
- Can giu backward compatibility toi thieu neu da co session folder cu
- Neu tung co file `.sqlite3` trong repo, coi do la du lieu cu va da xoa khoi runtime flow

## 10. Definition Of Done

Refactor duoc coi la xong khi:

- Khong con code runtime phu thuoc `sqlite3` cho session storage
- `SessionRepository` doc/ghi session bang file 100%
- `index.json` hoat dong cho list/filter session
- `data` layer chi con raw-oriented classes/store
- `repository` layer la noi parse/persist chinh
- `model` va `viewmodel` khong can biet den SQL hay DB schema
- Tai lieu docs mo ta duoc flow moi mot cach de explain cho team firmware/hardware

## 11. Ghi Chu Quyet Dinh

Quyet dinh tam thoi de implement:

- Khong dung SQLite
- Chon huong `Hybrid file-based persistence`
- `index.json` la session catalog
- Moi session la 1 folder rieng
- `raw_packets.jsonl` la optional, khong bat buoc cho buoc dau
