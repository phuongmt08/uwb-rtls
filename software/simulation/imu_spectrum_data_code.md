# Trich code Spectrum / Data / IMU

File nay gom cac doan code lien quan den luong du lieu IMU, xu ly LPF/ZUPT, tinh spectrum va cap nhat bieu do tu:

- `software/simulation/uwb-rtls_simulation.py`
- `software/simulation/src/core/config.js`
- `software/simulation/src/core/math_utils.js`
- `software/simulation/src/filters/ukf_prefilter.js`
- `software/simulation/src/workers/sim_worker.js`
- `software/simulation/src/view/plot_init.js`
- `software/simulation/src/view/plot_updater.js`
- `software/simulation/src/controller/ui_controller.js`
- `software/simulation/src/controller/ui_utils.js`

## `software/simulation/uwb-rtls_simulation.py`

### Parse log: lay du lieu IMU `ax`, `ay`, `gz` va payload data

```python
def parse_log(filepath):
    data = []
    pattern = re.compile(r"""
        (?P<type>Update|Init|Predict)          # Loai log
        \s+ax:\s*(?P<ax>[\d.-]+)               # Accel X
        \s+ay:\s*(?P<ay>[\d.-]+)               # Accel Y
        \s+gz:\s*(?P<gz>[\d.-]+)               # Gyro Z
        \s+px:\s*(?P<px>[\d.-]+)               # Pos X (Firmware)
        \s+py:\s*(?P<py>[\d.-]+)               # Pos Y (Firmware)
        \s+dt:\s*(?P<dt>[\d.-]+)               # Delta Time
        .*?                                    # Skip unknown content
        (?:mask:\s*(?P<mask>\d+)\s+)?          # Anchor Mask (Optional)
        d1:\s*(?P<d1>[\d.-]+)\s+               # Distance 1
        d2:\s*(?P<d2>[\d.-]+)\s+               # Distance 2
        d3:\s*(?P<d3>[\d.-]+)\s+               # Distance 3
        d4:\s*(?P<d4>[\d.-]+)                  # Distance 4
        \s+err:\s*(?P<err>\d+)                 # Error Code
    """, re.VERBOSE)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                raw_line = line.rstrip('\r\n')
                m = pattern.search(line)
                if m:
                    d = m.groupdict()

                    def parse_float_list(s):
                        return [float(x.strip()) for x in (s or "").split(',') if x.strip()] or [0,0,0,0]

                    def parse_quality(prefix):
                        bracket = re.search(rf"{prefix}:\s*\[([\d.,\s-]+)\]", raw_line)
                        if bracket:
                            return parse_float_list(bracket.group(1))

                        values = []
                        for anchor_idx in range(1, 5):
                            scalar = re.search(rf"{prefix}{anchor_idx}:\s*([\d.-]+)", raw_line)
                            values.append(float(scalar.group(1)) if scalar else 0)
                        return values

                    amp_vals = parse_quality('amp') if 'amp' in raw_line or 'fp_amp_norm' in raw_line else [0,0,0,0]
                    snr_vals = parse_quality('snr') if 'snr' in raw_line or 'fp_snr' in raw_line else [0,0,0,0]

                    amp_vals = [v if (math.isfinite(v) and -5000.0 < v < 5000.0) else 0.0 for v in amp_vals]
                    snr_vals = [v if (math.isfinite(v) and -5000.0 < v < 5000.0) else 0.0 for v in snr_vals]

                    data.append({
                        'line_no': line_no,
                        'raw_line': raw_line,
                        'type': d['type'],
                        'ax': float(d['ax']), 'ay': float(d['ay']), 'gz': float(d['gz']),
                        'px_fw': float(d['px']), 'py_fw': float(d['py']), 'dt': float(d['dt']),
                        'fp_amp_norm': amp_vals,
                        'fp_snr': snr_vals,
                        'mask': int(d['mask']) if d.get('mask') else 15,
                        'distances': [float(d['d1']), float(d['d2']), float(d['d3']), float(d['d4'])],
                        'err': int(d['err'])
                    })
    except: pass
    return data
```

