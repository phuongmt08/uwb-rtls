"""
===============================================================================
  UWB RTLS Studio — Antenna Delay Calibration ViewModel (host-driven)
===============================================================================
  File        : viewmodels/antenna_delay_calibration_viewmodel.py
  Description : Hiệu chuẩn antenna delay cho TỪNG ANCHOR độc lập, dùng 1 tag
                tham chiếu đặt ở vị trí đã biết chính xác.

                Toàn bộ toán học (sai số, bước hiệu chỉnh, hội tụ, số lần lặp)
                chạy ở đây (App), KHÔNG còn ở firmware — app_calib_master.c đã
                bị xoá. Firmware chỉ cần tag đang ranging bình thường
                (sensor_fusion_result_t đã tự stream distance tới từng anchor
                sẵn có, không cần lệnh "start calib" nào cả).

                Actuation: mỗi vòng lặp gửi antenna_delay_bcast_set(serial_number,
                tx, rx, persist=False) nhắm đúng 1 anchor; central's BLE firmware
                tự retry tới khi có bcast_apply_ack_t hoặc hết lượt (đã xây dựng
                phiên trước trong bb_router.c) — ViewModel không tự retry, chỉ
                đợi 1 khoảng "settle" rồi đo lại. Lần áp cuối cùng (hội tụ / hết
                vòng / user dừng) gửi lại với persist=True để lưu flash.

  MVVM Role   : VIEWMODEL

  Thuật toán (mỗi anchor độc lập, xem thesis 4.3.5):
    1. known_m = |vị trí tag tham chiếu - vị trí anchor (anchor_layout)|
    2. Gom 1 round N mẫu distance_mm cho đúng anchor_id đang calib.
    3. Loại round nếu std_m/timeout_rate vượt ngưỡng (giữ nguyên, không đổi delay).
    4. error_m = mean_m - known_m -> delta (DW units) = error_m * DW_UNITS_PER_METER
       (hằng số vật lý đúng ~426.4 DW/m, thay cho hằng số 213.0 sai 2 lần của
       app_calib_master.c cũ), nhân damping rồi cộng vào combined delay hiện tại.
    5. Tách 50/50 tx/rx (giữ quy tắc cũ, chưa có cơ sở tốt hơn) -> gửi bcast set.
    6. Đợi settle rồi đo lại; theo dõi best-so-far, hội tụ khi |error_m| < tolerance
       và bước thay đổi đủ nhỏ, hoặc hết max_iterations. Nếu tệ hơn -> rollback
       best + giảm damping.
===============================================================================
"""
import logging
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

# Firmware ANCHOR_DEFAULT_TX/RX_ANT_DLY (positioning_config.h) — used as the
# starting guess the first time an anchor is calibrated, since antenna_delay_bcast_set
# is fire-and-forget and has no read-back path to learn an anchor's current value.
ANCHOR_DEFAULT_COMBINED_DELAY = 16187 + 16187

# 1 DW1000 time unit = 1/(499.2MHz * 128) ~= 15.65ps one-way.
# Round-trip (combined TX+RX delay): distance error = c * 15.65ps / 2 ~= 2.345mm/unit.
# => DW units per meter of combined-delay error ~= 1 / 0.002345 ~= 426.4.
# (app_calib_master.c's old CALIB_A2A_M_TO_DW_UNITS=213.0f was off by ~2x.)
DW_UNITS_PER_METER = 1.0 / 0.002345

STATE_IDLE = "idle"
STATE_COLLECTING = "collecting"
STATE_SETTLING = "settling"
STATE_DONE = "done"
STATE_ERROR = "error"


