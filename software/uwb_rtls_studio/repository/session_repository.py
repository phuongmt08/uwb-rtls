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
import re
import shutil
from datetime import datetime

log = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
SHARED_DATA_DIR = os.path.abspath(os.path.join(APP_DIR, "..", "data"))
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
INDEX_FILE = os.path.join(SESSIONS_DIR, "index.json")
SESSION_STORE_DIR = os.path.join(DATA_DIR, "session_store")
HOT_STORE_DIR = os.path.join(SESSION_STORE_DIR, "hot")
ARCHIVE_STORE_DIR = os.path.join(SESSION_STORE_DIR, "archive")
HOT_STORAGE_DAYS = int(os.environ.get("UWB_RTLS_HOT_STORAGE_DAYS", "7"))
HOT_RUNS_PER_DAY = int(os.environ.get("UWB_RTLS_HOT_RUNS_PER_DAY", "20"))
SESSION_BROWSER_DIR = os.path.join(APP_DIR, "session_browser")
BROWSER_RANGING_DIR = os.path.join(SESSION_BROWSER_DIR, "ranging")
BROWSER_LOG_DIR = os.path.join(SESSION_BROWSER_DIR, "log")


class SessionRepository:
    def __init__(self):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        os.makedirs(HOT_STORE_DIR, exist_ok=True)
        os.makedirs(ARCHIVE_STORE_DIR, exist_ok=True)
        os.makedirs(BROWSER_RANGING_DIR, exist_ok=True)
        os.makedirs(BROWSER_LOG_DIR, exist_ok=True)
        self._ensure_index_file()
        self._rebuild_index_if_needed()
        self._apply_hot_retention()

    def get_sessions_dir(self) -> str:
        return SESSIONS_DIR

    def get_session_folder(self, session_id: str) -> str:
        return os.path.join(SESSIONS_DIR, session_id)

    def get_session_storage_folder(self, session_id: str) -> str:
        storage = self._read_storage_meta(session_id)
        run_dir = self._run_dir_from_storage(storage) if storage else ""
        return run_dir if run_dir and os.path.isdir(run_dir) else self.get_session_folder(session_id)
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
        return os.path.exists(self._resolve_session_file(session_id, filename))

    def count_session_files(self, session_id: str) -> int:
        if not session_id:
            return 0
        paths = set()
        session_folder = self.get_session_folder(session_id)
        if os.path.isdir(session_folder):
            for root, _, filenames in os.walk(session_folder):
                for filename in filenames:
                    paths.add(os.path.abspath(os.path.join(root, filename)))
        storage = self._read_storage_meta(session_id)
        run_dir = self._run_dir_from_storage(storage) if storage else ""
        if run_dir and os.path.isdir(run_dir):
            for root, _, filenames in os.walk(run_dir):
                for filename in filenames:
                    paths.add(os.path.abspath(os.path.join(root, filename)))
        return len(paths)

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
            return 1 if (os.path.exists(os.path.join(self.get_session_folder(session_id), "logs.csv")) or os.path.exists(os.path.join(self.get_session_folder(session_id), "logs.txt"))) else 0
        return sum(1 for name in os.listdir(log_dir) if name.lower().startswith("log_run_") and name.lower().endswith((".csv", ".txt")))

    def _storage_meta_path(self, session_id: str) -> str:
        return os.path.join(self.get_session_folder(session_id), "storage_meta.json")

    def _session_date_from_meta(self, session_meta: dict | None = None) -> str:
        raw_value = str((session_meta or {}).get("start_time_iso", "") or "").strip()
        if raw_value:
            try:
                return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except Exception:
                pass
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _parse_day_folder(name: str):
        try:
            return datetime.strptime(name, "%Y-%m-%d").date()
        except Exception:
            return None

    @staticmethod
    def _parse_run_index(name: str) -> int:
        if not name.startswith("run_"):
            return 0
        try:
            return int(name.split("_", 2)[1])
        except Exception:
            return 0

    def _day_runs_dir(self, root_dir: str, date_key: str) -> str:
        return os.path.join(root_dir, date_key, "runs")

    def _run_dir_from_storage(self, storage: dict, prefer_existing: bool = True) -> str:
        date_key = storage.get("date", "")
        run_label = storage.get("run_label", "")
        if not date_key or not run_label:
            return ""
        candidates = [
            os.path.join(HOT_STORE_DIR, date_key, "runs", run_label),
            os.path.join(ARCHIVE_STORE_DIR, date_key, "runs", run_label),
        ]
        if prefer_existing:
            for candidate in candidates:
                if os.path.isdir(candidate):
                    return candidate
        tier = storage.get("tier", "hot")
        root = ARCHIVE_STORE_DIR if tier == "archive" else HOT_STORE_DIR
        return os.path.join(root, date_key, "runs", run_label)

    def _list_day_run_dirs(self, root_dir: str, date_key: str) -> list[tuple[int, str]]:
        runs_dir = self._day_runs_dir(root_dir, date_key)
        if not os.path.isdir(runs_dir):
            return []
        runs = []
        for name in os.listdir(runs_dir):
            path = os.path.join(runs_dir, name)
            if not os.path.isdir(path):
                continue
            index = self._parse_run_index(name)
            if index <= 0:
                continue
            runs.append((index, path))
        runs.sort(key=lambda item: item[0])
        return runs

    def _next_daily_run_index(self, date_key: str) -> int:
        max_index = 0
        for root_dir in (HOT_STORE_DIR, ARCHIVE_STORE_DIR):
            for index, _ in self._list_day_run_dirs(root_dir, date_key):
                max_index = max(max_index, index)
        return max_index + 1

    def _read_storage_meta(self, session_id: str) -> dict:
        data = self._read_json(self._storage_meta_path(session_id), default={})
        return data if isinstance(data, dict) else {}

    def _write_storage_meta(self, session_id: str, storage: dict) -> None:
        os.makedirs(self.get_session_folder(session_id), exist_ok=True)
        self._write_json(self._storage_meta_path(session_id), storage)

    def _find_storage_for_session(self, session_id: str) -> dict:
        for root_dir, tier in ((HOT_STORE_DIR, "hot"), (ARCHIVE_STORE_DIR, "archive")):
            if not os.path.isdir(root_dir):
                continue
            for date_key in os.listdir(root_dir):
                for run_index, run_dir in self._list_day_run_dirs(root_dir, date_key):
                    meta = self._read_json(os.path.join(run_dir, "meta.json"), default={})
                    if isinstance(meta, dict) and meta.get("session_id") == session_id:
                        return {
                            "session_id": session_id,
                            "date": date_key,
                            "tier": tier,
                            "run_index": run_index,
                            "run_label": os.path.basename(run_dir),
                        }
        return {}

    def _ensure_session_storage(self, session_id: str, session_meta: dict | None = None) -> dict:
        storage = self._read_storage_meta(session_id)
        if storage and self._run_dir_from_storage(storage):
            self._write_storage_run_meta(session_id, session_meta=session_meta)
            return storage

        found = self._find_storage_for_session(session_id)
        if found:
            self._write_storage_meta(session_id, found)
            self._write_storage_run_meta(session_id, session_meta=session_meta)
            return found

        date_key = self._session_date_from_meta(session_meta)
        run_index = self._next_daily_run_index(date_key)
        storage = {
            "session_id": session_id,
            "date": date_key,
            "tier": "hot",
            "run_index": run_index,
            "run_label": f"run_{run_index:03d}",
        }
        run_dir = self._run_dir_from_storage(storage, prefer_existing=False)
        os.makedirs(run_dir, exist_ok=True)
        self._write_storage_meta(session_id, storage)
        self._write_storage_run_meta(session_id, session_meta=session_meta)
        self._apply_hot_retention()
        return storage

    def _write_storage_run_meta(self, session_id: str, session_meta: dict | None = None) -> None:
        storage = self._read_storage_meta(session_id)
        if not storage:
            return
        run_dir = self._run_dir_from_storage(storage)
        if not run_dir:
            return
        actual_tier = "archive" if os.path.abspath(run_dir).startswith(os.path.abspath(ARCHIVE_STORE_DIR)) else "hot"
        if storage.get("tier") != actual_tier:
            storage["tier"] = actual_tier
            self._write_storage_meta(session_id, storage)
        os.makedirs(run_dir, exist_ok=True)
        existing = self._read_json(os.path.join(run_dir, "meta.json"), default={})
        data = existing if isinstance(existing, dict) else {}
        data.update({
            "session_id": session_id,
            "date": storage.get("date", ""),
            "run_index": int(storage.get("run_index", 0) or 0),
            "run_label": storage.get("run_label", ""),
            "storage_tier": "archive" if run_dir.startswith(ARCHIVE_STORE_DIR) else "hot",
            "updated_at_iso": datetime.now().isoformat(),
        })
        if session_meta:
            data["session_meta"] = session_meta
        runs = self._read_runs(session_id)
        if runs:
            data["runs"] = runs
        self._write_json(os.path.join(run_dir, "meta.json"), data)
        self._upsert_day_manifest(storage, session_meta=session_meta)

    def _upsert_day_manifest(self, storage: dict, session_meta: dict | None = None) -> None:
        run_dir = self._run_dir_from_storage(storage)
        if not run_dir:
            return
        date_key = storage.get("date", "")
        day_dir = os.path.dirname(os.path.dirname(run_dir))
        manifest_path = os.path.join(day_dir, "manifest.json")
        manifest = self._read_json(manifest_path, default={})
        if not isinstance(manifest, dict):
            manifest = {}
        runs = manifest.get("runs", [])
        if not isinstance(runs, list):
            runs = []
        session_id = storage.get("session_id", "")
        runs = [item for item in runs if item.get("session_id") != session_id]
        runs.append({
            "session_id": session_id,
            "date": date_key,
            "run_index": int(storage.get("run_index", 0) or 0),
            "run_label": storage.get("run_label", ""),
            "storage_tier": "archive" if run_dir.startswith(ARCHIVE_STORE_DIR) else "hot",
            "run_dir": os.path.relpath(run_dir, day_dir),
            "start_time_iso": (session_meta or {}).get("start_time_iso", ""),
            "updated_at_iso": datetime.now().isoformat(),
        })
        runs.sort(key=lambda item: int(item.get("run_index", 0) or 0), reverse=True)
        manifest.update({"date": date_key, "runs": runs})
        self._write_json(manifest_path, manifest)

    def _apply_hot_retention(self) -> None:
        self._archive_old_hot_days()
        self._archive_overflow_hot_runs()

    def _archive_old_hot_days(self) -> None:
        if HOT_STORAGE_DAYS <= 0 or not os.path.isdir(HOT_STORE_DIR):
            return
        today = datetime.now().date()
        for date_key in os.listdir(HOT_STORE_DIR):
            day = self._parse_day_folder(date_key)
            if day is None:
                continue
            if (today - day).days < HOT_STORAGE_DAYS:
                continue
            for _, run_dir in self._list_day_run_dirs(HOT_STORE_DIR, date_key):
                self._archive_run_dir(date_key, run_dir)

    def _archive_overflow_hot_runs(self) -> None:
        if HOT_RUNS_PER_DAY <= 0 or not os.path.isdir(HOT_STORE_DIR):
            return
        for date_key in os.listdir(HOT_STORE_DIR):
            runs = self._list_day_run_dirs(HOT_STORE_DIR, date_key)
            if len(runs) <= HOT_RUNS_PER_DAY:
                continue
            overflow = runs[: max(0, len(runs) - HOT_RUNS_PER_DAY)]
            for _, run_dir in overflow:
                self._archive_run_dir(date_key, run_dir)

    def _archive_run_dir(self, date_key: str, source_dir: str) -> str:
        if not os.path.isdir(source_dir):
            return ""
        archive_runs_dir = self._day_runs_dir(ARCHIVE_STORE_DIR, date_key)
        os.makedirs(archive_runs_dir, exist_ok=True)
        base_name = os.path.basename(source_dir)
        destination = os.path.join(archive_runs_dir, base_name)
        if os.path.exists(destination):
            suffix = datetime.now().strftime("%H%M%S")
            destination = os.path.join(archive_runs_dir, f"{base_name}_archived_{suffix}")
        shutil.move(source_dir, destination)
        meta = self._read_json(os.path.join(destination, "meta.json"), default={})
        session_id = meta.get("session_id", "") if isinstance(meta, dict) else ""
        if session_id:
            hot_manifest_path = os.path.join(HOT_STORE_DIR, date_key, "manifest.json")
            hot_manifest = self._read_json(hot_manifest_path, default={})
            if isinstance(hot_manifest, dict) and isinstance(hot_manifest.get("runs"), list):
                hot_manifest["runs"] = [item for item in hot_manifest["runs"] if item.get("session_id") != session_id]
                self._write_json(hot_manifest_path, hot_manifest)
            storage = self._read_storage_meta(session_id)
            if storage:
                storage["tier"] = "archive"
                storage["run_label"] = os.path.basename(destination)
                self._write_storage_meta(session_id, storage)
                session_meta = meta.get("session_meta", {}) if isinstance(meta, dict) else {}
                if isinstance(session_meta, dict) and session_meta:
                    session_meta["storage"] = storage.copy()
                    session_folder = self.get_session_folder(session_id)
                    self._write_json(os.path.join(session_folder, "session_meta.json"), session_meta)
                    self._upsert_index_entry(self._build_index_entry(session_meta, session_folder))
                self._write_storage_run_meta(session_id, session_meta=session_meta)
        return destination

    def _resolve_session_file(self, session_id: str, rel_path: str) -> str:
        if not rel_path:
            return ""
        if os.path.isabs(rel_path):
            return rel_path if os.path.exists(rel_path) else rel_path
        session_folder = self.get_session_folder(session_id)
        candidate = os.path.normpath(os.path.join(session_folder, rel_path))
        if os.path.exists(candidate):
            return candidate
        filename = os.path.basename(rel_path)
        storage = self._read_storage_meta(session_id)
        for root_dir in (HOT_STORE_DIR, ARCHIVE_STORE_DIR):
            run_label = storage.get("run_label", "")
            date_key = storage.get("date", "")
            if not run_label or not date_key:
                continue
            candidate = os.path.join(root_dir, date_key, "runs", run_label, filename)
            if os.path.exists(candidate):
                return candidate
        return os.path.normpath(os.path.join(session_folder, rel_path))

    def _resolve_run_file(self, session_id: str, run: dict, rel_path: str) -> str:
        if not rel_path:
            return ""
        if os.path.isabs(rel_path):
            return rel_path
        session_path = self._resolve_session_file(session_id, rel_path)
        if os.path.exists(session_path):
            return session_path
        storage = run.get("storage") if isinstance(run.get("storage"), dict) else self._read_storage_meta(session_id)
        filename = os.path.basename(rel_path)
        for root_dir in (HOT_STORE_DIR, ARCHIVE_STORE_DIR):
            date_key = storage.get("date", "")
            run_label = storage.get("run_label", "")
            if not date_key or not run_label:
                continue
            candidate = os.path.join(root_dir, date_key, "runs", run_label, filename)
            if os.path.exists(candidate):
                return candidate
        return session_path
    def _merge_positions(
        self,
        positions: list[dict] | None,
        fusion_positions: list[dict] | None,
    ) -> list[dict]:
        merged = []
        pos_by_seq = {}
        for p in (positions or []):
            p_copy = p.copy()
            seq = p_copy.get("seq")
            if seq is not None and seq > 0:
                pos_by_seq[seq] = p_copy
            else:
                merged.append(p_copy)

        for f in (fusion_positions or []):
            f_copy = f.copy()
            dist_map = {a.get("anchor_id"): a.get("distance_mm", "") for a in f_copy.get("anchors", []) if a.get("anchor_id")}
            weight_map = {a.get("anchor_id"): a.get("weight", "") for a in f_copy.get("anchors", []) if a.get("anchor_id")}
            for k in ["d1_mm", "d2_mm", "d3_mm", "d4_mm"]:
                if f_copy.get(k) is None or f_copy.get(k) == "":
                    try:
                        idx = int(k[1])
                        if idx in dist_map:
                            f_copy[k] = dist_map[idx]
                    except Exception:
                        pass

            for k in ["w1", "w2", "w3", "w4"]:
                try:
                    idx = int(k[1])
                    f_copy[k] = weight_map.get(idx, "")
                except Exception:
                    f_copy[k] = ""

            seq = f_copy.get("seq")
            if seq is not None and seq > 0:
                if seq in pos_by_seq:
                    pos_by_seq[seq].update(f_copy)
                else:
                    pos_by_seq[seq] = f_copy
            else:
                merged.append(f_copy)

        merged.extend(pos_by_seq.values())
        merged.sort(key=lambda x: (x.get("timestamp_ms") or 0, x.get("received_at") or 0.0))
        return merged

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
        storage = self._ensure_session_storage(session_id, normalized_meta)
        normalized_meta["storage"] = storage.copy()
        self._write_json(os.path.join(session_folder, "session_meta.json"), normalized_meta)

        if device_config:
            self._write_json(os.path.join(session_folder, "config_snapshot.json"), device_config)

        if anchors:
            self._write_json(os.path.join(session_folder, "anchors.json"), anchors)

        if positions or fusion_positions:
            self.save_ranging_run(
                session_id,
                1,
                positions=positions,
                fusion_positions=fusion_positions,
                meta={
                    "run_id": "ranging_run_001",
                    "start_time_iso": normalized_meta.get("start_time_iso", ""),
                    "end_time_iso": normalized_meta.get("end_time_iso", ""),
                    "duration_sec": normalized_meta.get("duration_sec", 0.0),
                    "end_reason": normalized_meta.get("end_reason", "USER_END_SESSION"),
                },
            )

        if logs:
            self.save_log_run(
                session_id,
                1,
                logs=logs,
                meta={
                    "run_id": "log_run_001",
                    "start_time_iso": normalized_meta.get("start_time_iso", ""),
                    "end_time_iso": normalized_meta.get("end_time_iso", ""),
                    "duration_sec": normalized_meta.get("duration_sec", 0.0),
                    "end_reason": normalized_meta.get("end_reason", "USER_END_SESSION"),
                },
            )

        self._write_storage_run_meta(session_id, session_meta=normalized_meta)
        self._upsert_index_entry(self._build_index_entry(normalized_meta, session_folder))
        log.info("Session %s saved successfully to session_store.", session_id)
        return session_id

    def ensure_session(self, session_meta: dict) -> str:
        """Create/update session metadata, index, and daily storage mapping."""
        session_id = session_meta.get("session_id")
        if not session_id:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"SES_{timestamp_str}_session"
            session_meta["session_id"] = session_id

        session_folder = self.get_session_folder(session_id)
        os.makedirs(session_folder, exist_ok=True)
        normalized_meta = self._normalize_session_meta(session_meta)
        storage = self._ensure_session_storage(session_id, normalized_meta)
        normalized_meta["storage"] = storage.copy()
        self._write_json(os.path.join(session_folder, "session_meta.json"), normalized_meta)
        self._write_storage_run_meta(session_id, session_meta=normalized_meta)
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
        session_meta = self.get_session_meta(session_id)
        storage = self._ensure_session_storage(session_id, session_meta)
        run_dir = self._run_dir_from_storage(storage)
        os.makedirs(run_dir, exist_ok=True)

        files: list[str] = []
        merged = self._merge_positions(positions, fusion_positions)
        if merged:
            filename = f"ranging_{run_index:03d}.csv"
            path = os.path.join(run_dir, filename)
            self._write_positions_csv(path, merged)
            self._mirror_browser_file(path, "ranging", session_id)
            self._write_sensor_fusion_result_export(fusion_positions)
            files.append(filename)

        run_meta = dict(meta or {})
        run_meta.update({
            "stream_type": "ranging",
            "index": int(run_index),
            "files": files,
            "storage": storage.copy(),
            "data_source": "sensor_fusion_result",
            "sample_count": len(merged),
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
        session_meta = self.get_session_meta(session_id)
        storage = self._ensure_session_storage(session_id, session_meta)
        run_dir = self._run_dir_from_storage(storage)
        os.makedirs(run_dir, exist_ok=True)

        logs = list(logs or [])
        filename = f"log_{run_index:03d}.txt"
        txt_path = os.path.join(run_dir, filename)
        self._write_logs_txt(txt_path, logs)
        self._mirror_browser_file(txt_path, "log", session_id)
        files = [filename]

        run_meta = dict(meta or {})
        run_meta.update({
            "stream_type": "log",
            "index": int(run_index),
            "files": files,
            "storage": storage.copy(),
            "data_source": "log_data",
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
        self._write_storage_run_meta(session_id)

    def list_session_runs(self, session_id: str, stream_type: str | None = None) -> list[dict]:
        runs = self._read_runs(session_id)
        if stream_type:
            runs = [run for run in runs if run.get("stream_type") == stream_type]
        return [run.copy() for run in runs]

    def export_session_to(self, session_id: str, destination_dir: str) -> str:
        if not session_id or not destination_dir:
            return ""

        source = self.get_session_folder(session_id)
        storage = self._read_storage_meta(session_id)
        storage_run_dir = self._run_dir_from_storage(storage) if storage else ""
        if not os.path.isdir(source) and not os.path.isdir(storage_run_dir):
            return ""

        os.makedirs(destination_dir, exist_ok=True)
        destination = os.path.join(destination_dir, session_id)
        os.makedirs(destination, exist_ok=True)

        metadata_files = ("session_meta.json", "storage_meta.json", "runs.json", "config_snapshot.json", "anchors.json")
        for filename in metadata_files:
            self._copy_file_if_exists(os.path.join(source, filename), destination)

        ranging_destination = os.path.join(destination, "ranging")
        log_destination = os.path.join(destination, "log")
        messages_destination = os.path.join(destination, "messages")
        runs_destination = os.path.join(destination, "runs", storage.get("run_label", "run") if storage else "run")
        os.makedirs(ranging_destination, exist_ok=True)
        os.makedirs(log_destination, exist_ok=True)
        os.makedirs(messages_destination, exist_ok=True)

        if storage_run_dir and os.path.isdir(storage_run_dir):
            self._copy_folder_files(storage_run_dir, runs_destination)
            for filename in os.listdir(storage_run_dir):
                source_file = os.path.join(storage_run_dir, filename)
                if not os.path.isfile(source_file):
                    continue
                lowered = filename.lower()
                if lowered.endswith(".csv") and lowered.startswith(("ranging", "sensor_fusion")):
                    shutil.copy2(source_file, os.path.join(ranging_destination, filename))
                elif lowered.endswith(".txt") and lowered.startswith("log"):
                    shutil.copy2(source_file, os.path.join(log_destination, filename))

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
        storage = self._read_storage_meta(session_id)
        if not os.path.exists(session_folder) and not storage:
            log.warning("Session folder %s does not exist.", session_folder)
            return []

        if detail_type in ("ranging", "fusion"):
            rows = []
            for run in self.list_session_runs(session_id, "ranging"):
                for rel_path in run.get("files", []):
                    base = os.path.basename(rel_path).lower()
                    if not base.endswith(".csv"):
                        continue
                    if not (base.startswith("ranging") or base.startswith("sensor_fusion") or base == "positions.csv"):
                        continue
                    full_path = self._resolve_run_file(session_id, run, rel_path)
                    if os.path.exists(full_path):
                        rows.extend(self._read_positions_csv(full_path))
            if rows:
                return rows
            pos_path = os.path.join(session_folder, "positions.csv")
            if os.path.exists(pos_path):
                return self._read_positions_csv(pos_path)
            fusion_path = os.path.join(session_folder, "sensor_fusion_positions.csv")
            if os.path.exists(fusion_path):
                return self._read_positions_csv(fusion_path)
            return []

        if detail_type == "logs":
            rows = []
            for run in self.list_session_runs(session_id, "log"):
                for rel_path in run.get("files", []):
                    full_path = self._resolve_run_file(session_id, run, rel_path)
                    if not os.path.exists(full_path):
                        continue
                    lowered = full_path.lower()
                    if lowered.endswith(".txt"):
                        rows.extend(self._read_logs_txt(full_path))
                    elif lowered.endswith(".csv"):
                        rows.extend(self._read_logs_csv(full_path))
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
        storage = self._read_storage_meta(session_id) or self._find_storage_for_session(session_id)
        storage_run_dir = self._run_dir_from_storage(storage) if storage else ""
        index_entries = [item for item in self._read_index() if item.get("session_id") != session_id]
        self._write_index(index_entries)

        if storage_run_dir and os.path.isdir(storage_run_dir):
            try:
                shutil.rmtree(storage_run_dir)
            except Exception as exc:
                log.error("Error removing session storage folder %s: %s", storage_run_dir, exc)
                return False

        date_key = storage.get("date", "") if storage else ""
        if date_key:
            for root_dir in (HOT_STORE_DIR, ARCHIVE_STORE_DIR):
                manifest_path = os.path.join(root_dir, date_key, "manifest.json")
                manifest = self._read_json(manifest_path, default={})
                if isinstance(manifest, dict) and isinstance(manifest.get("runs"), list):
                    manifest["runs"] = [item for item in manifest["runs"] if item.get("session_id") != session_id]
                    self._write_json(manifest_path, manifest)

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
        storage = session_meta.get("storage", {}) if isinstance(session_meta.get("storage", {}), dict) else {}
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
            "storage_date": storage.get("date", ""),
            "storage_run_label": storage.get("run_label", ""),
            "storage_tier": storage.get("tier", ""),
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
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            for index, pos in enumerate(positions, start=1):
                writer.writerow([self._build_position_text_record(pos, index)])

    def _write_sensor_fusion_result_export(self, fusion_positions: list[dict] | None) -> str | None:
        if not fusion_positions:
            return None

        now = datetime.now()
        output_dir = os.path.join(SHARED_DATA_DIR, now.strftime("%d_%m_%y"))
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(
            output_dir,
            f"{now.strftime('%Y%m%d_%Hg%Mp')}_sensor_fusion_result.csv",
        )
        self._write_positions_csv(output_path, fusion_positions)
        return output_path

    @staticmethod
    def _csv_int(row: dict, key: str, default: int = 0) -> int:
        try:
            return int(float(row.get(key, default) or default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _csv_float(row: dict, key: str, default: float = 0.0) -> float:
        try:
            return float(row.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _value_float(value, default: float = 0.0) -> float:
        try:
            if value in ("", None):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _value_int(value, default: int = 0) -> int:
        try:
            if value in ("", None):
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _first_float(self, data: dict, *keys) -> float:
        for key in keys:
            value = data.get(key, "")
            if value not in ("", None):
                return self._value_float(value)
        return 0.0

    def _anchor_maps(self, data: dict) -> tuple[dict[int, int], dict[int, int]]:
        distances: dict[int, int] = {}
        weights: dict[int, int] = {}
        for anchor in data.get("anchors", []) or []:
            if not isinstance(anchor, dict):
                continue
            anchor_id = self._value_int(anchor.get("anchor_id"))
            if anchor_id:
                distances[anchor_id] = self._value_int(anchor.get("distance_mm"))
                weights[anchor_id] = self._value_int(anchor.get("weight"))
        return distances, weights

    def _distance_m(self, data: dict, anchor_index: int, distances_by_anchor: dict[int, int]) -> float:
        d_mm = data.get(f"d{anchor_index}_mm", "")
        if d_mm in ("", None):
            d_mm = distances_by_anchor.get(anchor_index, "")
        if d_mm not in ("", None):
            return self._value_float(d_mm) / 1000.0
        return self._first_float(data, f"d{anchor_index}")

    def _weight_raw(self, data: dict, anchor_index: int, weights_by_anchor: dict[int, int]) -> int:
        value = data.get(f"w{anchor_index}", "")
        if value not in ("", None):
            return self._value_int(value)
        return self._value_int(weights_by_anchor.get(anchor_index, 0))

    def _build_position_text_record(self, pos: dict, index: int) -> str:
        distances_by_anchor, weights_by_anchor = self._anchor_maps(pos)
        distances = [self._distance_m(pos, i, distances_by_anchor) for i in range(1, 5)]
        weights = [self._weight_raw(pos, i, weights_by_anchor) for i in range(1, 5)]

        status = str(pos.get("status") or "Update")
        dt = self._value_float(pos.get("dt", 0.0))
        update_dt = dt if status in ("Init", "Update") else 0.0
        predict_dt = dt if status == "Predict" else 0.0

        line = (
            f"({int(index):4d}/{self._value_int(pos.get('tx_frame_cnt', 0)):4d}) {status:<7s} "
            f"| ts: {self._value_int(pos.get('timestamp_ms', 0))} "
            f"| zone: {self._value_int(pos.get('zone_id', 0))} "
            f"| ukf_step: {self._value_int(pos.get('ukf_step', 0))} "
            f"| ax: {self._value_float(pos.get('ax', 0.0)):9.6f} "
            f"| ay: {self._value_float(pos.get('ay', 0.0)):9.6f} "
            f"| gz: {self._value_float(pos.get('gz', 0.0)):9.6f} "
            f"| tril_x: {self._first_float(pos, 'tril_x_m', 'tril_x', 'x_m', 'x'):9.6f} "
            f"| tril_y: {self._first_float(pos, 'tril_y_m', 'tril_y', 'y_m', 'y'):9.6f} "
            f"| ukf_x: {self._first_float(pos, 'ukf_x_m', 'ukf_x'):9.6f} "
            f"| ukf_y: {self._first_float(pos, 'ukf_y_m', 'ukf_y'):9.6f} "
            f"| ukf_yaw: {self._first_float(pos, 'ukf_yaw_deg', 'ukf_yaw'):9.6f} "
            f"| yaw: {self._first_float(pos, 'yaw_deg', 'yaw'):9.6f} "
            f"| update_dt: {update_dt:9.6f} "
            f"| predict_dt: {predict_dt:9.6f} "
            f"| mask: {self._value_int(pos.get('anchor_mask', pos.get('mask', 0)))} "
            f"| d1: {distances[0]:9.6f} | d2: {distances[1]:9.6f} "
            f"| d3: {distances[2]:9.6f} | d4: {distances[3]:9.6f} "
            f"| w1: {weights[0]} | w2: {weights[1]} | w3: {weights[2]} | w4: {weights[3]} "
            f"| err: {self._value_int(pos.get('ranging_error_count', pos.get('err_cnt', 0)))} "
            f"| amp1: {self._value_float(pos.get('fp_amp_norm1', 0.0)):9.6f} "
            f"| amp2: {self._value_float(pos.get('fp_amp_norm2', 0.0)):9.6f} "
            f"| amp3: {self._value_float(pos.get('fp_amp_norm3', 0.0)):9.6f} "
            f"| amp4: {self._value_float(pos.get('fp_amp_norm4', 0.0)):9.6f} "
            f"| snr1: {self._value_float(pos.get('fp_snr1', 0.0)):9.6f} "
            f"| snr2: {self._value_float(pos.get('fp_snr2', 0.0)):9.6f} "
            f"| snr3: {self._value_float(pos.get('fp_snr3', 0.0)):9.6f} "
            f"| snr4: {self._value_float(pos.get('fp_snr4', 0.0)):9.6f}"
        )
        return line

    def _parse_position_text_record(self, raw_line: str) -> dict | None:
        line_text = (raw_line or "").strip()
        if not line_text.startswith("(") or "|" not in line_text:
            return None

        status_match = re.search(r"\)\s*(?P<status>Init|Predict|Update)\b", line_text)
        if not status_match:
            return None

        fields = {}
        for part in line_text.split("|")[1:]:
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            fields[key.strip()] = value.strip()

        ukf_x = self._value_float(fields.get("ukf_x"))
        ukf_y = self._value_float(fields.get("ukf_y"))
        tril_x = self._value_float(fields.get("tril_x"))
        tril_y = self._value_float(fields.get("tril_y"))
        distances_m = [self._value_float(fields.get(f"d{i}")) for i in range(1, 5)]

        return {
            "time": "",
            "timestamp_ms": self._value_int(fields.get("ts")),
            "packet_timestamp_ms": 0,
            "seq": 0,
            "source": "sensor_fusion" if ukf_x or ukf_y else "ranging",
            "x_m": ukf_x if ukf_x or ukf_y else tril_x,
            "y_m": ukf_y if ukf_x or ukf_y else tril_y,
            "z_m": 0.0,
            "rms_error_m": 0.0,
            "anchor_mask": self._value_int(fields.get("mask")),
            "d1_mm": str(int(round(distances_m[0] * 1000.0))) if distances_m[0] else "",
            "d2_mm": str(int(round(distances_m[1] * 1000.0))) if distances_m[1] else "",
            "d3_mm": str(int(round(distances_m[2] * 1000.0))) if distances_m[2] else "",
            "d4_mm": str(int(round(distances_m[3] * 1000.0))) if distances_m[3] else "",
            "w1": fields.get("w1", ""),
            "w2": fields.get("w2", ""),
            "w3": fields.get("w3", ""),
            "w4": fields.get("w4", ""),
            "ukf_x_m": ukf_x,
            "ukf_y_m": ukf_y,
            "ukf_yaw_deg": fields.get("ukf_yaw", ""),
            "tril_x_m": tril_x,
            "tril_y_m": tril_y,
            "yaw_deg": fields.get("yaw", ""),
            "ranging_error_count": fields.get("err", ""),
            "zone_id": fields.get("zone", ""),
            "room_id": "",
            "local_x_m": "",
            "local_y_m": "",
            "local_z_m": "",
        }

    def _read_positions_csv(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        results: list[dict] = []
        with open(path, "r", encoding="utf-8") as handle:
            # Read first line to detect format
            first_line = handle.readline()
            handle.seek(0)

            if first_line.lstrip().startswith("(") and "|" in first_line:
                reader = csv.reader(handle)
                for row in reader:
                    if not row:
                        continue
                    parsed = self._parse_position_text_record(row[0])
                    if parsed is not None:
                        results.append(parsed)
                return results
            
            is_key_value = ":" in first_line
            if is_key_value:
                reader = csv.reader(handle)
                for row in reader:
                    row_dict = {}
                    for cell in row:
                        if ":" in cell:
                            parts = cell.split(":", 1)
                            row_dict[parts[0].strip()] = parts[1].strip()
                    if not row_dict:
                        continue
                    try:
                        results.append({
                            "time": row_dict.get("time", ""),
                            "timestamp_ms": int(row_dict.get("timestamp_ms") or 0),
                            "packet_timestamp_ms": int(row_dict.get("packet_timestamp_ms") or 0),
                            "seq": int(row_dict.get("seq") or 0),
                            "source": row_dict.get("source", "ranging"),
                            "x_m": float(row_dict.get("x_m") or 0.0),
                            "y_m": float(row_dict.get("y_m") or 0.0),
                            "z_m": float(row_dict.get("z_m") or 0.0),
                            "rms_error_m": float(row_dict.get("rms_error_m") or 0.0),
                            "anchor_mask": int(row_dict.get("anchor_mask") or 0),
                            "d1_mm": row_dict.get("d1_mm", ""),
                            "d2_mm": row_dict.get("d2_mm", ""),
                            "d3_mm": row_dict.get("d3_mm", ""),
                            "d4_mm": row_dict.get("d4_mm", ""),
                            "w1": row_dict.get("w1", ""),
                            "w2": row_dict.get("w2", ""),
                            "w3": row_dict.get("w3", ""),
                            "w4": row_dict.get("w4", ""),
                            "ukf_x_m": row_dict.get("ukf_x_m", ""),
                            "ukf_y_m": row_dict.get("ukf_y_m", ""),
                            "ukf_yaw_deg": row_dict.get("ukf_yaw_deg", ""),
                            "tril_x_m": row_dict.get("tril_x_m", ""),
                            "tril_y_m": row_dict.get("tril_y_m", ""),
                            "yaw_deg": row_dict.get("yaw_deg", ""),
                            "ranging_error_count": row_dict.get("ranging_error_count", ""),
                            "zone_id": row_dict.get("zone_id", ""),
                            "room_id": row_dict.get("room_id", ""),
                            "local_x_m": row_dict.get("local_x_m", ""),
                            "local_y_m": row_dict.get("local_y_m", ""),
                            "local_z_m": row_dict.get("local_z_m", ""),
                        })
                    except (TypeError, ValueError, KeyError):
                        continue
            else:
                reader = csv.DictReader(handle)
                for row in reader:
                    try:
                        results.append({
                            "time": row.get("time", ""),
                            "timestamp_ms": self._csv_int(row, "timestamp_ms"),
                            "packet_timestamp_ms": self._csv_int(row, "packet_timestamp_ms"),
                            "seq": self._csv_int(row, "seq"),
                            "source": row.get("source", "ranging"),
                            "x_m": self._csv_float(row, "x_m"),
                            "y_m": self._csv_float(row, "y_m"),
                            "z_m": self._csv_float(row, "z_m"),
                            "rms_error_m": self._csv_float(row, "rms_error_m"),
                            "anchor_mask": self._csv_int(row, "anchor_mask"),
                            "d1_mm": row.get("d1_mm", ""),
                            "d2_mm": row.get("d2_mm", ""),
                            "d3_mm": row.get("d3_mm", ""),
                            "d4_mm": row.get("d4_mm", ""),
                            "w1": row.get("w1", ""),
                            "w2": row.get("w2", ""),
                            "w3": row.get("w3", ""),
                            "w4": row.get("w4", ""),
                            "ukf_x_m": row.get("ukf_x_m", ""),
                            "ukf_y_m": row.get("ukf_y_m", ""),
                            "ukf_yaw_deg": row.get("ukf_yaw_deg", ""),
                            "tril_x_m": row.get("tril_x_m", ""),
                            "tril_y_m": row.get("tril_y_m", ""),
                            "yaw_deg": row.get("yaw_deg", ""),
                            "ranging_error_count": row.get("ranging_error_count", ""),
                            "zone_id": row.get("zone_id", ""),
                            "room_id": row.get("room_id", ""),
                            "local_x_m": row.get("local_x_m", ""),
                            "local_y_m": row.get("local_y_m", ""),
                            "local_z_m": row.get("local_z_m", ""),
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

    def get_session_record_files(self, session_id: str, detail_type: str) -> list[dict]:
        session_folder = self.get_session_folder(session_id)
        if not os.path.exists(session_folder) and not self._read_storage_meta(session_id):
            return []

        runs = self.list_session_runs(session_id)
        results = []

        def format_time(iso_str: str) -> str:
            if not iso_str:
                return "--"
            try:
                dt = datetime.fromisoformat(iso_str.replace("Z", ""))
                return dt.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                return iso_str

        def get_file_time(file_path: str) -> str:
            try:
                mtime = os.path.getmtime(file_path)
                dt = datetime.fromtimestamp(mtime)
                return dt.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                return "--"

        if runs:
            for run in runs:
                stream_type = run.get("stream_type", "")
                mapped_type = "ranging" if stream_type in ("ranging", "fusion") else "logs"
                if mapped_type != detail_type:
                    continue

                start_time = format_time(run.get("start_time_iso", ""))
                for rel_path in run.get("files", []):
                    full_path = self._resolve_run_file(session_id, run, rel_path)
                    if not os.path.exists(full_path):
                        continue

                    filename = os.path.basename(full_path)
                    lowered = filename.lower()
                    if detail_type == "ranging" and not lowered.endswith(".csv"):
                        continue
                    if detail_type == "logs" and not lowered.endswith((".txt", ".csv")):
                        continue
                    if detail_type == "logs" and lowered.endswith(".csv"):
                        txt_candidate = os.path.splitext(full_path)[0] + ".txt"
                        if os.path.exists(txt_candidate):
                            continue

                    file_time = get_file_time(full_path)
                    results.append({
                        "time": start_time if start_time != "--" else file_time,
                        "filename": filename,
                        "relative_path": full_path,
                    })
            if results:
                return results

        meta = self.get_session_meta(session_id)
        session_time = format_time(meta.get("start_time_iso", ""))

        if detail_type == "ranging":
            ranging_dir = os.path.join(session_folder, "ranging")
            if os.path.isdir(ranging_dir):
                for f in os.listdir(ranging_dir):
                    if f.lower().endswith(".csv"):
                        full_path = os.path.join(ranging_dir, f)
                        results.append({
                            "time": get_file_time(full_path) if get_file_time(full_path) != "--" else session_time,
                            "filename": f,
                            "relative_path": full_path,
                        })
            pos_path = os.path.join(session_folder, "positions.csv")
            if os.path.exists(pos_path):
                results.append({
                    "time": get_file_time(pos_path) if get_file_time(pos_path) != "--" else session_time,
                    "filename": "positions.csv",
                    "relative_path": pos_path,
                })
        else:
            log_dir = os.path.join(session_folder, "log")
            if not os.path.isdir(log_dir):
                log_dir = os.path.join(session_folder, "logs")
            if os.path.isdir(log_dir):
                for f in os.listdir(log_dir):
                    if f.lower().endswith((".txt", ".csv")):
                        full_path = os.path.join(log_dir, f)
                        if f.lower().endswith(".csv"):
                            txt_candidate = os.path.splitext(full_path)[0] + ".txt"
                            if os.path.exists(txt_candidate):
                                continue
                        results.append({
                            "time": get_file_time(full_path) if get_file_time(full_path) != "--" else session_time,
                            "filename": f,
                            "relative_path": full_path,
                        })
            txt_path = os.path.join(session_folder, "logs.txt")
            if os.path.exists(txt_path):
                results.append({
                    "time": get_file_time(txt_path) if get_file_time(txt_path) != "--" else session_time,
                    "filename": "logs.txt",
                    "relative_path": txt_path,
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