```python
def run_gen(log_file):
    log_data = parse_log(log_file)
    if not log_data: return None
    bias = {'ax': 0.0, 'ay': 0.0, 'gz': 0.0}
    fw_path = {'x': [], 'y': [], 'mask': []}
    fp_logs = {'amp': [[], [], [], []], 'snr': [[], [], [], []]}

    for entry in log_data:
        if entry['type'] == 'Init':
            bias['ax'], bias['ay'], bias['gz'] = entry['ax'], entry['ay'], entry['gz']
        if entry['type'] == 'Update':
            fw_path['x'].append(entry['px_fw'])
            fw_path['y'].append(entry['py_fw'])
            fw_path['mask'].append(entry.get('mask', 15))
            for i in range(4):
                val_amp = entry['fp_amp_norm'][i] if len(entry.get('fp_amp_norm', [])) > i else 0
                val_snr = entry['fp_snr'][i] if len(entry.get('fp_snr', [])) > i else 0
                fp_logs['amp'][i].append(val_amp)
                fp_logs['snr'][i].append(val_snr)

    payload = {
        'fw_path': fw_path,
        'fp_logs': fp_logs,
        'all_entries': log_data,
        'biases': bias,
        'thumb_svg': f'<svg viewBox="0 0 60 60">{svg_content}</svg>'
    }
    return payload
```

## `software/simulation/src/core/config.js`

### Cau hinh IMU / Dead Reckoning

```javascript
// --- IMU / Dead Reckoning ---
IMU: {
    VELOCITY_DECAY: 0.98,   // Velocity damping factor
    DEFAULT_ENABLE_ZUPT_UKF: false,
    ZUPT_COUNT_THRESHOLD: 10,
    DEFAULT_ZUPT_ACC: 0.15,
    DEFAULT_ZUPT_GYR: 0.05,
    DEFAULT_ENABLE_LPF: true,
    DEFAULT_LPF_CUTOFF_HZ: 2.0
},
```

## `software/simulation/src/core/math_utils.js`

### Tinh spectrum tu du lieu time-domain

```javascript
function computeTimeDomainSpectrum(values, times) {
    const clean = [];
    const cleanTimes = [];
    values.forEach((v, i) => {
        const t = times && times[i];
        if (Number.isFinite(v) && Number.isFinite(t)) {
            clean.push(v);
            cleanTimes.push(t);
        }
    });

    const n = clean.length;
    if (n < 4) return { freq: [], mag: [] };

    const maxFftSize = 16384;
    let nfft = 1;
    while ((nfft * 2) <= n && (nfft * 2) <= maxFftSize) nfft *= 2;
    if (nfft < 4) return { freq: [], mag: [] };

    const start = Math.max(0, n - nfft);
    const duration = cleanTimes[start + nfft - 1] - cleanTimes[start];
    const fs = duration > 0 ? (nfft - 1) / duration : 0;
    if (!Number.isFinite(fs) || fs <= 0) return { freq: [], mag: [] };

    let mean = 0;
    for (let i = 0; i < nfft; i++) mean += clean[start + i];
    mean /= nfft;

    const re = new Array(nfft);
    const im = new Array(nfft).fill(0);
    let windowSum = 0;
    for (let i = 0; i < nfft; i++) {
        const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (nfft - 1));
        re[i] = (clean[start + i] - mean) * w;
        windowSum += w;
    }

    for (let i = 1, j = 0; i < nfft; i++) {
        let bit = nfft >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            const tr = re[i]; re[i] = re[j]; re[j] = tr;
            const ti = im[i]; im[i] = im[j]; im[j] = ti;
        }
    }

    for (let len = 2; len <= nfft; len <<= 1) {
        const angle = -2 * Math.PI / len;
        const wLenRe = Math.cos(angle);
        const wLenIm = Math.sin(angle);
        for (let i = 0; i < nfft; i += len) {
            let wRe = 1;
            let wIm = 0;
            const half = len >> 1;
            for (let j = 0; j < half; j++) {
                const uRe = re[i + j];
                const uIm = im[i + j];
                const vRe = re[i + j + half] * wRe - im[i + j + half] * wIm;
                const vIm = re[i + j + half] * wIm + im[i + j + half] * wRe;
                re[i + j] = uRe + vRe;
                im[i + j] = uIm + vIm;
                re[i + j + half] = uRe - vRe;
                im[i + j + half] = uIm - vIm;

                const nextWRe = wRe * wLenRe - wIm * wLenIm;
                wIm = wRe * wLenIm + wIm * wLenRe;
                wRe = nextWRe;
            }
        }
    }

    const freq = [];
    const mag = [];
    const scale = windowSum > 0 ? 2 / windowSum : 2 / nfft;
    for (let k = 1; k <= Math.floor(nfft / 2); k++) {
        freq.push(k * fs / nfft);
        mag.push(scale * Math.sqrt(re[k] * re[k] + im[k] * im[k]));
    }

    return { freq, mag };
}
```

