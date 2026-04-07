from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP, Image

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


def _row_to_dict(r: sqlite3.Row, attachments: list[dict[str, Any]]) -> dict[str, Any]:
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
        "attachments": attachments,
    }


def _attachment_refs_by_log_id(conn: sqlite3.Connection, log_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    unique_ids = [int(i) for i in dict.fromkeys(log_ids)]
    if not unique_ids:
        return {}

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, log_id, kind, name, size_bytes, mime_type, width, height
        FROM log_attachments
        WHERE log_id IN ({','.join('?' for _ in unique_ids)})
        ORDER BY id ASC
        """,
        unique_ids,
    )

    result: dict[int, list[dict[str, Any]]] = {log_id: [] for log_id in unique_ids}
    for r in cur.fetchall():
        result.setdefault(int(r["log_id"]), []).append(
            {
                "id": int(r["id"]),
                "kind": str(r["kind"]),
                "name": str(r["name"]),
                "sizeBytes": int(r["size_bytes"]),
                "mime": str(r["mime_type"]) if r["mime_type"] is not None else None,
                "width": int(r["width"]) if r["width"] is not None else None,
                "height": int(r["height"]) if r["height"] is not None else None,
            }
        )
    return result


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
    rows = cur.fetchall()
    attachments_by_log_id = _attachment_refs_by_log_id(conn, [int(r["id"]) for r in rows])
    return [_row_to_dict(r, attachments_by_log_id.get(int(r["id"]), [])) for r in rows]


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
            like = f"%{contains}%"
            where.append(
                "(message LIKE ? OR EXISTS ("
                "SELECT 1 FROM log_attachments a "
                "WHERE a.log_id = logs.id AND (a.name LIKE ? OR a.content_text LIKE ?)"
                "))"
            )
            params.extend([like, like, like])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        return _select(conn, where_sql=where_sql, params=params, limit=limit)
    finally:
        conn.close()


@mcp.tool()
def attachment(id: int) -> dict[str, Any]:
    """Return attachment content by id."""
    settings = load_settings()
    conn = _connect_readonly(settings.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, log_id, kind, name, content_text, size_bytes, mime_type, width, height
            FROM log_attachments
            WHERE id = ?
            """,
            (int(id),),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("attachment not found")
        if str(row["kind"]) == "image":
            raise ValueError("attachment is an image; use image_attachment(id)")
        return {
            "id": int(row["id"]),
            "logId": int(row["log_id"]),
            "kind": str(row["kind"]),
            "name": str(row["name"]),
            "sizeBytes": int(row["size_bytes"]),
            "mime": str(row["mime_type"]) if row["mime_type"] is not None else None,
            "width": int(row["width"]) if row["width"] is not None else None,
            "height": int(row["height"]) if row["height"] is not None else None,
            "content": str(row["content_text"]),
        }
    finally:
        conn.close()


@mcp.tool()
def image_attachment(id: int) -> Image:
    """Return image attachment content by id."""
    settings = load_settings()
    conn = _connect_readonly(settings.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT kind, blob_path
            FROM log_attachments
            WHERE id = ?
            """,
            (int(id),),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("attachment not found")
        if str(row["kind"]) != "image":
            raise ValueError("attachment is not an image")
        blob_path = str(row["blob_path"]) if row["blob_path"] is not None else ""
        if not blob_path:
            raise ValueError("image attachment file path is missing")

        path = settings.blob_dir / blob_path
        if not path.exists():
            raise ValueError("image attachment file not found")
        return Image(path=str(path))
    finally:
        conn.close()


def main() -> None:
    # Important: MCP stdio uses stdout for protocol messages.
    # Do not print to stdout here; logging is fine.
    mcp.run()


if __name__ == "__main__":
    main()

