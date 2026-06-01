# 🔵 UWB RTLS Studio

> Desktop monitoring dashboard cho hệ thống UWB RTLS (Ultra-Wideband Real-Time Location System).  
> Built with **PySide6** + **MVVM Architecture** + **Protobuf Protocol**.

---

## 📌 Tổng Quan

UWB RTLS Studio là ứng dụng desktop cho phép:
- **Detect & Connect** dongle NRF52840 Central qua USB
- **Scan & Select** các BLE devices (Tags, Anchors)
- **Live Tracking** — vị trí tag realtime trên 2D map
- **Config** — đọc/ghi cấu hình (anchor layout, time sync, UWB, Ranging, ...)
- **Calibration** — antenna delay tuning, IMU calibration (Developer)
- **Log & Session History** — xem logs, browse + debug past sessions

---

## 🏗 Architecture — MVVM + Repository

```
┌───────────────────────────────────────────────────────────────────────┐
│                         APPLICATION FLOW                              │
│                                                                       │
│   ┌──────────┐    ┌──────────┐    ┌──────────────────────────────┐   │
│   │  Dongle  │───►│   Scan   │───►│         Main Window          │   │
│   │  Popup   │    │  Popup   │    │  ┌─────┬─────┬─────┬───┬───┐│   │
│   │  (auto)  │    │  (list)  │    │  │Info │Track│Conf │Cal│Log ││   │
│   └──────────┘    └──────────┘    │  │ Tab │ Tab │ Tab │Tab│Tab ││   │
│                                    │  └─────┴─────┴─────┴───┴───┘│   │
│                                    │  [End Session]    [Status Bar]│   │
│                                    └──────────────────────────────┘   │
│                                              │ End Session           │
│                                              ▼                       │
│                                    ┌──────────────────┐              │
│                                    │   Repository     │              │
│                                    │  (Session Save)  │              │
│                                    │  data/sessions/  │              │
│                                    └──────────────────┘              │
└───────────────────────────────────────────────────────────────────────┘
```

### MVVM + Repository Layer Diagram

```
┌─────────────────┐     Qt Signals     ┌──────────────────┐    Direct     ┌─────────────────┐
│      VIEWS      │ ◄════════════════► │   VIEWMODELS     │ ════════════► │     MODELS      │
│  (Pure UI)      │                    │  (Business Logic) │              │  (Data/State)   │
├─────────────────┤                    ├──────────────────┤              ├─────────────────┤
│ • DonglePopup   │                    │ • DongleVM       │              │ • DongleModel   │
│ • ScanPopup     │                    │ • ScanVM         │              │ • DeviceModel   │
│ • MainWindow    │                    │ • MainVM         │              │ • SessionModel  │
│ • DeviceInfoTab │                    │ • DeviceInfoVM   │              │ • RangingModel  │
│ • TrackingTab   │                    │ • TrackingVM     │              │ • ConfigModel   │
│ • ConfigTab     │                    │ • ConfigVM       │              │ • TelemetryModel│
│ • CalibTab      │                    │ • CalibVM        │              │ • LogModel      │
│ • LogTab        │                    │ • LogVM          │              │                 │
└─────────────────┘                    └──────────────────┘              └─────────────────┘
                                              │         │
                                              ▼         ▼
                                    ┌──────────────────┐  ┌─────────────────┐
                                    │    SERVICES       │  │   REPOSITORY    │
                                    ├──────────────────┤  ├─────────────────┤
                                    │ • SerialService   │  │ • SessionRepo   │
                                    │ • ProtocolService │  │ • SessionBrowser│
                                    │ • DongleDetect    │  │                 │
                                    │ • DataExport      │  │ → data/sessions/│
                                    └──────────────────┘  └─────────────────┘
```

### Quy tắc MVVM

| Rule | Mô tả |
|------|--------|
| View → ViewModel | View gọi ViewModel methods khi user tương tác |
| ViewModel → View | ViewModel emit Qt Signals, View listen via Slots |
| ViewModel → Model | ViewModel đọc/ghi Model trực tiếp |
| ViewModel → Repository | ViewModel gọi Repository để save/load sessions |
| View ✕ Model | View **KHÔNG BAO GIỜ** truy cập Model trực tiếp |
| ViewModel ✕ Import View | ViewModel **KHÔNG** import View classes |

---

