import os
import re
import json
import math
import sys
import csv
import datetime
import http.server
import socketserver
import webbrowser
import threading
import xml.etree.ElementTree as ET

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOFTWARE_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
DATA_DIR = os.path.join(SOFTWARE_DIR, 'data')
CSV_LOG_CATEGORIES = (
    {
        'key': 'scripts_fusion',
        'title': 'Scripts (Fusion)',
        'source': 'scripts',
        'suffix': 'fusion_frame_log_data',
    },
    {
        'key': 'scripts_fusion_log',
        'title': 'Scripts (Fusion Log)',
        'source': 'scripts',
        'suffix': 'ukf_log_data',
    },
    {
        'key': 'studio_fusion',
        'title': 'Studio (Fusion)',
        'source': 'studio',
        'suffix': 'sensor_fusion_result',
    },
    {
        'key': 'studio_fusion_log',
        'title': 'Studio (Fusion Log)',
        'source': 'studio',
        'suffix': 'ukf_log_data',
    },
)

def classify_csv_log(path):
    filename = os.path.basename(path)
    if not filename.endswith('.csv'):
        return None

    absolute_path = os.path.abspath(path)
    relative_path = os.path.relpath(absolute_path, DATA_DIR)
    path_parts = relative_path.replace('\\', '/').split('/')
    if not path_parts or path_parts[0] in ('', '.', '..'):
        return None

    source = path_parts[0].lower()
    for category in CSV_LOG_CATEGORIES:
        if source == category['source'] and filename.endswith(f"_{category['suffix']}.csv"):
            return category
    return None

def date_sort_key(folder_name):
    try:
        return datetime.datetime.strptime(folder_name, '%d_%m_%y')
    except ValueError:
        return datetime.datetime.min

def date_folder_for_csv(path):
    rel_parent = os.path.relpath(os.path.dirname(path), DATA_DIR).replace('\\', '/')
    if not rel_parent or rel_parent == '.':
        return 'Root'
    path_parts = rel_parent.split('/')
    if path_parts[0].lower() in ('scripts', 'studio'):
        return path_parts[1] if len(path_parts) > 1 else 'Root'
    return path_parts[0]

# --- METADATA CACHE ---
CACHE_FILE = os.path.join(BASE_DIR, '.simulation_metadata_cache.json')

def load_metadata_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_metadata_cache(cache):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
GT_SQUARE = {
    'x': [2.44, 7.32, 7.32, 2.44, 2.44],
    'y': [2.44, 2.44, 7.32, 7.32, 2.44]
}
GT_STEP_HORIZONTAL_M = 2.8
GT_STEP_VERTICAL_M = 5.6
DEFAULT_ANCHORS = [
    {'id': 1, 'x': 0.0,  'y': 0.0,  'z': 0.895},
    {'id': 2, 'x': 9.76, 'y': 0.0,  'z': 0.895},
    {'id': 3, 'x': 0.0,  'y': 9.76, 'z': 0.895},
    {'id': 4, 'x': 9.76, 'y': 9.76, 'z': 0.895},
]
GROUND_TRUTH_PARAMS = {
    'custom_track': {
        # world: ground truth is fixed in room/world coordinates and does not follow A1.
        # anchor_relative: ground truth coordinates are relative to the selected anchor.
        'coordinate_frame': 'world',
        'anchor_id': 1,
        'offset_x': 1,
        'offset_y': 1,
    }
}

def _anchor_origin(anchor_id):
    for anchor in DEFAULT_ANCHORS:
        if anchor['id'] == anchor_id:
            return anchor['x'], anchor['y']
    return 0.0, 0.0

def apply_groundtruth_params(track):
    if not track:
        return None

    params = GROUND_TRUTH_PARAMS.get(track.get('id'), {})
    frame = params.get('coordinate_frame', 'world')
    offset_x = float(params.get('offset_x', 0.0) or 0.0)
    offset_y = float(params.get('offset_y', 0.0) or 0.0)

    if frame == 'anchor_relative':
        ax, ay = _anchor_origin(int(params.get('anchor_id', 1) or 1))
        offset_x += ax
        offset_y += ay

    def shift_x(v):
        return None if v is None else v + offset_x

    def shift_y(v):
        return None if v is None else v + offset_y

    transformed = dict(track)
    transformed['x'] = [shift_x(v) for v in track.get('x', [])]
    transformed['y'] = [shift_y(v) for v in track.get('y', [])]
    transformed['segments'] = [
        [seg[0] + offset_x, seg[1] + offset_y, seg[2] + offset_x, seg[3] + offset_y, *seg[4:]]
        for seg in track.get('segments', [])
    ]
    transformed['coordinate_frame'] = frame
    transformed['groundtruth_offset'] = {'x': offset_x, 'y': offset_y}
    return transformed

