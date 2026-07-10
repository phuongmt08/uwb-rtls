import os
import sys
import glob
import re
import numpy as np
import matplotlib.pyplot as plt

# Thêm đường dẫn để import từ thư mục module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from module.module_csv import find_latest_csv_file

# Đặt SOURCE_DATA_FILE = None để dò file mới nhất, hoặc trỏ đường dẫn cứng
SOURCE_DATA_FILE = None

# ===== CONFIGURE TRUE DISTANCES HERE (in meters) =====
D1_TRUE = 0.0  # Distance from tag to Anchor 1
D2_TRUE = 3.4507  # Distance from tag to Anchor 2
D3_TRUE = 10.3520  # Distance from tag to Anchor 3
D4_TRUE = 7.7156  # Distance from tag to Anchor 4
# =====================================================

# ===== OUTLIER DETECTION SETTINGS =====
OUTLIER_METHOD = 'iqr'  # 'iqr', 'zscore', or 'none'
IQR_MULTIPLIER = 1.5    # Standard: 1.5 (conservative), 3.0 (loose)
ZSCORE_THRESHOLD = 3.0  # Standard: 3.0 sigma
# ======================================

def detect_outliers_iqr(data, multiplier=1.5):
    """
    Detect outliers using Interquartile Range (IQR) method.
    More robust than Z-score for skewed distributions.
    
    Returns: boolean mask (True = inlier, False = outlier)
    """
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1
    
    lower_bound = q1 - multiplier * iqr
    upper_bound = q3 + multiplier * iqr
    
    mask = (data >= lower_bound) & (data <= upper_bound)
    return mask

def detect_outliers_zscore(data, threshold=3.0):
    """
    Detect outliers using Z-score method.
    Good for normally distributed data.
    
    Returns: boolean mask (True = inlier, False = outlier)
    """
    mean = np.mean(data)
    std = np.std(data)
    
    if std == 0:  # All values identical
        return np.ones(len(data), dtype=bool)
    
    z_scores = np.abs((data - mean) / std)
    mask = z_scores < threshold
    return mask

def filter_outliers(data, method='iqr', **kwargs):
    """
    Filter outliers from data array.
    
    Args:
        data: numpy array
        method: 'iqr', 'zscore', or 'none'
        **kwargs: additional parameters for detection methods
    
    Returns:
        filtered_data, outlier_mask, num_outliers
    """
    if method == 'none':
        return data, np.ones(len(data), dtype=bool), 0
    elif method == 'iqr':
        multiplier = kwargs.get('multiplier', 1.5)
        mask = detect_outliers_iqr(data, multiplier)
    elif method == 'zscore':
        threshold = kwargs.get('threshold', 3.0)
        mask = detect_outliers_zscore(data, threshold)
    else:
        raise ValueError(f"Unknown method: {method}")
    
    filtered_data = data[mask]
    num_outliers = np.sum(~mask)
    
    return filtered_data, mask, num_outliers

