# Firmware positioning solution

## Phạm vi đúng của firmware

Firmware không triển khai WLS multilateration. WLS chỉ là method mô phỏng/offline dùng để kiểm thử, so sánh và đánh giá thuật toán trong thesis.

Trilateration 2D hiện có trong firmware cũng không nên được xem là thuật toán định vị chính. Nó là đường debug/bring-up để:

- tạo nghiệm vị trí sơ bộ khi UKF chưa initialized;
- kiểm tra pipeline ranging có tạo ra hình học hợp lý không;
- kiểm tra UKF update có hoạt động ổn không;
- stream/log một nghiệm hình học dễ quan sát khi debug.

Thuật toán firmware nên tập trung vào:

```text
TDMA ranging
  -> range/quality collection
  -> prefilter thống kê từng anchor
  -> measurement weight từng anchor
  -> chọn layout/range set phù hợp cho UKF
  -> UKF predict/update
  -> debug output: trilateration 2D, raw range, weight, residual, selected anchors
```

Vì vậy, solution này chỉ mô tả phần firmware cần làm cho UKF và prefilter. Không đưa WLS vào firmware roadmap.

## Hiện trạng code (đã triển khai Bước 1–4)

Các module liên quan:

- `app_tag.c`: gom kết quả ranging, đưa `distance_m`, `anchor_id`, `fp_amp_norm`, `quality_valid` vào `g_uwb_distance_queue`. `fp_snr` vẫn có trong packet/log nhưng chỉ là diagnostic thô, không dùng trong thuật toán.
- `freertos.c`: task `SensorFusion` lấy frame range, project range 3D sang 2D, chạy Mahalanobis prefilter, rescue anchor nếu thiếu (gắn cờ `rescued`), tính `measurement_weight` cho từng anchor qua `mw_anchor_compute_weights()`, chọn layout bằng `mw_select_ukf_layout_3()` (WGDOP), gọi trilateration debug và UKF update.
- `mw_filter.c`: Mahalanobis gate với hysteresis theo từng anchor (S vẫn là approximation, xem bên dưới).
- `mw_trilateration.c`: chứa `mw_huber_weight()`, `mw_anchor_compute_weights()` (qM/qFP/qR/sigma_r2 → w), `mw_select_ukf_layout_3()` (weighted information matrix + hysteresis), và trilateration 2D/3D với vai trò debug/init. Hàm composite score cũ `mw_trilateration_select_best()` được giữ lại để tham chiếu nhưng không còn nằm trên đường chạy chính.
- `sys_sensor_fusion.c`: UKF update bằng 3 range đã chọn, `R_ii = clamp(1/w_i, MW_UKF_R_MIN, MW_UKF_R_MAX)` được set trước mỗi `fusion_update()`.
- `bsp_uwb.c`: đọc first-path diagnostics từ DW1000, tạo `fp_amp_norm = (A1+A2+A3)/RXPACC` và `fp_snr = (A1+A2+A3)/(std_noise+1)`. `fp_snr` chưa hiệu chuẩn nên không dùng cho weight.
- Simulation (`software/simulation/src/`): cùng pipeline — `computeMeasurementWeights()` giống hệt firmware, WLS Gauss–Newton làm baseline offline, `selectBestTriplet()` chấm bằng WGDOP, UKF nhận `R_ii = clamp(1/w_i)`.

Các điểm của bản cũ đã được xử lý:

- ~~Chưa có `measurement_weight` riêng cho từng anchor~~ → đã có, kèm `q_mahalanobis`, `q_fp`, `q_residual`, `sigma_r2` trong `mw_tril_anchor_t`.
- ~~Anchor selection composite score~~ → đã thay bằng WGDOP; lỗi cũ đáng chú ý: GDOP penalty từng được tính nhưng không nằm trong score, và penalty `range/15` phạt trùng suy hao khoảng cách.
- ~~UKF R cố định~~ → adaptive theo weight, clamp chống lỗi Cholesky/inverse.
- ~~Rescue anchor được UKF tin ngang anchor sạch~~ → anchor `rescued` bị nhân `sigma_r2` với `MAHALANOBIS_PREFILTER_RESCUE_NOISE_SCALE_MIN` (4.0), nên R của nó tiến sát `MW_UKF_R_MAX`.
- ~~Residual/trilateration bị hiểu nhầm là thuật toán chính~~ → code/comment đã ghi rõ vai trò debug/init.

