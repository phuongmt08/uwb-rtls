# Butterworth IMU Filter trong UWB-RTLS Simulation

Tài liệu này mô tả phần lọc Butterworth vừa thêm cho dữ liệu IMU trong mô phỏng:

- Lọc các kênh IMU: `ax`, `ay`, `gz`.
- Cho phép chọn bậc lọc từ 1 đến 6.
- Có cấu hình tần số cắt `cutoff`.
- Tính và hiển thị Nyquist để tránh chọn cutoff vượt giới hạn lấy mẫu.
- Chạy song song 2 nhánh UKF:
  - `Simulated Path (UKF Fusion)`: dùng IMU raw chưa lọc.
  - `Simulated Path (UKF Fusion + IMU Butterworth)`: dùng IMU đã lọc Butterworth.

Các file liên quan:

- `software/simulation/src/core/config.js`
- `software/simulation/template_ukf_prefilter.html`
- `software/simulation/src/controller/ui_controller.js`
- `software/simulation/src/controller/ui_utils.js`
- `software/simulation/src/workers/sim_worker.js`
- `software/simulation/src/view/plot_init.js`
- `software/simulation/src/view/plot_updater.js`

## 1. Mục đích lọc IMU

Trong hệ UWB + IMU, IMU thường có nhiễu cao tần:

- `ax`, `ay`: nhiễu gia tốc, rung cơ khí, nhiễu sensor.
- `gz`: nhiễu gyro trục Z, làm yaw bị rung hoặc drift nhanh hơn.

UKF predict dùng IMU để dự đoán trạng thái:

```text
position, velocity, yaw <- integrate(ax, ay, gz, dt)
```

Nếu IMU nhiễu mạnh, phần predict sẽ tạo vận tốc/vị trí/yaw bị rung. Butterworth low-pass filter được thêm để giảm thành phần tần số cao trước khi đưa IMU vào nhánh UKF filtered.

Trong code hiện tại vẫn giữ nhánh raw để so sánh:

```javascript
filter.predict({ ax: entry.ax, ay: entry.ay, gz: entry.gz }, entry.dt);
filterLpf.predict(imuLpf, entry.dt);
```

Ý nghĩa:

- `filter`: UKF raw, dùng IMU gốc.
- `filterLpf`: UKF Butterworth, dùng `imuLpf` là IMU đã lọc.

Tên biến `lpf` được giữ lại để tương thích với code cũ, nhưng thuật toán bên trong đã đổi sang Butterworth.

## 2. Định nghĩa Butterworth filter

Butterworth filter là bộ lọc có đáp ứng biên độ phẳng tối đa trong dải thông. Với low-pass Butterworth bậc `N`, đáp ứng biên độ analog lý tưởng là:

```text
|H(jΩ)|² = 1 / (1 + (Ω / Ωc)^(2N))
```

Trong đó:

- `Ω`: tần số góc của tín hiệu.
- `Ωc`: tần số góc cắt.
- `N`: bậc bộ lọc.

Tại tần số cắt:

```text
Ω = Ωc
|H(jΩc)|² = 1 / 2
|H(jΩc)| = 1 / sqrt(2)
```

Tức tại cutoff, biên độ giảm khoảng `-3 dB`.

Đặc điểm chính:

- Dải thông mượt, không ripple.
- Bậc càng cao thì roll-off sau cutoff càng dốc.
- Bậc cao hơn cũng làm pha trễ hơn và response transient mạnh hơn.

## 3. Low-pass filter và cutoff

Trong mô phỏng này dùng low-pass Butterworth:

```text
cho qua: tần số thấp hơn cutoff
giảm: tần số cao hơn cutoff
```

Ví dụ nếu IMU predict khoảng `100 Hz`:

```text
fs = 100 Hz
Nyquist = fs / 2 = 50 Hz
cutoff = 2 Hz
```

Khi đó thành phần IMU dưới khoảng `2 Hz` được giữ nhiều hơn, còn rung/noise cao tần bị giảm.

## 4. Nyquist và giới hạn cutoff

Vì đây là dữ liệu rời rạc, tần số lớn nhất có thể biểu diễn đúng là Nyquist:

