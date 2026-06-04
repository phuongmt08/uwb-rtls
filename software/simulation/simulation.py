"""UKF simulation for UWB + IMU sensor fusion.

This module simulates a 10x10 meter room with three anchors in three corners.
It generates a reference trajectory, noisy IMU and UWB measurements, then
runs an Unscented Kalman Filter to fuse the data and compare results.
"""

import numpy as np

import module.config as config
from module.module_csv import find_latest_csv_file, parse_csv_data
from module.module_kinematic import calculate_imu_dead_reckoning_path, calculate_uwb_only_path
from module.module_ukf import create_ukf_context, ukf_predict, ukf_update, init_ukf_logging
from module.module_plot import plot_input_data, plot_position_estimates, plot_position_results
from module.module_txt import UKFLogger
from module.config import IMUSample, ANCHOR_POSITIONS
from math import atan2

def run_simulation_with_params(params: dict, on_init_parsed=None) -> None:
    """Run the UWB + IMU UKF simulation with specific parameters from GUI."""
    
    # Update config from params
    config.SOURCE_DATA_FILE = params.get("source_data_file", config.SOURCE_DATA_FILE)
    if not config.SOURCE_DATA_FILE:
        try:
            
            config.SOURCE_DATA_FILE = find_latest_csv_file()
        except FileNotFoundError as e:
            print(e)
            return
            
    # Update UKF Parameters
    config.Q_A = params.get("q_a", config.Q_A)
    config.Q_G = params.get("q_g", config.Q_G)
    config.R_UWB = params.get("r_uwb", config.R_UWB)
    config.PROCESS_NOISE_COV = np.diag([config.Q_A, config.Q_A, config.Q_G])
    config.MEASUREMENT_NOISE_COV = np.diag([config.R_UWB, config.R_UWB, config.R_UWB])
    
    config.IMU_EMA_ALPHA = params.get("imu_ema_alpha", config.IMU_EMA_ALPHA)
    config.IMU_ZUPT_THRESHOLD = params.get("imu_zupt_threshold", config.IMU_ZUPT_THRESHOLD)
    config.IMU_ZUPT_FRAMES = int(params.get("imu_zupt_frames", config.IMU_ZUPT_FRAMES))
    config.OUTPUT_TXT_ENABLED = params.get("txt_enabled", False)
    
    # Update Environment & Anchors
    config.ROOM_SIZE_M = params.get("room_size_m", config.ROOM_SIZE_M)
    config.DRAW_RECTANGLE = params.get("draw_rectangle", config.DRAW_RECTANGLE)
    config.RECT_WIDTH = params.get("rect_width", config.RECT_WIDTH)
    config.RECT_HEIGHT = params.get("rect_height", config.RECT_HEIGHT)
    
    config.ANCHOR_1_X = params.get("anchor_1_x", config.ANCHOR_1_X)
    config.ANCHOR_1_Y = params.get("anchor_1_y", config.ANCHOR_1_Y)
    config.ANCHOR_2_X = params.get("anchor_2_x", config.ANCHOR_2_X)
    config.ANCHOR_2_Y = params.get("anchor_2_y", config.ANCHOR_2_Y)
    config.ANCHOR_3_X = params.get("anchor_3_x", config.ANCHOR_3_X)
    config.ANCHOR_3_Y = params.get("anchor_3_y", config.ANCHOR_3_Y)
    config.ANCHOR_4_X = params.get("anchor_4_x", config.ANCHOR_4_X)
    config.ANCHOR_4_Y = params.get("anchor_4_y", config.ANCHOR_4_Y)
    
    config.ANCHOR_POSITIONS = np.array([
        [config.ANCHOR_1_X, config.ANCHOR_1_Y],
        [config.ANCHOR_2_X, config.ANCHOR_2_Y],
        [config.ANCHOR_3_X, config.ANCHOR_3_Y],
        [config.ANCHOR_4_X, config.ANCHOR_4_Y]
    ])
    
    # Update UKF Constants
    config.UKF_ALPHA = params.get("ukf_alpha", config.UKF_ALPHA)
    config.UKF_KAPPA = params.get("ukf_kappa", config.UKF_KAPPA)
    config.UKF_BETA = params.get("ukf_beta", config.UKF_BETA)
    config.UKF_LAMBDA = config.UKF_ALPHA**2 * (config.UKF_AUGMENTED_SIZE + config.UKF_KAPPA) - config.UKF_AUGMENTED_SIZE
    config.UKF_GAMMA = np.sqrt(config.UKF_AUGMENTED_SIZE + config.UKF_LAMBDA)
    
    # Update Sensors
    config.TEST_UKF_Q_R_Params = params.get("test_ukf_q_r_params", config.TEST_UKF_Q_R_Params)
    
    # We will overwrite these biases with init_event values below, 
    # but we read them from GUI just in case they are used elsewhere.
    config.IMU_BIAS_AX = params.get("imu_bias_ax", getattr(config, 'IMU_BIAS_AX', 0.0))
    config.IMU_BIAS_AY = params.get("imu_bias_ay", getattr(config, 'IMU_BIAS_AY', 0.0))
    config.IMU_BIAS_GZ = params.get("imu_bias_gz", getattr(config, 'IMU_BIAS_GZ', 0.0))
    
    # config.IMU_NOISE_STD_AX = params.get("imu_noise_std_ax", config.IMU_NOISE_STD_AX)
    # config.IMU_NOISE_STD_AY = params.get("imu_noise_std_ay", config.IMU_NOISE_STD_AY)
    # config.IMU_NOISE_STD_GZ = params.get("imu_noise_std_gz", config.IMU_NOISE_STD_GZ)
    
    # config.UWB_NOISE_STD_M = params.get("uwb_noise_std_m", config.UWB_NOISE_STD_M)
    # config.UWB_COMM_LOSS_RATE = params.get("uwb_comm_loss_rate", config.UWB_COMM_LOSS_RATE)
    # config.UWB_OUTLIER_RATE = params.get("uwb_outlier_rate", config.UWB_OUTLIER_RATE)
    # config.UWB_OUTLIER_MULTIPLIER = params.get("uwb_outlier_mult", getattr(config, 'UWB_OUTLIER_MULTIPLIER', 4.0))
    
    print(f"Dang xu ly file: {config.SOURCE_DATA_FILE}")

    events = parse_csv_data(config.SOURCE_DATA_FILE)
    if not events:
        print("No data parsed from CSV.")
        return

    # Process Init event
    init_event = events[0]
    
    # Override GUI biases with init_event readings for zeroing
    config.IMU_BIAS_AX = init_event.ax
    config.IMU_BIAS_AY = init_event.ay
    config.IMU_BIAS_GZ = init_event.gz
    
    if on_init_parsed:
        on_init_parsed(init_event.ax, init_event.ay, init_event.gz)
        
    init_x, init_y = init_event.px, init_event.py
    # init_theta = atan2(init_y - config.ANCHOR_1_Y, init_x - config.ANCHOR_1_X)  # góc từ anchor 1 đến tag
    init_theta = 0.0  # góc từ anchor 1 đến tag
    # Pre-calculate dead reckoning for comparison
    imu_dead_reckoning = calculate_imu_dead_reckoning_path(
        events, (init_x, init_y, 0.0), config.IMU_BIAS_AX, config.IMU_BIAS_AY, config.IMU_BIAS_GZ)
        
    # Use INITIAL_P from config (which was updated by GUI)
    ukf_initial_state = np.array([
        init_x, init_y, 0.0, 0.0, init_theta,
        config.IMU_BIAS_AX, config.IMU_BIAS_AY, config.IMU_BIAS_GZ,
    ])

    variants = {
        "raw": {"run": params.get("run_raw", True), "txt": params.get("txt_raw", True)},
        "ema": {"run": params.get("run_ema", True), "txt": params.get("txt_ema", True)},
        "raw_zupt": {"run": params.get("run_raw_zupt", True), "txt": params.get("txt_raw_zupt", True)},
        "ema_zupt": {"run": params.get("run_ema_zupt", True), "txt": params.get("txt_ema_zupt", True)}
    }
    
    contexts = {}
    ukf_results = {}
    imu_plot_data = {}
    
    for name, v_conf in variants.items():
        if v_conf["run"]:
            ctx = create_ukf_context(ukf_initial_state)
            ctx.use_advanced_propagate = params.get("use_advanced_propagate", False)
            if config.OUTPUT_TXT_ENABLED and v_conf["txt"]:
                logger = UKFLogger()
                logger.init_file(config.SOURCE_DATA_FILE, suffix=name)
                ctx.logger = logger
            contexts[name] = ctx
            init_ukf_logging(ctx)
            
            ukf_results[name] = {
                "positions_x": [], "positions_y": [], "theta": [], "timestamps": []
            }
            imu_plot_data[name] = {"ax": [], "ay": [], "gz": []}

    stm_positions_x, stm_positions_y, stm_times = [], [], []
    
    # State tracking for EMA and ZUPT
    ema_alpha = config.IMU_EMA_ALPHA
    current_ax_ema, current_ay_ema, current_gz_ema = init_event.ax, init_event.ay, init_event.gz
    
    zupt_raw_counter = 0
    zupt_ema_counter = 0
    
    # Frequency tracking counters
    predict_count = 0
    update_count = 0
    stm_count = 0
    
    t = 0.0
    for event in events:
        t += event.dt
        # Calculate EMA
        if event.type in ["Predict", "Init"]:
            current_ax_ema = ema_alpha * event.ax + (1 - ema_alpha) * current_ax_ema
            current_ay_ema = ema_alpha * event.ay + (1 - ema_alpha) * current_ay_ema
            # current_gz_ema = ema_alpha * event.gz + (1 - ema_alpha) * current_gz_ema
            current_gz_ema = event.gz
            
        # Determine ZUPT condition
        if abs(event.ax) < config.IMU_ZUPT_THRESHOLD and abs(event.ay) < config.IMU_ZUPT_THRESHOLD:
            zupt_raw_counter += 1
        else:
            zupt_raw_counter = 0
            
        if abs(current_ax_ema) < config.IMU_ZUPT_THRESHOLD and abs(current_ay_ema) < config.IMU_ZUPT_THRESHOLD:
            zupt_ema_counter += 1
        else:
            zupt_ema_counter = 0
            
        is_raw_zupt_active = (zupt_raw_counter >= config.IMU_ZUPT_FRAMES)
        is_ema_zupt_active = (zupt_ema_counter >= config.IMU_ZUPT_FRAMES)
        
        # Determine inputs
        inputs = {}
        if "raw" in contexts:
            inputs["raw"] = IMUSample(ax=event.ax, ay=event.ay, gz=event.gz)
        if "ema" in contexts:
            inputs["ema"] = IMUSample(ax=current_ax_ema, ay=current_ay_ema, gz=current_gz_ema)
        if "raw_zupt" in contexts:
            inputs["raw_zupt"] = IMUSample(
                ax=0.0 if is_raw_zupt_active else event.ax,
                ay=0.0 if is_raw_zupt_active else event.ay,
                gz=event.gz
            )
        if "ema_zupt" in contexts:
            inputs["ema_zupt"] = IMUSample(
                ax=0.0 if is_ema_zupt_active else current_ax_ema,
                ay=0.0 if is_ema_zupt_active else current_ay_ema,
                gz=current_gz_ema
            )

        if event.type == "Predict":
            predict_count += 1
            failed_names = []
            for name, ctx in contexts.items():
                imu_sample = inputs[name]
                try:
                    ukf_predict(ctx, imu_sample, event.dt, event.raw_line)
                    
                    imu_plot_data[name]["ax"].append(imu_sample.ax)
                    imu_plot_data[name]["ay"].append(imu_sample.ay)
                    imu_plot_data[name]["gz"].append(imu_sample.gz)
                except Exception as e:
                    print(f"\n[{name}] LOI TAI BUOC PREDICT!")
                    print(f"Chi tiet loi: {e}")
                    print(f"Du lieu IMU gay loi: ax={imu_sample.ax}, ay={imu_sample.ay}, gz={imu_sample.gz}")
                    print(f"Dong raw log: {event.raw_line}")
                    # print(f"Ma tran P hien tai: \n{ctx.P}")
                    print(f"Ngung chay bien the '{name}' tu thoi diem nay de giu nguyen do thi.\n")
                    failed_names.append(name)
                    if ctx.logger:
                        ctx.logger.close()
                        
            for name in failed_names:
                del contexts[name]
                
        elif event.type == "Init":
            for name in contexts.keys():
                imu_sample = inputs[name]
                imu_plot_data[name]["ax"].append(imu_sample.ax)
                imu_plot_data[name]["ay"].append(imu_sample.ay)
                imu_plot_data[name]["gz"].append(imu_sample.gz)
                
        elif event.type == "Update":
            update_count += 1
            stm_positions_x.append(event.px)
            stm_positions_y.append(event.py)
            stm_times.append(t)
            
            # Count STM32 position updates (non-zero px or py)
            if abs(event.px) > 1e-6 or abs(event.py) > 1e-6:
                stm_count += 1
            
            d_meas_all = event.distances[:len(config.ANCHOR_POSITIONS)]
            
            # Sử dụng mask nếu có (lớn hơn 0), ngược lại fallback về check > 1e-6 cho log cũ
            if hasattr(event, 'mask') and getattr(event, 'mask') > 0:
                active_indices = [idx for idx in range(len(d_meas_all)) if (getattr(event, 'mask') & (1 << idx)) and d_meas_all[idx] > 1e-6]
            else:
                active_indices = [idx for idx, d in enumerate(d_meas_all) if d > 1e-6]
            
            if len(active_indices) >= 3:
                active_d_meas = d_meas_all[active_indices][:3]
                active_anchors = config.ANCHOR_POSITIONS[active_indices][:3]
                failed_names = []
                for name, ctx in contexts.items():
                    try:
                        ukf_update(ctx, active_d_meas, active_anchors, event.raw_line)
                    except Exception as e:
                        print(f"\n[{name}] LOI TAI BUOC UPDATE!")
                        print(f"Chi tiet loi: {e}")
                        print(f"Du lieu UWB gay loi: d_meas={active_d_meas}")
                        print(f"Dong raw log: {event.raw_line}")
                        print(f"Ma tran P hien tai: \n{ctx.P}")
                        print(f"Ngung chay bien the '{name}' tu thoi diem nay de giu nguyen do thi.\n")
                        failed_names.append(name)
                        if ctx.logger:
                            ctx.logger.close()
                            
                for name in failed_names:
                    del contexts[name]
                    
            # For update events where we don't predict, we pad the imu_plot_data with nan so times align
            for name in contexts.keys():
                imu_plot_data[name]["ax"].append(np.nan)
                imu_plot_data[name]["ay"].append(np.nan)
                imu_plot_data[name]["gz"].append(np.nan)

        else:
            # For any other event type, pad with nan
            for name in contexts.keys():
                imu_plot_data[name]["ax"].append(np.nan)
                imu_plot_data[name]["ay"].append(np.nan)
                imu_plot_data[name]["gz"].append(np.nan)

        for name, ctx in contexts.items():
            ukf_results[name]["positions_x"].append(ctx.x[0])
            ukf_results[name]["positions_y"].append(ctx.x[1])
            ukf_results[name]["theta"].append(ctx.x[4])
            ukf_results[name]["timestamps"].append(t)

    # Format output
    final_estimates = {}
    for name, res in ukf_results.items():
        final_estimates[name] = {
            "timestamps": np.array(res["timestamps"]),
            "x": np.array(res["positions_x"]),
            "y": np.array(res["positions_y"]),
            "theta": np.array(res["theta"]),
        }
        
    for name, res in imu_plot_data.items():
        res["ax"] = np.array(res["ax"])
        res["ay"] = np.array(res["ay"])
        res["gz"] = np.array(res["gz"])

    stm_estimate = {
        "timestamps": np.array(stm_times),
        "x": np.array(stm_positions_x),
        "y": np.array(stm_positions_y),
    }

    # Close loggers
    for name, ctx in contexts.items():
        if ctx.logger:
            ctx.logger.close()

    # Print frequency statistics
    total_time = t
    print(f"\n{'='*60}")
    print(f"THONG KE TAN SO CAP NHAT")
    print(f"{'='*60}")
    print(f"Tong thoi gian:       {total_time:.3f} s")
    print(f"Tong so su kien:      {len(events)}")
    print(f"---")
    print(f"UKF Predict:          {predict_count} lan | {predict_count/total_time:.1f} Hz" if total_time > 0 else f"UKF Predict: {predict_count} lan")
    print(f"UKF Update (UWB):     {update_count} lan | {update_count/total_time:.1f} Hz" if total_time > 0 else f"UKF Update: {update_count} lan")
    print(f"STM32 Position:       {stm_count} lan | {stm_count/total_time:.1f} Hz" if total_time > 0 else f"STM32 Position: {stm_count} lan")
    print(f"Predict/Update ratio: {predict_count/update_count:.1f}:1" if update_count > 0 else "Predict/Update ratio: N/A")
    print(f"{'='*60}\n")

    print(f"Bat dau ve bieu do tu {len(events)} mau du lieu CSV...")
    
    if final_estimates:
        plot_input_data(events, imu_plot_data)
        plot_position_results(final_estimates, imu_dead_reckoning, stm_estimate, init_pos=(init_x, init_y))
    else:
        print("Khong co bien the UKF nao duoc chon de chay.")

if __name__ == "__main__":
    print("Vui long chay file gui.py de su dung giao dien.")
