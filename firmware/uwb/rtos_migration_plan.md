# Kế Hoạch Di Chuyển RTOS & Phân Tích Hệ Thống RTLS

Bản tài liệu này trình bày chi tiết kiến trúc phần mềm, luồng dữ liệu và các bước triển khai thực tế khi chuyển đổi hệ thống từ mô hình Polling sang FreeRTOS trên vi điều khiển STM32F4.

---

## 1. Kiến Trúc Task (7 Tasks)

| # | Task Name      | Priority               | Stack (Words) | Entry Function        | Trigger / Cơ chế                                                        |
|---|----------------|------------------------|---------------|-----------------------|-------------------------------------------------------------------------|
| 1 | `UwbRanging`   | `osPriorityRealtime`   | 512           | `uwb_ranging_entry`   | Block trên Semaphore (`g_uwb_isr_semHandle`) nhả từ UWB EXTI với timeout 10ms |
| 2 | `SensorFusion` | `osPriorityHigh`       | 1024          | `sensor_fusion_entry` | `osDelayUntil` (50Hz) + Queue Poll (UWB) — Phối hợp IMU (định kỳ) & UWB (bất định kỳ) |
| 3 | `Network`      | `osPriorityNormal`     | 512           | `network_entry`       | `osDelay(5)` — Parse UART/USB byte stream + Xử lý command               |
| 4 | `Logger`       | `osPriorityBelowNormal`| 512           | `logger_entry`        | Placeholder — Dùng để mở rộng các tính năng gửi log qua mạng sau này    |
| 5 | `FlashStorage` | `osPriorityLow`        | 256           | `flash_storage_entry` | `osDelay(2000)` — Flush log từ RAM vào Internal Flash mỗi 2 giây        |
| 6 | `IO`           | `osPriorityLow`        | 256           | `io_entry`            | Block trên Semaphore (`g_io_btn_semHandle`) — Xử lý Nút nhấn / LED     |
| 7 | `PM`           | `osPriorityLow`        | 256           | `power_manage_entry`  | `osDelay(1000)` — Giám sát Pin và trạng thái năng lượng mỗi 1 giây      |

---

## 2. Phân Tích Luồng Dữ Liệu (Data Flow)

Luồng dữ liệu trong hệ thống được thiết kế theo mô hình **Hybrid Event-Driven** (EXTI chỉ Signal, Task làm việc với SPI) kết hợp với **Periodic Processing**:

```text
[ Hardware Status ]       [ Real-time Tasks ]       [ System Services ]
       |                          |                         |
DW1000 EXTI ------------(Signal)--> UwbRanging Task         |
       |                          | (Dưới SPI Mutex)        |
       |                   (Ranging Queue) ------> SensorFusion Task (UKF)
       |                          |                         |
Button ISR --(Semaphore)-->    IO Task                      |
       |                          |                         |
       |                   [ RLOG_* Macros ]                |
       |                          |                         |
       |                   RAM Circular Buffer <---(2s)-- FlashStorage Task
       |                          |                         |
       |                   Network Task <-------------------/
```

---

## 3. Ước Tính Tài Nguyên & CPU Timing (Verified @ 84 MHz)

### 3.1. RAM Usage (SRAM 128KB)
*   **FreeRTOS Heap (Cấp phát động Stack/TCB/Primitives):** ~20 KB (Bao gồm Stacks của 7 task khoảng 12.8KB, TCBs, queues và mutexes).
*   **Shared Log Buffer (RAM):** 4 KB (Được cấu hình bởi `MEM_SHARED_LOG_RAM_SIZE` trong code mới, tối ưu hơn so với dự tính 16KB cũ).
*   **UKF Core State:** ~4 KB
*   **Tổng cộng:** ~30 KB (**~23%**). Hệ thống cực kỳ tối ưu về RAM, còn trống ~98 KB cho các tính năng mở rộng (như BLE Stack).