Điểm còn lại (chưa làm, có chủ đích):

- FP amplitude vẫn dùng threshold cố định (`MW_TRIL_FP_AMP_GOOD`), chưa chuẩn hóa theo khoảng cách — chờ log calibration (Bước 5).
- Mahalanobis gate vẫn dùng covariance xấp xỉ `S ≈ R_gate + k·|v|` — chờ Bước 6. Hệ quả cần biết khi tune: với `T_reject = 7.5`, mọi anchor đã qua gate có `uM = sqrt(d2) ≤ 2.74`, tức `qM ∈ [0.73, 1]` — thành phần Mahalanobis chỉ phân biệt yếu giữa các anchor được chấp nhận; sức phân biệt chính của weight đến từ `qFP`, `sigma_r2(d)` và `qR`. `qM` chủ yếu có tác dụng với anchor rescued (d2 lớn).

## Pipeline firmware đề xuất

Pipeline firmware nên được tổ chức như sau:

```text
1. Range acquisition
   TDMA DS-TWR trả về distance + FP diagnostics theo anchor.

2. Range projection
   Convert range 3D sang planar range nếu UKF dùng mặt phẳng 2D.

3. Per-anchor prefilter
   Mỗi anchor được đánh giá độc lập:
   - validity từ ranging layer
   - Mahalanobis consistency
   - first-path quality
   - distance-dependent variance

4. Frame-level consistency
   Dùng residual/trilateration debug chỉ để phát hiện anchor bất thường khi có đủ redundancy.

5. Measurement weight
   Tạo precision weight cho từng anchor.

6. Layout/range-set selection
   Chọn 3 anchor tốt nhất cho UKF update hiện tại, dựa trên weighted geometry và measurement weight.

7. UKF update
   UKF update bằng 3 range đã chọn, với R adaptive theo weight.

8. Debug outputs
   Xuất trilateration 2D, selected anchors, d2, FP quality, weight, UKF state để kiểm chứng.
```

## Data model cần thêm

Mở rộng `mw_tril_anchor_t` để mỗi anchor mang đủ dữ liệu prefilter:

```c
typedef struct {
    vec3d_t position;
    double distance;
    uint8_t id;
    bool valid;

    double d2_score;
    double r_adaptive;
    double fp_amp_norm;
    bool quality_valid;

    double q_mahalanobis;
    double q_fp;
    double q_residual;
    double sigma_r2;
    double measurement_weight;

    double layout_score;
    double debug_residual;
    double debug_tril_rms;
} mw_tril_anchor_t;
```

Ý nghĩa:

- `q_mahalanobis`: soft confidence từ innovation/d2.
- `q_fp`: soft confidence từ first-path diagnostics.
- `q_residual`: confidence từ residual trong frame, chỉ dùng khi đủ anchor.
- `sigma_r2`: phương sai range ước lượng.
- `measurement_weight`: precision weight cuối cùng.
- `layout_score`, `debug_residual`, `debug_tril_rms`: phục vụ chọn layout và debug, không đại diện cho thuật toán định vị chính.

## Measurement weight

Với mỗi anchor `i`:

```text
uM_i = sqrt(d2_i)
qM_i = huber(uM_i, cM)

qA_i = first_path_weight(anchor_i)
qR_i = residual_weight(anchor_i)

w_i = qM_i * qA_i * qR_i / (sigma_r2(d_i) + eps)
w_i = clamp(w_i, w_min, w_max)
```

Giai đoạn đầu có thể dùng:

```text
sigma_r2(d_i) = r_adaptive_i
```

vì firmware đã tính `r_adaptive` trong Mahalanobis prefilter.

Nếu `r_adaptive` chưa ổn định, dùng model đơn giản:

```text
sigma_r2(d) = R_base * (1 + k_dist * d^2)
```

Không nên dùng distance như một penalty độc lập kiểu `range / 15`. Distance nên đi vào phương sai, vì range xa không phải outlier; nó chỉ thường kém chính xác hơn.

