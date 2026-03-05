from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from logdog.config import load_settings


logger = logging.getLogger("logdog.mcp")
logging.basicConfig(level=logging.INFO)

mcp = FastMCP("logdog")


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    # Use uri mode=ro when the file exists; otherwise open normally (will just return empty results).
    if db_path.exists():
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA busy_timeout=3000;")
    cur.execute("PRAGMA query_only=ON;")
    return conn


def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    fields_raw = r["fields_json"]
    fields = json.loads(fields_raw) if fields_raw else None
    return {
        "id": int(r["id"]),
        "ts": int(r["ts"]),
        "level": str(r["level"]),
        "app": str(r["app"]),
        "message": str(r["message"]),
        "traceId": str(r["trace_id"]) if r["trace_id"] is not None else None,
        "fields": fields,
    }


def _select(
    conn: sqlite3.Connection,
    *,
    where_sql: str,
    params: list[Any],
    limit: int,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 5000))
    cur = conn.cursor()
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
    return [_row_to_dict(r) for r in cur.fetchall()]


@mcp.tool()
def recent(limit: int = 200, app: Optional[str] = None, level: Optional[str] = None) -> list[dict[str, Any]]:
    """Return latest log records (optionally filtered by app/level)."""
    settings = load_settings()
    conn = _connect_readonly(settings.db_path)
    try:
        where = []
        params: list[Any] = []
        if app:
            where.append("app = ?")
            params.append(app)
        if level:
            where.append("level = ?")
            params.append(level)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        return _select(conn, where_sql=where_sql, params=params, limit=limit)
    finally:
        conn.close()


@mcp.tool()
def query(
    limit: int = 200,
    app: Optional[str] = None,
    level: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    contains: Optional[str] = None,
    traceId: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Query logs with filters (time range, contains, traceId)."""
    settings = load_settings()
    conn = _connect_readonly(settings.db_path)
    try:
        where = []
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
        if traceId:
            where.append("trace_id = ?")
            params.append(traceId)
        if contains:
            where.append("message LIKE ?")
            params.append(f"%{contains}%")

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        return _select(conn, where_sql=where_sql, params=params, limit=limit)
    finally:
        conn.close()


def main() -> None:
    # Important: MCP stdio uses stdout for protocol messages.
    # Do not print to stdout here; logging is fine.
    mcp.run()


if __name__ == "__main__":
    main()

