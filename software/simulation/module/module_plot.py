import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from typing import Dict
from .config import SensorEvent, ANCHOR_POSITIONS, ROOM_SIZE_M

def _draw_anchors(ax: plt.Axes) -> None:
    for idx, anchor in enumerate(ANCHOR_POSITIONS):
        ax.scatter(anchor[0], anchor[1], marker="^", s=120, zorder=5, color="black")
        ax.annotate(f"A{idx}", xy=anchor, xytext=(anchor[0] + 0.2, anchor[1] + 0.2),
                    fontsize=8, color="black")

def _draw_reference_rectangle(ax: plt.Axes, start_x: float, start_y: float) -> None:
    from .config import DRAW_RECTANGLE, RECT_WIDTH, RECT_HEIGHT
    if DRAW_RECTANGLE:
        rect = patches.Rectangle((start_x, start_y), RECT_WIDTH, RECT_HEIGHT, 
                                 linewidth=1.5, edgecolor='red', facecolor='none', linestyle='--', zorder=4)
        ax.add_patch(rect)
        # Adding a proxy artist for the legend if needed is handled below

def _make_legend_interactive(fig) -> None:
    """Helper to toggle line visibility when clicking on legend items (Normal -> Faded -> Hidden)."""
    leg_to_orig = {}
    orig_alphas = {}  # Store initial alphas to restore them correctly
    
    for ax in fig.axes:
        leg = ax.get_legend()
        if not leg:
            continue
            
        handles, _ = ax.get_legend_handles_labels()
        leg_handles = leg.legend_handles if hasattr(leg, 'legend_handles') else leg.get_lines() + leg.get_patches()
        leg_texts = leg.get_texts()
        
        for leg_h, orig_h in zip(leg_handles, handles):
            leg_h.set_picker(True)
            leg_h.set_pickradius(5)
            leg_to_orig[leg_h] = orig_h
            if orig_h not in orig_alphas:
                orig_alphas[orig_h] = orig_h.get_alpha() if orig_h.get_alpha() is not None else 1.0
            
        for leg_t, orig_h in zip(leg_texts, handles):
            leg_t.set_picker(True)
            leg_to_orig[leg_t] = orig_h
            if orig_h not in orig_alphas:
                orig_alphas[orig_h] = orig_h.get_alpha() if orig_h.get_alpha() is not None else 1.0

    def on_pick(event):
        artist = event.artist
        if artist not in leg_to_orig:
            return
            
        orig_artist = leg_to_orig[artist]
        init_a = orig_alphas.get(orig_artist, 1.0)
        
        curr_visible = orig_artist.get_visible()
        curr_alpha = orig_artist.get_alpha()
        if curr_alpha is None:
            curr_alpha = 1.0
            
        # 3-state toggle logic: Normal -> Faded (15% alpha) -> Hidden
        if not curr_visible:
            # Currently Hidden -> Change to Normal
            orig_artist.set_visible(True)
            orig_artist.set_alpha(init_a)
            new_leg_alpha = 1.0
        elif abs(curr_alpha - init_a) < 0.01:
            # Currently Normal -> Change to Faded
            orig_artist.set_visible(True)
            orig_artist.set_alpha(init_a * 0.5)
            new_leg_alpha = 0.4
        else:
            # Currently Faded -> Change to Hidden
            orig_artist.set_visible(False)
            new_leg_alpha = 0.1
            
        # Update transparency of all legend objects related to this artist
        for lh, oh in leg_to_orig.items():
            if oh == orig_artist:
                lh.set_alpha(new_leg_alpha)
        
        fig.canvas.draw()

    fig.canvas.mpl_connect('pick_event', on_pick)

def _make_scroll_zoomable(fig, base_scale=1.2) -> None:
    """Add mouse wheel zoom functionality centered on cursor."""
    def handle_scroll(event):
        if event.inaxes is None:
            return
        ax = event.inaxes
        cur_xlim = ax.get_xlim()
        cur_ylim = ax.get_ylim()
        
        xdata = event.xdata
        ydata = event.ydata
        
        if event.button == 'up':
            scale_factor = 1 / base_scale
        elif event.button == 'down':
            scale_factor = base_scale
        else:
            scale_factor = 1
            
        new_width = (cur_xlim[1] - cur_xlim[0]) * scale_factor
        new_height = (cur_ylim[1] - cur_ylim[0]) * scale_factor
        
        rel_x = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0])
        rel_y = (cur_ylim[1] - ydata) / (cur_ylim[1] - cur_ylim[0])
        
        ax.set_xlim([xdata - new_width * (1 - rel_x), xdata + new_width * (rel_x)])
        ax.set_ylim([ydata - new_height * (1 - rel_y), ydata + new_height * (rel_y)])
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect('scroll_event', handle_scroll)

