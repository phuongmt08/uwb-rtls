#!/usr/bin/env python3
"""
UWB Antenna Delay Calculator
Tính toán antenna delay từ dữ liệu đo thực tế
"""

import os
import sys
import io
from datetime import datetime

# Đảm bảo in được emoji UTF-8 trên Windows console
if sys.platform.startswith('win'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Hằng số
DEFAULT_TARGET = 5.78
DEFAULT_UNITS = 114
DEFAULT_DELAY = 16436
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "antenna_config.txt")

def read_config():
    """Đọc file config"""
    data = {'target': DEFAULT_TARGET, 'units': DEFAULT_UNITS, 'anchors': {}}
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Đọc cấu hình chung
            if '=' in line:
                key, val = line.split('=')
                key = key.strip()
                val = val.strip()
                if key == 'TARGET_DISTANCE':
                    data['target'] = float(val)
                elif key == 'UNITS_PER_METER':
                    data['units'] = float(val)
                continue
            
            # Đọc anchor: A1, 16436, 5.84, 6.05
            parts = [p.strip() for p in line.split(',')]
            if len(parts) == 4:
                anchor = parts[0]
                delay = int(parts[1])
                min_val = float(parts[2])
                max_val = float(parts[3])
                data['anchors'][anchor] = {
                    'current_delay': delay,
                    'measured_range': (min_val, max_val)
                }
        
        return data
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"❌ Lỗi đọc file: {e}")
        return None

