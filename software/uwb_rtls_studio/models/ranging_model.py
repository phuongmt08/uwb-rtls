"""
==============================================================================
  UWB RTLS Studio — Ranging Model
===============================================================================
  File        : models/ranging_model.py
  Description : Data model cho ranging results và position data.
                Lưu trữ tọa độ tag, khoảng cách tới anchors,
                và history để vẽ trajectory trên map.

  MVVM Role   : MODEL — chỉ chứa data + buffer.

  Dữ liệu được quản lý:
    - Position hiện tại (x, y, z) của tag
    - Khoảng cách tới từng anchor
    - RMS error (đánh giá chất lượng position)
    - History buffer (N samples gần nhất cho trajectory)
    - Anchor layout (vị trí cố định của các anchor trên sơ đồ)
    - Ranging statistics (success rate, timeout count, ...)

  Được sử dụng bởi:
    - LiveTrackingViewModel → cập nhật position realtime
    - LiveTrackingTabView   → vẽ position lên canvas/map
    - HistoryTabView        → xem lại trajectory
    - StatusBarView         → hiển thị RMS error

  Protocol Messages liên quan:
    - ranging_start_t        (tag=16)  → Bắt đầu ranging
    - ranging_stop_t         (tag=17)  → Dừng ranging
    - ranging_result_t       (tag=18)  → Position + anchor distances
    - ranging_status_get_t   (tag=19)  → Lấy ranging statistics
    - ranging_status_resp_t  (tag=20)  → Response statistics
    - anchor_layout_get_t    (tag=43)  → Lấy vị trí anchors
    - anchor_layout_set_t    (tag=44)  → Set vị trí anchors
    - anchor_layout_resp_t   (tag=45)  → Response vị trí anchors

  Data fields:
    @dataclass
    class PositionSample:
        x_m: float                  # Tọa độ X (meter)
        y_m: float                  # Tọa độ Y (meter)
        z_m: float                  # Tọa độ Z (meter)
        rms_error_m: float          # RMS error (meter)
        timestamp_ms: int           # Timestamp từ device
        anchor_distances: list      # [{anchor_id, distance_mm, fp_amp}, ...]
        received_at: float          # time.time() lúc nhận

    @dataclass
    class AnchorPosition:
        anchor_id: int
        x_m: float
        y_m: float
        z_m: float

    @dataclass
    class RangingState:
        is_ranging: bool                        # Đang ranging hay không
        current_position: PositionSample | None # Sample mới nhất
        position_history: list[PositionSample]  # Buffer N samples
        anchor_layout: list[AnchorPosition]     # Vị trí các anchor
        stats: RangingStats                     # Thống kê
        max_history_size: int = 1000            # Giới hạn buffer

    @dataclass
    class RangingStats:
        total_count: int
        success_count: int
        failed_count: int
        timeout_count: int
        last_rms_error_m: float
        last_avg_rssi_dbm: int
        update_rate_hz: float               # Computed từ timestamps
===============================================================================
"""
import logging
import time
from collections import deque
from datetime import datetime
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from services.protocol_service import ProtocolService
from common.transport import VvAddress
from utils.app_state import shared_app_state, JobState, POLL_RANGING_STATUS_MS

log = logging.getLogger(__name__)

# Maximum number of position samples to keep in history
_MAX_HISTORY_SIZE = 100000
RANGING_UI_EMIT_INTERVAL_S = 0.0
RANGING_STATS_EMIT_INTERVAL_S = 0.20