### 3.2. CPU Timing & Budget
| Hoạt động / Task       | Thời gian thực tế / Ước tính | Chu kỳ | Tải CPU |
|-------------------------|-----------------------------|--------|---------|
| **UKF Predict**         | 2.0 ms (Measured)           | 20 ms  | 10.0%   |
| **UKF Update**          | 3.0 - 3.5 ms (Measured)     | 20 ms  | 17.5%   |
| **UwbRanging**          | 1.0 - 2.0 ms                | ~70 ms | 2.5%    |
| **FlashStorage (Write)**| 2.0 - 5.0 ms                | 2000 ms| < 0.1%  |
| **Network (Parsing)**   | 0.5 - 1.0 ms                | 5 ms   | ~10.0%  |
| **PM (Battery ADC)**    | 0.1 - 0.2 ms                | 1000 ms| < 0.1%  |
| **IO (Debounce/LED)**   | < 0.1 ms                    | 100 ms | < 0.1%  |
| **Tổng cộng**           |                             |        | **~40-45%** |

> [!WARNING]
> **Lưu ý đặc biệt về Flash (STM32F4)**: Thao tác **Erase Sector** (400ms - 800ms) của Internal Flash sẽ **khóa toàn bộ bus truy cập Flash của CPU (CPU Stall)** vì CPU nạp lệnh từ Flash. Lúc này, **độ ưu tiên của RTOS (Priority) không thể cứu được TDMA slot** vì toàn bộ CPU bị treo cứng tạm thời.
> **Giải pháp:** Runtime tuyệt đối **không thực hiện Erase khi đang chạy ranging**. Hệ thống chỉ ghi append dữ liệu nhỏ vào RAM/Flash, việc Erase Sector chỉ được thực hiện trong các safe window (khi bắt đầu boot, khi dừng ranging hoặc thông qua lệnh soft-stop).

---

## 4. Các RTOS Primitives (Queues, Mutexes, Semaphores)

Để đảm bảo các task giao tiếp an toàn và không tranh chấp tài nguyên, hệ thống cần các đối tượng RTOS sau:

### 4.1. Queues (Hàng đợi dữ liệu)
| Tên Queue | Kích thước | Kiểu dữ liệu | Mục đích |
|-----------|------------|--------------|----------|
| `g_uwb_distance_queue` | 4 items | `uwb_distance_msg_t` | Truyền dữ liệu ranging đa anchor (chứa `distances`, `anchor_ids`, `mask`, `timestamps`, `quality`) từ Task Ranging sang Task SensorFusion để giữ nguyên ngữ cảnh lọc (Innovation Gating). |
| `g_net_cmd_queue` | 8 items | `net_cmd_t` | (Mở rộng) Lưu các lệnh từ Network task chờ xử lý. |

### 4.2. Mutexes (Loại trừ tương hỗ - Bảo vệ tài nguyên)
| Tên Mutex | Tài nguyên bảo vệ | Ghi chú |
|-----------|-------------------|---------|
| `g_spi1_mutexHandle` | **SPI1 Bus** (UWB & IMU) | **Cực kỳ quan trọng**. Bảo vệ toàn bộ giao tiếp SPI1, đảm bảo đồng bộ hoàn toàn giữa Ranging và SensorFusion. |
| `g_logger_mutexHandle`| **RAM Buffer & vsnprintf**| Tránh corrupt log khi nhiều task cùng gọi RLOG đồng thời. |
| `g_ukf_mutexHandle`   | **g_ukf_state structure** | Cho phép Pre-filter truy cập vị trí dự đoán để tính Innovation Gating. |
| `g_config_mutexHandle`| **System Config Structure**| Bảo vệ cấu hình khi Task Network ghi và Task Ranging đọc. |

### 4.3. Semaphores (Tín hiệu điều khiển)
| Tên Semaphore | Loại | Trigger | Task nhận |
|---------------|------|---------|-----------|
| `g_uwb_isr_semHandle` | Binary | DW1000 EXTI ISR | `UwbRanging` (Chỉ nhả từ EXTI, không thực hiện SPI trong ISR) |
| `g_io_btn_semHandle`  | Binary | Button EXTI ISR | `IO` (Wakeup để debounce và xử lý SM) |
| `g_logger_semHandle` | Binary | Lệnh ghi Log mới | `Logger` (Wakeup để đẩy log ra USB/UART) |

---

## 5. Quản Lý Tài Nguyên & Đồng Bộ Hóa