def _segments_from_points(points):
    return [
        [points[i][0], points[i][1], points[i + 1][0], points[i + 1][1], False]
        for i in range(len(points) - 1)
    ]

def make_step_groundtruth(origin_x=0.0, origin_y=0.0, start_kind='start_1'):
    if start_kind == 'start_2':
        points = [
            (origin_x, origin_y),
            (origin_x - GT_STEP_HORIZONTAL_M, origin_y),
            (origin_x - GT_STEP_HORIZONTAL_M, origin_y - GT_STEP_VERTICAL_M),
            (origin_x - 2.0 * GT_STEP_HORIZONTAL_M, origin_y - GT_STEP_VERTICAL_M),
        ]
        name = 'Step 2.8-5.6-2.8 (data start = start 2)'
    else:
        points = [
            (origin_x, origin_y),
            (origin_x + GT_STEP_HORIZONTAL_M, origin_y),
            (origin_x + GT_STEP_HORIZONTAL_M, origin_y + GT_STEP_VERTICAL_M),
            (origin_x + 2.0 * GT_STEP_HORIZONTAL_M, origin_y + GT_STEP_VERTICAL_M),
        ]
        name = 'Step 2.8-5.6-2.8 (data start = start 1)'

    return {
        'id': f'step_route_{start_kind}',
        'name': name,
        'x': [point[0] for point in points],
        'y': [point[1] for point in points],
        'segments': _segments_from_points(points),
        'coordinate_frame': 'first_data_point',
        'start_kind': start_kind,
        'dimensions_m': {
            'horizontal': GT_STEP_HORIZONTAL_M,
            'vertical': GT_STEP_VERTICAL_M,
        },
    }

def first_payload_position(payload):
    if not payload:
        return 0.0, 0.0

    candidate_paths = []
    if payload.get('tril_path'):
        candidate_paths.append(payload.get('tril_path'))
    if payload.get('fw_path'):
        candidate_paths.append(payload.get('fw_path'))

    for path in candidate_paths:
        xs = path.get('x', [])
        ys = path.get('y', [])
        for x, y in zip(xs, ys):
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and math.isfinite(x) and math.isfinite(y):
                return x, y

    return 0.0, 0.0

def parse_graphml_groundtruth(filepath):
    if not os.path.exists(filepath):
        return None

    ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}
    tree = ET.parse(filepath)
    root = tree.getroot()

    key_names = {}
    for key in root.findall('g:key', ns):
        key_id = key.get('id')
        attr_name = key.get('attr.name')
        if key_id and attr_name:
            key_names[key_id] = attr_name

    nodes = {}
    graph = root.find('g:graph', ns)
    if graph is None:
        return None

    for node in graph.findall('g:node', ns):
        values = {}
        for data in node.findall('g:data', ns):
            values[key_names.get(data.get('key'), data.get('key'))] = data.text
        try:
            nodes[node.get('id')] = {
                'x': float(values['x']),
                'y': float(values['y'])
            }
        except (KeyError, TypeError, ValueError):
            continue

    segments = []
    for edge in graph.findall('g:edge', ns):
        src = nodes.get(edge.get('source'))
        dst = nodes.get(edge.get('target'))
        if src and dst:
            # Read the 'dotted' attribute (d2 key): True = single lane (22cm), False = double lane (44cm)
            is_dotted = False
            for data in edge.findall('g:data', ns):
                attr_name = key_names.get(data.get('key'), data.get('key'))
                if attr_name == 'dotted' and data.text:
                    is_dotted = data.text.strip().lower() == 'true'
            segments.append([src['x'], src['y'], dst['x'], dst['y'], is_dotted])

    if not segments:
        return None

    x = []
    y = []
    for seg in segments:
        x.extend([seg[0], seg[2], None])
        y.extend([seg[1], seg[3], None])

    return {
        'id': 'custom_track',
        'name': os.path.basename(filepath),
        'x': x,
        'y': y,
        'segments': segments
    }

def load_ground_truths(payload=None):
    square_segments = [
        [GT_SQUARE['x'][i], GT_SQUARE['y'][i], GT_SQUARE['x'][i + 1], GT_SQUARE['y'][i + 1], False]
        for i in range(len(GT_SQUARE['x']) - 1)
    ]
    tracks = [
        {
            'id': 'square',
            'name': 'Original Square',
            'x': GT_SQUARE['x'],
            'y': GT_SQUARE['y'],
            'segments': square_segments
        }
    ]

    custom = parse_graphml_groundtruth(os.path.join(BASE_DIR, 'custom_track_modified.xml'))
    if custom:
        tracks.append(apply_groundtruth_params(custom))

    start_x, start_y = first_payload_position(payload)
    tracks.append(make_step_groundtruth(start_x, start_y, 'start_1'))
    tracks.append(make_step_groundtruth(start_x, start_y, 'start_2'))

    return tracks