```text
f_nyquist = fs / 2
```

Nếu chọn cutoff lớn hơn hoặc quá sát Nyquist, thiết kế filter dễ không ổn định hoặc không có ý nghĩa. Vì vậy code clamp cutoff theo:

```text
cutoff_max = 0.95 * Nyquist
```

Trong `config.js`:

```javascript
IMU: {
    DEFAULT_LPF_CUTOFF_HZ: 2.0,
    DEFAULT_FILTER_ORDER: 2,
    MIN_FILTER_ORDER: 1,
    MAX_FILTER_ORDER: 6,
    CUTOFF_NYQUIST_MARGIN: 0.95
}
```

`CUTOFF_NYQUIST_MARGIN = 0.95` nghĩa là cutoff tối đa chỉ được bằng 95% Nyquist.

## 5. Cách tính sampling rate IMU

Sampling rate IMU được ước lượng từ các dòng `Predict`, vì các dòng này mới là dòng dùng IMU để predict.

Trong `ui_controller.js`, hàm `estimateImuTiming(entries)` lấy tất cả `dt` của entry type `Predict`:

```javascript
function estimateImuTiming(entries) {
    const dts = [];
    (entries || []).forEach(entry => {
        if (entry && entry.type === 'Predict' && Number.isFinite(entry.dt) && entry.dt > 0) {
            dts.push(entry.dt);
        }
    });
    if (!dts.length) {
        const fallbackFs = 2 * (SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ / SIM_CONFIG.IMU.CUTOFF_NYQUIST_MARGIN);
        return { sample_rate_hz: fallbackFs, nyquist_hz: fallbackFs / 2 };
    }
    dts.sort((a, b) => a - b);
    const medianDt = dts[Math.floor(dts.length / 2)];
    const sampleRateHz = medianDt > 0 ? 1 / medianDt : 0;
    return {
        sample_rate_hz: sampleRateHz,
        nyquist_hz: sampleRateHz / 2
    };
}
```

Code dùng median `dt`, không dùng average, vì median ít bị ảnh hưởng bởi vài mẫu bất thường.

Ví dụ:

```text
Predict dt = 0.01 s
fs = 1 / 0.01 = 100 Hz
Nyquist = 50 Hz
cutoff_max = 0.95 * 50 = 47.5 Hz
```

Sau đó controller clamp cutoff:

```javascript
const imuTiming = estimateImuTiming(rawData.all_entries);
const cutoffLimit = Math.max(0.05, imuTiming.nyquist_hz * SIM_CONFIG.IMU.CUTOFF_NYQUIST_MARGIN);
const cutoffInputValue = parseFloat(document.getElementById('imu_lpf_cutoff_range').value);
const requestedCutoff = Number.isFinite(cutoffInputValue) ? cutoffInputValue : SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ;
const imuCutoffHz = Math.min(Math.max(0.05, requestedCutoff), cutoffLimit);
```

Giá trị Nyquist được hiển thị trên UI:

```javascript
document.getElementById('imu_filter_nyquist_val').innerText =
    Number.isFinite(params.imu_nyquist_hz) ? params.imu_nyquist_hz.toFixed(2) : '--';
```

## 6. Bậc filter từ 1 đến 6

UI cho chọn bậc:

```html
<input type="range" id="imu_filter_order_range" min="1" max="6" step="1" value="2" ...>
<input type="number" id="imu_filter_order_input" min="1" max="6" step="1" value="2" ...>
```

Controller clamp order:

```javascript
const imuFilterOrder = Math.min(
    SIM_CONFIG.IMU.MAX_FILTER_ORDER,
    Math.max(
        SIM_CONFIG.IMU.MIN_FILTER_ORDER,
        parseInt(document.getElementById('imu_filter_order_range').value) ||
            SIM_CONFIG.IMU.DEFAULT_FILTER_ORDER
    )
);
```

Ý nghĩa từng bậc:

```text
Order 1: lọc nhẹ, ít trễ nhất, roll-off chậm.
Order 2: cân bằng tốt, mặc định hiện tại.
Order 3-4: lọc mạnh hơn, giảm noise cao tần rõ hơn.
Order 5-6: roll-off dốc, nhưng có thể tăng trễ và transient.
```