## 🧵 Threading & Concurrency Architecture

Để đảm bảo ứng dụng không bị đơ (freeze), giật lag (stutter), hay tràn bộ nhớ (memory leak) khi phải nhận/gửi dữ liệu liên tục từ UWB/BLE ở tốc độ cao (ví dụ 10Hz - 30Hz), ứng dụng sử dụng mô hình đa luồng (Multi-threading) với **PySide6 QThread** và cơ chế đồng bộ luồng nghiêm ngặt.

### 1. Phân chia luồng (Thread Separation)

Ứng dụng được chia làm 2 nhóm luồng chính:

*   **Main Thread (UI Thread):**
    *   Chạy vòng lặp sự kiện chính của Qt (`QApplication.exec()`).
    *   **Nhiệm vụ:** Render giao diện (Views), cập nhật bản đồ 2D tracking, xử lý logic (ViewModels), và quản lý trạng thái dữ liệu (Models).
    *   **Quy tắc:** Tuyệt đối KHÔNG chạy các tác vụ I/O (đọc/ghi file lớn, đọc/ghi Serial, network, delay/sleep) trên luồng này để tránh làm treo giao diện.

*   **Background Threads (Workers):**
    *   Chạy độc lập với UI, chuyên xử lý các tác vụ I/O hoặc blocking.
    *   `SerialReadWorker` (QThread): Chạy vòng lặp vô hạn `serial.read()`. Liên tục lắng nghe byte thô từ phần cứng mà không làm nghẽn UI.
    *   `DongleDetectWorker` (QThread): Gọi `serial.tools.list_ports` (hàm này rất chậm trên Windows, làm khựng app nếu chạy ở UI thread) để quét tìm thiết bị.
    *   `PeriodicPollWorker` (QThread): Chạy ngầm, dùng sleep/timer để định kỳ gửi lệnh hỏi (poll) pin, trạng thái hệ thống mỗi N giây.

### 2. Cơ chế đồng bộ và giao tiếp (Communication Mechanism)

Nếu luồng Background (như `SerialReadWorker`) trực tiếp thay đổi dữ liệu của Model hoặc gọi hàm vẽ của View, ứng dụng sẽ bị **CRASH** lập tức (Lỗi *Cross-thread UI access* hoặc *Race condition*). 
Vì vậy, luồng giao tiếp được thiết kế bảo mật bằng **Signals** và **Queues**:

*   **Từ Worker ➡️ Main Thread (Sử dụng Qt Signals):**
    *   Khi `SerialReadWorker` đọc được 1 khối byte, nó không tự xử lý mà phát ra một **Signal** (ví dụ: `data_received(bytes)`).
    *   Cơ chế của Qt (QueuedConnection) sẽ tự động bắt lấy tín hiệu này, đưa nó vào một hàng đợi an toàn, rồi Main Thread sẽ từ từ lấy ra xử lý.
    *   Main Thread giao khối byte cho `ProtocolService` để parse thành Protobuf, rồi cập nhật vào Model.

*   **Từ Main Thread ➡️ Hardware (Sử dụng Thread-safe Queue):**
    *   Khi người dùng nhấn nút trên giao diện, ViewModel sẽ ra lệnh tạo ra một mảng bytes.
    *   Thay vì gọi trực tiếp `serial.write()` (có thể bị OS block nếu buffer đầy), Main Thread đẩy mảng bytes này vào một **Thread-safe Queue** (`queue.Queue` trong Python).
    *   Một Service ở dưới (như `SerialService`) sẽ lấy dữ liệu từ Queue này và ghi xuống cổng USB một cách an toàn.

*   **Cơ chế chống tràn RAM (Backpressure & Buffering):**
    *   **Models:** Các model lưu trữ dữ liệu liên tục như `position_history` (Ranging) hay `entries` (Logs) sử dụng cấu trúc dữ liệu có giới hạn (như `collections.deque(maxlen=10000)`). Khi dữ liệu quá nhiều, cái cũ nhất sẽ tự động bị đẩy ra. Nhờ vậy, dù cắm máy chạy 24/7 thì RAM của app vẫn không bị phình to (Memory Leak).
    *   **Data Export/Repository:** Khi người dùng nhấn *End Session*, việc gom toàn bộ dữ liệu ghi xuống ổ cứng sẽ được đẩy sang Background Thread hoặc sử dụng Async I/O để giao diện vẫn mượt mà trong lúc lưu file lớn.

