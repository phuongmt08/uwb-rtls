import os
import glob
import re
import numpy as np
from .config import SensorEvent, SOURCE_DATA_FILE

def find_latest_csv_file(directory: str = None, file_type: str = "ukf_log_data") -> str:
    from datetime import datetime
    if directory is None:
        directory = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "csv")
    
    folders = []
    if os.path.exists(directory):
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                try:
                    folder_date = datetime.strptime(item, "%d_%m_%y")
                    folders.append((folder_date, item_path))
                except ValueError:
                    pass
                    
    if folders:
        folders.sort(key=lambda x: x[0], reverse=True)
        target_dir = folders[0][1]
    else:
        target_dir = directory
        
    csv_files = glob.glob(os.path.join(target_dir, f"*{file_type}*.csv"))
    
    # fallback
    if not csv_files and folders:
        for fd, dpath in folders:
            csv_files = glob.glob(os.path.join(dpath, f"*{file_type}*.csv"))
            if csv_files:
                target_dir = dpath
                break
                
    if not csv_files:
        csv_files = glob.glob(os.path.join(directory, f"*{file_type}*.csv"))
        
    pattern1 = re.compile(rf"(\d{{8}})_(\d{{6}})_{file_type}\.csv$")
    pattern2 = re.compile(rf"(\d{{8}})_(\d{{2}})g(\d{{2}})p_{file_type}\.csv$")
    
    valid_files = []
    for f in csv_files:
        basename = os.path.basename(f)
        match1 = pattern1.search(basename)
        match2 = pattern2.search(basename)
        
        if match1:
            date_str = match1.group(1)
            time_str = match1.group(2)
            timestamp = int(date_str + time_str)
            valid_files.append((timestamp, f))
        elif match2:
            date_str = match2.group(1)
            hour = match2.group(2).zfill(2)
            minute = match2.group(3).zfill(2)
            timestamp = int(date_str + hour + minute + "00")
            valid_files.append((timestamp, f))
    
    if not valid_files:
        raise FileNotFoundError(f"Không tìm thấy file CSV nào có định dạng hợp lệ trong {directory}")
    
    valid_files.sort(key=lambda x: x[0], reverse=True)
    latest_file = valid_files[0][1]
    
    print(f"Da chon file CSV moi nhat: {os.path.basename(latest_file)}")
    return latest_file