## `software/simulation/src/filters/ukf_prefilter.js`

### UKF predict dung IMU

```javascript
predict(imu, dt) {
    if (!this.is_initialized) return;
    if (!Number.isFinite(dt) || dt <= 0) return;

    const imuAx = Number.isFinite(imu.ax) ? imu.ax : this.x[5];
    const imuAy = Number.isFinite(imu.ay) ? imu.ay : this.x[6];
    const imuGz = Number.isFinite(imu.gz) ? imu.gz : this.x[7];

    // Generate augmented state/covariance, sigma points...

    for (let m = 0; m < this.num_sigmas; m++) {
        const sp = sigma_pts[m];
        const px = sp[0], py = sp[1], vx = sp[2], vy = sp[3], theta = sp[4];
        const bax = sp[5], bay = sp[6], bgz = sp[7];
        const n_ax = sp[8], n_ay = sp[9], n_gz = sp[10];

        const corrected_ax = imuAx - bax + n_ax;
        const corrected_ay = imuAy - bay + n_ay;
        const corrected_gz = imuGz - bgz + n_gz;

        const theta_new = normalizeAngle(theta + corrected_gz * dt);
        const cos_t = Math.cos(theta);
        const sin_t = Math.sin(theta);

        const ax_world = corrected_ax * cos_t - corrected_ay * sin_t;
        const ay_world = corrected_ax * sin_t + corrected_ay * cos_t;

        this.X_sigma_pred[m][0] = px + vx * dt + 0.5 * ax_world * dt * dt;
        this.X_sigma_pred[m][1] = py + vy * dt + 0.5 * ay_world * dt * dt;
        this.X_sigma_pred[m][2] = vx + ax_world * dt;
        this.X_sigma_pred[m][3] = vy + ay_world * dt;
        this.X_sigma_pred[m][4] = theta_new;
        this.X_sigma_pred[m][5] = bax;
        this.X_sigma_pred[m][6] = bay;
        this.X_sigma_pred[m][7] = bgz;
    }
}
```

## `software/simulation/src/workers/sim_worker.js`

### Khoi tao plot data va series cho spectrum

```javascript
const plotData = {
    vx_raw: [], vy_raw: [], vx: [], vy: [], vx_lpf: [], vy_lpf: [], zupt: [], ax: [], ay: [], gz: [],
    ax_lpf: [], ay_lpf: [], gz_lpf: [], yaw: [], times: [],
    ukf_yaw: []
};

const imuSpectrumSeries = {
    times: [],
    ax: [],
    ay: [],
    ax_lpf: [],
    ay_lpf: []
};
```

### Loc IMU bang LPF

