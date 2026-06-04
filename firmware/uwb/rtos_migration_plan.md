# RTOS Migration Plan

---

## Migration Checklist

### Tasks
- [x] `UwbRanging` — hoàn tất nhưng chưa migrate prefilter logic
- [ ] `SensorFusion` — chưa migrate hoàn tất
- [x] `Network` — hoàn tất
- [x] `FlashStorage` — hoàn tất
- [x] `PM` — hoàn tất
- [x] `SysMonitoring` — hoàn tất
- [x] `IO` — hoàn tất

### RTOS Primitives
- [x] Queue `g_uwb_distance_queue`
- [x] Mutex `g_spi1_mutexHandle`
- [x] Mutex `g_logger_mutexHandle`
- [x] Semaphore `g_uwb_isr_semHandle`
- [x] Semaphore `g_io_btn_semHandle`
- [x] Semaphore `g_monitoring_semHandle`
- [x] Semaphore `g_logger_semHandle`
- [ ] Semaphore `g_imu_isr_semHandle` *(placeholder, bsp_imu chưa hỗ trợ)*

### Boot Sequence
- [x] Pre-kernel init (`sys_config_init` → ... → `app_tag/anchor_init`)
- [x] `MX_FREERTOS_Init()` — tạo objects
- [x] `osKernelStart()`

### Performance & Timing Profiling (SEGGER SystemView + J-Link)
- [ ] Đo lường Interrupt Latency (UWB EXTI ISR -> `UwbRanging` Task wakeup): Mục tiêu < 50µs.
- [ ] Đo lường thời gian thực thi (Execution time) của thuật toán:
  - `SensorFusion` Predict (50Hz): Mục tiêu < 2.0ms.
  - `SensorFusion` Update (8-15Hz): Mục tiêu < 2.5ms.
  - `Network` (Protobuf decoding & Command parsing): Mục tiêu < 2.0ms.
- [ ] Đo lường thời gian chờ tranh chấp bus (SPI1 M utex Contention) giữa IMU và UWB: Mục tiêu < 1.0ms.
- [ ] Kiểm tra Jitter chu kỳ chạy của Task `Network` (5ms) và Task `PM` (100ms).
- [ ] Xác minh không xảy ra tình trạng Task Starvation trên các task độ ưu tiên thấp (`PM`, `IO`, `FlashStorage`) dưới áp lực ranging tần suất cao.

---

## Task Architecture

| # | Task | Priority | Stack | Trigger |
|---|---|---|---|---|
| 1 | `UwbRanging` | Realtime | 1536w | Semaphore EXTI DW1000 + timeout động |
| 2 | `SensorFusion` | High | 1024w | Queue poll 20ms + Semaphore IMU |
| 3 | `Network` | Normal | 512w | Định kỳ 5ms |
| 4 | `SysMonitoring` | BelowNormal | 512w | Định kỳ 30s |
| 5 | `FlashStorage` | Low | 512w | Định kỳ 2s |
| 6 | `PM` | Low | 1024w | Định kỳ 100ms |
| 7 | `IO` | Low | 512w | Semaphore EXTI Button + Timeout 100ms (Led/Debounce) |

---

## Data Flow

```
ICM-42688 ──(20ms)──────────────────► SensorFusion (Predict 50Hz)
DW1000 ────(EXTI ISR → Semaphore)──► UwbRanging
                                           │ SPI1 Mutex
                                     [Mahalanobis Filter]
                                           │
                                   g_uwb_distance_queue
                                           ▼
                                     SensorFusion (Update 8-15Hz)

Button EXTI ──(Semaphore / 100ms Timeout)──► IO Task
Any Task ────(RLOG_* → g_logger_semHandle)──► RAM Circular Buffer ──(2s flush)──► FlashStorage
UART/USB ───(5ms)──────────► Network Task
```
### Prefilter & Sorting logic
- **Mahalanobis Gating**:
  - Hoạt động trên task `UwbRanging` sau khi ranging thành công và thu thập được >= 3 distances.
  - Sử dụng dữ liệu trạng thái dự đoán từ biến toàn cục `ukf_data` (px, py, vx, vy) để lọc bỏ outliers.