Anchor được rescue không có `r_adaptive` hợp lệ (prefilter chỉ ghi khi accept), và `qM` một mình không đủ kéo weight xuống (ví dụ `d2 = 9` chỉ cho `qM ≈ 0.67`). Vì vậy anchor rescued bị nhân thêm:

```text
sigma_r2 *= MAHALANOBIS_PREFILTER_RESCUE_NOISE_SCALE_MIN   (= 4.0)
```

để `R_ii` của nó tiến sát `MW_UKF_R_MAX` — rescue giữ frame sống nhưng không được tin.

## Mahalanobis prefilter

Giữ cơ chế hiện tại trong `mw_filter.c` ở phase đầu:

```text
d2 = (d_meas - d_pred)^2 / S
S ~= R_gate + velocity_weight * |v|
```

Cần ghi rõ đây là approximation. Nó đủ tốt để gate outlier lớn và duy trì per-anchor hysteresis.

Quy tắc:

- Anchor accepted chuyển sang rejected nếu `d2 > T_reject`.
- Anchor rejected chỉ phục hồi nếu `d2 < T_recover`.
- Nếu sau gate còn thiếu anchor cho UKF update, rescue anchor rejected có `d2` nhỏ nhất, nhưng gán weight thấp.
- Smart rescue (đồng bộ với simulation): chỉ rescue anchor đã bị reject liên tiếp `MAHALANOBIS_PREFILTER_RESCUE_MIN_REJECT_STREAK` (= 5) frame và có `d2` hữu hạn. Spike nhất thời làm frame bỏ update (UKF predict-only) thay vì tiêm range xấu; reject kéo dài (filter lag, đổi hình học thật) vẫn được cứu.

Phase sau, nếu muốn đúng thống kê hơn, expose covariance từ UKF để tính:

```text
S_i = H_i P^- H_i^T + sigma_r2_i
```

Việc này là cải tiến sau, không phải điều kiện bắt buộc cho phase đầu.

## First-path quality

Firmware hiện có `fp_amp_norm`:

```text
fp_amp_norm = (fp_amp1 + fp_amp2 + fp_amp3) / rx_pream_count
```

Không dùng `fp_snr` trong solution này. `fp_snr` hiện được tính là `(fp_amp1+fp_amp2+fp_amp3)/(std_noise+1)` — biên độ first-path so với noise floor của LDE. Về lý thuyết nó bổ sung một chiều thông tin (noise floor thay đổi theo nhiễu/interference, trong khi `rx_pream_count` thì không), nhưng trong điều kiện bình thường nó tương quan mạnh với `fp_amp_norm` nên lợi ích biên nhỏ; ngoài ra `std_noise` phụ thuộc cấu hình PRF/PAC và chưa được hiệu chuẩn. Đưa một metric chưa kiểm chứng vào tích trọng số có thể phạt sai. Nếu vẫn giữ field `fp_snr` trong firmware, chỉ log để phân tích offline.

Hướng tốt hơn `fp_snr` nếu muốn thêm một chỉ báo NLOS thật sự: đọc thêm `CIR_PWR` (RX_FQUAL offset 0x6, hiện chưa đọc) và dùng chênh lệch **RX level − FP level (dB)** theo app note APS006 của Decawave. Tỉ số FP-power/total-power gần như bất biến theo khoảng cách và là chỉ báo NLOS chuẩn cho DW1000: chênh > ~10 dB → nghi NLOS, < ~6 dB → LOS. Metric này đáng đầu tư hơn việc cố dùng `fp_snr`.

Phase đầu:

```text
q_fp = clamp(fp_amp_norm / FP_AMP_GOOD, q_fp_min, 1)
```

Nếu `quality_valid == false`, không loại cứng. Dùng:

```text
q_fp = q_fp_unknown
```

ví dụ `0.5`.

Phase sau, khi có log LOS calibration:

```text
a_i = 20 log10(fp_amp_norm + eps)
uA_i = max(0, (mu_A(d_ref) - a_i) / (sigma_A(d_ref) + eps))
qA_i = huber(uA_i, cA)
```

`d_ref` nên là predicted distance từ UKF hoặc nghiệm sơ bộ, không nên là `d_meas` trong trường hợp NLOS.

## Residual chỉ dùng khi có redundancy

