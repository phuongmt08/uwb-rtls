"""Stateful helper for time sync correction flows."""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from common.transport import VvAddress

log = logging.getLogger(__name__)


class TimeSyncManager(QObject):
    correction_started = pyqtSignal(str)
    correction_finished = pyqtSignal(bool, int, int)
    verify_requested = pyqtSignal()
    set_requested = pyqtSignal(int, int)

    def __init__(
        self,
        request_query_fn,
        host_time_fn,
        timezone_offset_fn,
        parent=None,
    ):
        super().__init__(parent)
        self._request_query = request_query_fn
        self._host_time_fn = host_time_fn
        self._timezone_offset_fn = timezone_offset_fn
        self._active = False
        self._retry_count = 0
        self._max_retries = 3
        self._verify_delay_ms = 200
        self._retry_base_delay_ms = 500
        self._cooldown_s = 10.0
        self._cooldown_until = 0.0
        self._last_result = None
        self._verify_timer = QTimer(self)
        self._verify_timer.setSingleShot(True)
        self._verify_timer.timeout.connect(self._queue_verify)
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._queue_set)

    @property
    def is_active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._active = False
        self._retry_count = 0
        self._last_result = None
        self._verify_timer.stop()
        self._retry_timer.stop()

    def start(self, reason: str = "manual") -> bool:
        if self._active:
            log.debug("Time sync correction already active; reason=%s ignored.", reason)
            return False
        now = time.monotonic()
        if now < self._cooldown_until:
            log.warning("Time sync correction cooling down; reason=%s skipped.", reason)
            return False
        self._active = True
        self._retry_count = 0
        self.correction_started.emit(reason)
        log.info("Starting time sync correction flow: %s", reason)
        return self._queue_set()

    def handle_response(self, resp) -> dict:
        host_time_ms = int(self._host_time_fn() * 1000)
        tz_offset_min = int(self._timezone_offset_fn())
        tz_offset_sec = tz_offset_min * 60
        dev_time_ms = int(getattr(resp, "unix_time_ms", 0) or 0)
        time_diff_ms = abs(host_time_ms - dev_time_ms)
        is_synced = time_diff_ms <= self._threshold_ms()
        was_corrected = self._active
        result = {
            "dev_time_ms": dev_time_ms,
            "host_time_ms": host_time_ms,
            "tz_offset_sec": tz_offset_sec,
            "tz_offset_min": tz_offset_min,
            "time_diff_ms": time_diff_ms,
            "is_synced": is_synced,
            "was_corrected": was_corrected,
        }
        self._last_result = result
        if is_synced:
            self.finish(True, time_diff_ms)
            return result
        self._handle_drift(time_diff_ms)
        return result

    def finish(self, synced: bool, time_diff_ms: int = 0) -> None:
        self._verify_timer.stop()
        self._retry_timer.stop()
        was_active = self._active
        retry_count = self._retry_count
        self.reset()
        if was_active:
            self.correction_finished.emit(synced, time_diff_ms, retry_count)

    def _threshold_ms(self) -> int:
        from utils.constants import TIME_SYNC_THRESHOLD_MS
        return int(TIME_SYNC_THRESHOLD_MS)

    def _queue_set(self) -> bool:
        if not self._active:
            return False
        host_time_ms = int(self._host_time_fn() * 1000)
        tz_offset_min = int(self._timezone_offset_fn())
        self.set_requested.emit(host_time_ms, tz_offset_min)
        self._request_query(
            "time_sync_set",
            dst_addr=VvAddress.MCU,
            cache_ttl_s=0.0,
            force=True,
            unix_time_ms=host_time_ms,
            timezone_offset=tz_offset_min,
        )
        self._verify_timer.start(self._verify_delay_ms)
        return True

    def _queue_verify(self) -> bool:
        if not self._active:
            return False
        self.verify_requested.emit()
        self._request_query("time_sync_get", dst_addr=VvAddress.MCU, cache_ttl_s=0.0, force=True)
        return True

    def _handle_drift(self, time_diff_ms: int) -> None:
        if not self._active:
            return
        self._verify_timer.stop()
        self._retry_count += 1
        if self._retry_count > self._max_retries:
            self._cooldown_until = time.monotonic() + self._cooldown_s
            log.warning(
                "Time sync still out of threshold after %d retries; cooling down for %.1fs.",
                self._max_retries,
                self._cooldown_s,
            )
            self.finish(False, time_diff_ms)
            return
        delay_ms = self._retry_base_delay_ms * (2 ** (self._retry_count - 1))
        log.warning(
            "Time sync drift %dms exceeds threshold %dms; retry %d/%d in %dms.",
            time_diff_ms,
            self._threshold_ms(),
            self._retry_count,
            self._max_retries,
            delay_ms,
        )
        self._retry_timer.start(delay_ms)


__all__ = ["TimeSyncManager"]