def parse_csv_data(filepath: str) -> list[SensorEvent]:
    events = []
    
    with open(filepath, "r", encoding="utf-8") as f:
        first_line = f.readline()
        f.seek(0)
        
        # Check if it's the new standard CSV format
        if "frame_counter" in first_line:
            import csv
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt_val = float(row['dt'])
                    # Sanitize dt to prevent simulation breaking with corrupted data
                    if dt_val < 0 or dt_val > 5.0:
                        dt_val = 0.01  # Default to 10ms
                    # If Predict row has 0 dt, assume 100Hz (0.01s)
                    if dt_val == 0.0 and row['status'].strip() == 'Predict':
                        dt_val = 0.01
                    
                    events.append(SensorEvent(
                        type=row['status'].strip(),
                        ax=float(row['ax']),
                        ay=float(row['ay']),
                        gz=float(row['gz']),
                        px=float(row['px']) if row.get('px') else 0.0,
                        py=float(row['py']) if row.get('py') else 0.0,
                        distances=np.array([float(row['d1']), float(row['d2']), float(row['d3']), float(row['d4'])]),
                        dt=dt_val,
                        mask=int(row['mask']) if row.get('mask') else 0,
                        raw_line=",".join(str(v) for v in row.values())
                    ))
                except (ValueError, KeyError, TypeError):
                    # Skip corrupted rows missing necessary data
                    continue
            return events

    # Fallback to older text-based regex parser
    init_pattern = re.compile(
        r"Init\s+"
        r"(?:bias_ax|ax):\s*(?P<ax>[-\d.]+)\s+"
        r"(?:bias_ay|ay):\s*(?P<ay>[-\d.]+)\s+"
        r"(?:bias_gz|gz):\s*(?P<gz>[-\d.]+)\s+"
        r"(?:px:\s*(?P<px>[-\d.]+)\s+py:\s*(?P<py>[-\d.]+)\s+)?"
        r"dt:\s*(?P<dt>[-\d.]+)\s+"
        r"(?:mask:\s*(?P<mask>\d+)\s+)?"
        r"d1:\s*(?P<d1>[-\d.]+)\s+"
        r"d2:\s*(?P<d2>[-\d.]+)\s+"
        r"d3:\s*(?P<d3>[-\d.]+)\s+"
        r"d4:\s*(?P<d4>[-\d.]+)"
        r"(?:\s+err:\s*\d+)?"
    )

    event_pattern = re.compile(
        r"(?P<type>Predict|Update)\s+"
        r"ax:\s*(?P<ax>[-\d.]+)\s+"
        r"ay:\s*(?P<ay>[-\d.]+)\s+"
        r"gz:\s*(?P<gz>[-\d.]+)\s+"
        r"(?:px:\s*(?P<px>[-\d.]+)\s+py:\s*(?P<py>[-\d.]+)\s+)?"
        r"dt:\s*(?P<dt>[-\d.]+)\s+"
        r"(?:mask:\s*(?P<mask>\d+)\s+)?"
        r"d1:\s*(?P<d1>[-\d.]+)\s+"
        r"d2:\s*(?P<d2>[-\d.]+)\s+"
        r"d3:\s*(?P<d3>[-\d.]+)\s+"
        r"d4:\s*(?P<d4>[-\d.]+)"
        r"(?:\s+err:\s*\d+)?"
    )
    
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            m_init = init_pattern.search(line)
            if m_init:
                ax = float(m_init.group("ax"))
                ay = float(m_init.group("ay"))
                gz = float(m_init.group("gz"))
                px = float(m_init.group("px")) if m_init.group("px") else 0.0
                py = float(m_init.group("py")) if m_init.group("py") else 0.0
                mask = int(m_init.group("mask")) if m_init.group("mask") else 0
                distances = np.array([float(m_init.group(f"d{i}")) for i in range(1, 5)])
                events.append(SensorEvent(type="Init", ax=ax, ay=ay, gz=gz, px=px, py=py, distances=distances, dt=0.0, mask=mask, raw_line=line.strip()))
                continue

            m_event = event_pattern.search(line)
            if m_event:
                type_ = m_event.group("type")
                ax = float(m_event.group("ax"))
                ay = float(m_event.group("ay"))
                gz = float(m_event.group("gz"))
                px = float(m_event.group("px")) if m_event.group("px") else 0.0
                py = float(m_event.group("py")) if m_event.group("py") else 0.0
                dt = float(m_event.group("dt"))
                mask = int(m_event.group("mask")) if m_event.group("mask") else 0
                distances = np.array([float(m_event.group(f"d{i}")) for i in range(1, 5)])
                events.append(SensorEvent(type=type_, ax=ax, ay=ay, gz=gz, px=px, py=py, distances=distances, dt=dt, mask=mask, raw_line=line.strip()))
    return events

from datetime import datetime
from .config import PREDICT_THRESHOLD

def generate_timestamp_filename(prefix, suffix):
    """Generate filename with current timestamp in format: YYYYMMDD_HgMMp_ukf_log_data.csv"""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%Hg%Mp")
    date_folder = now.strftime("%d_%m_%y")
    # Base directory relative to this file's location
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    directory = os.path.join(base_dir, "csv")
    date_directory = os.path.join(directory, date_folder)
    if not os.path.exists(date_directory):
        os.makedirs(date_directory)
    filename = os.path.join(date_directory, f"{timestamp}_{prefix}{suffix}")
    return filename

def create_csv_file(filename):
    import csv
    csv_file = open(filename, 'w', newline='')
    csv_writer = csv.writer(csv_file)
    print(f"[INFO] CSV file created: {filename}")
    return csv_file, csv_writer

