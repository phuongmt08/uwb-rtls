"""
Repository for ranging-related protobuf packets.

It converts protobuf messages into plain dictionaries that are easy for models
and UI code to consume. It also keeps a small in-memory history for sessions and
debugging.
"""
from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import QObject, pyqtSignal


class RangingRepository(QObject):
    position_parsed = pyqtSignal(dict)
    sensor_fusion_parsed = pyqtSignal(dict)
    ranging_data_updated = pyqtSignal(dict)
    anchor_distances_parsed = pyqtSignal(list)
    anchor_layout_parsed = pyqtSignal(list)
    stats_parsed = pyqtSignal(dict)

    def __init__(self, max_positions: int = 100000, parent=None):
        super().__init__(parent)
        self._positions = deque(maxlen=max_positions)
        self._fusion_samples = deque(maxlen=max_positions)
        self._anchor_layout: list[dict] = []
        self._stats: dict = {}

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
        if param_name == "anchor_layout_resp":
            self.parse_anchor_layout(pkt.anchor_layout_resp)
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
            "d1_mm": distances_by_anchor.get(1, ""),
            "d2_mm": distances_by_anchor.get(2, ""),
            "d3_mm": distances_by_anchor.get(3, ""),
            "d4_mm": distances_by_anchor.get(4, ""),
            "anchors": anchors,
        }
        self._positions.append(sample)
        self.position_parsed.emit(sample)
        self.ranging_data_updated.emit(sample)
        if anchors:
            self.anchor_distances_parsed.emit(anchors)
        return sample

    def parse_sensor_fusion_result(self, res, seq: int = 0, packet_timestamp_ms: int = 0) -> dict:
        sample = {
            "source": "sensor_fusion",
            "seq": int(seq or 0),
            "ukf_x_m": float(getattr(res, "ukf_x_m", 0.0)),
            "ukf_y_m": float(getattr(res, "ukf_y_m", 0.0)),
            "ukf_yaw_deg": float(getattr(res, "ukf_yaw_deg", 0.0)),
            "tril_x_m": float(getattr(res, "tril_x_m", 0.0)),
            "tril_y_m": float(getattr(res, "tril_y_m", 0.0)),
            "yaw_deg": float(getattr(res, "yaw_deg", 0.0)),
            "ranging_error_count": int(getattr(res, "ranging_error_count", 0)),
            "timestamp_ms": int(getattr(res, "timestamp_ms", 0)),
            "packet_timestamp_ms": int(packet_timestamp_ms or 0),
            "received_at": time.time(),
        }
        self._fusion_samples.append(sample)
        self.sensor_fusion_parsed.emit(sample)
        return sample

    @staticmethod
    def _build_anchor_mask(anchors: list[dict]) -> int:
        mask = 0
        for anchor in anchors:
            anchor_id = int(anchor.get("anchor_id", 0) or 0)
            if 0 < anchor_id < 32:
                mask |= 1 << anchor_id
        return mask

    @staticmethod
    def _distances_by_anchor_id(anchors: list[dict]) -> dict[int, int]:
        distances: dict[int, int] = {}
        for anchor in anchors:
            anchor_id = int(anchor.get("anchor_id", 0) or 0)
            if anchor_id:
                distances[anchor_id] = int(anchor.get("distance_mm", 0) or 0)
        return distances

    def parse_anchor_layout(self, resp) -> list[dict]:
        anchors = [
            {
                "anchor_id": int(a.anchor_id),
                "x_m": float(a.x_m),
                "y_m": float(a.y_m),
                "z_m": float(a.z_m),
            }
            for a in getattr(resp, "anchors", [])
        ]
        self._anchor_layout = anchors
        self.anchor_layout_parsed.emit(anchors)
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
                "d1_mm": "",
                "d2_mm": "",
                "d3_mm": "",
                "d4_mm": "",
                "anchors": [],
            }
        )