```javascript
const applyImuLpf = (entry) => {
    if (!params.enable_imu_lpf) {
        last_ax_lpf = entry.ax;
        last_ay_lpf = entry.ay;
        last_gz_lpf = entry.gz;
        lpfInitialized = true;
        return { ax: entry.ax, ay: entry.ay, gz: entry.gz };
    }

    if (!lpfInitialized) {
        last_ax_lpf = entry.ax;
        last_ay_lpf = entry.ay;
        last_gz_lpf = entry.gz;
        lpfInitialized = true;
        return { ax: last_ax_lpf, ay: last_ay_lpf, gz: last_gz_lpf };
    }

    const cutoff = Math.max(0.01, params.imu_lpf_cutoff_hz || SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ);
    const dt = Number.isFinite(entry.dt) && entry.dt > 0 ? entry.dt : 0;
    const tau = 1 / (2 * Math.PI * cutoff);
    const alpha = dt > 0 ? dt / (tau + dt) : 1;
    last_ax_lpf += alpha * (entry.ax - last_ax_lpf);
    last_ay_lpf += alpha * (entry.ay - last_ay_lpf);
    last_gz_lpf += alpha * (entry.gz - last_gz_lpf);
    return { ax: last_ax_lpf, ay: last_ay_lpf, gz: last_gz_lpf };
};
```

### Xu ly Predict: IMU, ZUPT, velocity, yaw, spectrum series

```javascript
if (entry.type === 'Predict' && entry.dt > 0) {
    last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;
    const imuLpf = applyImuLpf(entry);

    const acc_mag = Math.sqrt((entry.ax - bias.ax)**2 + (entry.ay - bias.ay)**2);
    const gyr_mag = Math.abs(entry.gz - bias.gz);
    if (acc_mag < params.zupt_acc && gyr_mag < params.zupt_gyr) zupt_cnt++; else zupt_cnt = 0;
    zuptActive = zupt_cnt > SIM_CONFIG.IMU.ZUPT_COUNT_THRESHOLD;

    filter.predict({ ax: entry.ax, ay: entry.ay, gz: entry.gz }, entry.dt);
    filterLpf.predict(imuLpf, entry.dt);

    v_raw.x += (entry.ax - bias.ax) * entry.dt;
    v_raw.y += (entry.ay - bias.ay) * entry.dt;
    v_raw.x *= SIM_CONFIG.IMU.VELOCITY_DECAY;
    v_raw.y *= SIM_CONFIG.IMU.VELOCITY_DECAY;

    v_lpf.x += (imuLpf.ax - bias.ax) * entry.dt;
    v_lpf.y += (imuLpf.ay - bias.ay) * entry.dt;
    v_lpf.x *= SIM_CONFIG.IMU.VELOCITY_DECAY;
    v_lpf.y *= SIM_CONFIG.IMU.VELOCITY_DECAY;

    if (zuptActive) {
        v_clean.x = 0;
        v_clean.y = 0;
    } else {
        v_clean.x += (entry.ax - bias.ax) * entry.dt;
        v_clean.y += (entry.ay - bias.ay) * entry.dt;
        v_clean.x *= SIM_CONFIG.IMU.VELOCITY_DECAY;
        v_clean.y *= SIM_CONFIG.IMU.VELOCITY_DECAY;
    }

    yaw += (entry.gz - bias.gz) * entry.dt;

    imuSpectrumSeries.times.push(total_time);
    imuSpectrumSeries.ax.push(entry.ax - bias.ax);
    imuSpectrumSeries.ay.push(entry.ay - bias.ay);
    imuSpectrumSeries.ax_lpf.push(imuLpf.ax - bias.ax);
    imuSpectrumSeries.ay_lpf.push(imuLpf.ay - bias.ay);
}
```

### Dua IMU vao plotData va tinh spectrum

```javascript
plotData.vx_raw.push(v_raw.x);
plotData.vy_raw.push(v_raw.y);
plotData.vx.push(v_clean.x);
plotData.vy.push(v_clean.y);
plotData.vx_lpf.push(v_lpf.x);
plotData.vy_lpf.push(v_lpf.y);
plotData.zupt.push(zuptActive ? 1.0 : 0.0);
plotData.ax.push(last_ax - bias.ax);
plotData.ay.push(last_ay - bias.ay);
plotData.gz.push(last_gz - bias.gz);
plotData.ax_lpf.push(last_ax_lpf - bias.ax);
plotData.ay_lpf.push(last_ay_lpf - bias.ay);
plotData.gz_lpf.push(last_gz_lpf - bias.gz);
plotData.yaw.push(yaw * 180 / Math.PI);
plotData.ukf_yaw.push(filter.ukf.is_initialized ? filter.ukf.x[4] * 180 / Math.PI : 0);
plotData.times.push(total_time);
```