- **Sorting to get best triplet anchors**:
  - Hoạt động trên task `UwbRanging` sau khi `Mahalanobis Gating` lọc xong, tiến hành sort để chọn bộ 3 anchors tốt nhất rồi push vào `g_uwb_distance_queue`.


### UKF Predict vs Update (SensorFusion Task)

| | Predict | Update |
|---|---|---|
| Trigger | Timeout 20ms hoặc `g_imu_isr_semHandle` | Nhận từ `g_uwb_distance_queue` |
| Rate | 50 Hz | 8–15 Hz |
| Output | $p_x, p_y, v_x, v_y, \theta$ | Bias $b_{ax}, b_{ay}, b_{gz}$ |


---


## Resources

### RAM (SRAM 128 KB physical, 92 KB application partition)

> [!NOTE]
> Tổng dung lượng RAM vật lý của chip là **128 KB**. Tuy nhiên, trong file Linker Script (`STM32F411CEUX_FLASH.ld`), bộ nhớ được chia thành:
> *   `RAM` (Application workspace): **92 KB** (từ `0x20000000` đến `0x20017000`) - đây là không gian chứa Heap và Globals.
> *   `LOG_RAM` (Shared Log Buffer): **4 KB** (từ `0x20017000` đến `0x20018000`).
> *   **32 KB còn lại** được dành riêng cho phân vùng Bootloader hoạt động.
> Do đó, trình biên dịch và dashboard đo lường RAM sử dụng dựa trên vùng `RAM` 92 KB này.

| Vùng | Size | Tỷ lệ (trên 92 KB application workspace) |
|---|---|---|
| FreeRTOS Heap | 36.0 KB | 39.1% |
| Globals / BSS / Data | 35.7 KB | 38.8% |
| **Tổng sử dụng (Build stats)** | **71.7 KB** | **77.9% (Hiển thị ~71 / 92 KB - 77% trên dashboard)** |
| **RAM còn trống** | **20.3 KB** | **22.1%** |
| *Shared Log Buffer* | *4.0 KB* | *(Nằm riêng trên phân vùng `LOG_RAM` 4 KB)* |

### CPU Budget & Resource Profiling (Cortex-M4 @ 96 MHz, Hardware FPU Single-Precision)

Để đảm bảo đáp ứng thời gian thực nghiêm ngặt và tính an toàn hệ thống (safety margin), ta phân bổ tài nguyên CPU dự phòng dựa trên **phân tích định lượng worst-case** khi kích hoạt thuật toán UKF & Ranging đầy đủ:

#### Bảng Phân Bổ Tải CPU Dự Phòng (CPU Budget Allocation)

| Task Name | Hoạt động chính | Tải CPU Mục Tiêu / Dự Phòng (Worst-Case Target) | Đặc thù tính toán & Lý do phân bổ tải |
| :--- | :--- | :---: | :--- |
| **IDLE** | - | **63% ~ 74%** | CPU chuyển sang Sleep Mode để giảm mức tiêu thụ dòng điện. |
| **SensorFusion** | UKF Predict (50Hz) + Update (12Hz) | **15% ~ 20%** | **Ước lượng worst-case cẩn trọng**: Tính toán ma trận UKF, Cholesky, phép toán lượng giác 64-bit giả lập, in log dữ liệu UART định dạng float, và tiền lọc Mahalanobis prefilter. |
| **UwbRanging** | TDMA Ranging + TWR Math | **7% ~ 12%** | overhead giao tiếp SPI polling/blocking với DW1000, context switch FreeRTOS dồn dập, phép tính double TOF, và stuck IRQ recovery (Worst-case lên tới 12%). |
| **Network** | Protobuf decoding & parsing | **3% ~ 5%** | Giải mã gói tin Nanopb định kỳ 5ms, USB CDC và BLE Bridge. |
| **Logger / PM / Others** | Quản lý nguồn & Dọn dẹp | **1%** | Giám sát Stack/Heap/CPU định kỳ 30s và bảo vệ nguồn của PM. |
| **Tổng cộng CPU** | **Hệ thống chạy thuật toán** | **26% ~ 37%** | **Tổng tải dự phòng cực kỳ an toàn** (< 50% đảm bảo tính thời gian thực tối ưu). |