Residual không nên là trung tâm thuật toán firmware. Nó chỉ là frame-level sanity check.

Nếu chỉ có 3 anchor hợp lệ:

```text
q_residual = 1
```

Lý do: với 3 anchor trong 2D, trilateration có thể fit thành nghiệm dù một anchor sai; residual không đủ mạnh để kết luận.

Nếu có từ 4 anchor trở lên:

```text
e_i = d_i - norm(p_debug - anchor_i)
s_e = 1.4826 * median(|e_i - median(e)|) + eps
uR_i = |e_i - median(e)| / s_e
qR_i = huber(uR_i, cR)
```

`p_debug` có thể lấy từ trilateration debug hoặc UKF predicted state. Không dùng residual này để biến trilateration thành estimator chính.

## Layout selection cho UKF

Firmware hiện tại update UKF với đúng 3 range. Vì vậy layout selection vẫn chọn 3 anchor, nhưng cách chọn nên đổi từ composite score sang weighted geometry.

Với một candidate triplet `L`, tính:

```text
H_i = [(p_ref.x - ax_i) / r_i, (p_ref.y - ay_i) / r_i]
W = diag(w_i)
I = H^T W H
score = sqrt(trace(inv(I)))
```

Triplet có score nhỏ nhất được chọn.

`p_ref` lấy theo thứ tự:

1. UKF predicted position nếu UKF initialized.
2. Last valid UKF position.
3. Trilateration debug của candidate.
4. Tâm hình học anchor nếu chưa có state.

Giữ hysteresis để tránh layout nhảy:

```text
keep_previous = previous_score <= best_score * (1 + switch_margin) + eps
```

Anchor rescued vẫn có thể tham gia nếu cần duy trì đủ 3 range, nhưng vì weight thấp nên chỉ được chọn khi không còn layout tốt hơn.

## UKF update

UKF vẫn update bằng 3 range trong phase đầu để không phá cấu trúc `NUM_UPDATE_NOISE = 3`.

Thay đổi quan trọng là measurement covariance không còn cố định hoàn toàn. Trước khi gọi update:

```text
R_ii = 1 / w_i
R_ii = clamp(R_ii, R_min, R_max)
```

Với 3 anchor được chọn:

```text
R_data[0] = R_anchor_0
R_data[4] = R_anchor_1
R_data[8] = R_anchor_2
```

Như vậy UKF vẫn giữ architecture hiện tại, nhưng bắt đầu tin anchor theo chất lượng measurement thật.

Không cần triển khai N-anchor UKF ngay. Nếu sau này cần dùng nhiều hơn 3 anchor, đó là refactor riêng vì `NUM_UPDATE_NOISE` đang fixed compile-time.

## Vai trò của trilateration 2D

Trilateration 2D nên được giữ, nhưng đổi vai trò và cách gọi trong tài liệu/code comment:

- Không gọi là estimator chính.
- Không dùng để chứng minh thuật toán cuối cùng.
- Dùng để debug range geometry.
- Dùng để tạo initial position cho UKF khi chưa initialized.
- Dùng để stream một điểm so sánh nhanh khi debug.

Tên/comment nên phản ánh rõ:

```text
debug_trilateration_position
debug_trilateration_rms
```

thay vì khiến người đọc hiểu đây là pipeline định vị chính.

## WLS multilateration

WLS không triển khai trong firmware.

Vai trò của WLS:

- baseline mô phỏng/offline;
- kiểm thử chất lượng range/weight/layout trên log;
- so sánh với UKF để chứng minh lợi ích của temporal filtering;
- hỗ trợ viết thesis/report.

Nếu cần nhắc trong firmware docs, chỉ ghi:

```text
WLS is an offline evaluation baseline and is not part of the embedded runtime.
```

Không thêm `mw_multilateration.c`, không thêm WLS runtime, không đưa WLS vào lộ trình firmware.

## Lộ trình implement đề xuất

Trạng thái: Bước 1–4 đã triển khai trong firmware và simulation. Bước 5–6 chưa làm.

### Bước 1: Chuẩn hóa anchor quality — DONE

