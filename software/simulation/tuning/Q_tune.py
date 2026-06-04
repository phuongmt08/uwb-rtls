import os
import sys
import glob
import re
import numpy as np

# Thêm đường dẫn để import từ thư mục module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from module.module_csv import find_latest_csv_file

# Đặt SOURCE_DATA_FILE = None để dò file mới nhất, hoặc trỏ đường dẫn cứng
SOURCE_DATA_FILE = None

def main():
    if SOURCE_DATA_FILE is None:
        try:
            latest_file = find_latest_csv_file(file_type="imu_log_data")
        except FileNotFoundError as e:
            print(e)
            return
    else:
        latest_file = SOURCE_DATA_FILE

    print(f"Reading {latest_file}...")

    line_re = re.compile(r'ax:\s*([-\d.]+)\s+ay:\s*([-\d.]+)\s+gz:\s*([-\d.]+)')

    ax_list, ay_list, gz_list = [], [], []

    with open(latest_file, 'r') as f:
        for line in f:
            match = line_re.search(line)
            if match:
                ax_list.append(float(match.group(1)))
                ay_list.append(float(match.group(2)))
                gz_list.append(float(match.group(3)))

    if not ax_list:
        print("No valid IMU data found in the file.")
        return

    # ===== QUAN TRỌNG: ĐÂY LÀ DYNAMIC DATA HAY STATIC DATA? =====
    # Kiểm tra xem robot có di chuyển không
    ax_range = max(ax_list) - min(ax_list)
    ay_range = max(ay_list) - min(ay_list)
    gz_range = max(gz_list) - min(gz_list)
    
    print(f"\nData characteristics:")
    print(f"  ax range: {ax_range:.4f} m/s²")
    print(f"  ay range: {ay_range:.4f} m/s²")
    print(f"  gz range: {gz_range:.4f} rad/s")
    
    # Nếu range quá nhỏ → data tĩnh → KHÔNG DÙNG ĐƯỢC
    if ax_range < 0.1 and ay_range < 0.1 and gz_range < 0.01:
        print("\n⚠️  WARNING: Data appears STATIC (robot not moving)")
        print("⚠️  This is sensor noise, NOT process noise Q!")
        print("⚠️  Please collect data while robot is MOVING:")
        print("    - Push robot in square 2x2m")
        print("    - Include acceleration and turns")
        print("    - Data should show clear motion patterns\n")
        
        # Vẫn tính để so sánh
        Qax_sensor = np.var(ax_list)
        Qay_sensor = np.var(ay_list)
        Qgz_sensor = np.var(gz_list)
        
        print("Sensor noise variance (for reference only):")
        print(f"  Qax_sensor: {Qax_sensor:.8e}")
        print(f"  Qay_sensor: {Qay_sensor:.8e}")
        print(f"  Qgz_sensor: {Qgz_sensor:.8e}")
        print(f"\nExpected Q should be 10-100× larger:")
        print(f"  Qa_min: {(Qax_sensor + Qay_sensor) * 10 / 2:.8e}")
        print(f"  Qa_max: {(Qax_sensor + Qay_sensor) * 100 / 2:.8e}")
        print(f"  Qg_min: {Qgz_sensor * 10:.8e}")
        print(f"  Qg_max: {Qgz_sensor * 100:.8e}")
        return
    
    # Data động → OK
    print("\n✓ Data appears DYNAMIC (robot moving)")
    
    # Tính variance
    Qax = np.var(ax_list)
    Qay = np.var(ay_list)
    Qgz = np.var(gz_list)
    
    # Trung bình ax, ay cho Qa
    Qa = (Qax + Qay) / 2
    Qg = Qgz
    
    print("-" * 50)
    print(f"Samples collected: {len(ax_list)}")
    print(f"\nVariances:")
    print(f"  Qax: {Qax:.8e} (m/s²)²")
    print(f"  Qay: {Qay:.8e} (m/s²)²")
    print(f"  Qgz: {Qgz:.8e} (rad/s)²")
    print("-" * 50)
    print(f"\n📊 BASELINE PROCESS NOISE (Q):")
    print(f"  Q_A = {Qa:.8e}")
    print(f"  Q_G = {Qg:.8e}")
    print("-" * 50)
    print(f"\n💡 Usage in C code:")
    print(f"  #define Qa  {Qa:.3e}f")
    print(f"  #define Qg  {Qg:.3e}f")
    print("-" * 50)
    print(f"\n⚠️  NOTE: These are BASELINE values")
    print(f"  - Use for initial UKF run")
    print(f"  - Fine-tune with NIS binary search")
    print(f"  - Final Q may be 0.5× ~ 2× of baseline")
    print("-" * 50)

if __name__ == '__main__':
    main()