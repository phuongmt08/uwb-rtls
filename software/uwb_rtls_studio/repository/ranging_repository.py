"""
Repository for ranging-related protobuf packets.

It converts protobuf messages into plain dictionaries that are easy for models
and UI code to consume. It also keeps a small in-memory history for sessions and
debugging.
"""
from __future__ import annotations

import csv
import time
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state

log = logging.getLogger(__name__)

_CALIB_EXPORT_PREFIX = "ukf_log_data"
_CALIB_EXPORT_SUFFIX = ".csv"
_CALIB_PREDICT_THRESHOLD_M = 0.001


class RangingRepository(QObject):
    position_parsed = pyqtSignal(dict)
    sensor_fusion_parsed = pyqtSignal(dict)
    calib_data_parsed = pyqtSignal(dict)
    ranging_data_updated = pyqtSignal(dict)
    anchor_distances_parsed = pyqtSignal(list)
    anchor_layout_parsed = pyqtSignal(list)
    stats_parsed = pyqtSignal(dict)

    def __init__(self, max_positions: int = 100000, parent=None, *, calib_export_root: str | Path | None = None):
        super().__init__(parent)
        self._positions = deque(maxlen=max_positions)
        self._fusion_samples = deque(maxlen=max_positions)
        self._calib_samples = deque(maxlen=max_positions)
        self._anchor_layout: list[dict] = []
        self._stats: dict = {}
        self._calib_export_root = Path(calib_export_root) if calib_export_root is not None else self._default_calib_export_root()
        self._calib_export_path: Path | None = None
        self._calib_export_count = 0
        self._calib_prev_distances: list[float] | None = None
        shared_app_state.device_session_reset.connect(self.reset_session)

    def reset_session(self, _reason: str = "") -> None:
        self._positions.clear()
        self._fusion_samples.clear()
        self._calib_samples.clear()
        self._anchor_layout = []
        self._stats = {}
        self.reset_calib_export()

    def reset_calib_export(self) -> None:
        self._calib_export_path = None
        self._calib_export_count = 0
        self._calib_prev_distances = None

    @staticmethod
    def _decode_fixed2(value) -> float:
        return float(value) / 100.0

    @staticmethod
    def _payload_size(message) -> int:
        try:
            return int(message.ByteSize())
        except Exception:
            return 0

    @property
    def positions(self) -> list[dict]:
        return list(self._positions)

    @property
    def fusion_samples(self) -> list[dict]:
        return list(self._fusion_samples)

    @property
    def anchor_layout(self) -> list[dict]:
        return list(self._anchor_layout)

    @property
    def stats(self) -> dict:
        return self._stats.copy()

    @property
    def calib_export_path(self) -> str:
        return str(self._calib_export_path or "")

    def handle_packet(self, param_name: str, pkt) -> bool:
        seq = int(getattr(getattr(pkt, "hdr", None), "seq", 0) or 0)
        packet_timestamp_ms = int(getattr(getattr(pkt, "hdr", None), "timestamp", 0) or 0)
        if param_name == "ranging_result":
            self.parse_ranging_result(
                pkt.ranging_result,
                seq=seq,
                packet_timestamp_ms=packet_timestamp_ms,
            )
            return True
        if param_name == "sensor_fusion_result":
            self.parse_sensor_fusion_result(
                pkt.sensor_fusion_result,
                seq=seq,
                packet_timestamp_ms=packet_timestamp_ms,
            )
            return True
        if param_name == "calib_data":
            self.parse_calib_data(
                pkt.calib_data,
                seq=seq,
                packet_timestamp_ms=packet_timestamp_ms,
            )
            return True
        if param_name == "anchor_layout_resp":
            seq = int(getattr(getattr(pkt, "hdr", None), "seq", 0) or 0)
            anchors = self.parse_anchor_layout(pkt.anchor_layout_resp)
            log.info("[ANCHOR_LAYOUT] received seq=%s anchors=%s; published to SharedAppState/LiveTracking", seq, len(anchors))
            return True
        if param_name == "ranging_status_resp":
            self.parse_ranging_status(pkt.ranging_status_resp)
            return True
        return False

    def parse_ranging_result(self, res, seq: int = 0, packet_timestamp_ms: int = 0) -> dict:
        anchors = [
            {
                "anchor_id": int(a.anchor_id),
                "id": f"A{int(a.anchor_id)}",
                "distance_mm": int(a.distance_mm),
                "distance_cm": float(a.distance_mm) / 10.0,
                "fp_amp": int(a.fp_amp),
            }
            for a in getattr(res, "anchors", [])
        ]
        anchor_mask = self._build_anchor_mask(anchors)
        distances_by_anchor = self._distances_by_anchor_id(anchors)
        sample = {
            "source": "ranging",
            "seq": int(seq or 0),
            "payload_size": self._payload_size(res),
            "x": float(res.pos_x_m),
            "y": float(res.pos_y_m),
            "z": float(res.pos_z_m),
            "rms": float(res.rms_error_m),
            "x_m": float(res.pos_x_m),
            "y_m": float(res.pos_y_m),
            "z_m": float(res.pos_z_m),
            "rms_error_m": float(res.rms_error_m),
            "timestamp_ms": int(getattr(res, "timestamp_ms", 0)),
            "packet_timestamp_ms": int(packet_timestamp_ms or 0),
            "received_at": time.time(),
            "anchor_mask": anchor_mask,
            "anchor_mask_valid": bool(anchors),
            "d1_mm": distances_by_anchor.get(1, ""),
            "d2_mm": distances_by_anchor.get(2, ""),
            "d3_mm": distances_by_anchor.get(3, ""),
            "d4_mm": distances_by_anchor.get(4, ""),
            "d5_mm": distances_by_anchor.get(5, ""),
            "d6_mm": distances_by_anchor.get(6, ""),
            "anchors": anchors,
        }
        self._positions.append(sample)
        self.position_parsed.emit(sample)
        self.ranging_data_updated.emit(sample)
        if anchors:
            self.anchor_distances_parsed.emit(anchors)
        return sample

    def parse_sensor_fusion_result(self, res, seq: int = 0, packet_timestamp_ms: int = 0) -> dict:
        anchors = [
            {
                "anchor_id": int(getattr(anchor, "anchor_id", 0)),
                "distance_mm": int(getattr(anchor, "distance_mm", 0)),
                "weight": int(getattr(anchor, "weight", 0)),
            }
            for anchor in getattr(res, "anchors", [])
        ]
        distances_by_anchor = self._distances_by_anchor_id(anchors)

        sample = {
            "source": "sensor_fusion",
            "seq": int(seq or 0),
            "payload_size": self._payload_size(res),
            "ukf_step": int(getattr(res, "ukf_step", 0)),
            "ukf_x_m": self._decode_fixed2(getattr(res, "ukf_x_m", 0)),
            "ukf_y_m": self._decode_fixed2(getattr(res, "ukf_y_m", 0)),
            "ukf_yaw_deg": self._decode_fixed2(getattr(res, "ukf_yaw_deg", 0)),
            "tril_x_m": self._decode_fixed2(getattr(res, "tril_x_m", 0)),
            "tril_y_m": self._decode_fixed2(getattr(res, "tril_y_m", 0)),
            "yaw_deg": self._decode_fixed2(getattr(res, "yaw_deg", 0)),
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
        }
        if sample["zone_id"]:
            sample["room_id"] = str(sample["zone_id"])
            sample["local_x_m"] = sample["ukf_x_m"]
            sample["local_y_m"] = sample["ukf_y_m"]
            sample["local_z_m"] = 0.0
            sample["tril_local_x_m"] = sample["tril_x_m"]
            sample["tril_local_y_m"] = sample["tril_y_m"]
        self._fusion_samples.append(sample)
        self.sensor_fusion_parsed.emit(sample)
        return sample

    def parse_calib_data(self, data, seq: int = 0, packet_timestamp_ms: int = 0) -> dict:
        sample = {
            "source": "calib_data",
            "seq": int(seq or 0),
            "payload_size": self._payload_size(data),
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
        self._calib_samples.append(sample)
        self._append_calib_data_csv(sample)
        self.calib_data_parsed.emit(sample)
        return sample

    @staticmethod
    def _default_calib_export_root() -> Path:
        software_dir = Path(__file__).resolve().parents[2]
        return software_dir / "data" / "studio"

    def _ensure_calib_export_path(self) -> Path:
        if self._calib_export_path is not None:
            return self._calib_export_path
        now = datetime.now()
        output_dir = self._calib_export_root / now.strftime("%d_%m_%y")
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{_CALIB_EXPORT_PREFIX}{_CALIB_EXPORT_SUFFIX}"
        self._calib_export_path = output_dir / filename
        try:
            rel_path = self._calib_export_path.relative_to(Path(__file__).resolve().parents[2])
            log.info("Calib data CSV export started: %s", rel_path.as_posix())
        except ValueError:
            log.info("Calib data CSV export started: %s", self._calib_export_path)
        return self._calib_export_path

    def _append_calib_data_csv(self, sample: dict) -> None:
        try:
            path = self._ensure_calib_export_path()
            self._calib_export_count += 1
            status = self._classify_calib_status(sample)
            with path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow([
                    self._build_calib_text_record(sample, self._calib_export_count, status)
                ])
        except Exception as exc:
            log.warning("Failed to append calib_data CSV export: %s", exc)

    def _classify_calib_status(self, sample: dict) -> str:
        distances = [float(value) for value in sample.get("distance", [])]
        if self._calib_prev_distances is None:
            self._calib_prev_distances = distances.copy()
            return "Init"

        status = "Predict"
        if distances and any(abs(value) >= 1e-6 for value in distances):
            prev = self._calib_prev_distances or []
            if len(prev) != len(distances):
                status = "Update"
            else:
                for current, previous in zip(distances, prev):
                    if abs(current - previous) > _CALIB_PREDICT_THRESHOLD_M:
                        status = "Update"
                        break
        self._calib_prev_distances = distances.copy()
        return status

    @staticmethod
    def _calib_list_value(sample: dict, key: str, index: int, default: float = 0.0) -> float:
        values = sample.get(key, [])
        try:
            return float(values[index])
        except (TypeError, ValueError, IndexError):
            return default

    def _build_calib_text_record(self, sample: dict, index: int, status: str) -> str:
        dt = float(sample.get("dt", 0.0) or 0.0)
        update_dt = dt if status in ("Init", "Update") else 0.0
        predict_dt = dt if status == "Predict" else 0.0
        distances = [self._calib_list_value(sample, "distance", i) for i in range(6)]
        fp_amp = [self._calib_list_value(sample, "fp_amp_norm", i) for i in range(6)]
        fp_snr = [self._calib_list_value(sample, "fp_snr", i) for i in range(6)]

        return (
            f"({int(index):4d}/{int(sample.get('tx_frame_cnt', 0) or 0):4d}) {status:<7s} "
            f"| ts: {int(sample.get('packet_timestamp_ms', 0) or 0)} "
            f"| zone: 0 "
            f"| ukf_step: {1 if status in ('Init', 'Update') else 0} "
            f"| ax: {float(sample.get('ax', 0.0) or 0.0):9.6f} "
            f"| ay: {float(sample.get('ay', 0.0) or 0.0):9.6f} "
            f"| gz: {float(sample.get('gz', 0.0) or 0.0):9.6f} "
            f"| tril_x: {float(sample.get('px', 0.0) or 0.0):9.6f} "
            f"| tril_y: {float(sample.get('py', 0.0) or 0.0):9.6f} "
            f"| ukf_x: {float(sample.get('px', 0.0) or 0.0):9.6f} "
            f"| ukf_y: {float(sample.get('py', 0.0) or 0.0):9.6f} "
            f"| ukf_yaw: {0.0:9.6f} "
            f"| yaw: {0.0:9.6f} "
            f"| update_dt: {update_dt:9.6f} "
            f"| predict_dt: {predict_dt:9.6f} "
            f"| mask: {int(sample.get('anchor_mask', 0) or 0)} "
            f"| d1: {distances[0]:9.6f} | d2: {distances[1]:9.6f} "
            f"| d3: {distances[2]:9.6f} | d4: {distances[3]:9.6f} "
            f"| d5: {distances[4]:9.6f} | d6: {distances[5]:9.6f} "
            f"| w1: 0 | w2: 0 | w3: 0 | w4: 0 | w5: 0 | w6: 0 "
            f"| err: {int(sample.get('error_frame_cnt', 0) or 0)} "
            f"| amp1: {fp_amp[0]:9.6f} | amp2: {fp_amp[1]:9.6f} "
            f"| amp3: {fp_amp[2]:9.6f} | amp4: {fp_amp[3]:9.6f} "
            f"| amp5: {fp_amp[4]:9.6f} | amp6: {fp_amp[5]:9.6f} "
            f"| snr1: {fp_snr[0]:9.6f} | snr2: {fp_snr[1]:9.6f} "
            f"| snr3: {fp_snr[2]:9.6f} | snr4: {fp_snr[3]:9.6f} "
            f"| snr5: {fp_snr[4]:9.6f} | snr6: {fp_snr[5]:9.6f}"
        )

    @staticmethod
    def _build_anchor_mask(anchors: list[dict]) -> int:
        """Build an anchor mask from real anchor_id values."""
        anchor_ids = [int(anchor.get("anchor_id", 0) or 0) for anchor in anchors]
        zero_based = any(anchor_id == 0 for anchor_id in anchor_ids)
        mask = 0
        for anchor_id in anchor_ids:
            if zero_based:
                if 0 <= anchor_id < 32:
                    mask |= 1 << anchor_id
            elif 1 <= anchor_id <= 32:
                mask |= 1 << (anchor_id - 1)
        return mask

    @staticmethod
    def _distances_by_anchor_id(anchors: list[dict]) -> dict[int, int]:
        distances: dict[int, int] = {}
        for anchor in anchors:
            anchor_id = int(anchor.get("anchor_id", 0) or 0)
            if anchor_id >= 0:
                distances[anchor_id] = int(anchor.get("distance_mm", 0) or 0)
        return distances
    def parse_anchor_layout(self, resp) -> list[dict]:
        anchors = []
        for a in getattr(resp, "anchors", []):
            anchor_id = int(getattr(a, "anchor_id", 0))

            def coord_or_none(name: str):
                return float(getattr(a, name))

            x_m = coord_or_none("x_m")
            y_m = coord_or_none("y_m")
            z_m = coord_or_none("z_m")
            anchors.append({
                "anchor_id": anchor_id,
                "x_m": x_m,
                "y_m": y_m,
                "z_m": z_m,
                "x": x_m,
                "y": y_m,
                "z": z_m,
                "label": f"A{anchor_id}",
                "role": "anchor",
                "device_type": "uwb_anchor",
                "device_id": anchor_id,
                "zone_id": "",
                "zone_name": "",
                "zone_ids": [],
                "zone_names": [],
                "room_id": "",
                "local_x_m": x_m,
                "local_y_m": y_m,
                "placed": True,
                "is_scanned": False,
                "sync_state": "synced",
            })
        self._anchor_layout = [anchor.copy() for anchor in anchors]
        # Publish through shared state as well as the repository signal. Live
        # Tracking listens to shared_app_state.anchor_layout_changed, so without
        # this a parsed anchor_layout_resp can appear in debug but never reach
        # the canvas/table.
        shared_app_state.anchor_layout = [anchor.copy() for anchor in anchors]
        self.anchor_layout_parsed.emit([anchor.copy() for anchor in anchors])
        return anchors

    def update_anchor_layout_cache(self, anchors: list[dict], emit: bool = False) -> None:
        self._anchor_layout = [a.copy() for a in anchors]
        if emit:
            self.anchor_layout_parsed.emit(self.anchor_layout)

    def parse_ranging_status(self, resp) -> dict:
        stats = {
            "ranging_period_ms": int(getattr(resp, "ranging_period_ms", 0)),
            "ranging_total_count": int(getattr(resp, "ranging_total_count", 0)),
            "ranging_success_count": int(getattr(resp, "ranging_success_count", 0)),
            "ranging_failed_count": int(getattr(resp, "ranging_failed_count", 0)),
            "ranging_timeout_count": int(getattr(resp, "ranging_timeout_count", 0)),
            "total_count": int(getattr(resp, "ranging_total_count", 0)),
            "success_count": int(getattr(resp, "ranging_success_count", 0)),
            "failed_count": int(getattr(resp, "ranging_failed_count", 0)),
            "timeout_count": int(getattr(resp, "ranging_timeout_count", 0)),
            "last_ranging_time_ms": int(getattr(resp, "last_ranging_time_ms", 0)),
            "last_rms_error_m": float(getattr(resp, "last_rms_error_m", 0.0)),
            "last_avg_rssi_dbm": int(getattr(resp, "last_avg_rssi_dbm", 0)),
            "last_update_timestamp_ms": int(getattr(resp, "last_update_timestamp_ms", 0)),
            "received_at": time.time(),
        }
        total = stats["total_count"]
        stats["success_rate_percent"] = (stats["success_count"] / total * 100.0) if total else 0.0
        self._stats = stats
        shared_app_state.ranging_stats = stats
        self.stats_parsed.emit(stats)
        return stats

    def save_position(
        self,
        session_id: str,
        x: float,
        y: float,
        z: float,
        rms: float,
        timestamp_ms: int,
    ) -> None:
        self._positions.append(
            {
                "session_id": session_id,
                "x": x,
                "y": y,
                "z": z,
                "rms": rms,
                "x_m": x,
                "y_m": y,
                "z_m": z,
                "rms_error_m": rms,
                "timestamp_ms": timestamp_ms,
                "received_at": time.time(),
                "source": "ranging",
                "seq": 0,
                "anchor_mask": 0,
                "anchor_mask_valid": False,
                "payload_size": 0,
                "d1_mm": "",
                "d2_mm": "",
                "d3_mm": "",
                "d4_mm": "",
                "d5_mm": "",
                "d6_mm": "",
                "anchors": [],
            }
        )