class AntennaDelayCalibrationViewModel(QObject):
    progress_updated = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    operation_failed = pyqtSignal(str)

    def __init__(self, device_model, ranging_model, geofence_repo=None, parent=None):
        super().__init__(parent)
        self._model = device_model
        self._ranging_model = ranging_model
        self._geofence_repo = geofence_repo

        # User-configurable knobs — this is exactly what lets the App fully
        # control error tolerance / iteration count / rerun-on-bad-result.
        self.sample_window_s = 3.0
        self.samples_per_round_target = 30
        self.max_iterations = 8
        self.error_tolerance_m = 0.05
        self.step_tolerance_dw = 5
        self.damping = 0.5
        self.max_std_m = 0.15
        self.max_timeout_rate = 0.3
        self.settle_time_s = 2.0

        self._state = STATE_IDLE
        self._anchor_id = 0
        self._serial_number = 0
        self._known_m = 0.0
        self._combined_delay = 0
        self._best_delay = 0
        self._best_abs_error = None
        self._iteration = 0
        self._reject_count = 0
        self._max_rejects = self.max_iterations * 3
        self._samples: list[int] = []
        self._collect_started_at = 0.0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

        if self._ranging_model is not None:
            self._ranging_model.anchor_distances_updated.connect(self._on_anchor_distances)

    @property
    def is_running(self) -> bool:
        return self._state not in (STATE_IDLE, STATE_DONE, STATE_ERROR)

    def known_distance_for(self, anchor_id: int, tag_x_m: float, tag_y_m: float, tag_z_m: float) -> float | None:
        for anchor in (self._ranging_model.anchor_layout if self._ranging_model else []):
            if int(anchor.get("anchor_id", -1)) == int(anchor_id):
                dx = float(anchor.get("x_m", 0.0)) - tag_x_m
                dy = float(anchor.get("y_m", 0.0)) - tag_y_m
                dz = float(anchor.get("z_m", 0.0)) - tag_z_m
                return (dx * dx + dy * dy + dz * dz) ** 0.5
        return None

    def _find_anchor_entry(self, anchor_id: int) -> dict | None:
        if not self._geofence_repo:
            return None
        for anchor in self._geofence_repo.get_anchors():
            if int(anchor.get("anchor_id", -1)) == int(anchor_id):
                return anchor
        return None

    def serial_number_for(self, anchor_id: int) -> int:
        """Look up an anchor's hardware serial_number from the geofence map,
        where the user assigns it once per anchor (nothing in the wire
        protocol ties anchor_id <-> serial_number automatically)."""
        entry = self._find_anchor_entry(anchor_id)
        return int(entry.get("serial_number", 0) or 0) if entry else 0

    def save_anchor_serial_number(self, anchor_id: int, serial_number: int) -> bool:
        if not self._geofence_repo or serial_number <= 0:
            return False
        anchors = self._geofence_repo.get_anchors()
        matched = False
        for anchor in anchors:
            if int(anchor.get("anchor_id", -1)) == int(anchor_id):
                anchor["serial_number"] = int(serial_number)
                matched = True
                break
        if not matched:
            return False
        self._geofence_repo.set_anchors(anchors)
        if hasattr(self._geofence_repo, "save"):
            self._geofence_repo.save()
        return True

    def _last_known_combined_delay(self, anchor_id: int) -> int:
        # antenna_delay_bcast_set is fire-and-forget (no read-back), so the
        # anchor's current value is whatever we last remembered applying to it.
        entry = self._find_anchor_entry(anchor_id)
        if entry:
            return int(entry.get("last_combined_delay", ANCHOR_DEFAULT_COMBINED_DELAY) or ANCHOR_DEFAULT_COMBINED_DELAY)
        return ANCHOR_DEFAULT_COMBINED_DELAY

    def _remember_combined_delay(self, anchor_id: int, combined_delay: int) -> None:
        if not self._geofence_repo:
            return
        anchors = self._geofence_repo.get_anchors()
        for anchor in anchors:
            if int(anchor.get("anchor_id", -1)) == int(anchor_id):
                anchor["last_combined_delay"] = int(combined_delay)
                break
        self._geofence_repo.set_anchors(anchors)
        if hasattr(self._geofence_repo, "save"):
            self._geofence_repo.save()

    def start(self, anchor_id: int, tag_x_m: float, tag_y_m: float, tag_z_m: float):
        if self.is_running:
            self.operation_failed.emit("Calibration already running.")
            return False

        serial_number = self.serial_number_for(anchor_id)
        if serial_number <= 0:
            self.operation_failed.emit(
                f"Anchor {anchor_id} has no serial_number assigned in the anchor layout — set it first."
            )
            return False

        known_m = self.known_distance_for(anchor_id, tag_x_m, tag_y_m, tag_z_m)
        if known_m is None:
            self.operation_failed.emit(f"Anchor {anchor_id} not found in the current anchor layout.")
            return False

        self._anchor_id = int(anchor_id)
        self._serial_number = serial_number
        self._known_m = known_m
        self._combined_delay = self._last_known_combined_delay(self._anchor_id)
        self._best_delay = self._combined_delay
        self._best_abs_error = None
        self._iteration = 0
        self._damping = self.damping
        self._reject_count = 0
        self._max_rejects = self.max_iterations * 3
        self._start_round(STATE_COLLECTING)
        return True

    def stop(self):
        if not self.is_running:
            return
        self._timer.stop()
        self._finish(converged=False, reason="Stopped by user.")

    def _start_round(self, state: str):
        self._state = state
        self._samples = []
        self._collect_started_at = time.monotonic()
        self._timer.start(int(self.sample_window_s * 1000))
        self._emit_progress(f"Collecting samples for anchor {self._anchor_id}...")

    def _on_anchor_distances(self, anchors: list):
        if self._state != STATE_COLLECTING:
            return
        for anchor in anchors:
            if int(anchor.get("anchor_id", -1)) == self._anchor_id:
                distance_mm = int(anchor.get("distance_mm", 0) or 0)
                self._samples.append(distance_mm)
                break

    def _on_timer(self):
        if self._state == STATE_COLLECTING:
            self._process_round()
        elif self._state == STATE_SETTLING:
            self._start_round(STATE_COLLECTING)

    def _process_round(self):
        total = len(self._samples)
        valid = [s for s in self._samples if s > 0]
        timeout_rate = 1.0 - (len(valid) / total) if total > 0 else 1.0

        if len(valid) < 3:
            self._reject_count += 1
            if self._reject_count > self._max_rejects:
                self._finish(converged=False, reason="Too few valid samples — is the anchor reachable?")
                return
            self._emit_progress("Not enough valid samples this round, retrying...")
            self._start_round(STATE_COLLECTING)
            return

        mean_mm = sum(valid) / len(valid)
        variance = sum((s - mean_mm) ** 2 for s in valid) / len(valid)
        std_m = (variance ** 0.5) / 1000.0
        mean_m = mean_mm / 1000.0

        if std_m > self.max_std_m or timeout_rate > self.max_timeout_rate:
            self._reject_count += 1
            if self._reject_count > self._max_rejects:
                self._finish(converged=False, reason="Measurement quality never stabilized enough to proceed.")
                return
            self._emit_progress(
                f"Round rejected (std={std_m:.3f}m, timeout={timeout_rate:.0%}), retrying..."
            )
            self._start_round(STATE_COLLECTING)
            return

        error_m = mean_m - self._known_m
        abs_error = abs(error_m)

        if self._best_abs_error is None or abs_error < self._best_abs_error:
            self._best_abs_error = abs_error
            self._best_delay = self._combined_delay
        elif abs_error > self._best_abs_error:
            # This step made things worse — roll back and damp harder before retrying.
            self._combined_delay = self._best_delay
            self._damping *= 0.5

        self._iteration += 1
        step_dw = int(round(error_m * DW_UNITS_PER_METER * self._damping))

        converged = abs_error <= self.error_tolerance_m and abs(step_dw) <= self.step_tolerance_dw
        out_of_iterations = self._iteration >= self.max_iterations

        self._emit_progress(
            f"Iter {self._iteration}/{self.max_iterations}: error={error_m:+.3f}m "
            f"std={std_m:.3f}m delay={self._combined_delay}",
            extra={
                "iteration": self._iteration,
                "error_m": error_m,
                "std_m": std_m,
                "timeout_rate": timeout_rate,
                "combined_delay": self._combined_delay,
            },
        )

        if converged or out_of_iterations:
            self._finish(
                converged=converged,
                reason="Converged." if converged else "Reached max iterations without converging.",
            )
            return

        self._combined_delay = max(0, min(0xFFFF, self._combined_delay + step_dw))
        self._apply_delay(self._combined_delay, persist=False)
        self._state = STATE_SETTLING
        self._timer.start(int(self.settle_time_s * 1000))

    def _apply_delay(self, combined_delay: int, persist: bool):
        tx_delay = combined_delay // 2
        rx_delay = combined_delay - tx_delay
        self._model.request_antenna_delay_bcast_set(
            serial_number=self._serial_number,
            tx_antenna_delay=tx_delay,
            rx_antenna_delay=rx_delay,
            persist=persist,
        )
        self._remember_combined_delay(self._anchor_id, combined_delay)

    def _finish(self, converged: bool, reason: str):
        final_delay = self._best_delay if self._best_abs_error is not None else self._combined_delay
        # Always send one persist=True apply at the end, even if final_delay
        # matches what a prior round already live-applied — that prior round
        # used persist=False, so flash still needs the final confirmed write.
        self._apply_delay(final_delay, persist=True)

        self._state = STATE_DONE if converged else STATE_ERROR
        result = {
            "anchor_id": self._anchor_id,
            "serial_number": self._serial_number,
            "converged": converged,
            "iterations": self._iteration,
            "final_delay": final_delay,
            "best_abs_error_m": self._best_abs_error,
            "reason": reason,
        }
        self.finished.emit(result)
        self._state = STATE_IDLE

    def _emit_progress(self, text: str, extra: dict | None = None):
        payload = {"state": self._state, "anchor_id": self._anchor_id, "custom_status_text": text}
        if extra:
            payload.update(extra)
        self.progress_updated.emit(payload)
