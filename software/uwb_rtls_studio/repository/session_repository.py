"""
File-based session repository for UWB RTLS Studio.

Sessions are stored as folders under data/sessions/. A lightweight index.json
keeps searchable metadata so the app can list/filter sessions without any SQL
dependency.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import shutil
from datetime import datetime

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
INDEX_FILE = os.path.join(SESSIONS_DIR, "index.json")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_BROWSER_DIR = os.path.join(APP_DIR, "session_browser")
BROWSER_RANGING_DIR = os.path.join(SESSION_BROWSER_DIR, "ranging")
BROWSER_LOG_DIR = os.path.join(SESSION_BROWSER_DIR, "log")


class SessionRepository:
    def __init__(self):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        os.makedirs(BROWSER_RANGING_DIR, exist_ok=True)
        os.makedirs(BROWSER_LOG_DIR, exist_ok=True)
        self._ensure_index_file()
        self._rebuild_index_if_needed()

    def get_sessions_dir(self) -> str:
        return SESSIONS_DIR

    def get_session_folder(self, session_id: str) -> str:
        return os.path.join(SESSIONS_DIR, session_id)

    def get_browser_root(self) -> str:
        return SESSION_BROWSER_DIR

    def get_browser_ranging_folder(self, session_id: str = "") -> str:
        return os.path.join(BROWSER_RANGING_DIR, session_id) if session_id else BROWSER_RANGING_DIR

    def get_browser_log_folder(self, session_id: str = "") -> str:
        return os.path.join(BROWSER_LOG_DIR, session_id) if session_id else BROWSER_LOG_DIR

    def get_session_messages_folder(self, session_id: str) -> str:
        return os.path.join(self.get_session_folder(session_id), "messages")

    def session_file_exists(self, session_id: str, filename: str) -> bool:
        if not session_id or not filename:
            return False
        return os.path.exists(os.path.join(self.get_session_folder(session_id), filename))

    def count_session_files(self, session_id: str) -> int:
        if not session_id:
            return 0
        session_folder = self.get_session_folder(session_id)
        if not os.path.isdir(session_folder):
            return 0

        total = 0
        for _, _, filenames in os.walk(session_folder):
            total += len(filenames)
        return total

    def count_ranging_runs(self, session_id: str) -> int:
        if not session_id:
            return 0
        session_folder = self.get_session_folder(session_id)
        if not os.path.isdir(session_folder):
            return 0

        runs = self._read_runs(session_id)
        if runs:
            return sum(1 for run in runs if run.get("stream_type") == "ranging")

        count = 0
        if os.path.exists(os.path.join(session_folder, "positions.csv")):
            count += 1

        ranging_dir = os.path.join(session_folder, "ranging")
        if os.path.isdir(ranging_dir):
            for name in os.listdir(ranging_dir):
                lowered = name.lower()
                if lowered.endswith(".csv") and lowered.startswith("ranging"):
                    count += 1

        for name in os.listdir(session_folder):
            lowered = name.lower()
            if lowered.endswith(".csv") and lowered.startswith("ranging"):
                count += 1
        return count

    def count_log_runs(self, session_id: str) -> int:
        if not session_id:
            return 0
        runs = self._read_runs(session_id)
        if runs:
            return sum(1 for run in runs if run.get("stream_type") == "log")

        log_dir = os.path.join(self.get_session_folder(session_id), "log")
        if not os.path.isdir(log_dir):
            return 1 if os.path.exists(os.path.join(self.get_session_folder(session_id), "logs.csv")) else 0
        return sum(1 for name in os.listdir(log_dir) if name.lower().startswith("log_run_") and name.lower().endswith(".csv"))

    def save_session(
        self,
        session_meta: dict,
        device_config: dict | None = None,
        anchors: list | None = None,
        positions: list | None = None,
        fusion_positions: list | None = None,
        logs: list | None = None,
    ) -> str:
        session_id = session_meta.get("session_id")
        if not session_id:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_type = session_meta.get("session_type", "SESSION").lower()
            session_id = f"SES_{timestamp_str}_{session_type}"
            session_meta["session_id"] = session_id

        session_folder = self.get_session_folder(session_id)
        os.makedirs(session_folder, exist_ok=True)

        normalized_meta = self._normalize_session_meta(session_meta)
        self._write_json(os.path.join(session_folder, "session_meta.json"), normalized_meta)

        if device_config:
            self._write_json(os.path.join(session_folder, "config_snapshot.json"), device_config)

        if anchors:
            self._write_json(os.path.join(session_folder, "anchors.json"), anchors)

        if positions:
            positions_path = os.path.join(session_folder, "positions.csv")
            self._write_positions_csv(positions_path, positions)
            self._mirror_browser_file(positions_path, "ranging", session_id)

        if fusion_positions:
            fusion_path = os.path.join(session_folder, "sensor_fusion_positions.csv")
            self._write_positions_csv(fusion_path, fusion_positions)
            self._mirror_browser_file(fusion_path, "ranging", session_id)

        if logs:
            logs_csv_path = os.path.join(session_folder, "logs.csv")
            logs_txt_path = os.path.join(session_folder, "logs.txt")
            self._write_logs_csv(logs_csv_path, logs)
            self._write_logs_txt(logs_txt_path, logs)
            self._mirror_browser_file(logs_csv_path, "log", session_id)
            self._mirror_browser_file(logs_txt_path, "log", session_id)

        self._upsert_index_entry(self._build_index_entry(normalized_meta, session_folder))
        log.info("Session %s saved successfully to file repository.", session_id)
        return session_id

    def ensure_session(self, session_meta: dict) -> str:
        """Create/update only session metadata and index."""
        session_id = session_meta.get("session_id")
        if not session_id:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"SES_{timestamp_str}_session"
            session_meta["session_id"] = session_id

        session_folder = self.get_session_folder(session_id)
        os.makedirs(session_folder, exist_ok=True)
        normalized_meta = self._normalize_session_meta(session_meta)
        self._write_json(os.path.join(session_folder, "session_meta.json"), normalized_meta)
        self._upsert_index_entry(self._build_index_entry(normalized_meta, session_folder))
        return session_id

    def save_ranging_run(
        self,
        session_id: str,
        run_index: int,
        positions: list[dict] | None = None,
        fusion_positions: list[dict] | None = None,
        meta: dict | None = None,
    ) -> list[str]:
        session_folder = self.get_session_folder(session_id)
        run_dir = os.path.join(session_folder, "ranging")
        os.makedirs(run_dir, exist_ok=True)

        files: list[str] = []
        if positions is not None:
            path = os.path.join(run_dir, f"ranging_run_{run_index:03d}.csv")
            self._write_positions_csv(path, positions)
            self._mirror_browser_file(path, "ranging", session_id)
            files.append(os.path.relpath(path, session_folder))

        if fusion_positions:
            path = os.path.join(run_dir, f"sensor_fusion_run_{run_index:03d}.csv")
            self._write_positions_csv(path, fusion_positions)
            self._mirror_browser_file(path, "ranging", session_id)
            files.append(os.path.relpath(path, session_folder))

        run_meta = dict(meta or {})
        run_meta.update({
            "stream_type": "ranging",
            "index": int(run_index),
            "files": files,
            "sample_count": len(positions or []),
        })
        self.append_or_update_run_meta(session_id, run_meta)
        return files

    def save_log_run(
        self,
        session_id: str,
        run_index: int,
        logs: list[dict] | None = None,
        meta: dict | None = None,
    ) -> list[str]:
        session_folder = self.get_session_folder(session_id)
        run_dir = os.path.join(session_folder, "log")
        os.makedirs(run_dir, exist_ok=True)

        logs = list(logs or [])
        csv_path = os.path.join(run_dir, f"log_run_{run_index:03d}.csv")
        txt_path = os.path.join(run_dir, f"log_run_{run_index:03d}.txt")
        self._write_logs_csv(csv_path, logs)
        self._write_logs_txt(txt_path, logs)
        self._mirror_browser_file(csv_path, "log", session_id)
        self._mirror_browser_file(txt_path, "log", session_id)
        files = [
            os.path.relpath(csv_path, session_folder),
            os.path.relpath(txt_path, session_folder),
        ]

        run_meta = dict(meta or {})
        run_meta.update({
            "stream_type": "log",
            "index": int(run_index),
            "files": files,
            "sample_count": len(logs),
        })
        self.append_or_update_run_meta(session_id, run_meta)
        return files

    def append_or_update_run_meta(self, session_id: str, run_meta: dict) -> None:
        runs = self._read_runs(session_id)
        key = (run_meta.get("stream_type"), int(run_meta.get("index", 0) or 0))
        updated = False
        for idx, existing in enumerate(runs):
            existing_key = (existing.get("stream_type"), int(existing.get("index", 0) or 0))
            if existing_key == key:
                merged = existing.copy()
                merged.update(run_meta)
                runs[idx] = merged
                updated = True
                break
        if not updated:
            runs.append(run_meta.copy())
        runs.sort(key=lambda item: (item.get("stream_type", ""), int(item.get("index", 0) or 0)))
        self._write_runs(session_id, runs)

    def list_session_runs(self, session_id: str, stream_type: str | None = None) -> list[dict]:
        runs = self._read_runs(session_id)
        if stream_type:
            runs = [run for run in runs if run.get("stream_type") == stream_type]
        return [run.copy() for run in runs]

    def export_session_to(self, session_id: str, destination_dir: str) -> str:
        if not session_id or not destination_dir:
            return ""

        source = self.get_session_folder(session_id)
        if not os.path.isdir(source):
            return ""

        os.makedirs(destination_dir, exist_ok=True)
        destination = os.path.join(destination_dir, session_id)
        os.makedirs(destination, exist_ok=True)

        metadata_files = ("session_meta.json", "config_snapshot.json", "anchors.json")
        for filename in metadata_files:
            self._copy_file_if_exists(os.path.join(source, filename), destination)

        ranging_destination = os.path.join(destination, "ranging")
        log_destination = os.path.join(destination, "log")
        messages_destination = os.path.join(destination, "messages")
        os.makedirs(ranging_destination, exist_ok=True)
        os.makedirs(log_destination, exist_ok=True)
        os.makedirs(messages_destination, exist_ok=True)

        self._copy_folder_files(self.get_browser_ranging_folder(session_id), ranging_destination)
        self._copy_folder_files(self.get_browser_log_folder(session_id), log_destination)

        for filename in ("positions.csv", "sensor_fusion_positions.csv"):
            self._copy_file_if_exists(os.path.join(source, filename), ranging_destination)

        for filename in ("logs.csv", "logs.txt"):
            self._copy_file_if_exists(os.path.join(source, filename), log_destination)

        self._copy_folder_files(os.path.join(source, "ranging"), ranging_destination)
        self._copy_folder_files(os.path.join(source, "log"), log_destination)
        self._copy_folder_files(os.path.join(source, "logs"), log_destination)
        self._copy_folder_files(os.path.join(source, "messages"), messages_destination)
        return destination

    def _copy_file_if_exists(self, source_path: str, destination_dir: str) -> bool:
        if not os.path.isfile(source_path):
            return False

        os.makedirs(destination_dir, exist_ok=True)
        shutil.copy2(source_path, os.path.join(destination_dir, os.path.basename(source_path)))
        return True

    def _copy_folder_files(self, source_dir: str, destination_dir: str) -> int:
        if not os.path.isdir(source_dir):
            return 0

        copied = 0
        for root, _, filenames in os.walk(source_dir):
            relative_dir = os.path.relpath(root, source_dir)
            target_dir = destination_dir if relative_dir == "." else os.path.join(destination_dir, relative_dir)
            os.makedirs(target_dir, exist_ok=True)
            for filename in filenames:
                shutil.copy2(os.path.join(root, filename), os.path.join(target_dir, filename))
                copied += 1
        return copied

    def _mirror_browser_file(self, source_path: str, category: str, session_id: str) -> None:
        if not os.path.exists(source_path):
            return

        if category == "ranging":
            target_dir = self.get_browser_ranging_folder(session_id)
        else:
            target_dir = self.get_browser_log_folder(session_id)

        os.makedirs(target_dir, exist_ok=True)
        shutil.copy2(source_path, os.path.join(target_dir, os.path.basename(source_path)))

    def list_sessions(self, filters: dict | None = None) -> list[dict]:
        sessions = self._read_index()
        if filters:
            sessions = [item for item in sessions if self._matches_filters(item, filters)]
        sessions.sort(key=lambda item: item.get("start_time_iso", ""), reverse=True)
        return [item.copy() for item in sessions]

    def load_session_details(self, session_id: str, detail_type: str) -> list[dict]:
        session_folder = self.get_session_folder(session_id)
        if not os.path.exists(session_folder):
            log.warning("Session folder %s does not exist.", session_folder)
            return []

        if detail_type == "ranging":
            rows = []
            for run in self.list_session_runs(session_id, "ranging"):
                for rel_path in run.get("files", []):
                    if os.path.basename(rel_path).lower().startswith("ranging_run_"):
                        rows.extend(self._read_positions_csv(os.path.join(session_folder, rel_path)))
            if rows:
                return rows
            return self._read_positions_csv(os.path.join(session_folder, "positions.csv"))
        if detail_type == "fusion":
            rows = []
            for run in self.list_session_runs(session_id, "ranging"):
                for rel_path in run.get("files", []):
                    if os.path.basename(rel_path).lower().startswith("sensor_fusion_run_"):
                        rows.extend(self._read_positions_csv(os.path.join(session_folder, rel_path)))
            if rows:
                return rows
            return self._read_positions_csv(os.path.join(session_folder, "sensor_fusion_positions.csv"))
        if detail_type == "logs":
            rows = []
            for run in self.list_session_runs(session_id, "log"):
                for rel_path in run.get("files", []):
                    if rel_path.lower().endswith(".csv"):
                        rows.extend(self._read_logs_csv(os.path.join(session_folder, rel_path)))
            if rows:
                return rows
            csv_path = os.path.join(session_folder, "logs.csv")
            if os.path.exists(csv_path):
                return self._read_logs_csv(csv_path)
            return self._read_logs_txt(os.path.join(session_folder, "logs.txt"))
        return []

    def get_session_meta(self, session_id: str) -> dict:
        meta_path = os.path.join(self.get_session_folder(session_id), "session_meta.json")
        if os.path.exists(meta_path):
            return self._read_json(meta_path, default={})

        for item in self._read_index():
            if item.get("session_id") == session_id:
                return item.copy()
        return {}

    def delete_session(self, session_id: str) -> bool:
        session_folder = self.get_session_folder(session_id)
        index_entries = [item for item in self._read_index() if item.get("session_id") != session_id]
        self._write_index(index_entries)

        if os.path.exists(session_folder):
            try:
                shutil.rmtree(session_folder)
            except Exception as exc:
                log.error("Error removing session folder %s: %s", session_folder, exc)
                return False
        for browser_folder in (
            self.get_browser_ranging_folder(session_id),
            self.get_browser_log_folder(session_id),
        ):
            if os.path.isdir(browser_folder):
                try:
                    shutil.rmtree(browser_folder)
                except Exception as exc:
                    log.warning("Error removing browser folder %s: %s", browser_folder, exc)
        return True

    def _ensure_index_file(self) -> None:
        if not os.path.exists(INDEX_FILE):
            self._write_index([])

    def _rebuild_index_if_needed(self) -> None:
        if self._read_index():
            return

        rebuilt: list[dict] = []
        for name in os.listdir(SESSIONS_DIR):
            session_folder = os.path.join(SESSIONS_DIR, name)
            if not os.path.isdir(session_folder):
                continue

            meta_path = os.path.join(session_folder, "session_meta.json")
            if not os.path.exists(meta_path):
                continue

            meta = self._read_json(meta_path, default={})
            if not isinstance(meta, dict) or not meta.get("session_id"):
                continue
            rebuilt.append(self._build_index_entry(self._normalize_session_meta(meta), session_folder))

        if rebuilt:
            rebuilt.sort(key=lambda item: item.get("start_time_iso", ""), reverse=True)
            self._write_index(rebuilt)

    def _read_index(self) -> list[dict]:
        data = self._read_json(INDEX_FILE, default=[])
        return data if isinstance(data, list) else []

    def _write_index(self, sessions: list[dict]) -> None:
        self._write_json(INDEX_FILE, sessions)

    def _runs_path(self, session_id: str) -> str:
        return os.path.join(self.get_session_folder(session_id), "runs.json")

    def _read_runs(self, session_id: str) -> list[dict]:
        data = self._read_json(self._runs_path(session_id), default=[])
        return data if isinstance(data, list) else []

    def _write_runs(self, session_id: str, runs: list[dict]) -> None:
        os.makedirs(self.get_session_folder(session_id), exist_ok=True)
        self._write_json(self._runs_path(session_id), runs)

    def _upsert_index_entry(self, entry: dict) -> None:
        items = [item for item in self._read_index() if item.get("session_id") != entry.get("session_id")]
        items.append(entry)
        items.sort(key=lambda item: item.get("start_time_iso", ""), reverse=True)
        self._write_index(items)

    def _build_index_entry(self, session_meta: dict, session_folder: str) -> dict:
        device_info = session_meta.get("device_info", {})
        statistics = session_meta.get("statistics", {})
        return {
            "session_id": session_meta.get("session_id", ""),
            "session_type": session_meta.get("session_type", "SESSION"),
            "start_time_iso": session_meta.get("start_time_iso", ""),
            "end_time_iso": session_meta.get("end_time_iso", ""),
            "duration_sec": float(session_meta.get("duration_sec", 0.0) or 0.0),
            "end_reason": session_meta.get("end_reason", ""),
            "connected_device_mac": device_info.get("mac_address", ""),
            "connected_device_name": device_info.get("device_name", ""),
            "connected_device_role": device_info.get("device_role", ""),
            "connected_device_fw": device_info.get("fw_version", ""),
            "connected_device_serial": device_info.get("serial_number", ""),
            "dongle_port": session_meta.get("dongle_info", {}).get("port", ""),
            "total_packets_rx": int(statistics.get("total_packets_rx", 0) or 0),
            "success_packets_rx": int(statistics.get("success_packets_rx", 0) or 0),
            "avg_rms_error_m": float(statistics.get("avg_rms_error_m", 0.0) or 0.0),
            "session_folder": session_folder,
        }

    def _normalize_session_meta(self, session_meta: dict) -> dict:
        normalized = session_meta.copy()
        normalized["duration_sec"] = float(normalized.get("duration_sec", 0.0) or 0.0)
        normalized.setdefault("device_info", {})
        normalized.setdefault("statistics", {})
        normalized.setdefault("dongle_info", {})
        return normalized

    def _matches_filters(self, item: dict, filters: dict) -> bool:
        date_from = filters.get("date_from")
        if date_from and item.get("start_time_iso", "") < date_from:
            return False

        date_to = filters.get("date_to")
        if date_to and item.get("start_time_iso", "") > date_to:
            return False

        session_type = filters.get("session_type")
        if session_type and session_type != "ALL" and item.get("session_type", "").upper() != session_type.upper():
            return False

        device_mac = filters.get("device_mac")
        if device_mac and device_mac != "ALL" and item.get("connected_device_mac", "") != device_mac:
            return False

        return True
    @staticmethod
    def _display_time_from_position(pos: dict) -> str:
        explicit = str(pos.get("time", pos.get("timestamp", "")) or "").strip()
        if explicit:
            return explicit

        received_at = float(pos.get("received_at", 0.0) or 0.0)
        if received_at > 0:
            try:
                return datetime.fromtimestamp(received_at).strftime("%d/%m/%Y %H:%M:%S.%f")[:-3]
            except Exception:
                return ""
        return ""

    def _write_positions_csv(self, path: str, positions: list[dict]) -> None:
        fieldnames = [
            "time",
            "timestamp_ms",
            "packet_timestamp_ms",
            "seq",
            "source",
            "x_m",
            "y_m",
            "z_m",
            "rms_error_m",
            "anchor_mask",
            "d1_mm",
            "d2_mm",
            "d3_mm",
            "d4_mm",
            "ukf_x_m",
            "ukf_y_m",
            "ukf_yaw_deg",
            "tril_x_m",
            "tril_y_m",
            "yaw_deg",
            "ranging_error_count",
        ]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(fieldnames)
            for pos in positions:
                timestamp_ms = int(pos.get("timestamp_ms", 0) or 0)
                writer.writerow([
                    self._display_time_from_position(pos),
                    timestamp_ms,
                    int(pos.get("packet_timestamp_ms", 0) or 0),
                    pos.get("seq", 0),
                    pos.get("source", "ranging"),
                    pos.get("x_m", pos.get("x", 0.0)),
                    pos.get("y_m", pos.get("y", 0.0)),
                    pos.get("z_m", pos.get("z", 0.0)),
                    pos.get("rms_error_m", pos.get("rms", 0.0)),
                    pos.get("anchor_mask", 0),
                    pos.get("d1_mm", ""),
                    pos.get("d2_mm", ""),
                    pos.get("d3_mm", ""),
                    pos.get("d4_mm", ""),
                    pos.get("ukf_x_m", ""),
                    pos.get("ukf_y_m", ""),
                    pos.get("ukf_yaw_deg", ""),
                    pos.get("tril_x_m", ""),
                    pos.get("tril_y_m", ""),
                    pos.get("yaw_deg", ""),
                    pos.get("ranging_error_count", ""),
                ])

    def _read_positions_csv(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        results: list[dict] = []
        with open(path, "r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    results.append({
                        "time": row.get("time", ""),
                        "timestamp_ms": int(row["timestamp_ms"]),
                        "packet_timestamp_ms": int(row.get("packet_timestamp_ms", 0) or 0),
                        "seq": int(row.get("seq", 0) or 0),
                        "source": row.get("source", "ranging"),
                        "x_m": float(row["x_m"]),
                        "y_m": float(row["y_m"]),
                        "z_m": float(row["z_m"]),
                        "rms_error_m": float(row["rms_error_m"]),
                        "anchor_mask": int(row.get("anchor_mask", 0) or 0),
                        "d1_mm": row.get("d1_mm", ""),
                        "d2_mm": row.get("d2_mm", ""),
                        "d3_mm": row.get("d3_mm", ""),
                        "d4_mm": row.get("d4_mm", ""),
                        "ukf_x_m": row.get("ukf_x_m", ""),
                        "ukf_y_m": row.get("ukf_y_m", ""),
                        "ukf_yaw_deg": row.get("ukf_yaw_deg", ""),
                        "tril_x_m": row.get("tril_x_m", ""),
                        "tril_y_m": row.get("tril_y_m", ""),
                        "yaw_deg": row.get("yaw_deg", ""),
                        "ranging_error_count": row.get("ranging_error_count", ""),
                    })
                except (TypeError, ValueError, KeyError):
                    continue
        return results

    def _write_logs_csv(self, path: str, logs: list[dict]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "level", "source", "message"])
            for entry in logs:
                writer.writerow([
                    entry.get("timestamp", ""),
                    entry.get("level", "INFO"),
                    entry.get("source", ""),
                    entry.get("message", ""),
                ])

    def _read_logs_csv(self, path: str) -> list[dict]:
        results: list[dict] = []
        with open(path, "r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                results.append({
                    "timestamp": row.get("timestamp", ""),
                    "level": row.get("level", "INFO"),
                    "source": row.get("source", ""),
                    "message": row.get("message", ""),
                })
        return results

    def _write_logs_txt(self, path: str, logs: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            for entry in logs:
                handle.write(
                    "[{timestamp}] {level:<8} {source:<18} {message}\n".format(
                        timestamp=entry.get("timestamp", ""),
                        level=entry.get("level", "INFO"),
                        source=entry.get("source", ""),
                        message=entry.get("message", ""),
                    )
                )

    def _read_logs_txt(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        results: list[dict] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                message = line.rstrip("\n")
                if not message:
                    continue
                results.append({
                    "timestamp": "",
                    "level": "INFO",
                    "source": "TXT",
                    "message": message,
                })
        return results

    def _write_json(self, path: str, data) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def _read_json(self, path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            log.warning("Failed to read json file %s: %s", path, exc)
            return default