### 3. Cơ chế phân loại và điều phối dữ liệu (Data Routing & Queue)

Vì toàn bộ app kết nối qua duy nhất **1 Dongle**, rủi ro đơ app hoặc sập phần cứng do các Tab "giẫm đạp" lên nhau để giao tiếp là rất cao. Kiến trúc xử lý như sau:

*   **Bộ Điều Phối Trung Tâm (Central Dispatcher - `ProtocolService`):**
    *   Mọi dữ liệu từ Dongle bắn lên đều hội tụ về `ProtocolService`.
    *   Service này đọc mã lệnh (`tag`) và làm nhiệm vụ **Router**.
    *   *Ví dụ:* Gói tin streaming `ranging_result_t` bắn lên 10 lần/giây, `ProtocolService` sẽ **chỉ** phân phối nó về `LiveTrackingViewModel`. Các Tab khác như Info, Config, hay Log sẽ hoàn toàn không nhận được, không bị đánh thức, giúp bảo toàn CPU.

*   **Hàng Đợi Lệnh & Van Điều Tiết (Command Queue & Throttling):**
    *   Nếu Tab 1 đòi đọc Pin, Tab 3 đòi lưu Cấu hình, Tab 4 đòi lấy dữ liệu IMU trong cùng 1 mili-giây, Dongle UART Buffer sẽ bị tràn (Overflow).
    *   **Giải pháp:** Các Tab khi gửi lệnh **không** gọi trực tiếp vào phần cứng. Lệnh được đẩy vào một **Write Queue** (Hàng đợi gửi) nằm trong `SerialService`.
    *   Hệ thống sẽ bốc từng lệnh từ Queue ra, gửi xuống USB, và có khoảng nghỉ vi mô (vài ms) giữa các lệnh. Dongle sẽ không bao giờ bị nghẹn.

### 4. Sơ đồ Luồng (Thread & Concurrency Layout)

Dưới đây là sơ đồ kiến trúc luồng, minh họa rõ vị trí của **Queue** và **Semaphore (Lock)** để ngăn chặn đụng độ (Race Condition) làm crash app:

```text
                                [ MAIN THREAD (UI) ]
 ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
 │ Tab 1 (Info)   │ │ Tab 2 (Track)  │ │ Tab 3 (Config) │ │ Tab 4 (Log)    │
 └───────┬────────┘ └───────┬────────┘ └───────┬────────┘ └───────┬────────┘
         │ (Gửi lệnh)       │ (Gửi lệnh)       │ (Gửi lệnh)       │
         ▼                  ▼                  ▼                  ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                       ProtocolService (Router)                          │
 └────────────────────────────────────┬────────────────────────────────────┘
                                      │ 1. Đẩy lệnh (bytes) vào Queue
                                      ▼
                      [ Thread-safe WRITE QUEUE ] (queue.Queue)
                                      │
 ═════════════════════════════════════╪═════════════════════════════════════
                                      │ 2. Background Thread lấy lệnh ra
                      [ BACKGROUND THREAD (I/O) ]
                                      │
 ┌────────────────────────────────────▼────────────────────────────────────┐
 │                            SerialService                                │
 │                                                                         │
 │  ┌─────────────────────┐                         ┌───────────────────┐  │
 │  │ SerialWriteWorker   │ ◄── (SEMAPHORE/LOCK) ──►│ SerialReadWorker  │  │
 │  │ (Pop lệnh từ Queue) │    (Bảo vệ cổng USB)    │ (Vòng lặp đọc)    │  │
 │  └────────┬────────────┘                         └─────────▲─────────┘  │
 │           │ 3. serial.write()                              │ 4. read()  │
 └───────────┼────────────────────────────────────────────────┼────────────┘
             ▼                                                │
       [ USB PORT ] ──────────────────────────────────────────┘
```

**Vai trò của các công cụ đồng bộ (Synchronization Tools):**
1.  **Thread-safe Queue (Hàng đợi gửi lệnh):** 
    Dùng để chứa các lệnh từ Main Thread dội xuống. `queue.Queue` tự động xử lý khóa (thread-safe), nên 5 Tab có ra lệnh cùng lúc thì lệnh vẫn xếp hàng ngay ngắn, không bị đè lên nhau.
