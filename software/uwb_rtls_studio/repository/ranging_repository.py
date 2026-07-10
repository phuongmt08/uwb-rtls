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

from utils.app_state import shared_app_state


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

        sample = {
            "source": "sensor_fusion",
            "seq": int(seq or 0),
            "payload_size": self._payload_size(res),
            "ukf_x_m": self._decode_fixed2(getattr(res, "ukf_x_m", 0)),
            "ukf_y_m": self._decode_fixed2(getattr(res, "ukf_y_m", 0)),
            "ukf_yaw_deg": self._decode_fixed2(getattr(res, "ukf_yaw_deg", 0)),
            "tril_x_m": self._decode_fixed2(getattr(res, "tril_x_m", 0)),
            "tril_y_m": self._decode_fixed2(getattr(res, "tril_y_m", 0)),
            "yaw_deg": self._decode_fixed2(getattr(res, "yaw_deg", 0)),
            "anchor_mask": int(getattr(res, "anchor_mask", 0)),
            "anchor_mask_valid": True,
            "ranging_error_count": int(getattr(res, "ranging_error_count", 0)),
            "timestamp_ms": int(getattr(res, "timestamp_ms", 0)),
            "zone_id": int(getattr(res, "zone_id", 0)),
            "anchors": anchors,
            "packet_timestamp_ms": int(packet_timestamp_ms or 0),
            "received_at": time.time(),
        }
        self._fusion_samples.append(sample)
        self.sensor_fusion_parsed.emit(sample)
        return sample

    @staticmethod
    def _build_anchor_mask(anchors: list[dict]) -> int:
        """Build the protocol mask (bit 0 = Anchor 1, bit 1 = Anchor 2)."""
        mask = 0
        for anchor in anchors:
            anchor_id = int(anchor.get("anchor_id", 0) or 0)
            if 1 <= anchor_id <= 32:
                mask |= 1 << (anchor_id - 1)
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
        anchors = []
        for a in getattr(resp, "anchors", []):
            def coord_or_none(name: str):
                return float(getattr(a, name))

            anchors.append({
                "anchor_id": int(getattr(a, "anchor_id", 0)),
                "x_m": coord_or_none("x_m"),
                "y_m": coord_or_none("y_m"),
                "z_m": coord_or_none("z_m"),
            })
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
                "anchors": [],
            }
        )
