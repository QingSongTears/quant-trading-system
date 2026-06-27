"""
扫描结果持久化 + 断点续扫
==========================

SQLite 后端, 支持:
- 记录每条 trial (params/metrics/elapsed/error)
- 按 (strategy, space_name, start, end) 维度归档
- 断点续扫: 已存在的 params 组合跳过
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .runner import TrialResult


class ScanStore:
    """SQLite-backed scan 结果仓库"""

    DEFAULT_PATH = "database/scan_store.db"

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or self.DEFAULT_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    scan_id TEXT PRIMARY KEY,
                    strategy TEXT,
                    space_name TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS trials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT,
                    params_json TEXT,
                    metrics_json TEXT,
                    elapsed_sec REAL,
                    error TEXT,
                    timestamp TEXT,
                    FOREIGN KEY(scan_id) REFERENCES runs(scan_id)
                );
                CREATE INDEX IF NOT EXISTS idx_trials_scan ON trials(scan_id);
                CREATE INDEX IF NOT EXISTS idx_trials_params ON trials(params_json);
            """)

    def create_run(
        self,
        strategy: str,
        space_name: str,
        start: str,
        end: str,
    ) -> str:
        scan_id = f"{strategy}__{space_name}__{start}__{end}__{datetime.now().strftime('%Y%m%d%H%M%S')}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                (scan_id, strategy, space_name, start, end, datetime.now().isoformat()),
            )
        return scan_id

    def save_trial(self, scan_id: str, result: TrialResult) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO trials(scan_id, params_json, metrics_json, elapsed_sec, error, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    json.dumps(result.params, ensure_ascii=False, sort_keys=True),
                    json.dumps(result.metrics, ensure_ascii=False),
                    result.elapsed_sec,
                    result.error,
                    result.timestamp,
                ),
            )

    def exists(self, scan_id: str, params: dict) -> bool:
        """断点续扫: 检查该 params 组合是否已跑过"""
        params_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT 1 FROM trials WHERE scan_id = ? AND params_json = ?",
                (scan_id, params_json),
            )
            return cur.fetchone() is not None

    def load_results(self, scan_id: str) -> list[TrialResult]:
        """加载历史 trial"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT id, params_json, metrics_json, elapsed_sec, error, timestamp "
                "FROM trials WHERE scan_id = ? ORDER BY id",
                (scan_id,),
            )
            rows = cur.fetchall()
        out = []
        for trial_id, pj, mj, el, err, ts in rows:
            out.append(TrialResult(
                trial_id=trial_id,
                params=json.loads(pj),
                metrics=json.loads(mj),
                elapsed_sec=el,
                error=err,
                timestamp=ts,
            ))
        return out

    def list_runs(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT scan_id, strategy, space_name, start_date, end_date, created_at FROM runs ORDER BY created_at DESC")
            return [
                {"scan_id": r[0], "strategy": r[1], "space_name": r[2], "start": r[3], "end": r[4], "created_at": r[5]}
                for r in cur.fetchall()
            ]