2.  **Semaphore / Mutex Lock (Khóa bảo vệ cổng USB):**
    Thư viện `pyserial` có thể bị lỗi (Crash cấp OS) nếu có 2 luồng khác nhau cùng thao tác trên 1 cổng COM (một luồng đang `read()` thì luồng kia lại nhào vô `write()`). Để giải quyết, `SerialService` sử dụng một cái khóa (`QMutex` hoặc `threading.Semaphore`). 
    *Luật:* Trọng tài (Semaphore) chỉ cho 1 luồng cầm chìa khóa. Nếu WriteWorker đang gửi lệnh, ReadWorker phải đứng đợi vài mili-giây. Viết xong, trả chìa khóa lại thì ReadWorker mới được đọc. Điều này bảo vệ tuyệt đối cổng Dongle không bị đứng máy.

---

## 📂 Cấu Trúc Thư Mục

```
uwb_rtls_studio/
├── main.py                          # 🚀 Entry point — bootstrapper
├── version.py                       # 📌 Version string
├── README.md                        # 📖 File này
│
├── models/                          # 📦 DATA LAYER
│   ├── __init__.py                  #     Package description
│   ├── dongle_model.py              #     USB dongle state
│   ├── device_model.py              #     BLE devices (scan list, connected)
│   ├── session_model.py             #     Session lifecycle + data bundle + meta
│   ├── ranging_model.py             #     Position data + trajectory history
│   ├── config_model.py              #     All config groups
│   ├── telemetry_model.py           #     Battery, temperature, diagnostics
│   └── log_model.py                 #     Log entries (device + app logs)
│
├── viewmodels/                      # 🧠 LOGIC LAYER
│   ├── __init__.py                  #     Package description + MVVM diagram
│   ├── dongle_viewmodel.py          #     Detect + connect dongle flow
│   ├── scan_viewmodel.py            #     BLE scan + device selection flow
│   ├── main_viewmodel.py            #     Tab switching, session, User/Dev mode
│   ├── device_info_viewmodel.py     #     Tab 1: Device identity + telemetry
│   ├── live_tracking_viewmodel.py   #     Tab 2: Ranging + position updates
│   ├── config_viewmodel.py          #     Tab 3: Config (User+Dev scoped)
│   ├── calibration_viewmodel.py     #     Tab 4: Calibration (Developer only)
│   └── log_viewmodel.py             #     Tab 5: Log + Session History (User+Dev)
│
├── views/                           # 🎨 UI LAYER
│   ├── __init__.py                  #     Package description
│   ├── windows/
│   │   └── main_window.py           #     Main window (tabs + status bar)
│   ├── popups/
│   │   ├── dongle_popup.py          #     Popup 1: Detect dongle
│   │   └── scan_popup.py            #     Popup 2: Scan + connect device
│   ├── tabs/
│   │   ├── device_info_tab.py       #     Tab 1: Device Info (User+Dev)
│   │   ├── live_tracking_tab.py     #     Tab 2: Live Tracking (User+Dev)
│   │   ├── config_tab.py            #     Tab 3: Config (User+Dev scoped)
│   │   ├── calibration_tab.py       #     Tab 4: Calibration (Developer only)
│   │   └── log_tab.py               #     Tab 5: Log + Session History (User+Dev)
│   └── components/
│       ├── status_bar.py            #     Bottom status bar
│       ├── glass_button.py          #     Glassmorphism button
│       ├── position_canvas.py       #     2D position map (QPainter)
│       └── log_text_widget.py       #     Color-coded log display
│
├── services/                        # 🔌 I/O & COMMUNICATION LAYER
│   ├── __init__.py
│   ├── serial_service.py            #     USB/Serial port management
│   ├── protocol_service.py          #     HDLC + Protobuf encode/decode
│   ├── dongle_detect_service.py     #     Auto-detect dongle via VID/PID
│   └── data_export_service.py       #     Manual export (ad-hoc CSV/JSON)
│
├── repository/                      # 💾 PERSISTENCE LAYER (NEW)
│   ├── __init__.py                  #     Package description
│   ├── session_repository.py        #     Save/load/list session bundles
│   └── session_browser.py           #     Browse + filter past sessions
│
├── workers/                         # ⚡ BACKGROUND THREADS
│   ├── __init__.py
│   ├── serial_read_worker.py        #     Continuous serial reading
│   ├── dongle_detect_worker.py      #     COM port scanning
│   └── periodic_poll_worker.py      #     Periodic battery/status polling
│
├── utils/                           # 🛠 UTILITIES
│   ├── __init__.py
│   ├── constants.py                 #     VID/PID, baud rate, timeouts, UI sizes
│   ├── theme.py                     #     Dark theme (QSS stylesheet + colors)
│   └── helpers.py                   #     Format functions, conversions
│
├── resources/                       # 🎨 STATIC ASSETS
│   ├── __init__.py
│   └── icons/
│       └── README.txt               #     Icon inventory list
│
└── data/                            # 💾 SESSION HISTORY (gitignored)
    ├── __init__.py
    └── sessions/                    #     ← Tất cả session history ở đây
        ├── SES_20260530_123000_ranging/
        │   ├── session_meta.json    #     Metadata: type, time, device, stats
        │   ├── positions.csv        #     Position samples
        │   ├── logs.csv             #     Log entries
        │   ├── config_snapshot.json #     Device config snapshot
        │   └── ranging_stats.json   #     Ranging statistics
        ├── SES_20260530_140500_streaming/
        │   ├── session_meta.json
        │   └── logs.csv
        └── ... (all history preserved)
```