def plot_input_data(events: list[SensorEvent], imu_plot_data: Dict[str, Dict[str, np.ndarray]]) -> None:
    n = len(events)
    times = np.zeros(n)
    t = 0.0
    d_data = [np.zeros(n) for _ in range(4)]
    
    for i, e in enumerate(events):
        t += e.dt
        times[i] = t
        for j in range(4):
            d_data[j][i] = e.distances[j] if e.type == "Update" and e.distances[j] > 0 else np.nan

    fig1, axs1 = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    fig1.suptitle("INPUT – IMU Data", fontsize=13)

    colors = {"raw": "tomato", "ema": "darkred", "raw_zupt": "orange", "ema_zupt": "purple"}
    styles = {"raw": "-", "ema": "-", "raw_zupt": "--", "ema_zupt": "--"}
    alphas = {"raw": 0.5, "ema": 1.0, "raw_zupt": 0.8, "ema_zupt": 0.8}

    for variant_name, data in imu_plot_data.items():
        c = colors.get(variant_name, "blue")
        s = styles.get(variant_name, "-")
        a = alphas.get(variant_name, 1.0)
        
        n_data = len(data["ax"])
        axs1[0].plot(times[:n_data], data["ax"], label=f"ax {variant_name}", linestyle=s, color=c, alpha=a)
        axs1[1].plot(times[:n_data], data["ay"], label=f"ay {variant_name}", linestyle=s, color=c, alpha=a)
        axs1[2].plot(times[:n_data], data["gz"], label=f"gz {variant_name}", linestyle=s, color=c, alpha=a)

    axs1[0].set_ylabel("ax (m/s²)")
    axs1[1].set_ylabel("ay (m/s²)")
    axs1[2].set_ylabel("gz (rad/s)")
    
    for ax in axs1:
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.4)

    axs1[2].set_xlabel("Thời gian (s)")
    plt.tight_layout(rect=[0, 0.0, 1, 0.96])

    # fig2, axs2 = plt.subplots(4, 1, figsize=(11, 10), sharex=True)
    # fig2.suptitle("INPUT – UWB Khoảng cách", fontsize=13)

    uwb_colors = ["tomato", "darkorange", "hotpink", "mediumpurple"]
    # colors = ["tomato", "darkorange", "hotpink", "mediumpurple"]
    # for j in range(4):
    #     axs2[j].scatter(times, d_data[j], label=f"d{j+1}", s=18, alpha=0.8, color=colors[j])
    #     axs2[j].set_ylabel("Khoảng cách (m)")
    #     axs2[j].legend(loc="upper right")
    #     axs2[j].grid(True, alpha=0.4)

    # axs2[3].set_xlabel("Thời gian (s)")
    # plt.tight_layout(rect=[0, 0.0, 1, 0.96])

    fig3, ax3 = plt.subplots(1, 1, figsize=(11, 6))
    fig3.suptitle("INPUT – UWB Khoảng cách (Lines)", fontsize=13)
    
    for j in range(4):
        valid_idx = ~np.isnan(d_data[j])
        ax3.plot(times[valid_idx], d_data[j][valid_idx], label=f"d{j+1}", alpha=0.8, color=uwb_colors[j], linewidth=1.5)
        
    ax3.set_ylabel("Khoảng cách (m)")
    ax3.set_xlabel("Thời gian (s)")
    ax3.legend(loc="upper right")
    ax3.grid(True, alpha=0.4)
    plt.tight_layout(rect=[0, 0.0, 1, 0.96])

    # Build hover data for IMU plot (fig1)
    hover_fig1 = {axs1[0]: [], axs1[1]: [], axs1[2]: []}
    for variant_name, data in imu_plot_data.items():
        n_data = len(data["ax"])
        hover_fig1[axs1[0]].append((times[:n_data], data["ax"], f"ax {variant_name}"))
        hover_fig1[axs1[1]].append((times[:n_data], data["ay"], f"ay {variant_name}"))
        hover_fig1[axs1[2]].append((times[:n_data], data["gz"], f"gz {variant_name}"))

    # Build hover data for UWB plot (fig3)
    hover_fig3 = {ax3: []}
    for j in range(4):
        valid_idx = ~np.isnan(d_data[j])
        hover_fig3[ax3].append((times[valid_idx], d_data[j][valid_idx], f"d{j+1}"))

    for fig, hover, ax_list in [(fig1, hover_fig1, list(axs1)), (fig3, hover_fig3, [ax3])]:
        _make_legend_interactive(fig)
        _add_hover_index(fig, ax_list, hover)
        plt.figure(fig.number)
        plt.show()