def convert_imu_accel(ax, ay):
    """
    Chuyển đổi ax, ay của IMU sang hệ quy chiếu global dùng cho UKF & Đồ thị
    Mặc định:
        ax = -ay_imu
        ay = -ax_imu
    Bạn có thể chỉnh sửa hàm này để thay đổi logic đổi hệ trục tọa độ.
    """
    return ay, -ax

def parse_log(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
    if first_line.startswith('sof,') and 'ukf_x' in first_line and 'tril_x' in first_line:
        return parse_path_csv_log(filepath)

    data = []
    pattern = re.compile(r"""
        (?P<type>Update|Init|Predict)          # Loại log
        \s+ax:\s*(?P<ax>[\d.-]+)               # Accel X
        \s+ay:\s*(?P<ay>[\d.-]+)               # Accel Y
        \s+gz:\s*(?P<gz>[\d.-]+)               # Gyro Z
        \s+px:\s*(?P<px>[\d.-]+)               # Pos X (Firmware)
        \s+py:\s*(?P<py>[\d.-]+)               # Pos Y (Firmware)
        \s+dt:\s*(?P<dt>[\d.-]+)               # Delta Time
        .*?                                    # Skip unknown content
        (?:mask:\s*(?P<mask>\d+)\s+)?              # Anchor Mask (Optional)
        d1:\s*(?P<d1>[\d.-]+)\s+                   # Distance 1
        d2:\s*(?P<d2>[\d.-]+)\s+                   # Distance 2
        d3:\s*(?P<d3>[\d.-]+)\s+                   # Distance 3
        d4:\s*(?P<d4>[\d.-]+)                      # Distance 4
        \s+err:\s*(?P<err>\d+)                     # Error Code
    """, re.VERBOSE)
    try:
        previous_timestamp_ms = None
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                raw_line = line.rstrip('\r\n')
                if raw_line.lstrip().startswith('(') and '|' in raw_line:
                    status_match = re.search(r"\)\s*(?P<type>Update|Init|Predict)\b", raw_line)
                    if status_match:
                        fields = {}
                        for part in raw_line.split('|')[1:]:
                            if ':' in part:
                                key, value = part.split(':', 1)
                                fields[key.strip()] = value.strip()
                        counter_match = re.search(r"\(\s*(?P<frame>\d+)\s*/\s*(?P<tx>\d+)\s*\)", raw_line)
                        frame_counter = int(counter_match.group('frame')) if counter_match else (len(data) + 1)
                        tx_frame_cnt = int(counter_match.group('tx')) if counter_match else frame_counter
                        update_dt = safe_float(fields.get('update_dt'))
                        predict_dt = safe_float(fields.get('predict_dt'))
                        has_fusion_path = 'ukf_x' in fields or 'ukf_y' in fields or 'ukf_step' in fields
                        tril_x = safe_float(fields.get('tril_x'))
                        tril_y = safe_float(fields.get('tril_y'))
                        ukf_x = safe_float(fields.get('ukf_x'), tril_x)
                        ukf_y = safe_float(fields.get('ukf_y'), tril_y)
                        
                        category = classify_csv_log(filepath)
                        is_log_cat = category and category['suffix'] == 'ukf_log_data'
                        source_format = 'ukf_log' if is_log_cat else ('fusion_frame_csv' if has_fusion_path else 'ukf_log')
                        timestamp_ms = safe_int(fields.get('ts'), None)
                        ukf_step = safe_int(
                            fields.get('ukf_step'),
                            1 if status_match.group('type') == 'Update' else 0
                        )
                        entry_type = status_match.group('type')
                        if source_format == 'fusion_frame_csv':
                            # Studio labels every sensor_fusion_result row as
                            # "Update"; ukf_step is the actual firmware mode.
                            entry_type = 'Update' if ukf_step == 1 else 'Predict'
                            if (update_dt <= 0.0 and predict_dt <= 0.0 and
                                    timestamp_ms is not None and
                                    previous_timestamp_ms is not None):
                                timestamp_delta = (
                                    timestamp_ms - previous_timestamp_ms
                                ) & 0xffffffff
                                if timestamp_delta < 60000:
                                    if entry_type == 'Update':
                                        update_dt = timestamp_delta / 1000.0
                                    else:
                                        predict_dt = timestamp_delta / 1000.0
                        if timestamp_ms is not None:
                            previous_timestamp_ms = timestamp_ms
                        px_fw = tril_x if is_log_cat else (ukf_x if has_fusion_path else tril_x)
                        py_fw = tril_y if is_log_cat else (ukf_y if has_fusion_path else tril_y)
                        
                        ax_conv, ay_conv = convert_imu_accel(safe_float(fields.get('ax')), safe_float(fields.get('ay')))
                        
                        data.append({
                            'line_no': line_no,
                            'raw_line': raw_line,
                            'frame_counter': frame_counter,
                            'tx_frame_cnt': tx_frame_cnt,
                            'type': entry_type,
                            'source_format': source_format,
                            'timestamp_ms': timestamp_ms,
                            'zone': safe_int(fields.get('zone')),
                            'ukf_step': ukf_step,
                            'ax': ax_conv,
                            'ay': ay_conv,
                            'gz': safe_float(fields.get('gz')),
                            'px_fw': px_fw,
                            'py_fw': py_fw,
                            'dt': update_dt or predict_dt,
                            'update_dt': update_dt,
                            'predict_dt': predict_dt,
                            'tril_x': tril_x,
                            'tril_y': tril_y,
                            'ukf_x': ukf_x,
                            'ukf_y': ukf_y,
                            'yaw': safe_float(fields.get('yaw')),
                            'ukf_yaw': safe_float(fields.get('ukf_yaw')),
                            'fp_amp_norm': [safe_float(fields.get(f'amp{i}')) for i in range(1, 5)],
                            'fp_snr': [safe_float(fields.get(f'snr{i}')) for i in range(1, 5)],
                            # Preserve missing quality fields as null.  Older
                            # firmware logs only contain amp/snr; the replay
                            # worker must be able to distinguish "not logged"
                            # from a real confidence value of zero.
                            'fp_confidence': [
                                safe_optional_float(fields.get(
                                    f'fp_confidence{i}', fields.get(f'conf{i}')
                                ))
                                for i in range(1, 5)
                            ],
                            'quality_valid': [
                                safe_optional_int(fields.get(
                                    f'quality_valid{i}', fields.get(f'qvalid{i}')
                                ))
                                for i in range(1, 5)
                            ],
                            'mask': safe_int(fields.get('mask'), 15),
                            'distances': [safe_float(fields.get(f'd{i}')) for i in range(1, 5)],
                            'weights': [safe_int(fields.get(f'w{i}')) for i in range(1, 5)],
                            'err': safe_int(fields.get('err')),
                            'prefilter_reject_count': safe_int(fields.get('pf_reject_count')),
                        })
                        continue

                m = pattern.search(line)
                if m:
                    d = m.groupdict()
                    counter_match = re.search(r"\(\s*(?P<frame>\d+)\s*/\s*(?P<tx>\d+)\s*\)", raw_line)
                    frame_counter = int(counter_match.group('frame')) if counter_match else (len(data) + 1)
                    tx_frame_cnt = int(counter_match.group('tx')) if counter_match else frame_counter
                    
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
                    confidence_vals = parse_quality('conf') if 'conf' in raw_line else [None, None, None, None]
                    quality_valid_vals = parse_quality('qvalid') if 'qvalid' in raw_line else [None, None, None, None]

                    # Sanitize extreme outliers (values > 5000 or non-finite)
                    amp_vals = [v if (math.isfinite(v) and -5000.0 < v < 5000.0) else 0.0 for v in amp_vals]
                    snr_vals = [v if (math.isfinite(v) and -5000.0 < v < 5000.0) else 0.0 for v in snr_vals]

                    ax_conv, ay_conv = convert_imu_accel(float(d['ax']), float(d['ay']))

                    data.append({
                        'line_no': line_no,
                        'raw_line': raw_line,
                        'frame_counter': frame_counter,
                        'tx_frame_cnt': tx_frame_cnt,
                        'type': d['type'],
                        'ax': ax_conv, 'ay': ay_conv, 'gz': float(d['gz']),
                        'px_fw': float(d['px']), 'py_fw': float(d['py']), 'dt': float(d['dt']),
                        'fp_amp_norm': amp_vals,
                        'fp_snr': snr_vals,
                        'fp_confidence': confidence_vals,
                        'quality_valid': quality_valid_vals,
                        'mask': int(d['mask']) if d.get('mask') else 15,
                        'distances': [float(d['d1']), float(d['d2']), float(d['d3']), float(d['d4'])],
                        'err': int(d['err'])
                    })
    except: pass
    return data

def safe_float(value, default=0.0):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default

def safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def safe_optional_float(value):
    if value is None or str(value).strip() == '':
        return None
    return safe_float(value, None)

def safe_optional_int(value):
    if value is None or str(value).strip() == '':
        return None
    return safe_int(value, None)

def parse_path_csv_log(filepath):
    data = []
    prev_frame = None
    try:
        with open(filepath, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for line_no, row in enumerate(reader, 2):
                frame = safe_int(row.get('tx_frame_cnt'), len(data))
                recorded_dt = safe_float(row.get('dt'), None)
                if prev_frame is None:
                    dt = recorded_dt if recorded_dt is not None and recorded_dt > 0 else 0.0
                else:
                    frame_delta = max(1, frame - prev_frame)
                    fallback_dt = frame_delta * 0.02
                    dt = recorded_dt if recorded_dt is not None and recorded_dt > 0 else fallback_dt
                prev_frame = frame

                ukf_x = safe_float(row.get('ukf_x'))
                ukf_y = safe_float(row.get('ukf_y'))
                tril_x = safe_float(row.get('tril_x'), ukf_x)
                tril_y = safe_float(row.get('tril_y'), ukf_y)
                yaw = safe_float(row.get('yaw'), safe_float(row.get('ukf_yaw')))

                data.append({
                    'line_no': line_no,
                    'raw_line': ','.join(row.get(k, '') for k in (reader.fieldnames or [])),
                    'type': 'Update',
                    'source_format': 'path_csv',
                    'tx_frame_cnt': frame,
                    'ax': 0.0, 'ay': 0.0, 'gz': 0.0,
                    'px_fw': ukf_x, 'py_fw': ukf_y, 'dt': dt,
                    'ukf_x': ukf_x, 'ukf_y': ukf_y,
                    'tril_x': tril_x, 'tril_y': tril_y,
                    'yaw': yaw,
                    'ukf_yaw': safe_float(row.get('ukf_yaw'), yaw),
                    'fp_amp_norm': [0, 0, 0, 0],
                    'fp_snr': [0, 0, 0, 0],
                    'fp_confidence': [0, 0, 0, 0],
                    'quality_valid': [0, 0, 0, 0],
                    'mask': safe_int(row.get('anchor_mask'), 15),
                    'distances': [0.0, 0.0, 0.0, 0.0],
                    'err': safe_int(row.get('error_frame_cnt'), 0)
                })
    except Exception:
        pass
    return data

def run_gen(log_file):
    log_data = parse_log(log_file)
    if not log_data: return None
    log_format = log_data[0].get('source_format', 'ukf_log')
    is_recorded_path = log_format in ('path_csv', 'fusion_frame_csv')
    bias = {'ax': 0.0, 'ay': 0.0, 'gz': 0.0}
    fw_path = {'x': [], 'y': [], 'mask': []}
    tril_path = {'x': [], 'y': []}
    fp_logs = {'amp': [[], [], [], []], 'snr': [[], [], [], []]}
    for entry in log_data:
        if entry['type'] == 'Init':
            bias['ax'], bias['ay'], bias['gz'] = entry['ax'], entry['ay'], entry['gz']
        if entry['type'] == 'Update' or is_recorded_path:
            fw_path['x'].append(entry['px_fw'])
            fw_path['y'].append(entry['py_fw'])
            fw_path['mask'].append(entry.get('mask', 15))
            if is_recorded_path:
                tril_path['x'].append(entry.get('tril_x', entry.get('px_fw')))
                tril_path['y'].append(entry.get('tril_y', entry.get('py_fw')))
            for i in range(4):
                val_amp = entry['fp_amp_norm'][i] if len(entry.get('fp_amp_norm', [])) > i else 0
                val_snr = entry['fp_snr'][i] if len(entry.get('fp_snr', [])) > i else 0
                fp_logs['amp'][i].append(val_amp)
                fp_logs['snr'][i].append(val_snr)

    # --- GENERATE MINI THUMBNAIL (SVG) ---
    svg_content = ""
    if fw_path['x']:
        # Combine path and GT to find global bounds
        all_x = fw_path['x'] + GT_SQUARE['x']
        all_y = fw_path['y'] + GT_SQUARE['y']
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        dx, dy = max_x - min_x, max_y - min_y
        
        # Scale to fit 50x50 box (1:1 Aspect Ratio)
        max_dim = max(dx, dy, 0.1)
        scale = 50 / max_dim
        
        # Centering offsets
        off_x = (50 - dx * scale) / 2
        off_y = (50 - dy * scale) / 2

        def to_svg(x_list, y_list):
            pts = []
            for i in range(len(x_list)):
                px = 5 + off_x + (x_list[i] - min_x) * scale
                py = 55 - off_y - (y_list[i] - min_y) * scale
                pts.append(f"{px:.1f},{py:.1f}")
            return " ".join(pts)

        gt_pts = to_svg(GT_SQUARE['x'], GT_SQUARE['y'])
        fw_pts = to_svg(fw_path['x'], fw_path['y'])
        
        svg_content = f"""
            <polyline points="{gt_pts}" fill="none" stroke="#fecaca" stroke-width="1.5" stroke-dasharray="2,2" />
            <polyline points="{fw_pts}" fill="none" stroke="#a78bfa" stroke-width="1.6" />
        """
    
    payload = {
        'log_format': log_format,
        'fw_path': fw_path,
        'tril_path': tril_path,
        'fp_logs': fp_logs,
        'all_entries': log_data,
        'biases': bias,
        'thumb_svg': f'<svg viewBox="0 0 60 60">{svg_content}</svg>'
    }
    return payload

def load_template():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'template_ukf_prefilter.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()

def read_text_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def load_app_js_bundle():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parts = [
        'src/core/config.js',
        'src/core/math_utils.js',
        'src/view/plot_init.js',
        'src/view/plot_updater.js',
        'src/controller/ui_utils.js',
        'src/controller/ui_controller.js',
    ]
    return "\n\n".join(
        f"// ---- {part} ----\n" + read_text_file(os.path.join(script_dir, part))
        for part in parts
    )

def load_worker_js_bundle():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    worker = read_text_file(os.path.join(script_dir, 'src/workers/sim_worker.js'))
    worker = re.sub(r"^\s*importScripts\([^\n]+\);\s*\n", "", worker, count=1, flags=re.MULTILINE)
    parts = [
        'src/core/config.js',
        'src/core/math_utils.js',
        'src/filters/ukf_prefilter.js',
    ]
    prefix = "\n\n".join(
        f"// ---- {part} ----\n" + read_text_file(os.path.join(script_dir, part))
        for part in parts
    )
    return prefix + "\n\n// ---- src/workers/sim_worker.js ----\n" + worker

def get_report_source_mtime():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        'uwb-rtls_simulation.py',
        'custom_track_modified.xml',
        'template_ukf_prefilter.html',
        'src/core/config.js',
        'src/core/math_utils.js',
        'src/view/plot_init.js',
        'src/view/plot_updater.js',
        'src/controller/ui_utils.js',
        'src/controller/ui_controller.js',
        'src/workers/sim_worker.js',
        'src/filters/ukf_prefilter.js',
    ]
    return max(
        os.path.getmtime(os.path.join(script_dir, path))
        for path in paths
        if os.path.exists(os.path.join(script_dir, path))
    )

def render_template(template_content, filename, payload, ground_truths, app_js_bundle, worker_js_bundle):
    html = template_content.replace('__FILENAME__', filename)
    html = html.replace('__DATA_JSON__', json.dumps(payload))
    html = html.replace('__GROUND_TRUTHS_JSON__', json.dumps(ground_truths))
    html = html.replace('__APP_JS_BUNDLE__', app_js_bundle)
    html = html.replace('__SIM_WORKER_SOURCE_JSON__', json.dumps(worker_js_bundle))
    # Replace biases specifically
    html = html.replace('__BIAS_X__', f"{payload['biases']['ax']:.4f}")
    html = html.replace('__BIAS_Y__', f"{payload['biases']['ay']:.4f}")
    return html

def main():
    logs = []
    if os.path.isdir(DATA_DIR):
        for root, _, files in os.walk(DATA_DIR):
            for filename in files:
                log_path = os.path.join(root, filename)
                category = classify_csv_log(log_path)
                if category:
                    logs.append((log_path, category))
    logs.sort(key=lambda item: (date_folder_for_csv(item[0]), os.path.basename(item[0])), reverse=True)

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'template_ukf_prefilter.html')
    report_source_mtime = get_report_source_mtime() if os.path.exists(template_path) else 0
    template_content = load_template()
    app_js_bundle = load_app_js_bundle()
    worker_js_bundle = load_worker_js_bundle()

    metadata_cache = load_metadata_cache()
    cache_dirty = False
    sim_results = []
    for lp, category in logs:
        try:
            fn  = os.path.basename(lp)
            rn  = fn.replace('.csv', '_sim.html')
            rp = os.path.join(os.path.dirname(lp), rn)
            
            # Check if we need to regenerate
            log_mtime = os.path.getmtime(lp)
            html_exists = os.path.exists(rp)
            html_mtime = os.path.getmtime(rp) if html_exists else 0
            
            needs_gen = not html_exists or log_mtime > html_mtime or report_source_mtime > html_mtime
            
            # Look up metadata in cache
            rel_csv_path = os.path.relpath(lp, BASE_DIR).replace('\\', '/')
            cached_item = metadata_cache.get(rel_csv_path)
            
            if cached_item and cached_item.get('mtime') == log_mtime:
                num_updates = cached_item['samples']
                thumb_svg = cached_item['thumb']
            else:
                p = run_gen(lp)
                if not p: continue
                num_updates = len([e for e in p['all_entries'] if e['type'] == 'Update'])
                thumb_svg = p['thumb_svg']
                
                metadata_cache[rel_csv_path] = {
                    'mtime': log_mtime,
                    'samples': num_updates,
                    'thumb': thumb_svg
                }
                cache_dirty = True
                
            sim_results.append({
                'name': fn,
                'path': os.path.relpath(rp, BASE_DIR).replace('\\', '/'),
                'date': date_folder_for_csv(lp),
                'category': category['key'],
                'category_title': category['title'],
                'suffix': category['suffix'],
                'samples': num_updates,
                'thumb': thumb_svg,
                'needs_gen': needs_gen
            })
        except Exception as e:
            import traceback
            traceback.print_exc()

    if cache_dirty:
        save_metadata_cache(metadata_cache)


    grouped_results = {category['key']: {} for category in CSV_LOG_CATEGORIES}
    for r in sim_results:
        grouped_results.setdefault(r['category'], {}).setdefault(r['date'], []).append(r)

    html_sections = []
    cache_token = str(int(report_source_mtime))
    for category in CSV_LOG_CATEGORIES:
        date_groups = grouped_results.get(category['key'], {})
        sorted_dates = sorted(date_groups.keys(), key=date_sort_key, reverse=True)
        if not sorted_dates:
            category_body = '<div class="empty-state">No CSV files found.</div>'
        else:
            date_sections = []
            for folder in sorted_dates:
                items = sorted(date_groups[folder], key=lambda item: item['name'], reverse=True)
                items_html = "".join([
                    f'<a href="{r["path"]}?v={cache_token}" class="log-item">'
                    f'  <div style="display:flex;align-items:center;gap:15px;">'
                    f'    <div class="thumb">{r["thumb"]}</div>'
                    f'    <span>{r["name"]}</span>'
                    f'  </div>'
                    f'  <span class="sample-count">{r["samples"]} samples</span>'
                    f'</a>'
                    for r in items
                ])
                date_sections.append(f"""
                <div class="date-group">
                    <div class="date-header">{folder}</div>
                    <div class="log-list">
                        {items_html}
                    </div>
                </div>
                """)
            category_body = "\n".join(date_sections)

        section = f"""
        <details class="category-group category-{category['key']}">
            <summary class="category-header">
                <span class="category-title">{category['title']}</span>
                <span class="category-suffix">_{category['suffix']}.csv</span>
            </summary>
            <div class="category-body">
                {category_body}
            </div>
        </details>
        """
        html_sections.append(section)

    list_html = "\n".join(html_sections)

    dashboard_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UWB-RTLS Simulation</title>
    <style>
        body {{ 
            font-family: 'Inter', -apple-system, sans-serif; 
            padding: 60px; 
            background: #f8fafc; 
            color: #1e293b;
            line-height: 1.5;
        }}
        .container {{ 
            max-width: 900px; 
            margin: 0 auto; 
            background: white; 
            padding: 40px; 
            border-radius: 16px; 
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); 
        }}
        h1 {{ margin-top: 0; color: #1e293b; font-size: 2.25rem; }}
        p {{ color: #64748b; margin-bottom: 30px; }}
        .category-group {{
            margin-top: 18px;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            overflow: hidden;
            background: white;
        }}
        .category-header {{
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 16px 20px;
            cursor: pointer;
            user-select: none;
            list-style: none;
            transition: background 0.2s;
        }}
        .category-header:hover {{ background: #f8fafc; }}
        .category-header::-webkit-details-marker {{ display: none; }}
        .category-header::before {{
            content: '›';
            color: #7c3aed;
            font-size: 1.5rem;
            font-weight: 700;
            line-height: 1;
            transform: rotate(0deg);
            transition: transform 0.2s;
        }}
        .category-group[open] > .category-header {{
            border-bottom: 1px solid #e2e8f0;
            background: #f8fafc;
        }}
        .category-group[open] > .category-header::before {{ transform: rotate(90deg); }}
        .category-title {{ font-size: 1.25rem; font-weight: 700; color: #0f172a; }}
        .category-suffix {{
            margin-left: auto;
            color: #64748b;
            font-family: monospace;
            font-size: 0.9rem;
        }}
        .category-body {{ padding: 18px 20px 1px; }}
        .date-group {{ margin-bottom: 22px; }}
        .date-header {{ 
            background: #f1f5f9; 
            padding: 10px 20px; 
            font-weight: bold; 
            color: #475569; 
            border-radius: 8px 8px 0 0;
            border: 1px solid #e2e8f0;
            border-bottom: none;
            text-transform: uppercase;
            font-size: 0.85rem;
            letter-spacing: 0.05em;
        }}
        .log-list {{ 
            border: 1px solid #e2e8f0; 
            border-radius: 0 0 8px 8px; 
            overflow: hidden; 
        }}
        .log-item {{ 
            display: flex; 
            justify-content: space-between; 
            padding: 15px 20px; 
            border-bottom: 1px solid #f1f5f9; 
            text-decoration: none; 
            color: #7c3aed; 
            font-weight: 500;
            transition: background 0.2s;
            background: white;
        }}
        .log-item:hover {{ background: #f8fafc; }}
        .log-item:last-child {{ border-bottom: none; }}
        .thumb {{ 
            width: 50px; 
            height: 50px; 
            background: #f8fafc; 
            border: 1px solid #f1f5f9;
            border-radius: 6px; 
            display: flex; 
            align-items: center; 
            justify-content: center;
        }}
        .thumb svg {{ width: 100%; height: 100%; }}
        .sample-count {{ color: #64748b; font-weight: normal; font-size: 0.9rem; }}
        .empty-state {{
            border: 1px dashed #cbd5e1;
            border-radius: 8px;
            padding: 18px 20px;
            color: #94a3b8;
            background: #f8fafc;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>UWB-RTLS Simulation</h1>
        <p>Select a simulation log file to start real-time tuning and analysis.</p>
        {list_html}
    </div>
</body>
</html>
"""
    with open(os.path.join(BASE_DIR, "simulation_dashboard.html"), 'w', encoding='utf-8') as f:
        f.write(dashboard_html)
    
    print(f"\n[INFO] Loaded {len(sim_results)} simulation logs.")
    print(f"[INFO] Dashboard: {os.path.join(BASE_DIR, 'simulation_dashboard.html')}")

    # --- AUTO SERVER & BROWSER ---
    PORT = 8000
    MAX_TRIES = 10
    report_generation_lock = threading.Lock()

    class NoCacheHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            import urllib.parse
            # Parse requested path
            parsed_url = urllib.parse.urlparse(self.path)
            # Remove leading slash and unquote URL-encoded path
            rel_path = urllib.parse.unquote(parsed_url.path).lstrip('/')
            
            # Check if this is a simulation report request
            if rel_path.endswith('_sim.html'):
                # Map to potential CSV log path
                report_path = os.path.abspath(os.path.join(SOFTWARE_DIR, rel_path))
                lp = report_path.replace('_sim.html', '.csv')
                data_root = os.path.abspath(DATA_DIR)
                
                if (
                    os.path.exists(lp)
                    and os.path.abspath(lp).lower().startswith((data_root + os.sep).lower())
                    and classify_csv_log(lp)
                ):
                    log_mtime = os.path.getmtime(lp)
                    rp = report_path

                    # Always rebuild only the requested generated report using
                    # the current source bundles. CSV parsing and simulation
                    # algorithms are unchanged.
                    with report_generation_lock:
                        if os.path.exists(rp):
                            os.remove(rp)
                            print(f"[SERVER] Removed stale report: {os.path.basename(rp)}")

                        print(f"[SERVER] Generating simulation report on-demand for: {os.path.basename(lp)}")
                        p = run_gen(lp)
                        if p:
                            current_template = load_template()
                            current_app_bundle = load_app_js_bundle()
                            current_worker_bundle = load_worker_js_bundle()
                            os.makedirs(os.path.dirname(rp), exist_ok=True)
                            with open(rp, 'w', encoding='utf-8') as f:
                                gts = load_ground_truths(p)
                                f.write(render_template(
                                    current_template,
                                    os.path.basename(lp),
                                    p,
                                    gts,
                                    current_app_bundle,
                                    current_worker_bundle,
                                ))
                            print(f"[SERVER] Successfully generated: {os.path.basename(rp)}")
                            
                            # Update the metadata cache
                            rel_csv_path = os.path.relpath(lp, BASE_DIR).replace('\\', '/')
                            num_updates = len([e for e in p['all_entries'] if e['type'] == 'Update'])
                            metadata_cache[rel_csv_path] = {
                                'mtime': log_mtime,
                                'samples': num_updates,
                                'thumb': p['thumb_svg']
                            }
                            save_metadata_cache(metadata_cache)
            
            super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

    Handler = NoCacheHTTPRequestHandler
    
    # Serve from software/ so both simulation dashboard and data logs are available.
    os.chdir(SOFTWARE_DIR)

    httpd = None
    for attempt in range(MAX_TRIES):
        try:
            # Set allow_reuse_address to True to help with quick restarts
            socketserver.TCPServer.allow_reuse_address = True
            httpd = socketserver.TCPServer(("", PORT), Handler)
            break
        except OSError:
            print(f"[WARNING] Port {PORT} is busy, trying next...")
            PORT += 1
    
    if not httpd:
        print("[ERROR] Could not find an available port. Please close some applications.")
        sys.exit(1)

    def start_server(server):
        print(f"[SERVER] Running at http://localhost:{PORT}")
        print("[INFO] Press Ctrl+C to stop the server.")
        server.serve_forever()

    # Start server in a separate thread
    thread = threading.Thread(target=start_server, args=(httpd,))
    thread.daemon = True
    thread.start()

    # Open the dashboard in the default browser
    webbrowser.open(f"http://localhost:{PORT}/simulation/simulation_dashboard.html")
    
    # Keep the main thread alive so the server continues to run
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
