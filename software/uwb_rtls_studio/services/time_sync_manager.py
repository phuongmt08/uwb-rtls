"""Stateful helper for event-driven time sync correction flows."""
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
        send_command_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self._request_query = request_query_fn
        self._send_command = send_command_fn
        self._host_time_fn = host_time_fn
        self._timezone_offset_fn = timezone_offset_fn
        self._active = False
        self._retry_count = 0
        self._cooldown_s = 10.0
        self._cooldown_until = 0.0
        self._last_result = None
        self._pending_set_seq = None
        self._waiting_for_post_set_resp = False
        self._ack_timeout_timer = QTimer(self)
        self._ack_timeout_timer.setSingleShot(True)
        self._ack_timeout_timer.timeout.connect(self._on_set_ack_timeout)
        self._ack_timeout_ms = 1500

    @property
    def is_active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._active = False
        self._retry_count = 0
        self._last_result = None
        self._pending_set_seq = None
        self._waiting_for_post_set_resp = False
        self._ack_timeout_timer.stop()

    def start(self, reason: str = "manual") -> bool:
        if self._active:
            log.debug("Time sync flow already active; reason=%s ignored.", reason)
            return False
        now = time.monotonic()
        if now < self._cooldown_until:
            log.warning("Time sync correction cooling down; reason=%s skipped.", reason)
            return False
        self._active = True
        self._retry_count = 0
        self.correction_started.emit(reason)
        log.info("Starting event-driven time sync flow: %s", reason)
        if self._queue_verify():
            return True
        self.finish(False, 0)
        return False

    def handle_response(self, resp) -> dict:
        host_time_ms = int(self._host_time_fn() * 1000)
        tz_offset_min = int(self._timezone_offset_fn())
        tz_offset_sec = tz_offset_min * 60
        dev_time_ms = int(getattr(resp, "unix_time_ms", 0) or 0)
        time_diff_ms = abs(host_time_ms - dev_time_ms)
        is_synced = time_diff_ms <= self._threshold_ms()
        was_corrected = self._active or self._waiting_for_post_set_resp
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

        if self._waiting_for_post_set_resp:
            self.finish(is_synced, time_diff_ms)
            return result

        if is_synced:
            if self._active:
                self.finish(True, time_diff_ms)
            return result

        self._handle_drift(time_diff_ms)
        result["was_corrected"] = self._active or self._waiting_for_post_set_resp
        return result

    def handle_ack(self, ack_seq: int, response: int) -> None:
        if self._pending_set_seq is None or int(ack_seq) != int(self._pending_set_seq):
            return

        self._ack_timeout_timer.stop()
        self._pending_set_seq = None
        if int(response) != 1:
            log.warning("time_sync_set NACK response=%s; waiting for future time event.", response)
            self._cooldown_until = time.monotonic() + self._cooldown_s
            self.finish(False, self._last_result.get("time_diff_ms", 0) if self._last_result else 0)
            return

        log.info("time_sync_set ACK received; requesting one time_sync_get for post-set event update.")
        self._waiting_for_post_set_resp = True
        if not self._queue_verify():
            self.finish(False, self._last_result.get("time_diff_ms", 0) if self._last_result else 0)

    def finish(self, synced: bool, time_diff_ms: int = 0) -> None:
        was_active = self._active or self._waiting_for_post_set_resp
        retry_count = self._retry_count
        self.reset()
        if was_active:
            self.correction_finished.emit(synced, time_diff_ms, retry_count)

    def _threshold_ms(self) -> int:
        from utils.constants import TIME_SYNC_THRESHOLD_MS
        return int(TIME_SYNC_THRESHOLD_MS)

    def _queue_set(self) -> bool:
        if not self._active or self._pending_set_seq is not None or self._waiting_for_post_set_resp:
            return False
        if not self._send_command:
            log.warning("time_sync_set skipped: send command path is unavailable.")
            self._cooldown_until = time.monotonic() + self._cooldown_s
            self.finish(False, self._last_result.get("time_diff_ms", 0) if self._last_result else 0)
            return False

        host_time_ms = int(self._host_time_fn() * 1000)
        tz_offset_min = int(self._timezone_offset_fn())
        self.set_requested.emit(host_time_ms, tz_offset_min)
        pkt = self._send_command(
            "time_sync_set",
            dst_addr=VvAddress.MCU,
            unix_time_ms=host_time_ms,
            timezone_offset=tz_offset_min,
        )
        if pkt is None:
            log.warning("time_sync_set was not sent; waiting for future time event.")
            self._cooldown_until = time.monotonic() + self._cooldown_s
            self.finish(False, self._last_result.get("time_diff_ms", 0) if self._last_result else 0)
            return False

        self._pending_set_seq = int(pkt.hdr.seq)
        self._ack_timeout_timer.start(self._ack_timeout_ms)
        log.info("time_sync_set sent seq=%s; waiting for ACK before post-set check.", self._pending_set_seq)
        return True

    def _queue_verify(self) -> bool:
        if not self._active and not self._waiting_for_post_set_resp:
            return False
        if not self._request_query:
            log.warning("time_sync_get skipped: query path is unavailable.")
            return False
        self.verify_requested.emit()
        # Route verification through the shared sequential query queue. Sending
        # directly can interleave with sys_cfg/pos_calib/configuration GETs.
        queued = self._request_query("time_sync_get", dst_addr=VvAddress.MCU, cache_ttl_s=0.0, force=True, traffic_class="bootstrap")
        if not queued:
            log.warning("time_sync_get was not queued; waiting for future time event.")
            return False
        log.info("time_sync_get queued through the shared query pipeline.")
        return True

    def _handle_drift(self, time_diff_ms: int) -> None:
        if self._pending_set_seq is not None:
            return
        now = time.monotonic()
        if now < self._cooldown_until:
            log.debug("Time sync drift %dms seen during cooldown; waiting for future event.", time_diff_ms)
            return
        if not self._active:
            self._active = True
            self._retry_count = 0
            self.correction_started.emit(f"event drift {time_diff_ms}ms")
        log.warning(
            "Time sync drift %dms exceeds threshold %dms; sending one time_sync_set and waiting for ACK.",
            time_diff_ms,
            self._threshold_ms(),
        )
        self._queue_set()

    def _on_set_ack_timeout(self) -> None:
        if self._pending_set_seq is None:
            return
        log.warning("time_sync_set ACK timeout for seq=%s; no retry, waiting for future time event.", self._pending_set_seq)
        self._pending_set_seq = None
        self._cooldown_until = time.monotonic() + self._cooldown_s
        self.finish(False, self._last_result.get("time_diff_ms", 0) if self._last_result else 0)


__all__ = ["TimeSyncManager"]