def plot_position_estimates(
    imu_estimate: Dict[str, np.ndarray],
    uwb_estimate: Dict[str, np.ndarray],
    ukf_estimate: Dict[str, np.ndarray],
    stm_estimate: Dict[str, np.ndarray] = None,
    init_pos: tuple = (0.0, 0.0)
) -> None:
    common_kwargs = dict(xlim=(-1, ROOM_SIZE_M + 1), ylim=(-1, ROOM_SIZE_M + 1))

    fig4, ax4 = plt.subplots(figsize=(7, 7))
    fig4.suptitle("So sánh vị trí: IMU / UWB / UKF", fontsize=12)
    
    from .config import DRAW_RECTANGLE
    if DRAW_RECTANGLE:
        ax4.plot([], [], color="red", linestyle="--", linewidth=1.5, label="Qũy đạo mong muốn")
        _draw_reference_rectangle(ax4, init_pos[0], init_pos[1])

    ax4.plot(imu_estimate["x"], imu_estimate["y"],
             label="IMU dead reckoning", color="tomato", linewidth=1.5, alpha=0.85)
    ax4.plot(uwb_estimate["x"], uwb_estimate["y"],
             label="UWB trilateration", color="seagreen", linewidth=1.5, alpha=0.85)
    ax4.plot(ukf_estimate["x"], ukf_estimate["y"],
             label="UKF dự đoán", color="dodgerblue", linewidth=2.0, alpha=0.9)
             
    if stm_estimate is not None and len(stm_estimate["x"]) > 0:
        ax4.plot(stm_estimate["x"], stm_estimate["y"],
                 label="STM32 C calculation", color="purple", linewidth=1.8, linestyle="--", alpha=0.85)
             
    _draw_anchors(ax4)
    ax4.set_xlabel("X (m)")
    ax4.set_ylabel("Y (m)")
    ax4.set_xlim(*common_kwargs["xlim"])
    ax4.set_ylim(*common_kwargs["ylim"])
    ax4.legend(fontsize=9, loc="upper left")
    ax4.grid(True, alpha=0.4)
    plt.tight_layout()

    # Build hover data for position estimates plot
    hover_fig4 = {ax4: []}
    hover_fig4[ax4].append((imu_estimate["x"], imu_estimate["y"], "IMU"))
    hover_fig4[ax4].append((uwb_estimate["x"], uwb_estimate["y"], "UWB"))
    hover_fig4[ax4].append((ukf_estimate["x"], ukf_estimate["y"], "UKF"))
    if stm_estimate is not None and len(stm_estimate["x"]) > 0:
        hover_fig4[ax4].append((stm_estimate["x"], stm_estimate["y"], "STM32"))

    _make_legend_interactive(fig4)
    _add_hover_index(fig4, [ax4], hover_fig4)
    plt.show()