Trong filter realtime, bậc `N` được ghép từ:

- Nếu `N` lẻ: 1 section bậc 1 + nhiều section bậc 2.
- Nếu `N` chẵn: nhiều section bậc 2.

Ví dụ:

```text
N = 1 -> 1 first-order section
N = 2 -> 1 biquad section
N = 3 -> 1 first-order + 1 biquad
N = 4 -> 2 biquad
N = 5 -> 1 first-order + 2 biquad
N = 6 -> 3 biquad
```

## 7. Q factor cho các section Butterworth

Butterworth bậc cao được tách thành nhiều section bậc 2. Mỗi section cần một hệ số `Q`.

Trong code:

```javascript
const butterworthSectionQs = (order) => {
    const qs = [];
    const pairs = Math.floor(order / 2);
    for (let i = 1; i <= pairs; i++) {
        const angle = order % 2 === 0
            ? ((2 * i - 1) * Math.PI) / (2 * order)
            : (i * Math.PI) / order;
        qs.push(1 / (2 * Math.cos(angle)));
    }
    return qs;
};
```

Hàm này trả về danh sách `Q` cho các biquad Butterworth.

Ví dụ:

```text
Order 2 -> 1 Q
Order 4 -> 2 Q
Order 6 -> 3 Q
```

Các `Q` này quyết định damping của từng section để tổng thể tạo đáp ứng Butterworth.

## 8. Công thức first-order low-pass trong code

Với bậc lẻ, code thêm một section bậc 1.

Code:

```javascript
const applyFirstOrderLowpass = (state, x, cutoff, fs) => {
    const k = Math.tan(Math.PI * cutoff / fs);
    const norm = 1 / (1 + k);
    const b0 = k * norm;
    const b1 = b0;
    const a1 = (k - 1) * norm;
    const y = b0 * x + b1 * state.x1 - a1 * state.y1;
    state.x1 = x;
    state.y1 = y;
    return y;
};
```

Các biến:

```text
x: mẫu hiện tại
y: output sau lọc
state.x1: mẫu input trước đó
state.y1: output trước đó
fs: sampling rate
cutoff: tần số cắt
```

Công thức sai phân:

```text
y[n] = b0*x[n] + b1*x[n-1] - a1*y[n-1]
```

Hệ số:

```text
k = tan(pi * cutoff / fs)
norm = 1 / (1 + k)
b0 = k * norm
b1 = b0
a1 = (k - 1) * norm
```

Đây là dạng digital low-pass thu được từ bilinear transform.

## 9. Công thức biquad low-pass trong code

Các section bậc 2 dùng biquad low-pass.

Code:

```javascript
const applyBiquadLowpass = (state, x, cutoff, fs, q) => {
    const omega = 2 * Math.PI * cutoff / fs;
    const sinOmega = Math.sin(omega);
    const cosOmega = Math.cos(omega);
    const alpha = sinOmega / (2 * q);
    const a0 = 1 + alpha;
    const b0 = ((1 - cosOmega) / 2) / a0;
    const b1 = (1 - cosOmega) / a0;
    const b2 = b0;
    const a1 = (-2 * cosOmega) / a0;
    const a2 = (1 - alpha) / a0;
    const y = b0 * x + b1 * state.x1 + b2 * state.x2 - a1 * state.y1 - a2 * state.y2;
    state.x2 = state.x1;
    state.x1 = x;
    state.y2 = state.y1;
    state.y1 = y;
    return y;
};
```

Công thức sai phân:

```text
y[n] =
    b0*x[n] + b1*x[n-1] + b2*x[n-2]
    - a1*y[n-1] - a2*y[n-2]
```

Các biến:

```text
omega = 2*pi*cutoff/fs
alpha = sin(omega)/(2Q)
Q = hệ số damping của section Butterworth
```

State của mỗi biquad:

```text
x1: input n-1
x2: input n-2
y1: output n-1
y2: output n-2
```

## 10. Hàm lọc Butterworth tổng

Hàm chính trong worker:

```javascript
const applyButterworthLowpass = (filter, x, dt) => {
    const fs = Number.isFinite(dt) && dt > 0 ? 1 / dt : params.imu_sample_rate_hz;
    if (!Number.isFinite(fs) || fs <= 0) return x;

    const nyquist = fs / 2;
    const maxCutoff = Math.max(0.01, nyquist * SIM_CONFIG.IMU.CUTOFF_NYQUIST_MARGIN);
    const cutoff = Math.min(
        Math.max(0.01, params.imu_lpf_cutoff_hz || SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ),
        maxCutoff
    );
    const order = Math.min(
        SIM_CONFIG.IMU.MAX_FILTER_ORDER,
        Math.max(SIM_CONFIG.IMU.MIN_FILTER_ORDER, params.imu_filter_order || SIM_CONFIG.IMU.DEFAULT_FILTER_ORDER)
    );

    let y = x;
    if (order % 2 === 1) {
        y = applyFirstOrderLowpass(filter.first, y, cutoff, fs);
    }
    const qs = butterworthSectionQs(order);
    for (let i = 0; i < qs.length; i++) {
        y = applyBiquadLowpass(filter.biquads[i], y, cutoff, fs, qs[i]);
    }
    return y;
};
```

Luồng xử lý:

```text
1. Tính fs từ dt hiện tại:
   fs = 1 / dt

2. Tính Nyquist:
   nyquist = fs / 2

3. Clamp cutoff:
   cutoff <= 0.95 * nyquist

4. Clamp order:
   1 <= order <= 6

5. Nếu order lẻ:
   chạy first-order section trước

6. Chạy các biquad section còn lại

7. Trả về mẫu đã lọc
```

## 11. State filter cho từng kênh IMU

Mỗi kênh IMU có state filter riêng:

```javascript
const butterFilters = {
    ax: makeButterworthFilter(),
    ay: makeButterworthFilter(),
    gz: makeButterworthFilter()
};
```

Vì `ax`, `ay`, `gz` là ba tín hiệu khác nhau, không được dùng chung state. Nếu dùng chung state, output của kênh này sẽ bị lẫn lịch sử của kênh khác.

Hàm tạo state:

```javascript
const makeButterworthFilter = () => ({
    first: { x1: 0, y1: 0 },
    biquads: []
});
```

Khi nhận mẫu đầu tiên, filter được reset bằng chính giá trị đầu vào:

```javascript
const resetButterworthFilter = (filter, value) => {
    filter.first.x1 = value;
    filter.first.y1 = value;
    filter.biquads = Array.from({ length: 3 }, () => ({
        x1: value, x2: value, y1: value, y2: value
    }));
};
```

Lý do reset bằng giá trị đầu vào:

- Tránh output ban đầu bị kéo về 0.
- Giảm transient lúc mới bắt đầu.
- Với order tối đa 6, cần tối đa 3 biquad, nên tạo sẵn 3 state biquad.

## 12. Hàm applyImuLpf hiện tại

Tên hàm vẫn là `applyImuLpf` để giữ tương thích code cũ, nhưng bên trong đã dùng Butterworth.

```javascript
const applyImuLpf = (entry) => {
    if (!params.enable_imu_lpf) {
        last_ax_lpf = entry.ax;
        last_ay_lpf = entry.ay;
        last_gz_lpf = entry.gz;
        imuFilterInitialized = true;
        return { ax: entry.ax, ay: entry.ay, gz: entry.gz };
    }

    if (!imuFilterInitialized) {
        last_ax_lpf = entry.ax;
        last_ay_lpf = entry.ay;
        last_gz_lpf = entry.gz;
        resetButterworthFilter(butterFilters.ax, entry.ax);
        resetButterworthFilter(butterFilters.ay, entry.ay);
        resetButterworthFilter(butterFilters.gz, entry.gz);
        imuFilterInitialized = true;
        return { ax: last_ax_lpf, ay: last_ay_lpf, gz: last_gz_lpf };
    }

    last_ax_lpf = applyButterworthLowpass(butterFilters.ax, entry.ax, entry.dt);
    last_ay_lpf = applyButterworthLowpass(butterFilters.ay, entry.ay, entry.dt);
    last_gz_lpf = applyButterworthLowpass(butterFilters.gz, entry.gz, entry.dt);
    return { ax: last_ax_lpf, ay: last_ay_lpf, gz: last_gz_lpf };
};
```