---

## 🔄 Application Flow

### Flow 1: Dongle Detection (Startup)

```
App Start → DonglePopup opens
         → DongleDetectWorker scans COM ports
         → Match VID:0x1915 (Nordic) ?
            ├─ YES → "Detected Dongle Central NRF52840" popup
            │      → Open serial port (115200 baud)
            │      → Send device_information_get (tag=4)
            │      → Receive device_information_resp (tag=5)
            │      → ✅ "Connected!" → Close popup → Open ScanPopup
            └─ NO  → ❌ "Dongle not found" → [Retry] / [Cancel]
```

### Flow 2: BLE Scanning (Scan Popup)

```
ScanPopup opens → Auto send ble_scan_start (tag=51)
              → Dongle scans BLE devices
              → Receive ble_scan_result (tag=54) for each device
              → Display in table: Name | Type | RSSI | MAC | Serial
              → User selects a TAG device
              → Click [Connect]
              → Send ble_connect (tag=53)
              → Log: "Connecting..."
              → Receive confirmation + adv info
              → Log: "✅ Connected to UWB-Tag-01"
              → Close popup → Open MainWindow
```

### Flow 3: Main Window (Tabs)

```
MainWindow opens with connected device
├── Tab 1: Device Info     → device_info, battery, ble_status     (User+Dev)
├── Tab 2: Live Tracking   → ranging_start/stop/result            (User+Dev)
├── Tab 3: Config          → anchor layout, time sync, UWB, ...   (User+Dev scoped)
├── Tab 4: Calibration     → pos_calib, calib_status, IMU         (Developer only)
└── Tab 5: Log & History   → device logs, session history browser  (User+Dev)

[End Session] → Dừng protobuf activities → Save session → Browse later
[Close Window] → Full app shutdown
```

### Flow 4: End Session (Chi tiết)

```
User đang ranging/streaming/log
         │
         ▼
    [🔴 End Session] clicked
         │
         ├─ 1. Gửi end_session_t (tag=65) với reason phù hợp
         │     - SESSION_END_REASON_RANGING_RESULTS  (nếu ranging)
         │     - SESSION_END_REASON_DEBUG_STREAMING   (nếu streaming)
         │     - SESSION_END_REASON_LOG_DATA          (nếu log upload)
         │
         ├─ 2. Dừng protobuf activities
         │     - ranging_stop_t (nếu ranging)
         │     - Stop log stream (nếu streaming)
         │     ⚠ KHÔNG disconnect dongle / BLE / device
         │
         ├─ 3. Bundle toàn bộ session data
         │     - SessionState (type, time, stats)
         │     - Positions CSV (nếu ranging)
         │     - Logs CSV (tất cả log trong session)
         │     - Config snapshot JSON
         │     - Ranging stats JSON (nếu ranging)
         │     - Device info + Dongle info
         │
         ├─ 4. Save vào Repository
         │     → data/sessions/SES_20260530_123000_ranging/
         │     → session_meta.json + positions.csv + logs.csv + ...
         │     → Session được lưu VĨNH VIỄN (không tự xóa)
         │
         ├─ 5. App vẫn chạy bình thường
         │     - Dongle vẫn connected (USB serial)
         │     - BLE device vẫn connected
         │     - Có thể bắt đầu session mới ngay
         │
         └─ 6. User browse session history bất kỳ lúc nào
               → Tab 5 (Log & History) → Session History section
               → Open session → xem lại data để debug
```