- Thêm `measurement_weight`, `q_mahalanobis`, `q_fp`, `q_residual`, `sigma_r2` vào `mw_tril_anchor_t`.
- Viết helper `huber_weight()`, `compute_fp_weight()`, `compute_range_variance()`.
- Sau Mahalanobis gate trong `freertos.c`, tính weight cho từng anchor.
- Log `aid`, `distance`, `d2`, `fp_amp_norm`, `qM`, `qFP`, `qR`, `w`. Nếu cần giữ `fp_snr`, log riêng như diagnostic không tham gia quyết định.

Kết quả: firmware chạy như hiện tại nhưng có đầy đủ quality telemetry.

### Bước 2: Đổi triplet selection sang weighted geometry — DONE

- Sửa `mw_trilateration_select_best()` hoặc tách hàm mới `mw_select_ukf_layout_3()`.
- Input là anchors đã có `measurement_weight`.
- Score bằng weighted GDOP/weighted information matrix.
- Giữ hysteresis previous mask.

Kết quả: UKF vẫn nhận 3 anchor, nhưng 3 anchor được chọn theo weight + geometry thay vì cộng penalty heuristic.

### Bước 3: Adaptive UKF R — DONE (chưa log R_ii)

- Trước `fusion_update()`, set `ukf.R_data` theo 3 anchor được chọn.
- Clamp `R` để tránh Cholesky/inverse lỗi.
- Log `R_ii` theo anchor.

Kết quả: UKF giảm ảnh hưởng range nghi ngờ mà không cần đổi kích thước measurement vector.

### Bước 4: Làm rõ debug trilateration — DONE

- Đổi tên biến/log nếu cần để thể hiện đây là debug output.
- Không dùng trilateration residual khi chỉ có 3 anchor để tự tin vào measurement.
- Stream trilateration position kèm nhãn debug/baseline.

Kết quả: code và tài liệu không còn nhầm trilateration 2D là thuật toán chính.

### Bước 5: Calibration model cho FP và variance

- Thu log LOS theo khoảng cách.
- Fit `mu_A(d)`, `sigma_A(d)`, `sigma_r2(d)`.
- Thay FP threshold cố định bằng model theo khoảng cách.

Kết quả: measurement weight có cơ sở thống kê hơn.

### Bước 6: Optional full innovation covariance

- Expose predicted covariance `P^-` từ UKF nếu cần.
- Tính `S_i = H_i P^- H_i^T + sigma_r2_i`.
- Thay Mahalanobis approximation bằng innovation covariance thật.

Kết quả: Mahalanobis gate đúng hơn về thống kê.

## Cấu hình đề xuất

Đã thêm vào `positioning_config.h` (kèm `MW_LAYOUT_SWITCH_EPS`); giá trị là điểm khởi đầu, cần tune bằng log thực tế:

```c
#define MW_WEIGHT_EPS                 1.0e-6
#define MW_WEIGHT_MIN                 1.0e-3
#define MW_WEIGHT_MAX                 1.0e3

#define MW_HUBER_C_MAHALANOBIS        2.0
#define MW_HUBER_C_FP                 2.0
#define MW_HUBER_C_RESIDUAL           2.0

#define MW_FP_UNKNOWN_WEIGHT          0.5
#define MW_FP_MIN_WEIGHT              0.1

#define MW_SIGMA_R2_BASE              SYS_FUSION_UKF_R_UWB
#define MW_SIGMA_R2_K_DIST            0.01

#define MW_LAYOUT_SWITCH_MARGIN       MW_TRIL_SWITCH_MARGIN
#define MW_LAYOUT_SWITCH_EPS          MW_TRIL_SWITCH_SCORE_EPS

#define MW_UKF_R_MIN                  0.0025
#define MW_UKF_R_MAX                  0.25
```

Các giá trị này là điểm khởi đầu, cần tune bằng log thực tế.

## Tiêu chí kiểm chứng

Solution firmware đạt khi:

- Một anchor NLOS có `measurement_weight` giảm, nhưng frame vẫn update được nếu còn đủ anchor.
- Khi tag đứng yên, selected triplet không nhảy liên tục.
- Khi chỉ có 3 anchor, residual không được dùng để tạo confidence giả.
- Khi FP diagnostics missing, anchor không bị loại cứng nếu range vẫn hợp lý.
- Khi UKF predicted state chưa ổn định, Mahalanobis gate không loại sạch mọi anchor; rescue giữ đủ 3 range nhưng weight thấp.
- Weighted layout detect được geometry suy biến.
- UKF không lỗi Cholesky/inverse khi weight quá nhỏ hoặc quá lớn.
- Log có đủ: raw range, projected range, d2, FP quality, measurement weight, selected anchors, adaptive R, UKF state, debug trilateration.