#### Phân Tích Định Lượng Thời Gian Thực Thi (Execution Profiling)

##### A. Tại sao Task `UwbRanging` dự kiến chiếm từ 7% - 12% CPU?
Mặc dù tần số Ranging chỉ khoảng 10-15Hz, nhưng mỗi chu kỳ Ranging là một chuỗi các sự kiện dồn dập (1 POLL TX $\rightarrow$ 3 RESP RX $\rightarrow$ 1 FINAL TX $\rightarrow$ 3 RESULT RX):
1. **SPI Polling Overhead**: Giao tiếp SPI với DW1000 không dùng DMA cho các giao dịch nhỏ (đọc timestamp, đọc status register, ghi antenna delay) để tránh overhead thiết lập DMA. Mỗi giao dịch SPI Polling này sẽ block CPU trong các vòng lặp chờ cờ SPI TXE/RXNE.
   - Mỗi chu kỳ TWR thực hiện khoảng **120 giao dịch SPI ngắn**.
   - Mỗi lần Polling block CPU khoảng **50 $\mu s$**. Ở 15Hz: $15 \times 120 \times 50 \mu s = 90\text{ms} / s$ CPU bị block $\rightarrow \mathbf{9.0\% \text{ CPU}}$.
2. **Overhead đổi ngữ cảnh (Context Switching)**: 
   - Với khoảng **120 ngắt EXTI mỗi giây**, FreeRTOS mất khoảng $120 \times 2 \times 3 \mu s \approx \mathbf{0.72\text{ms}}$ cho việc context switch.
3. **TOF Math Emulation**: 
   - Phép tính toán hiệu chỉnh TOF sử dụng kiểu dữ liệu `double` 64-bit để tránh tràn số timestamp 40-bit của DW1000. Do chip chỉ có FPU đơn độ chính xác (32-bit), các phép toán 64-bit phải giả lập bằng phần mềm (software emulation), tốn khoảng **400 chu kỳ máy** cho mỗi phép tính.
4. **Stuck IRQ Recovery (Worst-case 12%)**:
   - Khi có nhiễu làm mất ngắt DW1000, driver phải chạy cơ chế recovery cưỡng bức quét chân PA4, tốn thêm **3% ~ 5%** CPU.

##### B. Tại sao Task `SensorFusion` (UKF) cần dự phòng tới 15% - 20% CPU?
UKF là thuật toán cực nặng về tính toán ma trận. Nếu code rơi vào các cạm bẫy hiệu năng, tải CPU sẽ tăng vọt:
1. **Cạm bẫy giả lập 64-bit (Double Precision Emulation)**:
   - Nếu thư viện ma trận dùng chung hoặc code UKF vô tình sử dụng kiểu dữ liệu `double` (hoặc gọi các hàm lượng giác chuẩn như `sin()`, `cos()`, `sqrt()` thay vì `sinf()`, `cosf()`, `sqrtf()`):
   - Một phép tính lượng giác/căn bậc hai 64-bit giả lập bằng phần mềm tốn tới **1,200 - 1,500 chu kỳ máy** (so với 1-15 chu kỳ nếu dùng FPU phần cứng float 32-bit).
   - Với khoảng 1,200 phép toán trong toàn bộ chu trình UKF Predict + Update, việc dùng `double` sẽ cắn tới **1.44 triệu chu kỳ máy** mỗi lần chạy.
   - Tại 96MHz, thời gian CPU thực thi cho mỗi bước Update là:
     $$\text{Execution Time} = \frac{1,440,000}{96,000,000 \text{ Hz}} \approx \mathbf{15\text{ms}}$$
   - Tại tần số Update 12Hz, tải CPU thực tế của riêng UKF sẽ là:
     $$\text{CPU Load} = 12 \text{ Hz} \times 15\text{ms} = 180\text{ms} / s \rightarrow \mathbf{18.0\% \text{ CPU}}!$$