### Flow 5: Kill App (khác End Session)

```
User đóng window hoặc File > Exit
         │
         ├─ Nếu session đang active → auto End Session + save trước
         ├─ Disconnect BLE (ble_disconnect_t)
         ├─ Close serial port
         ├─ Stop all worker threads
         ├─ Cleanup resources
         └─ Exit process (sys.exit)
```

---

## 🖥 User Mode vs Developer Mode

### Tab Visibility

| Tab | User Mode | Developer Mode |
|-----|-----------|----------------|
| Tab 1: Device Info | ✅ Full | ✅ Full |
| Tab 2: Live Tracking | ✅ Full | ✅ Full |
| Tab 3: Config | ✅ User sections | ✅ All sections |
| Tab 4: Calibration | ❌ **Hidden** | ✅ Full |
| Tab 5: Log & History | ✅ Device logs | ✅ All logs |

### Config Tab — Section Visibility

| Config Section | User Mode | Developer Mode |
|----------------|-----------|----------------|
| 👤 Anchor/Tag Layout | ✅ Read | ✅ Read/Write |
| 👤 Time Synchronization | ✅ Read/Write | ✅ Read/Write |
| 👤 Ranging Configuration | ✅ Read/Write | ✅ Read/Write |
| 👤 UWB Basic Info | ✅ Read-only | ✅ Read/Write |
| 🔧 UWB Advanced Config | ❌ Hidden | ✅ Read/Write |
| 🔧 Sensor Fusion (UKF) | ❌ Hidden | ✅ Read/Write |
| 🔧 BLE Connection Params | ❌ Hidden | ✅ Read/Write |
| 🔧 System Commands | ❌ Hidden | ✅ |

### Log Tab — Content Visibility

| Log Content | User Mode | Developer Mode |
|-------------|-----------|----------------|
| Device Logs (INFO/WARN/ERROR) | ✅ | ✅ |
| Device Logs (DEBUG) | ❌ Filtered | ✅ |
| App Internal Logs | ❌ Hidden | ✅ |
| Raw Protocol Logs (TX/RX) | ❌ Hidden | ✅ |
| Session History Browser | ✅ | ✅ |
| Export CSV/TXT | ✅ | ✅ |
| Clear Device Logs | ❌ Hidden | ✅ |
| Advanced Filters | ❌ Hidden | ✅ |

Toggle via dropdown/button in title bar.

---

## 📡 Protocol Dependency

Toàn bộ communication sử dụng **Protobuf** messages qua **HDLC framing**.

| Layer | Module | Location |
|-------|--------|----------|
| Proto defs | `protocol.proto` | `protocol/protos/` |
| Python bindings | `protocol_pb2.py` | `software/common/` |
| HDLC + Parser | `transport.py` | `software/common/` |
| Command factory | `commands.py` | `software/common/` |

Packet flow:
```
ViewModel → CommandFactory.build() → ProtocolService.encode()
         → SerialService.write() → USB → Dongle → BLE → Device

Device → BLE → Dongle → USB → SerialService.read()
       → ProtocolService.decode() → dispatch → ViewModel
```

---

## 📦 Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| PySide6 | ≥ 6.5 | Qt GUI framework |
| pyserial | ≥ 3.5 | Serial/USB communication |
| protobuf | ≥ 4.0 | Protocol buffer runtime |

---

## 🚧 Development Status

- [x] Project layout (MVVM + Repository structure)
- [x] File scaffolding with documentation
- [x] Session persistence design (Repository layer)
- [ ] Models implementation (dataclasses)
- [ ] Repository implementation (session save/load)
- [ ] Services implementation (serial, protocol)
- [ ] Workers implementation (QThread)
- [ ] ViewModels implementation (business logic)
- [ ] Views implementation (PySide6 UI)
- [ ] Theme & styling
- [ ] Icon assets
- [ ] Testing
- [ ] Build & packaging

---

## ✍ Author

**Trung Quan** — UWB RTLS Project  
Date: 2026