class RangingModel(QObject):
    position_updated = pyqtSignal(float, float, float, float) # x, y, z, rms
    sensor_fusion_updated = pyqtSignal(dict)
    session_sample_recorded = pyqtSignal(dict)
    calib_data_updated = pyqtSignal(dict)
    anchor_distances_updated = pyqtSignal(list)
    stats_updated = pyqtSignal(dict)
    anchor_layout_updated = pyqtSignal(list)

    def __init__(self, protocol_service: ProtocolService, ranging_repo=None, command_bus=None, parent=None):
        super().__init__(parent)
        self._protocol = protocol_service
        self._ranging_repo = ranging_repo
        shared_app_state.device_session_reset.connect(self._on_device_session_reset)
        self._command_bus = command_bus
        if self._ranging_repo:
            self._ranging_repo.position_parsed.connect(self._handle_position_sample)
            self._ranging_repo.sensor_fusion_parsed.connect(self._handle_sensor_fusion_sample)
            self._ranging_repo.calib_data_parsed.connect(self._handle_calib_data_sample)
            self._ranging_repo.anchor_distances_parsed.connect(self._handle_anchor_distances)
            self._ranging_repo.anchor_layout_parsed.connect(self._handle_anchor_layout_data)
            self._ranging_repo.stats_parsed.connect(self._handle_stats_data)
        else:
            self._protocol.packet_received.connect(self._on_packet)

        # ── State ────────────────────────────────────────────────────
        self.is_ranging = False

        # Position history buffer (bounded deque to prevent memory leak)
        self._position_history = deque(maxlen=_MAX_HISTORY_SIZE)
        self._fusion_history = deque(maxlen=_MAX_HISTORY_SIZE)

        # Anchor layout (fixed positions)
        self._anchor_layout = []   # list of {anchor_id, x_m, y_m, z_m}

        # Ranging statistics
        self._stats = {
            "total_count": 0,
            "success_count": 0,
            "last_rms_error_m": 0.0,
            "update_rate_hz": 0.0,
        }
        self._last_result_time = 0.0
        self._last_fusion_sample: dict | None = None
        self._last_position_emit_at = 0.0
        self._last_fusion_emit_at = 0.0
        self._last_anchor_emit_at = 0.0
        self._last_stats_emit_at = 0.0

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(POLL_RANGING_STATUS_MS)
        self._status_timer.timeout.connect(self.request_ranging_status)

    # ── Public properties ────────────────────────────────────────────

    @property
    def position_history(self):
        return list(self._position_history)

    @property
    def fusion_history(self):
        return list(self._fusion_history)

    @property
    def anchor_layout(self):
        return self._anchor_layout

    def _update_hz_stat(self, now: float) -> None:
        if self._last_result_time > 0:
            dt = now - self._last_result_time
            if dt > 0:
                instant_rate = min(50.0, 1.0 / dt)  # Cap at 50Hz to filter out OS/serial buffering spikes
                current_rate = self._stats.get("update_rate_hz", 0.0)
                if current_rate <= 0.0:
                    new_rate = instant_rate
                else:
                    # Exponential Moving Average to smooth out instant Jitter
                    new_rate = 0.15 * instant_rate + 0.85 * current_rate
                self._stats["update_rate_hz"] = round(new_rate, 1)
        self._last_result_time = now

    def _send_command(
        self,
        command_name: str,
        dst_addr: int,
        command_params: dict | None = None,
        traffic_class: str = "",
        yaw_deg: int | float | None = None,
        is_ukf_reinit: bool | None = None,
    ):
        params = dict(command_params or {})
        if yaw_deg is not None:
            params["yaw_deg"] = yaw_deg
        if is_ukf_reinit is not None:
            params["is_ukf_reinit"] = is_ukf_reinit
        if self._command_bus:
            return self._command_bus.send(
                command_name,
                dst_addr=dst_addr,
                command_params=params,
                traffic_class=traffic_class,
            )
        return self._protocol.send_command(command_name, dst_addr=dst_addr, command_params=params)

    def _request_query(
        self,
        command_name: str,
        dst_addr: int,
        cache_ttl_s: float | None = None,
        force: bool = False,
        traffic_class: str = "",
        command_params: dict | None = None,
        flow_name: str = "",
        timeout_s: float | None = None,
    ):
        params = dict(command_params or {})
        if self._command_bus:
            return self._command_bus.request(
                command_name,
                dst_addr=dst_addr,
                cache_ttl_s=cache_ttl_s,
                force=force,
                traffic_class=traffic_class,
                flow_name=flow_name,
                timeout_s=timeout_s,
                command_params=params,
            )
        return shared_app_state.enqueue_query(
            command_name,
            dst_addr=dst_addr,
            command_params=params,
            traffic_class=traffic_class,
            flow_name=flow_name,
            timeout_s=timeout_s,
        )

    def send_command(self, command_name: str, dst_addr: int = VvAddress.MCU, command_params: dict | None = None):
        """Public model command path used by ViewModels when no CommandBus is injected."""
        return self._send_command(command_name, dst_addr=dst_addr, command_params=command_params)


    def request_zone_profile(self, zone_id: int = 1, force: bool = True, traffic_class: str = "manual", flow_name: str = "live_tracking_map"):
        zone = max(1, int(zone_id or 1))
        device = shared_app_state.connected_device
        role = str(device.get("Role") or device.get("device_role") or device.get("role") or "").strip().upper()
        if role and role != "TAG":
            log.debug("Live tracking map context query skipped for role=%s: zone_profile_get is TAG-only", role)
            return False
        log.debug(
            "Live tracking map context query: zone_profile_get zone_id=%s flow=%s force=%s",
            zone,
            flow_name,
            force,
        )
        return self._request_query(
            "zone_profile_get",
            dst_addr=VvAddress.MCU,
            cache_ttl_s=0.0 if force else None,
            force=force,
            traffic_class=traffic_class,
            flow_name=flow_name,
            timeout_s=4.0,
            command_params={"zone_id": zone},
        )

    def start_ranging(self, yaw_deg: int | float = 0, is_ukf_reinit: bool = False):
        pkt = self._send_command(
            "ranging_start",
            dst_addr=VvAddress.MCU,
            yaw_deg=yaw_deg,
            is_ukf_reinit=is_ukf_reinit,
        )
        self.is_ranging = True
        shared_app_state.ranging_active = True
        shared_app_state.update_job("ranging_session", JobState.RUNNING)
        self._status_timer.start()
        return pkt

    def stop_ranging(self):
        pkt = self._send_command("ranging_stop", dst_addr=VvAddress.MCU)
        self.is_ranging = False
        shared_app_state.ranging_active = False
        shared_app_state.update_job("ranging_session", JobState.IDLE)
        self._status_timer.stop()
        return pkt

    def request_ranging_status(self, force: bool = False):
        if (
            not force
            and self.is_ranging
            and self._last_result_time > 0.0
            and (time.time() - self._last_result_time) < 3.0
        ):
            # When ranging_result packets are flowing, avoid stealing airtime
            # with periodic status polls.
            return None
        return self._request_query(
            "ranging_status_get",
            dst_addr=VvAddress.MCU,
            cache_ttl_s=0.0,
            force=force,
            traffic_class="background",
        )

    def set_anchor_layout(self, anchors: list):
        """Set anchor positions. Called by ConfigViewModel."""
        self._anchor_layout = anchors
        if self._ranging_repo and hasattr(self._ranging_repo, "update_anchor_layout_cache"):
            self._ranging_repo.update_anchor_layout_cache(anchors)
        shared_app_state.anchor_layout = anchors

    def _on_device_session_reset(self, reason: str = ""):
        self.clear_history()
        if str(reason or "").strip().lower() == "read from device refresh":
            return
        self.is_ranging = False
        self._status_timer.stop()

    def clear_history(self):
        """Clear position history buffer."""
        self._position_history.clear()
        self._fusion_history.clear()
        self._stats = {
            "total_count": 0,
            "success_count": 0,
            "last_rms_error_m": 0.0,
            "update_rate_hz": 0.0,
        }
        self._last_result_time = 0.0
        self._last_fusion_sample = None
        self._last_position_emit_at = 0.0
        self._last_fusion_emit_at = 0.0
        self._last_anchor_emit_at = 0.0
        self._last_stats_emit_at = 0.0
        shared_app_state.ranging_stats = self._stats.copy()

    # ── Packet handler ───────────────────────────────────────────────

    def _on_packet(self, param_name: str, pkt) -> None:
        seq, packet_timestamp_ms = self._packet_meta(pkt)
        if param_name == "ranging_result":
            self._handle_ranging_result(
                pkt.ranging_result,
                seq=seq,
                packet_timestamp_ms=packet_timestamp_ms,
            )
        elif param_name == "sensor_fusion_result":
            self._handle_sensor_fusion_result(
                pkt.sensor_fusion_result,
                seq=seq,
                packet_timestamp_ms=packet_timestamp_ms,
            )
        elif param_name == "calib_data":
            self._handle_calib_data(
                pkt.calib_data,
                seq=seq,
                packet_timestamp_ms=packet_timestamp_ms,
            )
        elif param_name == "anchor_layout_resp":
            self._handle_anchor_layout(pkt.anchor_layout_resp)
        elif param_name == "ranging_status_resp":
            self._handle_ranging_status(pkt.ranging_status_resp)

    @staticmethod
    def _packet_meta(pkt) -> tuple[int, int]:
        hdr = getattr(pkt, "hdr", None)
        return (
            int(getattr(hdr, "seq", 0) or 0),
            int(getattr(hdr, "timestamp", 0) or 0),
        )

    @staticmethod
    def _extract_room_frame_fields(payload) -> dict:
        room_id = ""
        for field_name in ("room_id", "active_room_id", "zone_id"):
            value = getattr(payload, field_name, "")
            if value not in (None, ""):
                room_id = str(value)
                break

        def first_value(*names, default=None):
            for name in names:
                value = getattr(payload, name, None)
                if value is not None:
                    return value
            return default

        local_x = first_value("local_x_m", "pos_local_x_m", "ukf_local_x_m", "tril_local_x_m")
        local_y = first_value("local_y_m", "pos_local_y_m", "ukf_local_y_m", "tril_local_y_m")
        local_z = first_value("local_z_m", "pos_local_z_m", default=None)
        return {
            "room_id": room_id,
            "local_x_m": float(local_x) if local_x is not None else None,
            "local_y_m": float(local_y) if local_y is not None else None,
            "local_z_m": float(local_z) if local_z is not None else None,
        }
    @staticmethod
    def _payload_size(message) -> int:
        try:
            return int(message.ByteSize())
        except Exception:
            return 0

    @staticmethod
    def _parse_anchor_distances(res) -> tuple[list[dict], int, dict[int, int]]:
        anchors = []
        distances_by_anchor: dict[int, int] = {}
        anchor_ids = []
        for anchor in getattr(res, "anchors", []):
            anchor_id = int(getattr(anchor, "anchor_id", 0) or 0)
            distance_mm = int(getattr(anchor, "distance_mm", 0) or 0)
            anchor_ids.append(anchor_id)
            anchors.append({
                "id": f"A{anchor_id}",
                "anchor_id": anchor_id,
                "distance_mm": distance_mm,
                "distance_cm": distance_mm / 10.0,
                "fp_amp": int(getattr(anchor, "fp_amp", 0) or 0),
            })
            distances_by_anchor[anchor_id] = distance_mm

        zero_based = any(anchor_id == 0 for anchor_id in anchor_ids)
        anchor_mask = 0
        for anchor_id in anchor_ids:
            if zero_based:
                if 0 <= anchor_id < 32:
                    anchor_mask |= 1 << anchor_id
            elif 1 <= anchor_id <= 32:
                anchor_mask |= 1 << (anchor_id - 1)
        return anchors, anchor_mask, distances_by_anchor
    def _handle_ranging_result(self, res, seq: int = 0, packet_timestamp_ms: int = 0):
        now = time.time()
        anchors, anchor_mask, distances_by_anchor = self._parse_anchor_distances(res)

        # Store in history buffer
        room_frame = self._extract_room_frame_fields(res)
        sample = {
            "x_m": float(getattr(res, "pos_x_m", 0.0)),
            "y_m": float(getattr(res, "pos_y_m", 0.0)),
            "z_m": float(getattr(res, "pos_z_m", 0.0)),
            "rms_error_m": float(getattr(res, "rms_error_m", 0.0)),
            "timestamp_ms": int(getattr(res, "timestamp_ms", 0)),
            "packet_timestamp_ms": int(packet_timestamp_ms or 0),
            "received_at": now,
            "source": "ranging",
            "seq": int(seq or 0),
            "payload_size": self._payload_size(res),
            "anchor_mask": anchor_mask,
            "anchor_mask_valid": bool(anchors),
            "d1_mm": distances_by_anchor.get(1, ""),
            "d2_mm": distances_by_anchor.get(2, ""),
            "d3_mm": distances_by_anchor.get(3, ""),
            "d4_mm": distances_by_anchor.get(4, ""),
            "d5_mm": distances_by_anchor.get(5, ""),
            "d6_mm": distances_by_anchor.get(6, ""),
            "anchors": anchors,
            "room_id": room_frame["room_id"],
            "local_x_m": room_frame["local_x_m"],
            "local_y_m": room_frame["local_y_m"],
            "local_z_m": room_frame["local_z_m"],
        }
        self._position_history.append(sample)

        # Update statistics
        self._stats["total_count"] += 1
        self._stats["success_count"] += 1
        self._stats["last_rms_error_m"] = sample["rms_error_m"]
        self._update_hz_stat(now)

        self._emit_position_if_due(sample, now=now)
        if anchors:
            self._emit_anchor_distances_if_due(anchors, now=now)
        self._emit_stats_if_due(now=now)

    def _handle_sensor_fusion_result(self, res, seq: int = 0, packet_timestamp_ms: int = 0):
        anchors = [
            {
                "anchor_id": int(getattr(anchor, "anchor_id", 0)),
                "distance_mm": int(getattr(anchor, "distance_mm", 0)),
                "weight": int(getattr(anchor, "weight", 0)),
            }
            for anchor in getattr(res, "anchors", [])
        ]
        distances_by_anchor = {
            anchor["anchor_id"]: anchor["distance_mm"] for anchor in anchors
        }
        room_frame = self._extract_room_frame_fields(res)
        sample = {
            "ukf_step": int(getattr(res, "ukf_step", 0)),
            "ukf_x_m": float(getattr(res, "ukf_x_m", 0)) / 100.0,
            "ukf_y_m": float(getattr(res, "ukf_y_m", 0)) / 100.0,
            "ukf_yaw_deg": float(getattr(res, "ukf_yaw_deg", 0)) / 100.0,
            "tril_x_m": float(getattr(res, "tril_x_m", 0)) / 100.0,
            "tril_y_m": float(getattr(res, "tril_y_m", 0)) / 100.0,
            "yaw_deg": float(getattr(res, "yaw_deg", 0)) / 100.0,
            "anchor_mask": int(getattr(res, "anchor_mask", 0)),
            "anchor_mask_valid": True,
            "ranging_error_count": int(getattr(res, "ranging_error_count", 0)),
            "prefilter_reject_count": int(getattr(res, "prefilter_reject_count", 0)),
            "timestamp_ms": int(getattr(res, "timestamp_ms", 0)),
            "zone_id": int(getattr(res, "zone_id", 0)),
            "anchors": anchors,
            "d1_mm": distances_by_anchor.get(1, ""),
            "d2_mm": distances_by_anchor.get(2, ""),
            "d3_mm": distances_by_anchor.get(3, ""),
            "d4_mm": distances_by_anchor.get(4, ""),
            "d5_mm": distances_by_anchor.get(5, ""),
            "d6_mm": distances_by_anchor.get(6, ""),
            "packet_timestamp_ms": int(packet_timestamp_ms or 0),
            "received_at": time.time(),
            "source": "sensor_fusion",
            "seq": int(seq or 0),
            "payload_size": self._payload_size(res),
            "room_id": room_frame["room_id"],
            "local_x_m": room_frame["local_x_m"],
            "local_y_m": room_frame["local_y_m"],
            "local_z_m": room_frame["local_z_m"],
        }
        if sample["zone_id"] and sample.get("local_x_m") is None and sample.get("local_y_m") is None:
            sample["room_id"] = str(sample["zone_id"])
            sample["local_x_m"] = sample["ukf_x_m"]
            sample["local_y_m"] = sample["ukf_y_m"]
            sample["local_z_m"] = 0.0
            sample["tril_local_x_m"] = sample["tril_x_m"]
            sample["tril_local_y_m"] = sample["tril_y_m"]
        self._handle_sensor_fusion_sample(sample)

    def _handle_ranging_status(self, resp):
        total = int(getattr(resp, "ranging_total_count", 0))
        success = int(getattr(resp, "ranging_success_count", 0))
        failed = int(getattr(resp, "ranging_failed_count", 0))
        timeout = int(getattr(resp, "ranging_timeout_count", 0))
        stats = {
            "ranging_period_ms": int(getattr(resp, "ranging_period_ms", 0)),
            "ranging_total_count": total,
            "ranging_success_count": success,
            "ranging_failed_count": failed,
            "ranging_timeout_count": timeout,
            "total_count": total,
            "success_count": success,
            "failed_count": failed,
            "timeout_count": timeout,
            "last_ranging_time_ms": int(getattr(resp, "last_ranging_time_ms", 0)),
            "last_rms_error_m": float(getattr(resp, "last_rms_error_m", 0.0)),
            "last_avg_rssi_dbm": int(getattr(resp, "last_avg_rssi_dbm", 0)),
            "last_update_timestamp_ms": int(getattr(resp, "last_update_timestamp_ms", 0)),
            "success_rate_percent": (success / total * 100.0) if total else 0.0,
        }
        self._handle_stats_data(stats)

    def _handle_anchor_layout(self, resp):
        """Parse anchor_layout_resp and store."""
        self._anchor_layout = []
        for a in resp.anchors:
            def coord_or_none(name: str):
                return float(getattr(a, name))

            self._anchor_layout.append({
                "anchor_id": a.anchor_id,
                "x_m": coord_or_none("x_m"),
                "y_m": coord_or_none("y_m"),
                "z_m": coord_or_none("z_m"),
                "label": f"A{a.anchor_id}",
                "role": "anchor",
                "device_type": "uwb_anchor",
                "device_id": a.anchor_id,
                "zone_id": "",
                "zone_name": "",
                "zone_ids": [],
                "zone_names": [],
                "placed": True,
                "is_scanned": False,
                "sync_state": "synced",
            })
        log.info("Anchor layout received: %d anchors", len(self._anchor_layout))
        self.anchor_layout_updated.emit(self._anchor_layout)
        shared_app_state.anchor_layout = self._anchor_layout

    def _handle_position_sample(self, sample: dict):
        stored = sample.copy()
        stored.setdefault("source", "ranging")
        stored.setdefault("seq", 0)
        stored.setdefault("anchor_mask", 0)
        stored.setdefault("anchor_mask_valid", bool(stored.get("anchors")))
        stored.setdefault("payload_size", 0)
        stored.setdefault("d1_mm", "")
        stored.setdefault("d2_mm", "")
        stored.setdefault("d3_mm", "")
        stored.setdefault("d4_mm", "")
        stored.setdefault("d5_mm", "")
        stored.setdefault("d6_mm", "")
        self._position_history.append(stored)
        self._stats["total_count"] += 1
        self._stats["success_count"] += 1
        self._stats["last_rms_error_m"] = stored.get("rms_error_m", stored.get("rms", 0.0))

        now = stored.get("received_at", time.time())
        self._update_hz_stat(now)

        self._emit_position_if_due(stored, now=now)
        self._emit_stats_if_due(now=now)

    def _handle_sensor_fusion_sample(self, sample: dict):
        enriched = sample.copy()
        enriched.setdefault("source", "sensor_fusion")
        enriched.setdefault("seq", 0)
        enriched.setdefault("payload_size", 0)
        enriched.setdefault("anchor_mask_valid", True)
        enriched.setdefault("vx_mps", 0.0)
        enriched.setdefault("vy_mps", 0.0)

        prev = self._last_fusion_sample
        if prev:
            curr_ts = enriched.get("timestamp_ms", 0)
            prev_ts = prev.get("timestamp_ms", 0)
            dt_s = 0.0
            if curr_ts and prev_ts and curr_ts > prev_ts:
                dt_s = (curr_ts - prev_ts) / 1000.0
            else:
                dt_s = enriched.get("received_at", time.time()) - prev.get("received_at", time.time())

            if dt_s > 0:
                enriched["vx_mps"] = (enriched["ukf_x_m"] - prev.get("ukf_x_m", 0.0)) / dt_s
                enriched["vy_mps"] = (enriched["ukf_y_m"] - prev.get("ukf_y_m", 0.0)) / dt_s

        self._last_fusion_sample = enriched.copy()
        self._fusion_history.append(enriched.copy())
        self.session_sample_recorded.emit(enriched.copy())
        now = enriched.get("received_at", time.time())
        self._stats["total_count"] = int(self._stats.get("total_count", 0)) + 1
        self._stats["success_count"] = int(self._stats.get("success_count", 0)) + 1
        self._stats["ranging_error_count"] = enriched.get("ranging_error_count", 0)
        self._update_hz_stat(now)
        self._emit_sensor_fusion_if_due(enriched, now=now)
        if enriched.get("anchors"):
            self._emit_anchor_distances_if_due(enriched["anchors"], now=now)
        self._emit_stats_if_due(now=now)

    def _handle_calib_data_sample(self, sample: dict):
        self.calib_data_updated.emit(sample.copy())

    def _handle_calib_data(self, data, seq: int = 0, packet_timestamp_ms: int = 0):
        sample = {
            "source": "calib_data",
            "seq": int(seq or 0),
            "packet_timestamp_ms": int(packet_timestamp_ms or 0),
            "received_at": time.time(),
            "anchor_mask": int(getattr(data, "anchor_mask", 0)),
            "tx_frame_cnt": int(getattr(data, "tx_frame_cnt", 0)),
            "ax": float(getattr(data, "ax", 0.0)),
            "ay": float(getattr(data, "ay", 0.0)),
            "gz": float(getattr(data, "gz", 0.0)),
            "px": float(getattr(data, "px", 0.0)),
            "py": float(getattr(data, "py", 0.0)),
            "distance": [float(value) for value in getattr(data, "distance", [])],
            "fp_amp_norm": [float(value) for value in getattr(data, "fp_amp_norm", [])],
            "fp_snr": [float(value) for value in getattr(data, "fp_snr", [])],
            "error_frame_cnt": int(getattr(data, "error_frame_cnt", 0)),
            "dt": float(getattr(data, "dt", 0.0)),
        }
        self._handle_calib_data_sample(sample)

    def _should_emit(self, attr_name: str, now: float, interval_s: float) -> bool:
        last = float(getattr(self, attr_name, 0.0) or 0.0)
        if last <= 0.0 or now - last >= interval_s:
            setattr(self, attr_name, now)
            return True
        return False

    def _emit_position_if_due(self, sample: dict, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        if not self._should_emit("_last_position_emit_at", now, RANGING_UI_EMIT_INTERVAL_S):
            return
        self.position_updated.emit(
            float(sample.get("x_m", sample.get("x", 0.0))),
            float(sample.get("y_m", sample.get("y", 0.0))),
            float(sample.get("z_m", sample.get("z", 0.0))),
            float(sample.get("rms_error_m", sample.get("rms", 0.0))),
        )

    def _emit_sensor_fusion_if_due(self, sample: dict, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        if self._should_emit("_last_fusion_emit_at", now, RANGING_UI_EMIT_INTERVAL_S):
            self.sensor_fusion_updated.emit(sample.copy())

    def _emit_anchor_distances_if_due(self, anchors: list, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        if self._should_emit("_last_anchor_emit_at", now, RANGING_UI_EMIT_INTERVAL_S):
            self.anchor_distances_updated.emit(anchors)

    def _emit_stats_if_due(self, now: float | None = None, force: bool = False) -> None:
        now = time.time() if now is None else float(now)
        if force or self._should_emit("_last_stats_emit_at", now, RANGING_STATS_EMIT_INTERVAL_S):
            snapshot = self._stats.copy()
            self.stats_updated.emit(snapshot)
            shared_app_state.ranging_stats = snapshot

    def build_session_samples(self) -> list[dict]:
        """Return a combined, time-ordered snapshot for session export."""
        samples: list[dict] = []
        for item in self._position_history:
            copied = item.copy()
            copied.setdefault("source", "ranging")
            copied.setdefault("time", datetime.fromtimestamp(copied.get("received_at", time.time())).strftime("%d/%m/%Y %H:%M:%S"))
            samples.append(copied)
        for item in self._fusion_history:
            copied = item.copy()
            copied.setdefault("source", "sensor_fusion")
            copied.setdefault("time", datetime.fromtimestamp(copied.get("received_at", time.time())).strftime("%d/%m/%Y %H:%M:%S"))
            samples.append(copied)
        samples.sort(key=lambda row: (int(row.get("timestamp_ms", 0) or 0), row.get("source", "")))
        return samples

    def _handle_anchor_distances(self, anchors: list):
        self._emit_anchor_distances_if_due(anchors)

    def _handle_anchor_layout_data(self, anchors: list):
        self._anchor_layout = list(anchors)
        self.anchor_layout_updated.emit(self._anchor_layout)
        shared_app_state.anchor_layout = self._anchor_layout

    def _handle_stats_data(self, stats: dict):
        self._stats.update(stats)
        self._emit_stats_if_due(force=True)