2. **In Log Dữ Liệu UART Định Dạng `%f`**:
   - Việc định dạng chuỗi số thực dấu phẩy động (`printf` hoặc RLOG với các tham số tọa độ `ukf_data.px, ukf_data.py`) tốn rất nhiều tài nguyên CPU, mất khoảng **5,000 - 8,000 chu kỳ máy** cho mỗi chuỗi. Nếu in log liên tục ở tần số 15Hz, overhead này sẽ ngốn thêm **1.5% - 2.5% CPU**.

##### Chỉ Thị Bắt Buộc Để Kiểm Soát Tải CPU của UKF:
* **FPU Optimization**: Ép buộc sử dụng kiểu dữ liệu `float` (32-bit single precision) và các hàm toán học tối ưu phần cứng (`sinf`, `cosf`, `sqrtf`) trong toàn bộ logic tính toán ma trận UKF. Tuyệt đối tránh sử dụng kiểu `double` 64-bit để không kích hoạt thư viện giả lập phần mềm.
* **Sequential Measurement Update**: Thay vì thực hiện cập nhật đồng thời cả 3 Anchor (đòi hỏi nghịch đảo ma trận đo đạc $3 \times 3$ rất nặng), ta sẽ cập nhật tuần tự từng Anchor một để biến phép nghịch đảo ma trận thành phép chia số thực $1 \times 1$.

---

## IMPLEMENTATION NOTE

- **ISR**: Chỉ thực hiện nhả semaphore từ ISR, tuyệt đối không gọi các hàm read/write SPI/I2C trực tiếp trong ISR.
- **SPI1**: `UwbRanging` chiếm `g_spi1_mutexHandle` trước khi gọi `dwt_isr`. IMU tranh chấp mutex gián tiếp thông qua hàm CS pin control `bsp_cs_set()`.
- **Stack Overflow**: `configCHECK_FOR_STACK_OVERFLOW = 2` bắt buộc để phát hiện sớm lỗi tràn stack.
- **SysMonitoring**: Được phân tách làm 2 phần in ra định kỳ 30s:
  - *Giám sát % Stack & Heap*: Tính toán động và in phần trăm sử dụng hiện tại và đỉnh (peak) của Heap, cùng với % stack đã sử dụng thực tế của mỗi Task (dựa trên High Water Mark và kích thước khởi tạo ban đầu) để hỗ trợ tối ưu hóa dung lượng RAM.
  - *Giám sát % CPU block*: Đã triển khai thông qua hàm `bsp_util_print_cpu_stats()`, sử dụng `uxTaskGetSystemState` để in tỷ lệ phần trăm CPU tiêu thụ thực tế của mỗi Task nhằm phát hiện nghẽn hệ thống.
- **Power Management (PM)**:
  - *Sạc*: Ngắt sạc khi `SOC >= 95%` hoặc `TEMP > 60°`C; bật lại khi `SOC < 60%` hoặc `TEMP < 45°C`.
  - *Bảo vệ nguồn*: Kéo chân `PWR_HOLD` xuống mức thấp để tắt nguồn hệ thống nếu `SOC < 5%` hoặc `VBAT < 3000mV` tránh xả kiệt pin.
  - **VDDA Sag**: Sử dụng ngắt Analog Watchdog bắt sụt áp tức thời trên đường `VDDA`.

---

## Schedulability Check & Response Time Analysis (RTA)