Nếu checkbox filter tắt:

```text
imuLpf = raw IMU
```

Nếu checkbox filter bật:

```text
imuLpf = Butterworth(raw IMU)
```

## 13. Chạy UKF với raw IMU và filtered IMU

Trong vòng lặp `Predict`:

```javascript
if (entry.type === 'Predict' && entry.dt > 0) {
    last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;
    const imuLpf = applyImuLpf(entry);

    ...

    filter.predict({ ax: entry.ax, ay: entry.ay, gz: entry.gz }, entry.dt);
    filterLpf.predict(imuLpf, entry.dt);
}
```

Ý nghĩa:

```text
filter:
    nhánh UKF raw
    dùng ax/ay/gz gốc
    hiển thị: Simulated Path (UKF Fusion)

filterLpf:
    nhánh UKF Butterworth
    dùng ax/ay/gz đã lọc
    hiển thị: Simulated Path (UKF Fusion + IMU Butterworth)
```

Như vậy biểu đồ `Trajectory Comparison` có thể so sánh trực tiếp:

- Đường raw UKF bị ảnh hưởng bởi nhiễu IMU như thế nào.
- Đường Butterworth UKF mượt hơn hay lệch/trễ hơn ra sao.

## 14. Velocity, yaw và spectrum

Trong worker, dữ liệu đã lọc còn được lưu vào `plotData`:

```javascript
plotData.ax_lpf.push(last_ax_lpf - bias.ax);
plotData.ay_lpf.push(last_ay_lpf - bias.ay);
plotData.gz_lpf.push(last_gz_lpf - bias.gz);
```

Velocity nhánh filtered:

```javascript
v_lpf.x += (imuLpf.ax - bias.ax) * entry.dt;
v_lpf.y += (imuLpf.ay - bias.ay) * entry.dt;
v_lpf.x *= SIM_CONFIG.IMU.VELOCITY_DECAY;
v_lpf.y *= SIM_CONFIG.IMU.VELOCITY_DECAY;
```

Spectrum:

```javascript
imuSpectrumSeries.ax.push(entry.ax - bias.ax);
imuSpectrumSeries.ay.push(entry.ay - bias.ay);
imuSpectrumSeries.ax_lpf.push(imuLpf.ax - bias.ax);
imuSpectrumSeries.ay_lpf.push(imuLpf.ay - bias.ay);
```

Sau đó tính phổ:

```javascript
plotData.accelSpectrum = {
    ax: computeTimeDomainSpectrum(imuSpectrumSeries.ax, imuSpectrumSeries.times),
    ay: computeTimeDomainSpectrum(imuSpectrumSeries.ay, imuSpectrumSeries.times),
    ax_lpf: computeTimeDomainSpectrum(imuSpectrumSeries.ax_lpf, imuSpectrumSeries.times),
    ay_lpf: computeTimeDomainSpectrum(imuSpectrumSeries.ay_lpf, imuSpectrumSeries.times)
};
```

Vì vậy biểu đồ spectrum có thể so sánh:

```text
Ax raw spectrum vs Ax Butterworth spectrum
Ay raw spectrum vs Ay Butterworth spectrum
```

## 15. UI control đã thêm

Trong `template_ukf_prefilter.html`:

```html
<label>
    IMU Butterworth Cutoff (Hz):
    <span id="imu_lpf_cutoff_val" class="val">2.00</span>
    <span style="color:#64748b;">
        | Nyquist: <span id="imu_filter_nyquist_val">--</span> Hz
    </span>
</label>
```

```html
<label>IMU Butterworth Order: <span id="imu_filter_order_val" class="val">2</span></label>
<input type="range" id="imu_filter_order_range" min="1" max="6" step="1" value="2" ...>
<input type="number" id="imu_filter_order_input" min="1" max="6" step="1" value="2" ...>
```

Checkbox:

```html
<input type="checkbox" id="enable_imu_lpf" checked onchange="update()">
<span>Enable IMU Butterworth filter for UKF predict</span>
```