### 5.1. Quản lý Bus SPI (SPI1) & Cơ chế Đồng bộ hóa Ngắt (Hybrid Event-Driven)
Để đảm bảo **không xung đột với IMU** (cũng chạy trên `SPI1`) và đạt hiệu quả CPU cao nhất dưới FreeRTOS mà không bị "starve" các task khác, hệ thống áp dụng cơ chế **Hybrid Event-Driven**:
*   **EXTI chỉ phát tín hiệu (Signal only):** Chân ngắt UWB IRQ (PA4) kích hoạt EXTI ISR. Bên trong ISR của STM32, ta tuyệt đối **không thực hiện bất kỳ giao tiếp SPI nào** (không gọi `dwt_isr()`, không đọc status/rx_data/tx_ts). Hàm ngắt chỉ làm một nhiệm vụ duy nhất là nhả Semaphore:
    `osSemaphoreRelease(g_uwb_isr_semHandle)`.
*   **Xử lý hoàn toàn trong Task (Dưới SPI Mutex):** Task `UwbRanging` ở trạng thái Blocked để chờ Semaphore này với **Timeout 10ms** (để tự phục hồi nếu bị lỡ ngắt). Khi Semaphore được nhả hoặc hết timeout, Task sẽ thực hiện:
    1. Lock SPI Mutex: `osMutexAcquire(g_spi1_mutexHandle, osWaitForever)`.
    2. Gọi hàm xử lý ngắt chính thức của Decawave: `dwt_isr()`. Hàm này sẽ đọc ghi SPI, tự động phân tích cờ trạng thái `SYS_STATUS` và kích hoạt các callback tương ứng (`uwb_rx_cb` / `uwb_tx_cb`). Vì các callback này chạy trong Task Context, toàn bộ quá trình đọc dữ liệu/timestamp và re-arm RX qua SPI đều được bảo vệ an toàn tuyệt đối.
    3. Unlock SPI Mutex: `osMutexRelease(g_spi1_mutexHandle)` ngay sau khi kết thúc chu kỳ xử lý.
*   **Kết quả:** Bus SPI1 được bảo vệ 100%, loại bỏ hoàn toàn lỗi re-entrancy SPI từ ISR. Task `SensorFusion` (IMU) và các tác vụ khác hoạt động trơn tru không bị nghẽn hay giật lag.

### 5.2. Innovation Gating & Pre-filter
*   **Logic:** Trước khi nạp Distance vào UKF, cần kiểm tra: `abs(Measured_Dist - Predicted_Dist) < Threshold`.
*   **Shared Memory:** `g_ukf_state` lưu trữ trạng thái x, y, z hiện tại. Logic Pre-filter đọc dữ liệu này qua `g_ukf_mutexHandle` để thực hiện lọc dữ liệu lỗi (Outlier rejection) trước khi Update.

---

## 6. Xử Lý Lỗi & Phục Hồi Hệ Thống

1.  **Kẹt trạng thái DW1000:**
    *   Trong cơ chế Smart Polling, nếu DW1000 gặp lỗi hoặc kẹt trạng thái khiến chân IRQ không lên HIGH quá 10ms:
    *   Hành động: Task `UwbRanging` sẽ chủ động quét cưỡng bức (Force Read) thanh ghi `SYS_STATUS` để xóa cờ lỗi, hoặc reset cứng DW1000 và tái khởi động RX (`dwt_rxenable`).
2.  **I2C/SPI Timeout:**
    *   Sử dụng timeout ngắn (5ms) cho các lệnh HAL để tránh treo Task PM hoặc SensorFusion nếu sensor bị lỗi phần cứng.
3.  **Flash Write Protection:**
    *   Task FlashStorage kiểm tra dung lượng trống trước khi ghi. Nếu RAM buffer đầy, dữ liệu cũ nhất sẽ bị ghi đè thay vì làm treo hệ thống.

---

## 7. Thứ Tự Khởi Tạo (Hardware Boot Sequence)

Quy trình khởi động được đồng bộ chặt chẽ từ nhánh `develop`, phân tách rõ ràng giữa **Khởi tạo phần cứng/hệ thống cơ bản (Pre-kernel)** và **Lập lịch chạy tác vụ (Task Runtime)**:

### 7.1. Khởi tạo trước hệ điều hành (Pre-kernel Init)
1.  `sys_config_init()`: Đọc cấu hình hệ thống từ Flash.
2.  `bsp_util_init()`: Khởi tạo các bộ định thời/delay cơ bản.
3.  `sys_flash_storage_init()`: Khởi tạo phân vùng lưu trữ Flash.
4.  `sys_logger_init()`: Khởi tạo bộ đệm và hệ thống ghi Log (RAM Shared buffer 4KB).
5.  `bsp_io_init()`: Cấu hình các chân GPIO cho LED, Nút nhấn và DIP switch.
6.  **`bsp_imu_init()`**: Cấu hình và hiệu chuẩn cảm biến IMU (Bắt buộc trước UWB).
7.  **`bsp_uwb_init()`**: Khởi tạo chip DW1000 (Load LDE microcode).
8.  `sys_pm_init()`: Khởi tạo trình quản lý nguồn và giám sát pin.
9.  `serial_init()` / `network_core_init()` / `network_cmd_init()`: Khởi tạo các bộ parse lệnh UART/USB.
10. `app_tag_init()` / `app_anchor_init()`: Cấu hình ban đầu cho trạng thái Ranging.

### 7.2. Lập lịch chạy tác vụ (Task Runtime)
11. `MX_FREERTOS_Init()`: Khởi tạo các đối tượng RTOS (Mutexes, Queues, Semaphores) và đăng ký các luồng (`osThreadNew` cho 7 tasks).
12. `osKernelStart()`: Khởi động bộ lập lịch FreeRTOS.

## 8. Phân Tích Stack chi tiết (Stack Analysis)

| Task Name      | Stack (Words) | Lý do cấu hình |
|----------------|---------------|----------------|
| `SensorFusion` | 1024          | Thuật toán UKF dùng nhiều ma trận và FPU context saving tốn stack. |
| `UwbRanging`   | 512           | Driver DecaWave có các cấu trúc config lớn, cần đệm đủ. |
| `Network`      | 512           | Giải mã Protobuf (NanoPB) và xử lý chuỗi command. |
| Các task khác  | 256           | Tác vụ đơn giản, không dùng đệ quy hoặc biến cục bộ lớn. |

---

## 9. Chỉ Số Latency & Hiệu Năng Mục Tiêu

*   **Interrupt Latency:** Mục tiêu < 50µs (Thời gian phản hồi từ khi chân UWB IRQ EXTI kích hoạt đến khi Task `UwbRanging` được đánh thức thông qua `g_uwb_isr_semHandle`).
*   **Ranging Update Rate:** 15Hz - 20Hz (Bị giới hạn bởi timing của giao thức UWB).
*   **Position Latency:** < 100ms (Từ lúc có ngắt UWB đến khi UKF xuất tọa độ).
*   **Flash Write Latency:** < 50ms (Không được gây trễ cho các task khác nhờ được bảo vệ bằng cơ chế append-only và erase trong safe-window).

---

## 10. Chiến Lược Debug & Giám Sát

1.  **SystemView / TraceALYZER:** Sử dụng để quan sát đồ thị chạy của các Task, phát hiện Task nào đang chiếm CPU quá mức hoặc bị Starvation.
2.  **Runtime Stats:** Kích hoạt `configGENERATE_RUN_TIME_STATS` để xem % CPU của từng task qua command `sys stats`.
3.  **Stack Overflow Hook:** Luôn bật `configCHECK_FOR_STACK_OVERFLOW = 2` để treo hệ thống và báo lỗi ngay khi có task tràn stack.

> [!NOTE]
> **Tối ưu hóa băng thông & giảm thiểu nghẽn Log (Logging Rate Limitation)**:
> Để tránh gây nghẽn hàng đợi ghi Flash hoặc nghẽn luồng truyền tin thời gian thực trong quá trình Ranging, hệ thống sẽ **xóa bỏ hoàn toàn các dòng LOG được lặp lại nhiều lần với tần suất cao** (ví dụ: Log kết quả từng chu kỳ ranging đơn lẻ). Toàn bộ log trạng thái định kỳ sẽ được chuyển đổi sang dạng **Monitor** (tổng hợp thống kê số liệu và chỉ gửi/ghi log sau mỗi vài chục giây hoặc vài phút).