def plot_outliers(data, mask, title, d_true):
    """
    Visualize data with outliers marked.
    """
    plt.figure(figsize=(12, 4))
    
    # Plot 1: Time series
    plt.subplot(1, 2, 1)
    inliers = data[mask]
    outliers = data[~mask]
    inlier_idx = np.where(mask)[0]
    outlier_idx = np.where(~mask)[0]
    
    plt.plot(inlier_idx, inliers, 'b.', alpha=0.5, label='Inliers')
    plt.plot(outlier_idx, outliers, 'rx', markersize=8, label='Outliers')
    plt.axhline(d_true, color='g', linestyle='--', label='Ground Truth')
    plt.xlabel('Sample index')
    plt.ylabel('Distance (m)')
    plt.title(f'{title} — Time Series')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    # Plot 2: Histogram
    plt.subplot(1, 2, 2)
    plt.hist(inliers, bins=50, alpha=0.7, label='Inliers', edgecolor='black')
    plt.hist(outliers, bins=20, alpha=0.7, color='red', label='Outliers', edgecolor='black')
    plt.axvline(d_true, color='g', linestyle='--', linewidth=2, label='Ground Truth')
    plt.xlabel('Distance (m)')
    plt.ylabel('Count')
    plt.title(f'{title} — Distribution')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(f'R_tune_{title.lower().replace(" ", "_")}.png', dpi=150)
    print(f"  Plot saved: R_tune_{title.lower().replace(' ', '_')}.png")

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
    print(f"Total samples collected: {len(d1_list)}")
    
    # Use predefined constants for ground truth
    d1_true, d2_true, d3_true, d4_true = D1_TRUE, D2_TRUE, D3_TRUE, D4_TRUE
    
    print(f"\n📏 Ground Truth Distances:")
    print(f"  Anchor 1: {d1_true:.3f} m")
    print(f"  Anchor 2: {d2_true:.3f} m")
    print(f"  Anchor 3: {d3_true:.3f} m")
    print(f"  Anchor 4: {d4_true:.3f} m")
    
    # ===== OUTLIER DETECTION =====
    print("\n" + "-" * 60)
    print(f"🔍 Outlier Detection: {OUTLIER_METHOD.upper()}")
    
    # Convert to numpy arrays
    d1_arr = np.array(d1_list)
    d2_arr = np.array(d2_list)
    d3_arr = np.array(d3_list)
    d4_arr = np.array(d4_list)
    
    # Filter outliers for each anchor
    if OUTLIER_METHOD == 'iqr':
        d1_clean, mask1, out1 = filter_outliers(d1_arr, 'iqr', multiplier=IQR_MULTIPLIER)
        d2_clean, mask2, out2 = filter_outliers(d2_arr, 'iqr', multiplier=IQR_MULTIPLIER)
        d3_clean, mask3, out3 = filter_outliers(d3_arr, 'iqr', multiplier=IQR_MULTIPLIER)
        d4_clean, mask4, out4 = filter_outliers(d4_arr, 'iqr', multiplier=IQR_MULTIPLIER)
        print(f"  Method: IQR with multiplier={IQR_MULTIPLIER}")
    elif OUTLIER_METHOD == 'zscore':
        d1_clean, mask1, out1 = filter_outliers(d1_arr, 'zscore', threshold=ZSCORE_THRESHOLD)
        d2_clean, mask2, out2 = filter_outliers(d2_arr, 'zscore', threshold=ZSCORE_THRESHOLD)
        d3_clean, mask3, out3 = filter_outliers(d3_arr, 'zscore', threshold=ZSCORE_THRESHOLD)
        d4_clean, mask4, out4 = filter_outliers(d4_arr, 'zscore', threshold=ZSCORE_THRESHOLD)
        print(f"  Method: Z-score with threshold={ZSCORE_THRESHOLD}")
    else:
        d1_clean, mask1, out1 = d1_arr, np.ones(len(d1_arr), dtype=bool), 0
        d2_clean, mask2, out2 = d2_arr, np.ones(len(d2_arr), dtype=bool), 0
        d3_clean, mask3, out3 = d3_arr, np.ones(len(d3_arr), dtype=bool), 0
        d4_clean, mask4, out4 = d4_arr, np.ones(len(d4_arr), dtype=bool), 0
        print(f"  Method: NONE (no filtering)")
    
    print(f"\n  Outliers detected:")
    print(f"    Anchor 1: {out1} / {len(d1_arr)} ({100*out1/len(d1_arr):.1f}%)")
    print(f"    Anchor 2: {out2} / {len(d2_arr)} ({100*out2/len(d2_arr):.1f}%)")
    print(f"    Anchor 3: {out3} / {len(d3_arr)} ({100*out3/len(d3_arr):.1f}%)")
    print(f"    Anchor 4: {out4} / {len(d4_arr)} ({100*out4/len(d4_arr):.1f}%)")
    print(f"    Total outliers: {out1+out2+out3+out4} / {4*len(d1_arr)} ({100*(out1+out2+out3+out4)/(4*len(d1_arr)):.1f}%)")
    
    # Warning if too many outliers
    total_outlier_rate = (out1+out2+out3+out4) / (4*len(d1_arr))
    if total_outlier_rate > 0.2:
        print(f"\n  ⚠️  WARNING: High outlier rate (>20%)!")
        print(f"    - Check for NLOS conditions")
        print(f"    - Verify anchor placement")
        print(f"    - Consider increasing IQR_MULTIPLIER or ZSCORE_THRESHOLD")
    
    # ===== COMPUTE R FROM CLEAN DATA =====
    print("\n" + "-" * 60)
    print("Computing measurement noise from residuals (clean data)...")
    
    # Compute residuals
    residuals_d1 = d1_clean - d1_true
    residuals_d2 = d2_clean - d2_true
    residuals_d3 = d3_clean - d3_true
    residuals_d4 = d4_clean - d4_true
    
    # Variance of residuals = measurement noise
    R1 = np.var(residuals_d1, ddof=1)  # ddof=1 for sample variance
    R2 = np.var(residuals_d2, ddof=1)
    R3 = np.var(residuals_d3, ddof=1)
    R4 = np.var(residuals_d4, ddof=1)
    
    # Average across anchors
    Ruwb = np.mean([R1, R2, R3, R4])
    
    # Statistics
    mean_d1 = np.mean(d1_clean)
    mean_d2 = np.mean(d2_clean)
    mean_d3 = np.mean(d3_clean)
    mean_d4 = np.mean(d4_clean)
    
    bias_d1 = mean_d1 - d1_true
    bias_d2 = mean_d2 - d2_true
    bias_d3 = mean_d3 - d3_true
    bias_d4 = mean_d4 - d4_true
    
    std_d1 = np.sqrt(R1)
    std_d2 = np.sqrt(R2)
    std_d3 = np.sqrt(R3)
    std_d4 = np.sqrt(R4)
    
    print("\n📏 Ground Truth vs Measurement (after outlier removal):")
    print(f"  Anchor 1: True={d1_true:.3f}m, Mean={mean_d1:.3f}m, Bias={bias_d1:+.4f}m, Std={std_d1:.4f}m")
    print(f"  Anchor 2: True={d2_true:.3f}m, Mean={mean_d2:.3f}m, Bias={bias_d2:+.4f}m, Std={std_d2:.4f}m")
    print(f"  Anchor 3: True={d3_true:.3f}m, Mean={mean_d3:.3f}m, Bias={bias_d3:+.4f}m, Std={std_d3:.4f}m")
    print(f"  Anchor 4: True={d4_true:.3f}m, Mean={mean_d4:.3f}m, Bias={bias_d4:+.4f}m, Std={std_d4:.4f}m")
    
    print("\n📊 Measurement Noise Variance (R) per anchor:")
    print(f"  R1 = {R1:.8f} m² (std = {std_d1:.4f} m)")
    print(f"  R2 = {R2:.8f} m² (std = {std_d2:.4f} m)")
    print(f"  R3 = {R3:.8f} m² (std = {std_d3:.4f} m)")
    print(f"  R4 = {R4:.8f} m² (std = {std_d4:.4f} m)")
    
    print("-" * 60)
    print(f"\n✅ MEASUREMENT NOISE PARAMETER:")
    print(f"  Ruwb = {Ruwb:.8f} m²")
    print(f"  Ruwb_std = {np.sqrt(Ruwb):.4f} m  (standard deviation)")
    print("-" * 60)
    
    print(f"\n💡 Usage in C code:")
    print(f"  #define R_uwb  {Ruwb:.6f}f")
    print("-" * 60)

    print(f"\n💡 Usage in Python code:")
    print(f"  R_UWB = {Ruwb:.6f}")
    print("-" * 60)
    
    # Quality check
    ruwb_std = np.sqrt(Ruwb)
    if ruwb_std > 0.5:
        print(f"\n⚠️  WARNING: Ruwb is large (std > 0.5m)")
        print(f"  - Check for multipath/NLOS")
        print(f"  - Verify anchor positions")
        print(f"  - Consider environment calibration")
    elif ruwb_std < 0.05:
        print(f"\n✓ Excellent: Very low measurement noise (std < 5cm)")
    elif ruwb_std < 0.15:
        print(f"\n✓ Good: Measurement noise within typical range")
    else:
        print(f"\n△ Fair: Measurement noise acceptable but could be better")
    
    # Check bias
    max_bias = max(abs(bias_d1), abs(bias_d2), abs(bias_d3), abs(bias_d4))
    if max_bias > 0.1:
        print(f"\n⚠️  WARNING: Large bias detected (max={max_bias:.3f}m)")
        print(f"  - UWB may have systematic error")
        print(f"  - Verify ground truth measurements")
        print(f"  - Consider antenna delay calibration")
    
    print("-" * 60)
    
    # ===== VISUALIZATION =====
    print("\n📊 Generating visualization plots...")
    try:
        plot_outliers(d1_arr, mask1, "Anchor 1", d1_true)
        plot_outliers(d2_arr, mask2, "Anchor 2", d2_true)
        plot_outliers(d3_arr, mask3, "Anchor 3", d3_true)
        plot_outliers(d4_arr, mask4, "Anchor 4", d4_true)
        print("\n✓ All plots saved successfully")
    except Exception as e:
        print(f"\n⚠️  Could not generate plots: {e}")
    
    print("-" * 60)

if __name__ == '__main__':
    main()