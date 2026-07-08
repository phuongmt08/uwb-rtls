# DW1000 diagnostics & ranging accuracy — đối chiếu app note Decawave

Tài liệu này đối chiếu firmware hiện tại với các app note chính thức của
Decawave/Qorvo (DW1000 User Manual §4.7, APS006, APS011) và liệt kê những gì
firmware nên bổ sung để cải thiện độ chính xác. Mục nào đã triển khai trong
repo được đánh dấu DONE.

Nguồn tham khảo:

- DW1000 User Manual §4.7.1–4.7.2 (công thức FP power / RX power).
- APS006 (Part 2): channel diagnostics, phân biệt LOS/NLOS bằng chênh lệch
  RX level − FP level.
- APS011: Sources of error in TWR schemes — clock drift và range bias theo
  received signal level (RSL).

## 1. Công thức chuẩn từ User Manual

DW1000 cung cấp diagnostics cho mỗi frame nhận:

| Thanh ghi | Trường | Ý nghĩa |
|---|---|---|
| RX_TIME offset 0x7 | `FP_AMPL1` (F1) | Biên độ CIR tại first-path index |
| RX_FQUAL offset 0x0 | `STD_NOISE` | Độ lệch chuẩn noise của LDE |
| RX_FQUAL offset 0x2 | `FP_AMPL2` (F2) | Biên độ first path, mẫu +1 |
| RX_FQUAL offset 0x4 | `FP_AMPL3` (F3) | Biên độ first path, mẫu +2 |
| RX_FQUAL offset 0x6 | `CIR_PWR` (C) | Tổng công suất CIR |
| RX_FINFO | `RXPACC` (N) | Số symbol preamble tích lũy |

Công suất first path và công suất thu:

```text
FP_POWER [dBm] = 10*log10((F1^2 + F2^2 + F3^2) / N^2) - A
RX_POWER [dBm] = 10*log10((C * 2^17) / N^2) - A

A = 113.77 (PRF 16 MHz) | 121.74 (PRF 64 MHz)
```

Lưu ý từ User Manual: khi tính công suất *tuyệt đối*, `N` phải được hiệu
chỉnh saturation (RXPACC bão hòa khi preamble dài — dùng RXPACC_NOSAT để bù).
Firmware của ta cấu hình PRF runtime (16 hoặc 64 MHz từ `sys_config`), nên
hằng `A` phải chọn theo cấu hình đang chạy nếu cần giá trị dBm tuyệt đối.

## 2. Chỉ báo NLOS: RX level − FP level (APS006) — DONE (phase log)

APS006 khuyến nghị dùng chênh lệch giữa công suất thu tổng và công suất
first path làm chỉ báo NLOS:

```text
delta = RX_POWER - FP_POWER
      = 10*log10( (C * 2^17) / (F1^2 + F2^2 + F3^2) )   [dB]

delta < ~6 dB   → khả năng cao LOS
delta > ~10 dB  → khả năng cao NLOS
```

Điểm mấu chốt: trong hiệu số này `N` (RXPACC) và `A` **triệt tiêu nhau**,
nên delta không cần hiệu chỉnh saturation, không phụ thuộc PRF constant, và
gần như bất biến theo khoảng cách. Nó phân biệt được "first path yếu vì xa"
(cả RX lẫn FP cùng giảm → delta không đổi) với "first path yếu vì bị che"
(FP giảm mạnh hơn RX → delta tăng) — điều mà `fp_amp_norm` (chia RXPACC) và
`fp_snr` (chia noise floor) đều không làm được.

Trạng thái triển khai (log-only, chưa tham gia weighting):

```text
bsp_uwb.c        đọc CIR_PWR, tính rx_fp_delta_db_q8 mỗi frame RX
sys_ranging.c    anchor gộp POLL/FINAL bằng max (giữ leg xấu hơn),
                 gửi trong RESULT message (OTA format đổi — phải nạp lại
                 đồng thời anchor và tag)
app_tag.c        đưa vào uwb_distance_msg_t.rx_fp_delta_db[]
freertos.c       gắn vào mw_tril_anchor_t.rx_fp_delta_db (diagnostic)
sys_sensor_fusion.c  snapshot + xuất qua fusion log frame
bsp_io.c         frame log thêm rx_fp_delta_db[4] ở cuối (length byte
                 phân biệt format cũ/mới)
Python tool      parser hỗ trợ cả hai format; cột dlt1..dlt4 trong log
```

Bước tiếp theo (calibration trước, weighting sau):

1. Thu log LOS ở nhiều khoảng cách/hướng → histogram delta theo anchor.
2. Thu log NLOS có chủ đích (người đứng chắn, vách) → xác nhận hai phân bố
   tách nhau quanh 6–10 dB như APS006 mô tả.
3. Khi đã có ngưỡng theo môi trường thật, map delta vào `q_fp`
   (ví dụ sigmoid quanh ngưỡng LOS/NLOS) thay cho — hoặc nhân với —
   weight biên độ hiện tại. Không đưa vào weighting trước khi có bước 1–2.

## 3. Range bias theo mức tín hiệu thu (APS011 §3) — CHƯA LÀM, ưu tiên cao

APS011 chỉ ra timestamp của DW1000 bị lệch phụ thuộc mức tín hiệu tới chip:
tín hiệu mạnh → timestamp sớm (đo ngắn lại), tín hiệu yếu → đo dài ra. Sai
số **hệ thống** này lên tới hàng chục cm — lớn hơn nhiều so với nhiễu ngẫu
nhiên mà UKF đang xử lý:

Trích Table 2 (APS011, kênh 500 MHz, đã calibrate antenna delay để zero
point tại −81 dBm/PRF16 và −77 dBm/PRF64):

| RSL (dBm) | Bias PRF 16 (cm) | Bias PRF 64 (cm) |
|---|---|---|
| −61 | −19.8 | −11.0 |
| −65 | −17.9 | −10.0 |
| −69 | −14.3 | −8.2 |
| −73 | −10.9 | −5.1 |
| −77 | −5.9 | 0.0 |
| −81 | 0.0 | 3.5 |
| −85 | 6.5 | 4.9 |
| −89 | 9.7 | 7.1 |
| −93 | 11.0 | 8.1 |

Cách áp dụng: `Actual = Reported − RangeBiasCorrection(RSL)`.

Đề xuất triển khai:

1. Tính `RX_POWER` (cần `A` theo PRF và hiệu chỉnh RXPACC saturation —
   khác với delta ở mục 2, ở đây cần giá trị tuyệt đối).
2. Nội suy tuyến tính trong bảng RSL→bias (chọn cột theo PRF đang chạy),
   áp vào `distance_m` ngay sau `calculate_distance()` trong
   `sys_ranging.c`, trước khi RESULT rời anchor.
3. Lưu ý tương tác với antenna delay calibration: bảng của Decawave giả
   định zero point tại −81/−77 dBm; quy trình A2A calibration hiện tại của
   ta chỉnh antenna delay tại một khoảng cách cố định, tức là tự đặt zero
   point tại RSL của khoảng cách đó. Khi thêm bảng bias phải calibrate lại
   antenna delay *sau khi* bật correction, nếu không sẽ bù hai lần.
4. EVK1000 dùng TX −41.3 dBm/MHz và anten 0 dB; board của ta khác gain →
   bảng cần dịch theo trục RSL bằng dữ liệu đo thật (fit offset là đủ,
   dạng đường cong giữ nguyên).

Đây là mục có tỉ lệ lợi ích/chi phí tốt nhất còn lại về độ chính xác tuyệt
đối: sai số hệ thống ±10–20 cm hiện không được mô hình nào trong pipeline
(prefilter/UKF) xử lý, vì nó không phải nhiễu ngẫu nhiên và không phải NLOS.

## 4. Clock drift & frequency drift (APS011 §2) — kiểm tra lại, rủi ro thấp

- TWR một chiều nhạy với clock offset; DS-TWR 3 message (đang dùng) đã khử
  phần lớn. APS011 nhấn mạnh **frequency drift trong lúc crystal warm-up**
  (vài giây đầu sau power-on) vẫn gây sai số — tránh dùng range trong vài
  giây đầu sau khi thiết bị wake, hoặc để radio ổn định trước khi tin số liệu.
- Giữ response delay ngắn và **đối xứng** giữa hai phía (TDMA slot hiện tại
  cố định — đạt); mọi thay đổi slot timing nên giữ tính đối xứng này.
- DW1000 có xtal trim (`dwt_setxtaltrim`); nếu log cho thấy carrier
  integrator lệch lớn giữa các node, trim lại giúp giảm cả drift lẫn
  packet loss. Chỉ làm khi có bằng chứng từ log.

## 5. Các mục nhỏ khác từ app note

- **RXPACC saturation (User Manual §4.7.1):** khi dùng công suất tuyệt đối
  (mục 3), đọc thêm RXPACC_NOSAT và trừ chênh lệch trước khi chia N².
  Với preamble 64–128 hiện dùng, mức bão hòa thấp nhưng vẫn nên bù đúng.
- **`fp_snr` hiện tại** (`fp_sum/std_noise`): không có trong app note nào —
  đây là metric tự chế. Giữ log-only đúng như quyết định trong
  `firmware_positioning_solution.md`; delta ở mục 2 là bản thay thế có căn
  cứ. Có thể bỏ hẳn `fp_snr` khỏi weighting roadmap.
- **STD_NOISE/MAX_NOISE ratio:** APS006 dùng thêm tỉ số max_noise/std_noise
  như chỉ báo chất lượng ước lượng noise. Ta đã đọc cả hai thanh ghi —
  có thể log thêm tỉ số này miễn phí khi cần phân tích sâu.
- **Không dùng delta/diagnostics của frame lỗi:** chỉ đọc diagnostics khi
  RX_OK (code hiện tại đã đúng — `capture_rx_quality` chỉ chạy trong
  đường event RX OK).

## 6. Thứ tự ưu tiên đề xuất

1. Thu log calibration cho delta NLOS (mục 2) — hạ tầng đã xong, chỉ cần đo.
2. Range bias theo RSL (mục 3) — sai số hệ thống lớn nhất chưa xử lý;
   làm sau khi có RX_POWER tuyệt đối (kèm RXPACC_NOSAT + A theo PRF).
3. Map delta vào `q_fp` sau khi calibration mục 2 hoàn tất.
4. Xtal trim / warm-up guard nếu log cho thấy cần (mục 4).

Links: [DW1000 User Manual (Qorvo forum mirror)](https://forum.qorvo.com/uploads/default/original/1X/83b21451e94b165579319c2a4bd95d4c5c2b451c.pdf) ·
[APS011 Sources of error in TWR (mirror)](https://thetoolchain.com/mirror/dw1000/aps011_sources_of_error_in_twr.pdf) ·
[FP-power-based NLOS mitigation (arXiv 2403.19706)](https://arxiv.org/pdf/2403.19706)