```javascript
plotData.accelSpectrum = {
    ax: computeTimeDomainSpectrum(imuSpectrumSeries.ax, imuSpectrumSeries.times),
    ay: computeTimeDomainSpectrum(imuSpectrumSeries.ay, imuSpectrumSeries.times),
    ax_lpf: computeTimeDomainSpectrum(imuSpectrumSeries.ax_lpf, imuSpectrumSeries.times),
    ay_lpf: computeTimeDomainSpectrum(imuSpectrumSeries.ay_lpf, imuSpectrumSeries.times)
};
```

## `software/simulation/src/view/plot_init.js`

### Khoi tao bieu do acceleration va spectrum

```javascript
Plotly.newPlot('accel', [
    { x: [], y: [], name: 'Ax', mode: 'lines', type: 'scatter', line: { color: '#2563eb' },
      hovertemplate: 'Ax: %{y:.3f} m/sÂ²<extra></extra>' },
    { x: [], y: [], name: 'Ay', mode: 'lines', type: 'scatter', line: { color: '#16a34a' },
      hovertemplate: 'Ay: %{y:.3f} m/sÂ²<extra></extra>' },
    { x: [], y: [], name: 'Ax LPF', mode: 'lines', type: 'scatter', line: { color: '#eb0808', dash: 'dash', width: 2 },
      hovertemplate: 'Ax LPF: %{y:.3f} m/s^2<extra></extra>' },
    { x: [], y: [], name: 'Ay LPF', mode: 'lines', type: 'scatter', line: { color: '#e45f07', dash: 'dash', width: 2 },
      hovertemplate: 'Ay LPF: %{y:.3f} m/s^2<extra></extra>' },
    { x: [], y: [], name: 'ZUPT Active', fill: 'tozeroy', yaxis: 'y2', mode: 'lines', line: { color: '#cbd5e1', width: 0 }, opacity: 0.3, hovertemplate: 'ZUPT Active<extra></extra>' },
    { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
], {
    margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
    xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
    yaxis: { title: 'Acceleration (m/sÂ²)' },
    yaxis2: { overlaying: 'y', side: 'right', range: [0, 1], showgrid: false, zeroline: false, showticklabels: false },
    hovermode: 'x unified'
});
```

```javascript
Plotly.newPlot('accel_spectrum', [
    { x: [], y: [], name: 'Ax Spectrum', mode: 'lines', type: 'scatter', line: { color: '#2563eb', width: 2 },
      hovertemplate: 'Ax %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
    { x: [], y: [], name: 'Ay Spectrum', mode: 'lines', type: 'scatter', line: { color: '#16a34a', width: 2 },
      hovertemplate: 'Ay %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
    { x: [], y: [], name: 'Ax LPF Spectrum', mode: 'lines', type: 'scatter', line: { color: '#60a5fa', dash: 'dash', width: 2 },
      hovertemplate: 'Ax LPF %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
    { x: [], y: [], name: 'Ay LPF Spectrum', mode: 'lines', type: 'scatter', line: { color: '#86efac', dash: 'dash', width: 2 },
      hovertemplate: 'Ay LPF %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
    { x: [], y: [], name: 'LPF Cutoff', mode: 'lines', type: 'scatter', line: { color: '#ef4444', dash: 'dot', width: 2 },
      hovertemplate: 'Cutoff: %{x:.3f} Hz<extra></extra>' }
], {
    margin: { t: 40, b: 40, l: 60, r: 40 },
    xaxis: { title: 'Frequency (Hz)', rangemode: 'tozero' },
    yaxis: { title: 'Amplitude' },
    hovermode: 'x unified'
});
```

