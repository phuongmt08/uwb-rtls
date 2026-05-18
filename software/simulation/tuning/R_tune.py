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

# ===== CONFIGURE TRUE DISTANCES HERE (in meters) =====
D1_TRUE = 1.0  # Distance from tag to Anchor 1
D2_TRUE = 1.0  # Distance from tag to Anchor 2
D3_TRUE = 1.0  # Distance from tag to Anchor 3
D4_TRUE = 1.0  # Distance from tag to Anchor 4
# =====================================================

def main():
    if SOURCE_DATA_FILE is None:
        try:
            latest_file = find_latest_csv_file(file_type="uwb_log_data")
        except FileNotFoundError as e:
            print(e)
            return
    else:
        latest_file = SOURCE_DATA_FILE

    print(f"Reading {latest_file}...")

    line_re = re.compile(r'd1:\s*([-\d.]+)\s+d2:\s*([-\d.]+)\s+d3:\s*([-\d.]+)\s+d4:\s*([-\d.]+)')

    d1_list, d2_list, d3_list, d4_list = [], [], [], []

    with open(latest_file, 'r') as f:
        for line in f:
            match = line_re.search(line)
            if match:
                d1_list.append(float(match.group(1)))
                d2_list.append(float(match.group(2)))
                d3_list.append(float(match.group(3)))
                d4_list.append(float(match.group(4)))

    if not d1_list:
        print("No valid UWB data found in the file.")
        return

    print("-" * 60)
    print(f"📊 UWB Static Test — Measurement Noise (R) Tuning")
    print("-" * 60)
    print(f"Samples collected: {len(d1_list)}")
    
    # ===== CRITICAL: NEED GROUND TRUTH =====
    print(f"\n⚠️  IMPORTANT: You must provide GROUND TRUTH distances!")
    print(f"  - Measure actual distance from tag to each anchor using a ruler")
    print(f"  - Accuracy: <1cm error")
    print(f"  - Tag must be STATIONARY during data collection\n")
    
    # Use predefined constants for ground truth
    d1_true, d2_true, d3_true, d4_true = D1_TRUE, D2_TRUE, D3_TRUE, D4_TRUE
    
    # ===== CORRECT METHOD: RESIDUAL VARIANCE =====
    print("\n" + "-" * 60)
    print("Computing measurement noise from residuals...")
    
    # Convert to numpy arrays
    d1_arr = np.array(d1_list)
    d2_arr = np.array(d2_list)
    d3_arr = np.array(d3_list)
    d4_arr = np.array(d4_list)
    
    # Compute residuals
    residuals_d1 = d1_arr - d1_true
    residuals_d2 = d2_arr - d2_true
    residuals_d3 = d3_arr - d3_true
    residuals_d4 = d4_arr - d4_true
    
    # Variance of residuals = measurement noise
    R1 = np.var(residuals_d1)
    R2 = np.var(residuals_d2)
    R3 = np.var(residuals_d3)
    R4 = np.var(residuals_d4)
    
    # Average across anchors
    Ruwb = np.mean([R1, R2, R3, R4])
    
    # Statistics
    mean_d1 = np.mean(d1_arr)
    mean_d2 = np.mean(d2_arr)
    mean_d3 = np.mean(d3_arr)
    mean_d4 = np.mean(d4_arr)
    
    bias_d1 = mean_d1 - d1_true
    bias_d2 = mean_d2 - d2_true
    bias_d3 = mean_d3 - d3_true
    bias_d4 = mean_d4 - d4_true
    
    std_d1 = np.sqrt(R1)
    std_d2 = np.sqrt(R2)
    std_d3 = np.sqrt(R3)
    std_d4 = np.sqrt(R4)
    
    print("\n📏 Ground Truth vs Measurement:")
    print(f"  Anchor 1: True={d1_true:.3f}m, Mean={mean_d1:.3f}m, Bias={bias_d1:+.3f}m, Std={std_d1:.3f}m")
    print(f"  Anchor 2: True={d2_true:.3f}m, Mean={mean_d2:.3f}m, Bias={bias_d2:+.3f}m, Std={std_d2:.3f}m")
    print(f"  Anchor 3: True={d3_true:.3f}m, Mean={mean_d3:.3f}m, Bias={bias_d3:+.3f}m, Std={std_d3:.3f}m")
    print(f"  Anchor 4: True={d4_true:.3f}m, Mean={mean_d4:.3f}m, Bias={bias_d4:+.3f}m, Std={std_d4:.3f}m")
    
    print("\n📊 Measurement Noise Variance (R):")
    print(f"  R1 = {R1:.8f} m²")
    print(f"  R2 = {R2:.8f} m²")
    print(f"  R3 = {R3:.8f} m²")
    print(f"  R4 = {R4:.8f} m²")
    
    print("-" * 60)
    print(f"\n✅ MEASUREMENT NOISE PARAMETER:")
    print(f"  Ruwb = {Ruwb:.8f} m²")
    print(f"  Ruwb_std = {np.sqrt(Ruwb):.3f} m  (standard deviation)")
    print("-" * 60)
    
    print(f"\n💡 Usage in C code:")
    print(f"  #define R_uwb  {Ruwb:.6f}f")
    print("-" * 60)
    
    # Quality check
    if np.sqrt(Ruwb) > 0.5:
        print(f"\n⚠️  WARNING: Ruwb is large (std > 0.5m)")
        print(f"  - Check for multipath/NLOS")
        print(f"  - Verify anchor positions")
        print(f"  - Consider environment calibration")
    elif np.sqrt(Ruwb) < 0.05:
        print(f"\n✓ Excellent: Very low measurement noise (std < 5cm)")
    else:
        print(f"\n✓ Good: Measurement noise within typical range")
    
    print("-" * 60)

if __name__ == '__main__':
    main()