Dựa trên lý thuyết lập lịch thời gian thực (Rate Monotonic Scheduling - RMS & Phân tích thời gian đáp ứng - RTA), ta tiến hành đánh giá tính lập lịch được (Schedulability Test) của tập tác vụ (Task Set) trong firmware:

### 1. Phép Kiểm Tra Hệ Số Sử Dụng CPU (CPU Utilization Bound Test)

Theo lý thuyết của **Liu & Layland**, đối với tập gồm $n$ tác vụ có độ ưu tiên tĩnh, nếu tổng hệ số sử dụng CPU ($U$) nhỏ hơn ngưỡng Bound ($U_{bound}$), tập tác vụ đó **chắc chắn lập lịch được**:
$$U = \sum_{i=1}^{n} \frac{C_i}{p_i} \le U_{bound} = n(2^{\frac{1}{n}} - 1)$$

Xét 3 tác vụ cốt lõi trong hệ thống (worst-case execution time):
1. **$T_{Rng}$ (`UwbRanging`)**: $C_{Rng} = 2.0\text{ms}, p_{Rng} = 70\text{ms} \rightarrow U_{Rng} \approx 2.86\%$
2. **$T_{SF}$ (`SensorFusion`)**: $C_{SF} = 2.7\text{ms}, p_{SF} = 20\text{ms} \rightarrow U_{SF} = 13.50\%$
3. **$T_{Net}$ (`Network`)**: $C_{Net} = 0.5\text{ms}, p_{Net} = 5\text{ms} \rightarrow U_{Net} = 10.00\%$

*   **Tổng hệ số sử dụng**: $U = 2.86\% + 13.50\% + 10.00\% = \mathbf{26.36\%}$
*   **Ngưỡng Bound cho $n = 3$ tasks**: $U_{bound} = 3(2^{\frac{1}{3}} - 1) \approx \mathbf{77.98\%}$

$$U = 26.36\% \le U_{bound} = 77.98\% \quad \rightarrow \mathbf{PASS} \text{ (RMS Bound Test Good)}$$

---

### 2. Phân Tích Thời Gian Đáp Ứng Tệ Nhất (Response Time Analysis - RTA)

Trong thực tế, ta không gán độ ưu tiên theo RMS thuần túy (vốn yêu cầu task chu kỳ ngắn nhất `Network` phải có độ ưu tiên cao nhất), mà gán theo **mức độ quan trọng (criticality)**: `UwbRanging` (Realtime) > `SensorFusion` (High) > `Network` (Normal). 

Do đó, ta phải sử dụng công thức **RTA** lặp để tính Worst-Case Response Time ($R_i$) cho từng tác vụ:
$$R_i = C_i + B_i + \sum_{j \in hp(i)} \left\lceil \frac{R_i}{p_j} \right\rceil C_j$$
*(Trong đó $B_i$ là Blocking Time do tranh chấp Mutex tài nguyên dùng chung, $hp(i)$ là tập các task có độ ưu tiên cao hơn $T_i$)*.

#### A. Đối với $T_{Rng}$ (`UwbRanging` - Ưu tiên cao nhất)
*   **Blocking Time $B_{Rng}$**: Tranh chấp bus SPI1 thông qua `g_spi1_mutexHandle` với IMU driver. Thời gian giữ mutex tối đa của IMU là $B_{Rng} = 0.4\text{ms}$.
*   **Tính toán $R_{Rng}$**:
    $$R_{Rng} = C_{Rng} + B_{Rng} = 2.0\text{ms} + 0.4\text{ms} = \mathbf{2.4\text{ms}}$$
*   **Đánh giá**: $R_{Rng} = 2.4\text{ms} \le D_{Rng} = 70\text{ms} \quad \rightarrow \mathbf{PASS}$ (Hoàn thành cực kỳ an toàn).