## `software/simulation/src/view/plot_updater.js`

### Cap nhat du lieu IMU va spectrum len bieu do

```javascript
Plotly.restyle('accel', {
    x: [x_axis, x_axis, x_axis, x_axis, x_axis],
    y: [plotData.ax, plotData.ay, plotData.ax_lpf, plotData.ay_lpf, plotData.zupt],
    customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
}, [0, 1, 2, 3, 4]);

const spectrum = plotData.accelSpectrum || {};
const cutoff = parseFloat(document.getElementById('imu_lpf_cutoff_range').value);
const maxSpecY = [spectrum.ax, spectrum.ay, spectrum.ax_lpf, spectrum.ay_lpf]
    .flatMap(s => s && s.mag ? s.mag : [])
    .filter(Number.isFinite)
    .reduce((m, v) => Math.max(m, v), 0);

Plotly.restyle('accel_spectrum', {
    x: [
        spectrum.ax ? spectrum.ax.freq : [],
        spectrum.ay ? spectrum.ay.freq : [],
        spectrum.ax_lpf ? spectrum.ax_lpf.freq : [],
        spectrum.ay_lpf ? spectrum.ay_lpf.freq : [],
        [cutoff, cutoff]
    ],
    y: [
        spectrum.ax ? spectrum.ax.mag : [],
        spectrum.ay ? spectrum.ay.mag : [],
        spectrum.ax_lpf ? spectrum.ax_lpf.mag : [],
        spectrum.ay_lpf ? spectrum.ay_lpf.mag : [],
        [0, maxSpecY || 1]
    ]
}, [0, 1, 2, 3, 4]);
```

```javascript
Plotly.restyle('yaw_plot', {
    x: [x_axis, x_axis, x_axis, x_axis],
    y: [plotData.gz, plotData.gz_lpf, plotData.yaw, plotData.ukf_yaw],
    customdata: [plotData.times, plotData.times, plotData.times, plotData.times]
}, [0, 1, 2, 3]);
```

## `software/simulation/src/controller/ui_controller.js`

### Lay tham so IMU LPF tu UI va truyen vao worker

```javascript
const params = {
    zupt_acc: parseFloat(document.getElementById('zupt_acc_range').value),
    zupt_gyr: parseFloat(document.getElementById('zupt_gyr_range').value),
    enable_smoother: document.getElementById('enable_smoother').checked,
    enable_mahalanobis: document.getElementById('enable_mahalanobis').checked,
    enable_imu_lpf: document.getElementById('enable_imu_lpf').checked,
    imu_lpf_cutoff_hz: parseFloat(document.getElementById('imu_lpf_cutoff_range').value),
};

document.getElementById('imu_lpf_cutoff_val').innerText = params.imu_lpf_cutoff_hz.toFixed(2);
```

## `software/simulation/src/controller/ui_utils.js`

### Luu va khoi phuc cau hinh IMU LPF

```javascript
const config = {
    zupt_acc: document.getElementById('zupt_acc_input').value,
    zupt_gyr: document.getElementById('zupt_gyr_input').value,
    enable_imu_lpf: document.getElementById('enable_imu_lpf').checked,
    imu_lpf_cutoff_hz: document.getElementById('imu_lpf_cutoff_input').value,
};

localStorage.setItem('uwb_sim_defaults', JSON.stringify(config));
```

```javascript
if (loadTuning && config.enable_imu_lpf !== undefined) {
    document.getElementById('enable_imu_lpf').checked = config.enable_imu_lpf;
}
if (loadTuning && config.imu_lpf_cutoff_hz) {
    document.getElementById('imu_lpf_cutoff_input').value = config.imu_lpf_cutoff_hz;
    document.getElementById('imu_lpf_cutoff_range').value = config.imu_lpf_cutoff_hz;
    document.getElementById('imu_lpf_cutoff_val').innerText = parseFloat(config.imu_lpf_cutoff_hz).toFixed(2);
}
```