def write_config(data):
    """Ghi file config"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write("# UWB Antenna Delay Configuration\n")
        f.write(f"# Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("# ============================================\n")
        f.write(f"TARGET_DISTANCE = {data['target']}\n")
        f.write(f"UNITS_PER_METER = {data['units']}\n\n")
        f.write("# anchor, current_delay, min_measured, max_measured\n")
        for anchor, info in sorted(data['anchors'].items()):
            min_val, max_val = info['measured_range']
            f.write(f"{anchor}, {info['current_delay']}, {min_val:.2f}, {max_val:.2f}\n")

def calculate_delay(anchor, measured_range, current_delay, target, units):
    """Tính antenna delay mới"""
    avg = (measured_range[0] + measured_range[1]) / 2
    error = avg - target
    # Tăng antenna delay -> distance giảm, giảm antenna delay -> distance tăng
    # Vì vậy delta cùng chiều với error (error > 0 thì cần tăng delay để giảm khoảng cách đo được)
    delta = error * units
    new_delay = int(current_delay + delta)
    
    return {
        'anchor': anchor,
        'avg': avg,
        'range': measured_range,
        'error': error,
        'delta': delta,
        'old': current_delay,
        'new': new_delay
    }

def mode1_manual():
    """Mode 1: Nhập thủ công"""
    print("\n" + "="*80)
    print("📝 MODE 1: NHẬP DỮ LIỆU THỦ CÔNG")
    print("="*80)
    
    # Đọc cấu hình từ file config nếu tồn tại
    existing_data = read_config()
    if existing_data is None:
        existing_data = {
            'target': DEFAULT_TARGET,
            'units': DEFAULT_UNITS,
            'anchors': {}
        }
    
    # Nhập cấu hình chung
    target_val = existing_data.get('target', DEFAULT_TARGET)
    target_input = input(f"Giá trị chuẩn (m) [Mặc định từ file: {target_val}]: ").strip()
    target = float(target_input) if target_input else target_val
    existing_data['target'] = target
    
    units_val = existing_data.get('units', DEFAULT_UNITS)
    units_input = input(f"Hệ số quy đổi (units/m) [Mặc định từ file: {units_val}]: ").strip()
    units = float(units_input) if units_input else units_val
    existing_data['units'] = units
    
    # Ghi file cấu hình sau khi cập nhật target và units
    write_config(existing_data)
    
    print("\nChọn chế độ tính toán:")
    print("  1. Tính cho cả 4 anchor (lần lượt từ A1 đến A4)")
    print("  2. Tính cho từng anchor cụ thể")
    
    while True:
        mode_choice = input("Chọn (1/2): ").strip()
        if mode_choice in ('1', '2'):
            break
        print("❌ Vui lòng nhập 1 hoặc 2!")
        
    def run_calculation_for_anchor(anchor_name):
        print(f"\n🔹 {anchor_name}:")
        
        # Nhập delay
        anchor_info = existing_data['anchors'].get(anchor_name, {})
        current_delay_val = anchor_info.get('current_delay', DEFAULT_DELAY)
        delay_input = input(f"  Delay hiện tại [Mặc định từ file: {current_delay_val}]: ").strip()
        current_delay = int(delay_input) if delay_input else current_delay_val
        
        # Nhập khoảng đo
        measured_range_val = anchor_info.get('measured_range', None)
        if measured_range_val:
            range_hint = f"{measured_range_val[0]:.2f}-{measured_range_val[1]:.2f}"
        else:
            range_hint = "chưa có"
            
        while True:
            range_input = input(f"  Khoảng đo (m) [Mặc định từ file: {range_hint}]: ").strip()
            if not range_input:
                if measured_range_val:
                    min_val, max_val = measured_range_val
                    break
                else:
                    print("⚠️ Chưa có khoảng đo trong file config cho anchor này. Vui lòng nhập khoảng đo!")
                    continue
            
            try:
                if '-' in range_input:
                    min_val, max_val = map(float, range_input.split('-'))
                else:
                    val = float(range_input)
                    min_val = max_val = val
                break
            except Exception:
                print("❌ Sai định dạng! Nhập theo dạng: 6.34-6.53 hoặc một số thực.")
                
        # Tính toán
        result = calculate_delay(anchor_name, (min_val, max_val), current_delay, existing_data['target'], existing_data['units'])
        print(f"  ✅ Kết quả tính cho {anchor_name}:")
        print(f"     Sai số: {result['error']:+.3f} m")
        print(f"     Delay mới: {result['old']} → {result['new']}")
        
        # Cập nhật trực tiếp vào file config (gồm delay mới và khoảng đo)
        existing_data['anchors'][anchor_name] = {
            'current_delay': result['new'],
            'measured_range': (min_val, max_val)
        }
        write_config(existing_data)
        print(f"  💾 Đã lưu cấu hình mới của {anchor_name} vào {CONFIG_FILE} (delay = {result['new']})")
        return result

    if mode_choice == '1':
        print("\n📊 BẮT ĐẦU TÍNH CHO CẢ 4 ANCHOR:")
        print("-"*80)
        for i in range(1, 5):
            run_calculation_for_anchor(f"A{i}")
        return existing_data
        
    else:
        calculated_results = {}
        first_prompt = True
        
        while True:
            if first_prompt:
                anchor_num_str = input("\nNhập số anchor muốn tính (1-4): ").strip()
                first_prompt = False
            else:
                anchor_num_str = input("\nNhập tiếp số anchor (1-4) hoặc 'q' để thoát: ").strip()
                
            if anchor_num_str.lower() == 'q':
                print("\n" + "="*80)
                print("📋 KẾT QUẢ ANTENNA DELAY ĐÃ TÍNH TRONG PHIÊN NÀY:")
                print("="*80)
                if not calculated_results:
                    print("  Không có anchor nào được tính toán mới.")
                else:
                    print(f"{'Anchor':<8} {'Đo được (m)':<18} {'TB (m)':<10} {'Sai số':<12} {'Delay cũ':<10} {'Delay mới':<10}")
                    print("-"*80)
                    for anchor_name, res in calculated_results.items():
                        range_str = f"{res['range'][0]:.2f}–{res['range'][1]:.2f}"
                        if res['range'][0] == res['range'][1]:
                            range_str = f"{res['range'][0]:.2f}"
                        
                        print(f"{res['anchor']:<8} {range_str:<18} {res['avg']:.3f}{'':<7} "
                              f"{res['error']:+.3f}{'':<9} {res['old']:<10} {res['new']:<10}")
                    print("-"*80)
                    print("\n📋 CODE CẤU HÌNH CHO CÁC ANCHOR ĐÃ TÍNH:")
                    for anchor_name, res in calculated_results.items():
                        num = anchor_name[1:]
                        print(f"#define ANCHOR_{num}_TX_ANT_DLY   {res['new']}")
                        print(f"#define ANCHOR_{num}_RX_ANT_DLY   {res['new']}")
                print("="*80)
                return None
            
            if anchor_num_str not in ('1', '2', '3', '4'):
                print("❌ Vui lòng nhập số từ 1 đến 4 hoặc 'q'!")
                continue
                
            anchor_name = f"A{anchor_num_str}"
            result = run_calculation_for_anchor(anchor_name)
            calculated_results[anchor_name] = result

def mode2_from_file():
    """Mode 2: Đọc từ file"""
    print("\n" + "="*80)
    print("📂 MODE 2: ĐỌC DỮ LIỆU TỪ FILE")
    print("="*80)
    
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ Không tìm thấy file: {CONFIG_FILE}")
        create = input("Tạo file template mới? (y/n): ").strip().lower()
        if create == 'y':
            # Tạo file template
            data = {
                'target': DEFAULT_TARGET,
                'units': DEFAULT_UNITS,
                'anchors': {
                    'A1': {'current_delay': 16436, 'measured_range': (5.84, 6.05)},
                    'A2': {'current_delay': 16436, 'measured_range': (6.34, 6.53)},
                    'A3': {'current_delay': 16436, 'measured_range': (5.23, 5.58)},
                    'A4': {'current_delay': 16436, 'measured_range': (5.27, 5.43)}
                }
            }
            write_config(data)
            print(f"✅ Đã tạo file template: {CONFIG_FILE}")
            print("📝 Hãy sửa file và chạy lại!")
        return None
    
    # Đọc file
    data = read_config()
    if not data or not data['anchors']:
        print("❌ File không có dữ liệu hoặc bị lỗi!")
        return None
    
    # Hiển thị dữ liệu
    print("\n✅ Dữ liệu từ file:")
    print("-"*80)
    print(f"Giá trị chuẩn: {data['target']} m")
    print(f"Hệ số: {data['units']} units/m\n")
    
    for anchor, info in sorted(data['anchors'].items()):
        min_val, max_val = info['measured_range']
        print(f"  {anchor}: delay={info['current_delay']}, đo={min_val:.2f}–{max_val:.2f} m")
    
    return data

def print_results(data):
    """In kết quả"""
    if not data or not data['anchors']:
        return
    
    print("\n" + "="*80)
    print("📊 KẾT QUẢ TÍNH TOÁN")
    print("="*80)
    print(f"Giá trị chuẩn: {data['target']} m")
    print(f"Hệ số quy đổi: {data['units']} units/m")
    print("-"*80)
    
    # Tính toán
    results = []
    for anchor, info in data['anchors'].items():
        result = calculate_delay(
            anchor,
            info['measured_range'],
            info['current_delay'],
            data['target'],
            data['units']
        )
        results.append(result)
    
    # Bảng kết quả
    print(f"{'Anchor':<8} {'Đo được (m)':<18} {'TB (m)':<10} {'Sai số':<12} {'Delay cũ':<10} {'Delay mới':<10}")
    print("-"*80)
    
    for r in results:
        range_str = f"{r['range'][0]:.2f}–{r['range'][1]:.2f}"
        if r['range'][0] == r['range'][1]:
            range_str = f"{r['range'][0]:.2f}"
        
        print(f"{r['anchor']:<8} {range_str:<18} {r['avg']:.3f}{'':<7} "
              f"{r['error']:+.3f}{'':<9} {r['old']:<10} {r['new']:<10}")
    
    print("-"*80)
    
    # Code cấu hình
    print("\n📋 CODE CẤU HÌNH:")
    print("-"*80)
    
    # Kiểm tra dùng chung
    all_same = all(r['new'] == results[0]['new'] for r in results)
    
    if all_same and len(results) > 1:
        print(f"#define ANCHOR_DEFAULT_TX_ANT_DLY   {results[0]['new']}")
        print(f"#define ANCHOR_DEFAULT_RX_ANT_DLY   {results[0]['new']}")
        print("\n# Hoặc dùng riêng:")
    
    for r in results:
        num = r['anchor'][1:]
        print(f"#define ANCHOR_{num}_TX_ANT_DLY   {r['new']}")
        print(f"#define ANCHOR_{num}_RX_ANT_DLY   {r['new']}")
        if r != results[-1]:
            print()
    
    # Giá trị trung bình
    if len(results) > 1 and not all_same:
        avg_delay = int(sum(r['new'] for r in results) / len(results))
        print("\n" + "-"*80)
        print(f"📌 Giá trị chung (trung bình): {avg_delay}")
        print(f"#define ANCHOR_DEFAULT_TX_ANT_DLY   {avg_delay}")
        print(f"#define ANCHOR_DEFAULT_RX_ANT_DLY   {avg_delay}")
    
    print("="*80)
    
    # Lưu kết quả
    save = input("\n💾 Lưu kết quả ra file? (y/n): ").strip().lower()
    if save == 'y':
        filename = f"result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("UWB ANTENNA DELAY RESULTS\n")
            f.write("="*80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            for r in results:
                f.write(f"{r['anchor']}:\n")
                f.write(f"  Measured: {r['range'][0]:.2f}–{r['range'][1]:.2f} m (avg: {r['avg']:.3f} m)\n")
                f.write(f"  Error: {r['error']:+.3f} m\n")
                f.write(f"  Delay: {r['old']} → {r['new']}\n\n")
            
            f.write("-"*80 + "\n")
            f.write("CODE:\n")
            f.write("-"*80 + "\n")
            for r in results:
                num = r['anchor'][1:]
                f.write(f"#define ANCHOR_{num}_TX_ANT_DLY   {r['new']}\n")
                f.write(f"#define ANCHOR_{num}_RX_ANT_DLY   {r['new']}\n\n")
        
        print(f"✅ Đã lưu vào file: {filename}")

def main():
    """Hàm chính"""
    print("="*80)
    print("🔧 UWB ANTENNA DELAY CALCULATOR")
    print("="*80)
    
    print("\nChọn mode:")
    print("  1. Nhập dữ liệu thủ công")
    print("  2. Đọc từ file config")
    print("  3. Thoát")
    
    choice = input("\nChọn (1/2/3): ").strip()
    
    if choice == '1':
        data = mode1_manual()
        if data is None:
            return
    elif choice == '2':
        data = mode2_from_file()
    else:
        print("👋 Tạm biệt!")
        return
    
    if data and data['anchors']:
        print_results(data)
    else:
        print("❌ Không có dữ liệu để xử lý!")

if __name__ == "__main__":
    main()