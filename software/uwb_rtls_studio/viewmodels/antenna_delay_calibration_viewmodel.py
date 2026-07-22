"""
===============================================================================
  UWB RTLS Studio — Antenna Delay Calibration ViewModel (host-driven)
===============================================================================
  File        : viewmodels/antenna_delay_calibration_viewmodel.py
  Description : Hiệu chuẩn antenna delay cho TỪNG ANCHOR độc lập hoặc TẤT CẢ ANCHOR
                đồng thời (TDMA Parallel Mode), dùng 1 tag tham chiếu đặt ở vị trí
                đã biết chính xác.

                Toàn bộ toán học (sai số, bước hiệu chỉnh, hội tụ, số lần lặp)
                chạy ở đây (App), KHÔNG còn ở firmware. Firmware chỉ cần tag đang
                ranging bình thường (TDMA range data stream sẵn có).

                Tag Antenna Delay được giữ nguyên mức mặc định nhà sản xuất (Golden Tag).
                Toàn bộ sai số khoảng cách được gán 100% để hiệu chuẩn cho Target Anchor.

                Actuation: mỗi vòng lặp gửi antenna_delay_bcast_set(serial_number,
                tx, rx, persist=False) nhắm đúng 1 anchor. Lần áp cuối cùng
                (hội tụ / hết vòng) gửi lại với persist=True để lưu flash.

  MVVM Role   : VIEWMODEL
===============================================================================
"""
from dataclasses import dataclass, field
import logging
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

# Firmware ANCHOR_DEFAULT_TX/RX_ANT_DLY (positioning_config.h)
ANCHOR_DEFAULT_COMBINED_DELAY = 16187 + 16187

# 1 DW1000 time unit = 1/(499.2MHz * 128) ~= 15.65ps one-way.
# Round-trip (combined TX+RX delay): distance error = c * 15.65ps / 2 ~= 2.3458mm/unit.
# => DW units per meter of combined-delay error ~= 1 / 0.002345 ~= 426.4.
DW_UNITS_PER_METER = 1.0 / 0.002345

STATE_IDLE = "idle"
STATE_COLLECTING = "collecting"
STATE_SETTLING = "settling"
STATE_DONE = "done"
STATE_ERROR = "error"


@dataclass
class AnchorCalibState:
    anchor_id: int
    serial_number: int
    known_m: float
    combined_delay: int = ANCHOR_DEFAULT_COMBINED_DELAY
    best_delay: int = ANCHOR_DEFAULT_COMBINED_DELAY
    best_abs_error: float | None = None
    iteration: int = 0
    damping: float = 0.5
    # `done` = stop feeding this anchor samples / count it toward all_done (set on
    # success, on giving up after too many rejects, or on ranging silence timeout).
    # `converged` = actually met error_tolerance_m — NOT the same as `done`, since an
    # anchor that merely ran out of iterations or went silent is done but not converged.
    done: bool = False
    converged: bool = False
    reason: str = ""
    samples: list[int] = field(default_factory=list)
    reject_count: int = 0
    # Discard samples until this monotonic timestamp — the anchor may not have
    # applied its most recent antenna_delay_bcast_set yet, so measurements taken
    # right after a change would mix old-delay and new-delay distances.
    settle_until: float = 0.0
    last_sample_at: float = 0.0