def _point_to_segment_dist(px, py, x1, y1, x2, y2):
    """Compute minimum distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return np.hypot(px - x1, py - y1)
    t = np.clip(((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy), 0, 1)
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return np.hypot(px - proj_x, py - proj_y)

def _compute_mean_error_to_rect(xs, ys, init_pos, rect_w, rect_h):
    """Compute mean distance from trajectory points to nearest edge of ground truth rectangle."""
    x0, y0 = init_pos
    # 4 corners of the rectangle
    corners = [
        (x0, y0),
        (x0 + rect_w, y0),
        (x0 + rect_w, y0 + rect_h),
        (x0, y0 + rect_h),
    ]
    # 4 edges
    segments = [
        (corners[0], corners[1]),
        (corners[1], corners[2]),
        (corners[2], corners[3]),
        (corners[3], corners[0]),
    ]
    
    total_err = 0.0
    n = len(xs)
    if n == 0:
        return 0.0
    for i in range(n):
        min_d = float('inf')
        for (sx1, sy1), (sx2, sy2) in segments:
            d = _point_to_segment_dist(xs[i], ys[i], sx1, sy1, sx2, sy2)
            if d < min_d:
                min_d = d
        total_err += min_d
    return total_err / n

def _add_hover_index(fig, axs, data_dict):
    """Add hover tooltip showing CSV index when mouse is near a data point.
    
    data_dict: dict mapping ax -> list of (xs_array, ys_array, label_str)
    """
    annot = {}
    for ax in axs:
        a = ax.annotate("", xy=(0, 0), xytext=(15, 15),
                         textcoords="offset points",
                         bbox=dict(boxstyle="round,pad=0.3", fc="lightyellow", ec="gray", alpha=0.9),
                         fontsize=8, zorder=100)
        a.set_visible(False)
        annot[ax] = a

    def on_move(event):
        if event.inaxes is None or event.inaxes not in annot:
            for a in annot.values():
                if a.get_visible():
                    a.set_visible(False)
                    fig.canvas.draw_idle()
            return
        
        ax = event.inaxes
        a = annot[ax]
        
        if ax not in data_dict:
            return
            
        best_dist = float('inf')
        best_info = None
        
        # Get axis display transform for proper distance calculation
        for xs, ys, label in data_dict[ax]:
            if len(xs) == 0:
                continue
            # Transform data to display coordinates for distance calc
            display_coords = ax.transData.transform(np.column_stack([xs, ys]))
            mouse_display = np.array([event.x, event.y])
            dists = np.hypot(display_coords[:, 0] - mouse_display[0],
                             display_coords[:, 1] - mouse_display[1])
            idx = np.argmin(dists)
            if dists[idx] < best_dist:
                best_dist = dists[idx]
                best_info = (xs[idx], ys[idx], idx, label)
        
        if best_info is not None and best_dist < 30:  # 30 pixels threshold
            x_val, y_val, idx, label = best_info
            a.xy = (x_val, y_val)
            a.set_text(f"{label}\nidx={idx}\nx={x_val:.3f}\ny={y_val:.3f}")
            a.set_visible(True)
            fig.canvas.draw_idle()
        else:
            if a.get_visible():
                a.set_visible(False)
                fig.canvas.draw_idle()
    
    fig.canvas.mpl_connect('motion_notify_event', on_move)

def plot_position_results(
    ukf_estimates: Dict[str, Dict[str, np.ndarray]],
    imu_estimate: Dict[str, np.ndarray],
    stm_estimate: Dict[str, np.ndarray] = None,
    init_pos: tuple = (0.0, 0.0)
) -> None:
    fig, axs = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.suptitle("KẾT QUẢ – UKF dự đoán", fontsize=13)
    
    from .config import DRAW_RECTANGLE, RECT_WIDTH, RECT_HEIGHT
    if DRAW_RECTANGLE:
        axs[0].plot([], [], color="red", linestyle="--", linewidth=1.5, label="Qũy đạo mong muốn")
        _draw_reference_rectangle(axs[0], init_pos[0], init_pos[1])

    colors = {"raw": "dodgerblue", "ema": "limegreen", "raw_zupt": "darkorange", "ema_zupt": "red"}
    
    first_key = list(ukf_estimates.keys())[0] if ukf_estimates else None
    
    # Compute mean errors vs ground truth rectangle
    error_texts = []
    if DRAW_RECTANGLE:
        for name, est in ukf_estimates.items():
            err = _compute_mean_error_to_rect(est["x"], est["y"], init_pos, RECT_WIDTH, RECT_HEIGHT)
            error_texts.append(f"UKF {name}: {err:.3f}m")
        
        if stm_estimate is not None and len(stm_estimate["x"]) > 0:
            err = _compute_mean_error_to_rect(stm_estimate["x"], stm_estimate["y"], init_pos, RECT_WIDTH, RECT_HEIGHT)
            error_texts.append(f"STM32: {err:.3f}m")
    
    for name, est in ukf_estimates.items():
        c = colors.get(name, "blue")
        axs[0].plot(est["x"], est["y"], label=f"UKF {name}", color=c, linewidth=1.5)
        if name == first_key:
            axs[0].scatter(est["x"][0], est["y"][0], color="lime", s=90, zorder=5, label="Điểm đầu")
    
    if stm_estimate is not None and len(stm_estimate["x"]) > 0:
        axs[0].plot(stm_estimate["x"], stm_estimate["y"],
                    label="Vị trí STM32", color="tomato", linewidth=1.5, linestyle="--")

    _draw_anchors(axs[0])
    axs[0].set_xlabel("X (m)")
    axs[0].set_ylabel("Y (m)")
    axs[0].set_xlim(-1, ROOM_SIZE_M + 1)
    axs[0].set_ylim(-1, ROOM_SIZE_M + 1)
    axs[0].legend(fontsize=9, loc="upper left")
    axs[0].grid(True, alpha=0.4)
    
    # Display mean error text box on trajectory plot
    if error_texts:
        err_str = "Mean Error vs GT:\n" + "\n".join(error_texts)
        axs[0].text(0.98, 0.02, err_str, transform=axs[0].transAxes,
                    fontsize=7, verticalalignment='bottom', horizontalalignment='right',
                    bbox=dict(boxstyle='round,pad=0.4', facecolor='lightyellow', edgecolor='gray', alpha=0.9))
    
    max_len = 0
    time_axis = []
    for est in ukf_estimates.values():
        if len(est["timestamps"]) > max_len:
            max_len = len(est["timestamps"])
            time_axis = est["timestamps"]
    
    for name, est in ukf_estimates.items():
        c1 = colors.get(name, "blue")
        c2 = colors.get(name, "blue") # We could use different shades, but legend will help
        axs[1].plot(est["timestamps"], est["x"], label=f"x {name}", linewidth=1.2, color=c1)
        axs[1].plot(est["timestamps"], est["y"], label=f"y {name}", linewidth=1.2, linestyle=":", color=c2)
                
    if stm_estimate is not None and len(stm_estimate["x"]) > 0:
        axs[1].plot(stm_estimate["timestamps"], stm_estimate["x"],
                    label="x STM32", linewidth=1.4, linestyle="--", color="mediumpurple")
        axs[1].plot(stm_estimate["timestamps"], stm_estimate["y"],
                    label="y STM32", linewidth=1.4, linestyle="--", color="orchid")
                
    axs[1].set_title("Vị trí theo thời gian")
    axs[1].set_xlabel("Thời gian (s)")
    axs[1].set_ylabel("Vị trí (m)")
    axs[1].legend(fontsize=9, loc="upper left")
    axs[1].grid(True, alpha=0.4)
    
    imu_yaw_deg = np.degrees(imu_estimate["theta"])
    axs[2].plot(time_axis[:len(imu_yaw_deg)], imu_yaw_deg[:len(time_axis)], label="IMU Yaw (raw)", color="tomato", linewidth=1.5, alpha=0.85)
    
    for name, est in ukf_estimates.items():
        c = colors.get(name, "blue")
        ukf_yaw_deg = np.degrees(est["theta"])
        axs[2].plot(est["timestamps"], ukf_yaw_deg, label=f"Yaw {name}", color=c, linewidth=1.5, alpha=0.9)
    
    axs[2].set_title("So sánh góc Yaw")
    axs[2].set_xlabel("Thời gian (s)")
    axs[2].set_ylabel("Góc (độ)")
    axs[2].legend(fontsize=9, loc="upper left")
    axs[2].grid(True, alpha=0.4)

    # Build hover data for all 3 subplots
    hover_data = {
        axs[0]: [],
        axs[1]: [],
        axs[2]: [],
    }
    
    for name, est in ukf_estimates.items():
        hover_data[axs[0]].append((est["x"], est["y"], f"UKF {name}"))
        hover_data[axs[1]].append((est["timestamps"], est["x"], f"x {name}"))
        hover_data[axs[1]].append((est["timestamps"], est["y"], f"y {name}"))
        ukf_yaw = np.degrees(est["theta"])
        hover_data[axs[2]].append((est["timestamps"], ukf_yaw, f"Yaw {name}"))
    
    if stm_estimate is not None and len(stm_estimate["x"]) > 0:
        hover_data[axs[0]].append((stm_estimate["x"], stm_estimate["y"], "STM32"))
        hover_data[axs[1]].append((stm_estimate["timestamps"], stm_estimate["x"], "x STM32"))
        hover_data[axs[1]].append((stm_estimate["timestamps"], stm_estimate["y"], "y STM32"))
    
    imu_yaw_trunc = imu_yaw_deg[:len(time_axis)]
    time_trunc = time_axis[:len(imu_yaw_deg)]
    hover_data[axs[2]].append((time_trunc, imu_yaw_trunc, "IMU Yaw"))

    plt.tight_layout()
    _make_legend_interactive(fig)
    _make_scroll_zoomable(fig)
    _add_hover_index(fig, list(axs), hover_data)
    plt.show()