Tên ID `enable_imu_lpf` và `imu_lpf_cutoff_*` vẫn giữ lại để không phải đổi dây chuyền các đoạn code cũ, nhưng label đã đổi sang Butterworth.

## 16. Save/load config

Trong `ui_utils.js`, schema version được tăng:

```javascript
const UWB_SIM_DEFAULTS_SCHEMA_VERSION = 6;
```

Khi save defaults:

```javascript
imu_filter_order: document.getElementById('imu_filter_order_input').value,
```

Khi load defaults:

```javascript
if (loadTuning && config.imu_filter_order) {
    const order = Math.min(
        SIM_CONFIG.IMU.MAX_FILTER_ORDER,
        Math.max(SIM_CONFIG.IMU.MIN_FILTER_ORDER, parseInt(config.imu_filter_order))
    );
    document.getElementById('imu_filter_order_input').value = order;
    document.getElementById('imu_filter_order_range').value = order;
    document.getElementById('imu_filter_order_val').innerText = order;
}
```

## 17. Các tham số nên tune

Các tham số quan trọng:

```text
enable_imu_lpf:
    bật/tắt Butterworth branch

imu_lpf_cutoff_hz:
    tần số cắt, đơn vị Hz

imu_filter_order:
    bậc lọc từ 1 đến 6

imu_nyquist_hz:
    fs / 2, hiển thị để biết cutoff hợp lệ
```

Gợi ý tuning:

```text
Nếu đường UKF raw rung:
    giảm cutoff hoặc tăng order.

Nếu đường Butterworth bị trễ/đuối khi chuyển động nhanh:
    tăng cutoff hoặc giảm order.

Nếu cutoff gần Nyquist:
    filter gần như không lọc nhiều, hoặc response dễ xấu hơn.

Nếu order quá cao:
    lọc mạnh nhưng có thể tăng phase lag.
```

Với IMU predict khoảng 100 Hz:

```text
Nyquist = 50 Hz
cutoff 1-5 Hz: lọc mạnh, hợp cho chuyển động chậm/mượt.
cutoff 5-15 Hz: giữ phản ứng nhanh hơn.
order 2: mặc định cân bằng.
order 4: lọc mạnh hơn nhưng có thêm trễ.
```

## 18. Tóm tắt luồng dữ liệu

```text
Log entry Predict
    |
    |-- raw IMU: entry.ax, entry.ay, entry.gz
    |       |
    |       '--> filter.predict(...)
    |            -> Simulated Path (UKF Fusion)
    |
    '-- Butterworth IMU:
            imuLpf = applyImuLpf(entry)
            |
            '--> filterLpf.predict(...)
                 -> Simulated Path (UKF Fusion + IMU Butterworth)
```

Update UWB vẫn chạy cho cả hai nhánh:

```text
filter.update(acceptedMeasurements)
filterLpf.update(acceptedMeasurementsLpf)
```

Nhờ vậy hai đường khác nhau chủ yếu do IMU predict raw vs IMU predict đã lọc.

## 19. Lưu ý kỹ thuật

1. Đây là filter realtime/causal.

   Mỗi output chỉ dùng mẫu hiện tại và lịch sử trước đó. Vì vậy có phase delay. Không giống `filtfilt` offline zero-phase.

2. Cutoff được clamp hai lớp.

   - UI clamp theo median `Predict dt`.
   - Worker clamp lại theo `dt` hiện tại.

3. Bậc cao không phải lúc nào cũng tốt hơn.

   Bậc cao giảm noise mạnh hơn, nhưng có thể làm đường UKF phản ứng chậm hơn khi tag đổi hướng nhanh.

4. Tên biến `lpf` còn tồn tại trong code.

   Đây là tên lịch sử từ bản lọc một cực cũ. Ý nghĩa hiện tại là nhánh IMU đã lọc Butterworth.

5. Với log `path_csv` không có IMU raw, filter không tạo spectrum IMU thực.

   Nhánh path CSV chỉ hiển thị path-level data có sẵn như `ukf_x`, `ukf_y`, `tril_x`, `tril_y`.

