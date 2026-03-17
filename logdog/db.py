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
class AttachmentRefRow:
    id: int
    kind: str
    name: str
    size_bytes: int


@dataclass(frozen=True)
class AttachmentRow:
    id: int
    log_id: int
    kind: str
    name: str
    content: str
    size_bytes: int


@dataclass(frozen=True)
class LogRow:
    id: int
    ts: int
    level: str
    app: str
    message: str
    trace_id: Optional[str]
    fields: Optional[dict[str, Any]]
    attachments: list[AttachmentRefRow]


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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS log_attachments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              log_id INTEGER NOT NULL,
              kind TEXT NOT NULL,
              name TEXT NOT NULL,
              content_text TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_app_ts ON logs(app, ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_level_ts ON logs(level, ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_trace_id ON logs(trace_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_log_attachments_log_id ON log_attachments(log_id);")
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
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> LogRow:
        ts_ms = int(ts) if ts is not None else _now_ms()
        fields_json = json.dumps(fields, ensure_ascii=False) if fields is not None else None
        attachment_rows: list[AttachmentRefRow] = []

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
            for attachment in attachments or []:
                content = str(attachment["content"])
                size_bytes = len(content.encode("utf-8"))
                cur.execute(
                    """
                    INSERT INTO log_attachments(log_id, kind, name, content_text, size_bytes)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (log_id, str(attachment["kind"]), str(attachment["name"]), content, size_bytes),
                )
                attachment_rows.append(
                    AttachmentRefRow(
                        id=int(cur.lastrowid),
                        kind=str(attachment["kind"]),
                        name=str(attachment["name"]),
                        size_bytes=size_bytes,
                    )
                )
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
            attachments=attachment_rows,
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

        return self._rows_to_logs(rows)

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
            like = f"%{contains}%"
            where.append(
                "(message LIKE ? OR EXISTS ("
                "SELECT 1 FROM log_attachments a "
                "WHERE a.log_id = logs.id AND (a.name LIKE ? OR a.content_text LIKE ?)"
                "))"
            )
            params.extend([like, like, like])

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

        return self._rows_to_logs(rows)

    def apps(self, *, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT app, COUNT(*) AS cnt, MAX(ts) AS last_ts
                FROM logs
                GROUP BY app
                ORDER BY last_ts DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()

        return [{"app": str(r["app"]), "count": int(r["cnt"]), "lastTs": int(r["last_ts"])} for r in rows]

    def attachment(self, attachment_id: int) -> Optional[AttachmentRow]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, log_id, kind, name, content_text, size_bytes
                FROM log_attachments
                WHERE id = ?
                """,
                (int(attachment_id),),
            )
            row = cur.fetchone()

        if row is None:
            return None

        return AttachmentRow(
            id=int(row["id"]),
            log_id=int(row["log_id"]),
            kind=str(row["kind"]),
            name=str(row["name"]),
            content=str(row["content_text"]),
            size_bytes=int(row["size_bytes"]),
        )

    def purge(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM logs;")
            before = int(cur.fetchone()[0])
            cur.execute("DELETE FROM logs;")
            self._conn.commit()

            # Try to shrink file(s): checkpoint WAL and incremental vacuum.
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            cur.execute("PRAGMA incremental_vacuum(2000);")
            self._conn.commit()

        return before

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

    def _rows_to_logs(self, rows: Iterable[sqlite3.Row]) -> list[LogRow]:
        row_list = list(rows)
        attachments_by_log = self._attachment_refs_by_log_id(int(r["id"]) for r in row_list)
        return [self._row_to_log(r, attachments_by_log.get(int(r["id"]), [])) for r in row_list]

    def _attachment_refs_by_log_id(self, log_ids: Iterable[int]) -> dict[int, list[AttachmentRefRow]]:
        unique_ids = [int(i) for i in dict.fromkeys(int(i) for i in log_ids)]
        if not unique_ids:
            return {}

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT id, log_id, kind, name, size_bytes
                FROM log_attachments
                WHERE log_id IN ({','.join('?' for _ in unique_ids)})
                ORDER BY id ASC
                """,
                unique_ids,
            )
            rows = cur.fetchall()

        result: dict[int, list[AttachmentRefRow]] = {log_id: [] for log_id in unique_ids}
        for r in rows:
            result.setdefault(int(r["log_id"]), []).append(
                AttachmentRefRow(
                    id=int(r["id"]),
                    kind=str(r["kind"]),
                    name=str(r["name"]),
                    size_bytes=int(r["size_bytes"]),
                )
            )
        return result

    @staticmethod
    def _row_to_log(r: sqlite3.Row, attachments: list[AttachmentRefRow]) -> LogRow:
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
            attachments=attachments,
        )