class AntennaDelayCalibrationViewModel(QObject):
    progress_updated = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    operation_failed = pyqtSignal(str)

    def __init__(self, device_model, ranging_model, geofence_repo=None, parent=None):
        super().__init__(parent)
        self._model = device_model
        self._ranging_model = ranging_model
        self._geofence_repo = geofence_repo

        # User-configurable knobs
        self.sample_window_s = 3.0
        self.samples_per_round_target = 30
        self.max_iterations = 8
        self.error_tolerance_m = 0.05
        self.step_tolerance_dw = 5
        self.damping = 0.5
        self.max_std_m = 0.15
        self.max_timeout_rate = 0.3
        self.settle_time_s = 2.0
        # Parallel mode has no per-anchor timer driving progress — an anchor that
        # never appears in the TDMA stream (unreachable, wrong serial) would
        # otherwise stall the whole session forever. This watchdog treats
        # prolonged silence as a rejected round so it can eventually give up.
        self.anchor_silence_timeout_s = 5.0

        self._state = STATE_IDLE
        self._is_parallel_mode = False
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

        # Parallel multi-anchor state dictionary
        self._states: dict[int, AnchorCalibState] = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

        self._parallel_watchdog_timer = QTimer(self)
        self._parallel_watchdog_timer.setInterval(1000)
        self._parallel_watchdog_timer.timeout.connect(self._on_parallel_watchdog)

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
        """Single Anchor Calibration Mode"""
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

        self._is_parallel_mode = False
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

    def start_all(self, tag_x_m: float, tag_y_m: float, tag_z_m: float):
        """TDMA Multi-Anchor Parallel Calibration Mode"""
        if self.is_running:
            self.operation_failed.emit("Calibration already running.")
            return False

        anchors_layout = self._ranging_model.anchor_layout if self._ranging_model else []
        if not anchors_layout:
            self.operation_failed.emit("No anchors found in current layout.")
            return False

        self._states.clear()
        for anchor in anchors_layout:
            aid = int(anchor.get("anchor_id", -1))
            serial = self.serial_number_for(aid)
            if serial <= 0:
                log.warning(f"Skip Anchor {aid}: missing serial_number")
                continue

            dx = float(anchor.get("x_m", 0.0)) - tag_x_m
            dy = float(anchor.get("y_m", 0.0)) - tag_y_m
            dz = float(anchor.get("z_m", 0.0)) - tag_z_m
            known_m = (dx * dx + dy * dy + dz * dz) ** 0.5

            initial_delay = self._last_known_combined_delay(aid)
            now = time.monotonic()

            self._states[aid] = AnchorCalibState(
                anchor_id=aid,
                serial_number=serial,
                known_m=known_m,
                combined_delay=initial_delay,
                best_delay=initial_delay,
                damping=self.damping,
                last_sample_at=now,
            )

        if not self._states:
            self.operation_failed.emit("No valid anchors with serial numbers to calibrate.")
            return False

        self._max_rejects = self.max_iterations * 3
        self._is_parallel_mode = True
        self._state = STATE_COLLECTING
        self._parallel_watchdog_timer.start()
        self._emit_progress(f"Started parallel calibration for {len(self._states)} anchors...")
        return True

    def stop(self):
        if not self.is_running:
            return
        self._timer.stop()
        if self._is_parallel_mode:
            self._parallel_watchdog_timer.stop()
            for st in self._states.values():
                if not st.done:
                    st.done = True
                    st.reason = "Stopped by user."
                    self._apply_delay_for_anchor(st.serial_number, st.anchor_id, st.best_delay, persist=True)
            self._state = STATE_ERROR
            self.finished.emit({"parallel": True, "results": self._collect_parallel_results(), "reason": "Stopped by user."})
            self._state = STATE_IDLE
        else:
            self._finish(converged=False, reason="Stopped by user.")

    def _start_round(self, state: str):
        self._state = state
        self._samples = []
        self._timer.start(int(self.sample_window_s * 1000))
        self._emit_progress(f"Collecting samples for anchor {self._anchor_id}...")

    def _on_anchor_distances(self, anchors: list):
        if self._state != STATE_COLLECTING:
            return

        if self._is_parallel_mode:
            now = time.monotonic()
            for item in anchors:
                aid = int(item.get("anchor_id", -1))
                if aid not in self._states:
                    continue

                st = self._states[aid]
                if st.done:
                    continue

                # The anchor showed up in this TDMA cycle, so it's alive even if
                # we end up discarding this particular sample below.
                st.last_sample_at = now
                if now < st.settle_until:
                    continue  # still settling after our last delay change

                dist_mm = int(item.get("distance_mm", 0) or 0)
                st.samples.append(dist_mm)

                if len(st.samples) >= self.samples_per_round_target:
                    self._process_parallel_anchor_round(st)

            if self._states and all(st.done for st in self._states.values()):
                self._finish_all_parallel()
        else:
            for anchor in anchors:
                if int(anchor.get("anchor_id", -1)) == self._anchor_id:
                    distance_mm = int(anchor.get("distance_mm", 0) or 0)
                    self._samples.append(distance_mm)
                    break

    def _on_parallel_watchdog(self):
        """Catches anchors that never show up in the TDMA stream at all (wrong
        serial_number, powered off, out of range) — otherwise nothing would
        ever drive them toward `done` and the whole session would hang."""
        if not self._is_parallel_mode or self._state != STATE_COLLECTING:
            return
        now = time.monotonic()
        for st in self._states.values():
            if st.done:
                continue
            if now - st.last_sample_at > self.anchor_silence_timeout_s:
                st.reject_count += 1
                st.last_sample_at = now  # don't re-count the same silence every tick
                if st.reject_count > self._max_rejects:
                    st.done = True
                    st.converged = False
                    st.reason = "No ranging data received (anchor unreachable?)."
                    log.warning(f"Anchor {st.anchor_id} giving up: {st.reason}")

        if self._states and all(st.done for st in self._states.values()):
            self._finish_all_parallel()

    def _on_timer(self):
        if not self._is_parallel_mode:
            if self._state == STATE_COLLECTING:
                self._process_round()
            elif self._state == STATE_SETTLING:
                self._start_round(STATE_COLLECTING)

    def _process_round(self):
        """Single-Anchor Mode Batch Processor"""
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

        # Correct Rollback Logic
        if self._best_abs_error is None or abs_error < self._best_abs_error:
            self._best_abs_error = abs_error
            self._best_delay = self._combined_delay
            self._iteration += 1
            step_dw = int(round(error_m * DW_UNITS_PER_METER * self._damping))
            self._combined_delay = max(0, min(0xFFFF, self._combined_delay + step_dw))
        else:
            # Worsened error -> rollback to best_delay & decrease damping
            self._iteration += 1
            self._damping *= 0.5
            self._combined_delay = self._best_delay
            log.warning(
                f"Error worsened ({abs_error:.3f}m > {self._best_abs_error:.3f}m). "
                f"Rolling back to best delay {self._best_delay}."
            )

        converged = abs_error <= self.error_tolerance_m
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

        self._apply_delay(self._combined_delay, persist=False)
        self._state = STATE_SETTLING
        self._timer.start(int(self.settle_time_s * 1000))

    def _process_parallel_anchor_round(self, st: AnchorCalibState):
        """Parallel Multi-Anchor Processor per anchor batch"""
        valid = [s for s in st.samples if s > 0]
        total = len(st.samples)
        st.samples = []

        timeout_rate = 1.0 - (len(valid) / total) if total > 0 else 1.0
        if len(valid) < 5 or timeout_rate > self.max_timeout_rate:
            st.reject_count += 1
            if st.reject_count > self._max_rejects:
                st.done = True
                st.converged = False
                st.reason = "Gave up: measurement quality never stabilized (timeout rate too high)."
                log.warning(f"Anchor {st.anchor_id} failed: High timeout rate")
            return

        mean_mm = sum(valid) / len(valid)
        variance = sum((s - mean_mm) ** 2 for s in valid) / len(valid)
        std_m = (variance ** 0.5) / 1000.0
        mean_m = mean_mm / 1000.0

        if std_m > self.max_std_m:
            st.reject_count += 1
            if st.reject_count > self._max_rejects:
                st.done = True
                st.converged = False
                st.reason = "Gave up: measurement quality never stabilized (std too high)."
                log.warning(f"Anchor {st.anchor_id} failed: High std dev")
            return

        error_m = mean_m - st.known_m
        abs_error = abs(error_m)

        if st.best_abs_error is None or abs_error < st.best_abs_error:
            st.best_abs_error = abs_error
            st.best_delay = st.combined_delay
            st.iteration += 1
            step_dw = int(round(error_m * DW_UNITS_PER_METER * st.damping))
            st.combined_delay = max(0, min(0xFFFF, st.combined_delay + step_dw))
        else:
            st.iteration += 1
            st.damping *= 0.5
            st.combined_delay = st.best_delay

        reached_tolerance = abs_error <= self.error_tolerance_m
        out_of_iterations = st.iteration >= self.max_iterations

        self._emit_progress(
            f"Anchor {st.anchor_id} Iter {st.iteration}: error={error_m:+.3f}m std={std_m:.3f}m delay={st.combined_delay}"
        )

        if reached_tolerance or out_of_iterations:
            st.done = True
            st.converged = reached_tolerance
            st.reason = "Converged." if reached_tolerance else "Reached max iterations without converging."
            self._apply_delay_for_anchor(st.serial_number, st.anchor_id, st.best_delay, persist=True)
        else:
            # Give the anchor a moment to actually pick up the new delay before
            # trusting the next batch of samples for this anchor.
            st.settle_until = time.monotonic() + self.settle_time_s
            self._apply_delay_for_anchor(st.serial_number, st.anchor_id, st.combined_delay, persist=False)

    def _apply_delay(self, combined_delay: int, persist: bool):
        self._apply_delay_for_anchor(self._serial_number, self._anchor_id, combined_delay, persist)

    def _apply_delay_for_anchor(self, serial_number: int, anchor_id: int, combined_delay: int, persist: bool):
        tx_delay = combined_delay // 2
        rx_delay = combined_delay - tx_delay
        self._model.request_antenna_delay_bcast_set(
            serial_number=serial_number,
            tx_antenna_delay=tx_delay,
            rx_antenna_delay=rx_delay,
            persist=persist,
        )
        self._remember_combined_delay(anchor_id, combined_delay)

    def _finish(self, converged: bool, reason: str):
        final_delay = self._best_delay if self._best_abs_error is not None else self._combined_delay
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

    def _collect_parallel_results(self) -> dict:
        return {
            aid: {
                "converged": st.converged,
                "iterations": st.iteration,
                "final_delay": st.best_delay,
                "best_abs_error_m": st.best_abs_error,
                "reason": st.reason,
            }
            for aid, st in self._states.items()
        }

    def _finish_all_parallel(self):
        self._parallel_watchdog_timer.stop()
        self._state = STATE_DONE
        self.finished.emit({
            "parallel": True,
            "results": self._collect_parallel_results(),
            "reason": "All anchors completed.",
        })
        self._state = STATE_IDLE

    def _emit_progress(self, text: str, extra: dict | None = None):
        payload = {"state": self._state, "anchor_id": self._anchor_id, "custom_status_text": text}
        if extra:
            payload.update(extra)
        self.progress_updated.emit(payload)