#### B. Đối với $T_{SF}$ (`SensorFusion` - Ưu tiên trung bình)
*   **Tập ưu tiên cao hơn**: $hp(SF) = \{T_{Rng}\}$
*   **Blocking Time $B_{SF}$**: $0.0\text{ms}$.
*   **Tính toán lặp $R_{SF}$**:
    *   Khởi tạo: $R_{SF}^{(0)} = C_{SF} = 2.7\text{ms}$
    *   Vòng lặp 1: $R_{SF}^{(1)} = 2.7 + \left\lceil \frac{2.7}{70} \right\rceil \times 2.0 = 2.7 + 1 \times 2.0 = 4.7\text{ms}$
    *   Vòng lặp 2: $R_{SF}^{(2)} = 2.7 + \left\lceil \frac{4.7}{70} \right\rceil \times 2.0 = 4.7\text{ms}$ *(Hội tụ)*
*   **Đánh giá**: $R_{SF} = \mathbf{4.7\text{ms}} \le D_{SF} = 20\text{ms} \quad \rightarrow \mathbf{PASS}$ (Đảm bảo hoàn thành trước deadline).

#### C. Đối với $T_{Net}$ (`Network` - Ưu tiên thấp nhất)
*   **Tập ưu tiên cao hơn**: $hp(Net) = \{T_{Rng}, T_{SF}\}$
*   **Tính toán lặp $R_{Net}$**:
    *   Khởi tạo: $R_{Net}^{(0)} = C_{Net} = 0.5\text{ms}$
    *   Vòng lặp 1: $R_{Net}^{(1)} = 0.5 + \left\lceil \frac{0.5}{70} \right\rceil \times 2.0 + \left\lceil \frac{0.5}{20} \right\rceil \times 2.7 = 0.5 + 2.0 + 2.7 = 5.2\text{ms}$
    *   Vòng lặp 2: $R_{Net}^{(2)} = 0.5 + \left\lceil \frac{5.2}{70} \right\rceil \times 2.0 + \left\lceil \frac{5.2}{20} \right\rceil \times 2.7 = 5.2\text{ms}$ *(Hội tụ)*
*   **Đánh giá**:
    $$R_{Net} = \mathbf{5.2\text{ms}} > D_{Net} = \mathbf{5\text{ms}} \quad \rightarrow \mathbf{FAIL!}$$

> [!WARNING]
> **Phát hiện xung đột tài nguyên thời gian thực (Jitter & Missed Deadline)**:
> Do `Network` chu kỳ quá ngắn (5ms) nhưng độ ưu tiên lại được xếp thấp nhất, trong kịch bản tệ nhất khi cả 3 task cùng thức dậy, `Network` sẽ bị trễ (miss deadline) **0.2ms** do bị cướp quyền hoàn toàn bởi `UwbRanging` và `SensorFusion`.

---

### 3. Đề Xuất Giải Pháp Khắc Phục (Optimizations)

Để đảm bảo hệ thống đạt trạng thái 100% lập lịch được và triệt tiêu jitter cho `Network`:

1.  **Tăng chu kỳ Task `Network` lên $10\text{ms}$ hoặc $20\text{ms}$** *(Khuyến nghị)*:
    *   Việc quét dữ liệu UART/USB ở tần số 200Hz (5ms) là không cần thiết đối với thiết bị tag.
    *   Nếu tăng $p_{Net} = 10\text{ms} \rightarrow D_{Net} = 10\text{ms}$. Lúc này, $R_{Net} = 5.2\text{ms} < D_{Net} = 10\text{ms}$ $\rightarrow \mathbf{PASS}$ hoàn hảo!
2.  **Tối ưu hóa WCET $C_{SF}$ của `SensorFusion` xuống dưới $1.5\text{ms}$**:
    *   Sử dụng toán tử lượng giác FPU phần cứng float 32-bit (`sinf`, `cosf`) và Sequential Update.
    *   Nếu $C_{SF} = 1.0\text{ms} \rightarrow R_{Net} = 0.5 + 2.0 + 1.0 = \mathbf{3.5\text{ms}} < D_{Net} = 5\text{ms}$ $\rightarrow \mathbf{PASS}$!