## Giải pháp sắp tới (sau review triển khai Bước 1–4)

Xếp theo mức ưu tiên, dựa trên các điểm phát hiện khi review code:

1. **Thống nhất UKF update giữa firmware và simulation (quan trọng nhất).**
   Simulation chặn correction cho `theta` và 3 bias khi update bằng UWB
   (range không quan sát được các state này), còn firmware update cả 8 state.
   Range nhiễu có thể kéo lệch bias trong firmware theo cách mà simulation
   không tái hiện được. Hoặc firmware cũng chặn 4 thành phần đó, hoặc chứng
   minh bằng log rằng chúng hội tụ. Chi phí nhỏ, giảm rủi ro phân kỳ dài hạn.

2. **N-anchor UKF update.** Hiện bỏ ~25% thông tin mỗi frame khi có 4 range
   hợp lệ. UKF simulation đã hỗ trợ M động; firmware cần refactor
   `NUM_UPDATE_NOISE` compile-time. Đây là cải tiến độ chính xác lớn nhất
   còn lại. Khi làm xong, layout selection chuyển từ "chọn 3" sang
   "loại anchor tồi" và WGDOP chỉ còn dùng để giám sát suy biến.

3. **Bước 5 — calibration:** thu log LOS theo khoảng cách, fit `mu_A(d)`,
   `sigma_A(d)`, `sigma_r2(d)`; thay FP threshold cố định. Đồng thời cân nhắc
   thêm floor cho scale MAD: `s_e = max(1.4826*MAD, sigma_r(d)) + eps` —
   khi 4 residual đều rất sạch, MAD quá nhỏ làm anchor hơi nhiễu bị phạt
   `qR` quá tay dù không phải NLOS.

4. **Chỉ báo NLOS từ CIR_PWR — DONE (phase log).** Toàn chuỗi đã triển khai:
   BSP đọc CIR_PWR + tính `rx_fp_delta_db`, anchor gửi trong RESULT (OTA
   format đổi — nạp lại đồng thời anchor và tag), tag log qua fusion log
   frame (cột `dlt1..dlt4`). Còn lại: thu log calibration LOS/NLOS rồi mới
   map vào `q_fp`. Chi tiết và các cải thiện khác từ app note Decawave:
   xem `docs/dw1000_diagnostics_improvements.md` (đáng chú ý nhất là range
   bias theo RSL từ APS011 — sai số hệ thống ±10–20 cm chưa được bù).

5. **Bước 6 — full innovation covariance** cho gate, và khi đó chọn lại
   `T_reject/T_recover` theo phân vị chi-square thực (simulation đang dùng
   40/20 với S thật, firmware 7.5/5 với S xấp xỉ — hai bộ ngưỡng không so
   sánh được với nhau).

6. **Telemetry:** log `R_ii` và `measurement_weight` theo anchor vào raw
   debug stream / protobuf (field `weight` hiện chỉ stream 100/0 theo
   selected mask), để tiêu chí kiểm chứng đo được bằng log.

7. **Đồng bộ chính sách rescue sim ↔ firmware — DONE.** Firmware đã theo
   simulation: per-anchor `reject_streak` trong `mw_filter`, chỉ rescue khi
   streak ≥ 5 và `d2` hữu hạn (`MAHALANOBIS_PREFILTER_RESCUE_MIN_REJECT_STREAK`).

## Kết luận

Firmware roadmap đúng nên là:

```text
current Mahalanobis + heuristic triplet
  -> per-anchor measurement weight
  -> weighted layout selection cho 3-anchor UKF update
  -> adaptive UKF R theo measurement weight
  -> better FP/distance variance calibration
  -> optional full innovation covariance
```

Không đưa WLS vào firmware. Không xem trilateration 2D là thuật toán chính. Firmware runtime tập trung vào UKF, còn trilateration là debug/initialization path và WLS là offline evaluation baseline.
