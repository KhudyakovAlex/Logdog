from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


def _db_total_bytes(db_path: Path) -> int:
    base = int(db_path.stat().st_size) if db_path.exists() else 0
    wal = int((db_path.parent / (db_path.name + "-wal")).stat().st_size) if (db_path.parent / (db_path.name + "-wal")).exists() else 0
    shm = int((db_path.parent / (db_path.name + "-shm")).stat().st_size) if (db_path.parent / (db_path.name + "-shm")).exists() else 0
    return base + wal + shm


@dataclass(frozen=True)
class LogRow:
    id: int
    ts: int
    level: str
    app: str
    message: str
    trace_id: Optional[str]
    fields: Optional[dict[str, Any]]


class LogdogDB:
    def __init__(
        self,
        db_path: Path,
        *,
        db_max_bytes: int,
        retention_target_fraction: float = 0.9,
        retention_check_interval_s: int = 10,
    ) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db_max_bytes = int(db_max_bytes)
        self._target_bytes = int(self._db_max_bytes * float(retention_target_fraction))
        self._retention_check_interval_s = int(retention_check_interval_s)

        self._lock = threading.RLock()
        self._last_retention_at = 0.0

        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            self._apply_pragmas()
            self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _apply_pragmas(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=3000;")
        cur.execute("PRAGMA foreign_keys=ON;")
        # Must be set before creating tables to be effective without full VACUUM.
        cur.execute("PRAGMA auto_vacuum=INCREMENTAL;")
        self._conn.commit()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              level TEXT NOT NULL,
              app TEXT NOT NULL,
              message TEXT NOT NULL,
              trace_id TEXT NULL,
              fields_json TEXT NULL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_app_ts ON logs(app, ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_level_ts ON logs(level, ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_trace_id ON logs(trace_id);")
        self._conn.commit()

    def insert(
        self,
        *,
        level: str,
        app: str,
        message: str,
        ts: Optional[int] = None,
        trace_id: Optional[str] = None,
        fields: Optional[dict[str, Any]] = None,
    ) -> LogRow:
        ts_ms = int(ts) if ts is not None else _now_ms()
        fields_json = json.dumps(fields, ensure_ascii=False) if fields is not None else None

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO logs(ts, level, app, message, trace_id, fields_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts_ms, level, app, message, trace_id, fields_json),
            )
            log_id = int(cur.lastrowid)
            self._conn.commit()

        self.maybe_enforce_retention()

        return LogRow(
            id=log_id,
            ts=ts_ms,
            level=level,
            app=app,
            message=message,
            trace_id=trace_id,
            fields=fields,
        )

    def recent(self, *, limit: int = 100, app: Optional[str] = None, level: Optional[str] = None) -> list[LogRow]:
        limit = max(1, min(int(limit), 5000))
        where: list[str] = []
        params: list[Any] = []
        if app:
            where.append("app = ?")
            params.append(app)
        if level:
            where.append("level = ?")
            params.append(level)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT id, ts, level, app, message, trace_id, fields_json
                FROM logs
                {where_sql}
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = cur.fetchall()

        return [self._row_to_log(r) for r in rows]

    def query(
        self,
        *,
        app: Optional[str] = None,
        level: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        contains: Optional[str] = None,
        trace_id: Optional[str] = None,
        limit: int = 200,
    ) -> list[LogRow]:
        limit = max(1, min(int(limit), 5000))
        where: list[str] = []
        params: list[Any] = []

        if app:
            where.append("app = ?")
            params.append(app)
        if level:
            where.append("level = ?")
            params.append(level)
        if since is not None:
            where.append("ts >= ?")
            params.append(int(since))
        if until is not None:
            where.append("ts <= ?")
            params.append(int(until))
        if trace_id:
            where.append("trace_id = ?")
            params.append(trace_id)
        if contains:
            where.append("message LIKE ?")
            params.append(f"%{contains}%")

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT id, ts, level, app, message, trace_id, fields_json
                FROM logs
                {where_sql}
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = cur.fetchall()

        return [self._row_to_log(r) for r in rows]

    def maybe_enforce_retention(self) -> None:
        now = time.time()
        if (now - self._last_retention_at) < self._retention_check_interval_s:
            return

        with self._lock:
            # Double-check inside lock to avoid stampede.
            now2 = time.time()
            if (now2 - self._last_retention_at) < self._retention_check_interval_s:
                return
            self._last_retention_at = now2

        self._enforce_retention_if_needed()

    def _enforce_retention_if_needed(self) -> None:
        if self._db_max_bytes <= 0:
            return

        total = _db_total_bytes(self._db_path)
        if total <= self._db_max_bytes:
            return

        # Delete oldest rows in batches until under target.
        batch = 10_000
        while _db_total_bytes(self._db_path) > self._target_bytes:
            deleted = self._delete_oldest(batch)
            if deleted <= 0:
                break

        # Try to shrink file(s): checkpoint WAL and incremental vacuum.
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            cur.execute("PRAGMA incremental_vacuum(2000);")
            self._conn.commit()

    def _delete_oldest(self, limit: int) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id FROM logs
                ORDER BY ts ASC, id ASC
                LIMIT ?
                """,
                (int(limit),),
            )
            ids = [int(r["id"]) for r in cur.fetchall()]
            if not ids:
                return 0

            cur.execute(
                f"DELETE FROM logs WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            )
            self._conn.commit()
            return int(cur.rowcount)

    @staticmethod
    def _row_to_log(r: sqlite3.Row) -> LogRow:
        fields_raw = r["fields_json"]
        fields = json.loads(fields_raw) if fields_raw else None
        return LogRow(
            id=int(r["id"]),
            ts=int(r["ts"]),
            level=str(r["level"]),
            app=str(r["app"]),
            message=str(r["message"]),
            trace_id=str(r["trace_id"]) if r["trace_id"] is not None else None,
            fields=fields,
        )