def write_frame_to_csv(csv_writer, frame_data, frame_counter, prev_distances):
    counter_str = f"({frame_counter:4d}/{frame_data['tx_frame_cnt']:4d})"
    
    if frame_data['tx_frame_cnt'] == 1:
        line = f"{counter_str} Init    ax: {frame_data['ax']:9.6f} ay: {frame_data['ay']:9.6f} gz: {frame_data['gz']:9.6f} px: {frame_data['px']:9.6f} py: {frame_data['py']:9.6f} dt: {frame_data['dt']:9.6f} mask: {frame_data['anchor_mask']} d1: {frame_data['distances'][0]:9.6f} d2: {frame_data['distances'][1]:9.6f} d3: {frame_data['distances'][2]:9.6f} d4: {frame_data['distances'][3]:9.6f} err: {frame_data['err_cnt']} amp1: {frame_data['fp_amp_norm'][0]:9.6f} amp2: {frame_data['fp_amp_norm'][1]:9.6f} amp3: {frame_data['fp_amp_norm'][2]:9.6f} amp4: {frame_data['fp_amp_norm'][3]:9.6f} snr1: {frame_data['fp_snr'][0]:9.6f} snr2: {frame_data['fp_snr'][1]:9.6f} snr3: {frame_data['fp_snr'][2]:9.6f} snr4: {frame_data['fp_snr'][3]:9.6f}"
        csv_writer.writerow([line])
        return "Init", frame_data['distances'].copy()
        
    status = "Predict"
    all_distances_zero = all(abs(d) < 1e-6 for d in frame_data['distances'])
    if not all_distances_zero:
        if prev_distances is not None:
            distances_changed = False
            for i in range(len(frame_data['distances'])):
                if abs(frame_data['distances'][i] - prev_distances[i]) > PREDICT_THRESHOLD:
                    distances_changed = True
                    break
            if distances_changed:
                status = "Update"
    
    new_prev_distances = frame_data['distances'].copy()
    line = f"{counter_str} {status:7s} ax: {frame_data['ax']:9.6f} ay: {frame_data['ay']:9.6f} gz: {frame_data['gz']:9.6f} px: {frame_data['px']:9.6f} py: {frame_data['py']:9.6f} dt: {frame_data['dt']:9.6f} mask: {frame_data['anchor_mask']} d1: {frame_data['distances'][0]:9.6f} d2: {frame_data['distances'][1]:9.6f} d3: {frame_data['distances'][2]:9.6f} d4: {frame_data['distances'][3]:9.6f} err: {frame_data['err_cnt']} amp1: {frame_data['fp_amp_norm'][0]:9.6f} amp2: {frame_data['fp_amp_norm'][1]:9.6f} amp3: {frame_data['fp_amp_norm'][2]:9.6f} amp4: {frame_data['fp_amp_norm'][3]:9.6f} snr1: {frame_data['fp_snr'][0]:9.6f} snr2: {frame_data['fp_snr'][1]:9.6f} snr3: {frame_data['fp_snr'][2]:9.6f} snr4: {frame_data['fp_snr'][3]:9.6f}"
    csv_writer.writerow([line])
    return status, new_prev_distances

def write_uwb_frame_to_csv(csv_writer, frame_data, rx_cnt):
    # Output format: (  23/  24) d1:  0.000000 d2:  0.000000 d3:  0.000000 d4:  0.000000
    counter_str = f"({rx_cnt:4d}/{frame_data['tx_frame_cnt']:4d})"
    d = frame_data['distances']
    line = f"{counter_str} d1: {d[0]:9.6f} d2: {d[1]:9.6f} d3: {d[2]:9.6f} d4: {d[3]:9.6f}"
    csv_writer.writerow([line])
    return line

def write_fusion_frame_to_csv(csv_writer, frame_data, rx_cnt):
    counter_str = f"({rx_cnt:4d}/{frame_data['tx_frame_cnt']:4d})"
    ukf_step = int(frame_data.get('ukf_step', frame_data.get('error_count', 0)))
    status = "Update" if ukf_step == 1 else "Predict"
    line = (
        f"{counter_str} {status:7s} "
        f"ukf_step: {ukf_step} "
        f"dt: {frame_data.get('dt', 0.0):9.6f} "
        f"ukf_x: {frame_data['ukf_x']:9.6f} "
        f"ukf_y: {frame_data['ukf_y']:9.6f} "
        f"ukf_yaw: {frame_data['ukf_yaw']:9.6f} "
        f"tril_x: {frame_data['tril_x']:9.6f} "
        f"tril_y: {frame_data['tril_y']:9.6f} "
        f"yaw: {frame_data['yaw']:9.6f} "
        f"mask: {frame_data.get('anchor_mask', 0)} "
        f"err: {frame_data.get('error_count', frame_data.get('err_cnt', 0))}"
    )
    csv_writer.writerow([line])
    return status

def print_frame_data(frame_data):
    print(f"Frame #{frame_data['tx_frame_cnt']}: "
          f"ax={frame_data['ax']:.3f}, "
          f"ay={frame_data['ay']:.3f}, "
          f"gz={frame_data['gz']:.3f}, "
          f"px={frame_data['px']:.3f}, "
          f"py={frame_data['py']:.3f}, "
          f"dt={frame_data['dt']:.6f}, "
          f"mask={frame_data['anchor_mask']}, "
          f"dist={[f'{d:.3f}' for d in frame_data['distances']]}, "
          f"err={frame_data['err_cnt']}, "
          f"amp={[f'{a:.3f}' for a in frame_data['fp_amp_norm']]}, "
          f"snr={[f'{s:.3f}' for s in frame_data['fp_snr']]}")
