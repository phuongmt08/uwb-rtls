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
STATE_APPLYING = "applying"
STATE_SETTLING = "settling"
STATE_DONE = "done"
STATE_ERROR = "error"

BCAST_ANTENNA_DELAY_TAG = 88
BCAST_APPLY_ACK_TIMEOUT_MS = 11000


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
    apply_pending: bool = False


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
        self._started_ranging_for_calibration = False
        self._apply_queue: list[dict] = []
        self._pending_apply: dict | None = None
        self._pending_single_result: dict | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

        self._apply_ack_timer = QTimer(self)
        self._apply_ack_timer.setSingleShot(True)
        self._apply_ack_timer.timeout.connect(self._on_apply_ack_timeout)

        self._parallel_watchdog_timer = QTimer(self)
        self._parallel_watchdog_timer.setInterval(1000)
        self._parallel_watchdog_timer.timeout.connect(self._on_parallel_watchdog)

        if self._ranging_model is not None:
            self._ranging_model.anchor_distances_updated.connect(self._on_anchor_distances)
        if self._model is not None and hasattr(self._model, "scan_data_updated"):
            self._model.scan_data_updated.connect(self._on_scan_data_updated)
        if self._model is not None and hasattr(self._model, "bcast_apply_ack_received"):
            self._model.bcast_apply_ack_received.connect(self._on_bcast_apply_ack)

    @property
    def is_running(self) -> bool:
        return self._state not in (STATE_IDLE, STATE_DONE, STATE_ERROR)

    def _current_anchor_layout(self) -> list[dict]:
        """Use the currently loaded Studio map, then fall back to device layout."""
        if self._geofence_repo is not None and hasattr(self._geofence_repo, "get_anchors"):
            map_anchors = self._geofence_repo.get_anchors()
            if map_anchors:
                return map_anchors
        if self._ranging_model is not None:
            return list(self._ranging_model.anchor_layout or [])
        return []

    def known_distance_for(
        self,
        anchor_id: int,
        tag_x_m: float,
        tag_y_m: float,
        tag_z_m: float = 0.0,
    ) -> float | None:
        """Return the 2D ground-truth distance in the current anchor-layout frame.

        The ranging distance consumed by this calibration workflow is treated
        as planar, so Z must not be mixed into the calibration error.
        tag_z_m remains accepted only for compatibility with older callers.
        """
        _ = tag_z_m
        for anchor in self._current_anchor_layout():
            if int(anchor.get("anchor_id", -1)) == int(anchor_id):
                dx = float(anchor.get("x_m", 0.0)) - tag_x_m
                dy = float(anchor.get("y_m", 0.0)) - tag_y_m
                return (dx * dx + dy * dy) ** 0.5
        return None

    def _find_anchor_entry(self, anchor_id: int) -> dict | None:
        if not self._geofence_repo:
            return None
        for anchor in self._geofence_repo.get_anchors():
            if int(anchor.get("anchor_id", -1)) == int(anchor_id):
                return anchor
        return None

    def discovered_serial_number_for(self, anchor_id: int) -> int:
        resolver = getattr(self._model, "discovered_anchor_serial_number", None)
        if not callable(resolver):
            return 0
        return int(resolver(anchor_id) or 0)

    def serial_number_for(self, anchor_id: int) -> int:
        entry = self._find_anchor_entry(anchor_id)
        saved_serial = int(entry.get("serial_number", 0) or 0) if entry else 0
        discovered_serial = self.discovered_serial_number_for(anchor_id)

        if discovered_serial > 0:
            if discovered_serial != saved_serial:
                self.save_anchor_serial_number(anchor_id, discovered_serial)
            return discovered_serial
        return saved_serial

    def _on_scan_data_updated(self, _devices: list) -> None:
        anchor_ids = {
            int(anchor.get("anchor_id", -1))
            for anchor in self._current_anchor_layout()
            if int(anchor.get("anchor_id", -1)) >= 0
        }
        for anchor_id in anchor_ids:
            serial_number = self.discovered_serial_number_for(anchor_id)
            if serial_number <= 0:
                continue
            entry = self._find_anchor_entry(anchor_id)
            saved_serial = int(entry.get("serial_number", 0) or 0) if entry else 0
            if saved_serial == serial_number:
                continue
            self.save_anchor_serial_number(anchor_id, serial_number)

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

    def _has_reference_tag_connection(self) -> bool:
        role = str(getattr(self._model, "connected_role", "") or "").strip().upper()
        if role == "TAG":
            return True
        self.operation_failed.emit(
            "Connect Studio to the reference Tag before starting antenna-delay calibration."
        )
        return False

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

    def start(
        self,
        anchor_id: int,
        tag_x_m: float,
        tag_y_m: float,
        tag_z_m: float = 0.0,
    ):
        """Single Anchor Calibration Mode"""
        if self.is_running:
            self.operation_failed.emit("Calibration already running.")
            return False
        if not self._has_reference_tag_connection():
            return False

        serial_number = self.serial_number_for(anchor_id)
        if serial_number <= 0:
            self.operation_failed.emit(
                f"Anchor {anchor_id} was not found in the BLE ADV snapshot. "
                "Run a BLE scan so Studio can map Anchor ID to its advertised serial."
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
        self._reset_apply_pipeline()
        self._ensure_ranging_started()
        self._start_round(STATE_COLLECTING)
        return True

    def start_all(
        self,
        tag_x_m: float,
        tag_y_m: float,
        tag_z_m: float = 0.0,
    ):
        """TDMA Multi-Anchor Parallel Calibration Mode"""
        if self.is_running:
            self.operation_failed.emit("Calibration already running.")
            return False
        if not self._has_reference_tag_connection():
            return False

        anchors_layout = self._current_anchor_layout()
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
            known_m = (dx * dx + dy * dy) ** 0.5

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
        self._reset_apply_pipeline()
        self._ensure_ranging_started()
        self._parallel_watchdog_timer.start()
        self._emit_progress(f"Started parallel calibration for {len(self._states)} anchors...")
        return True

    def _ensure_ranging_started(self) -> None:
        self._started_ranging_for_calibration = False
        if self._ranging_model is None or bool(getattr(self._ranging_model, "is_ranging", False)):
            return
        start_ranging = getattr(self._ranging_model, "start_ranging", None)
        if callable(start_ranging):
            start_ranging()
            self._started_ranging_for_calibration = True

    def _stop_owned_ranging(self) -> None:
        if not self._started_ranging_for_calibration:
            return
        self._started_ranging_for_calibration = False
        stop_ranging = getattr(self._ranging_model, "stop_ranging", None)
        if callable(stop_ranging):
            stop_ranging()

    def stop(self):
        if not self.is_running:
            return
        self._timer.stop()
        self._parallel_watchdog_timer.stop()
        self._reset_apply_pipeline()
        self._stop_owned_ranging()
        if self._is_parallel_mode:
            for st in self._states.values():
                if not st.done:
                    st.done = True
                    st.reason = "Stopped by user."
            self._state = STATE_ERROR
            self.finished.emit({"parallel": True, "results": self._collect_parallel_results(), "reason": "Stopped by user."})
            self._state = STATE_IDLE
        else:
            result = {
                "anchor_id": self._anchor_id,
                "serial_number": self._serial_number,
                "converged": False,
                "iterations": self._iteration,
                "final_delay": self._best_delay,
                "best_abs_error_m": self._best_abs_error,
                "reason": "Stopped by user.",
            }
            self._state = STATE_ERROR
            self.finished.emit(result)
            self._state = STATE_IDLE

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
                if st.done or st.apply_pending:
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

            self._maybe_finish_all_parallel()
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
            if st.done or st.apply_pending:
                continue
            if now - st.last_sample_at > self.anchor_silence_timeout_s:
                st.reject_count += 1
                st.last_sample_at = now  # don't re-count the same silence every tick
                if st.reject_count > self._max_rejects:
                    st.done = True
                    st.converged = False
                    st.reason = "No ranging data received (anchor unreachable?)."
                    log.warning(f"Anchor {st.anchor_id} giving up: {st.reason}")

        self._maybe_finish_all_parallel()

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

        self._state = STATE_APPLYING
        self._queue_delay_apply(
            self._serial_number,
            self._anchor_id,
            self._combined_delay,
            persist=False,
            purpose="single_iteration",
        )

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
            st.apply_pending = True
            st.settle_until = float("inf")
            self._queue_delay_apply(
                st.serial_number,
                st.anchor_id,
                st.best_delay,
                persist=True,
                purpose="parallel_final",
            )
        else:
            # Give the anchor a moment to actually pick up the new delay before
            # trusting the next batch of samples for this anchor.
            st.apply_pending = True
            st.settle_until = float("inf")
            self._queue_delay_apply(
                st.serial_number,
                st.anchor_id,
                st.combined_delay,
                persist=False,
                purpose="parallel_iteration",
            )

    def _reset_apply_pipeline(self) -> None:
        self._apply_ack_timer.stop()
        self._apply_queue.clear()
        self._pending_apply = None
        self._pending_single_result = None

    def _queue_delay_apply(
        self,
        serial_number: int,
        anchor_id: int,
        combined_delay: int,
        *,
        persist: bool,
        purpose: str,
    ) -> None:
        self._apply_queue.append({
            "serial_number": int(serial_number),
            "anchor_id": int(anchor_id),
            "combined_delay": int(combined_delay),
            "persist": bool(persist),
            "purpose": str(purpose),
        })
        self._send_next_delay_apply()

    def _send_next_delay_apply(self) -> None:
        if self._pending_apply is not None or not self._apply_queue:
            return

        request = self._apply_queue.pop(0)
        combined_delay = request["combined_delay"]
        tx_delay = combined_delay // 2
        rx_delay = combined_delay - tx_delay
        pkt = self._model.request_antenna_delay_bcast_set(
            serial_number=request["serial_number"],
            tx_antenna_delay=tx_delay,
            rx_antenna_delay=rx_delay,
            persist=request["persist"],
        )
        hdr = getattr(pkt, "hdr", None)
        if hdr is None:
            self._complete_delay_apply(request, success=False, detail="BCAST command was not transmitted.")
            self._send_next_delay_apply()
            return

        request["cmd_seq"] = int(getattr(hdr, "seq", 0) or 0)
        self._pending_apply = request
        self._apply_ack_timer.start(BCAST_APPLY_ACK_TIMEOUT_MS)
        self._emit_progress(
            f"Applying delay to Anchor {request['anchor_id']} "
            f"(seq={request['cmd_seq']}, persist={int(request['persist'])})..."
        )

    def _on_bcast_apply_ack(self, ack: dict) -> None:
        request = self._pending_apply
        if request is None:
            return
        if (
            int(ack.get("serial_number", 0) or 0) != request["serial_number"]
            or int(ack.get("cmd_seq", 0) or 0) != request["cmd_seq"]
            or int(ack.get("cmd_tag", 0) or 0) != BCAST_ANTENNA_DELAY_TAG
        ):
            return

        self._apply_ack_timer.stop()
        self._pending_apply = None
        success = bool(ack.get("success", False))
        detail = "ACK confirmed." if success else "Target reported apply failure."
        self._complete_delay_apply(request, success=success, detail=detail)
        self._send_next_delay_apply()
        self._maybe_finish_all_parallel()

    def _on_apply_ack_timeout(self) -> None:
        request = self._pending_apply
        if request is None:
            return
        self._pending_apply = None
        self._complete_delay_apply(
            request,
            success=False,
            detail="No matching BCAST apply ACK after Central retries.",
        )
        self._send_next_delay_apply()
        self._maybe_finish_all_parallel()

    def _complete_delay_apply(self, request: dict, *, success: bool, detail: str) -> None:
        anchor_id = request["anchor_id"]
        purpose = request["purpose"]
        if success:
            self._remember_combined_delay(anchor_id, request["combined_delay"])

        self._emit_progress(
            f"Anchor {anchor_id}: {detail}",
            extra={
                "anchor_id": anchor_id,
                "apply_success": success,
                "combined_delay": request["combined_delay"],
            },
        )

        if purpose == "single_iteration":
            if success:
                self._state = STATE_SETTLING
                self._timer.start(int(self.settle_time_s * 1000))
            else:
                self._finish(
                    converged=False,
                    reason=f"Could not apply candidate delay: {detail}",
                    apply_final=False,
                )
            return

        if purpose == "single_final":
            result = self._pending_single_result
            self._pending_single_result = None
            if result is None:
                return
            if not success:
                result["converged"] = False
                result["reason"] = f"{result['reason']} Final delay was not confirmed: {detail}"
            self._finalize_single(result)
            return

        st = self._states.get(anchor_id)
        if st is None:
            return
        st.apply_pending = False
        if not success:
            st.done = True
            st.converged = False
            st.reason = f"Delay apply failed: {detail}"
            return
        if purpose == "parallel_iteration":
            st.settle_until = time.monotonic() + self.settle_time_s

    def _finish(self, converged: bool, reason: str, *, apply_final: bool = True):
        self._timer.stop()
        final_delay = self._best_delay if self._best_abs_error is not None else self._combined_delay
        result = {
            "anchor_id": self._anchor_id,
            "serial_number": self._serial_number,
            "converged": converged,
            "iterations": self._iteration,
            "final_delay": final_delay,
            "best_abs_error_m": self._best_abs_error,
            "reason": reason,
        }
        if not apply_final:
            self._finalize_single(result)
            return

        self._state = STATE_APPLYING
        self._pending_single_result = result
        self._queue_delay_apply(
            self._serial_number,
            self._anchor_id,
            final_delay,
            persist=True,
            purpose="single_final",
        )

    def _finalize_single(self, result: dict) -> None:
        self._state = STATE_DONE if result.get("converged") else STATE_ERROR
        self._stop_owned_ranging()
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
        self._stop_owned_ranging()
        self._state = STATE_DONE
        self.finished.emit({
            "parallel": True,
            "results": self._collect_parallel_results(),
            "reason": "All anchors completed.",
        })
        self._state = STATE_IDLE

    def _maybe_finish_all_parallel(self) -> None:
        if (
            self._is_parallel_mode
            and self._states
            and all(st.done and not st.apply_pending for st in self._states.values())
            and self._pending_apply is None
            and not self._apply_queue
        ):
            self._finish_all_parallel()

    def _emit_progress(self, text: str, extra: dict | None = None):
        payload = {"state": self._state, "anchor_id": self._anchor_id, "custom_status_text": text}
        if extra:
            payload.update(extra)
        self.progress_updated.emit(payload)
