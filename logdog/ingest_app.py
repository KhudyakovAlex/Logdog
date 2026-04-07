from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from logdog import __version__
from logdog.config import load_settings
from logdog.db import AttachmentRow, LogRow, LogdogDB
from logdog.models import AppInfo, AttachmentOut, AttachmentRef, LogIn, LogOut


settings = load_settings()
db = LogdogDB(
    settings.db_path,
    blob_dir=settings.blob_dir,
    db_max_bytes=settings.db_max_bytes,
    retention_target_fraction=settings.retention_target_fraction,
    retention_check_interval_s=settings.retention_check_interval_s,
)

app = FastAPI(title="Logdog ingest", version=__version__)

UI_DIR = Path(__file__).resolve().parent / "ui"
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _row_to_out(row: LogRow) -> LogOut:
    return LogOut(
        id=row.id,
        ts=row.ts,
        level=row.level,
        app=row.app,
        message=row.message,
        traceId=row.trace_id,
        fields=row.fields,
        attachments=[
            AttachmentRef(
                id=a.id,
                kind=a.kind,
                name=a.name,
                sizeBytes=a.size_bytes,
                mime=a.mime_type,
                width=a.width,
                height=a.height,
            )
            for a in row.attachments
        ],
    )


def _attachment_to_out(row: AttachmentRow) -> AttachmentOut:
    return AttachmentOut(
        id=row.id,
        logId=row.log_id,
        kind=row.kind,
        name=row.name,
        sizeBytes=row.size_bytes,
        mime=row.mime_type,
        width=row.width,
        height=row.height,
        content=row.content,
        downloadUrl=f"/api/attachments/{row.id}/file" if row.kind == "image" else None,
    )


def _cleanup_saved_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


def _prepare_attachment(attachment: object) -> tuple[dict[str, object], Path | None]:
    if not hasattr(attachment, "kind"):
        raise HTTPException(status_code=422, detail="invalid attachment payload")

    kind = getattr(attachment, "kind")
    name = getattr(attachment, "name")
    if kind != "image":
        return {
            "kind": kind,
            "name": name,
            "content": getattr(attachment, "content"),
        }, None

    mime = getattr(attachment, "mime")
    ext = IMAGE_EXTENSIONS.get(str(mime))
    if not ext:
        raise HTTPException(status_code=422, detail=f"unsupported image mime: {mime}")

    try:
        raw = base64.b64decode(str(getattr(attachment, "contentBase64")), validate=True)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid image base64: {e}") from e
    if not raw:
        raise HTTPException(status_code=422, detail="image attachment is empty")

    rel_path = Path(time.strftime("%Y%m%d")) / f"{int(time.time() * 1000)}-{secrets.token_hex(6)}{ext}"
    abs_path = settings.blob_dir / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(raw)
    return (
        {
            "kind": "image",
            "name": name,
            "size_bytes": len(raw),
            "mime_type": mime,
            "blob_path": rel_path.as_posix(),
            "width": getattr(attachment, "width"),
            "height": getattr(attachment, "height"),
        },
        abs_path,
    )


@app.on_event("shutdown")
def _shutdown() -> None:
    db.close()


@app.post("/logs", response_model=LogOut, status_code=201)
async def post_log(request: Request) -> LogOut:
    body = await request.body()
    if len(body) > settings.http_max_bytes:
        raise HTTPException(status_code=413, detail="payload too large")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    try:
        log_in = LogIn.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    saved_files: list[Path] = []
    try:
        prepared_attachments = []
        for attachment in log_in.attachments:
            prepared, saved_path = _prepare_attachment(attachment)
            prepared_attachments.append(prepared)
            if saved_path is not None:
                saved_files.append(saved_path)

        row = db.insert(
            ts=log_in.ts,
            level=log_in.level,
            app=log_in.app,
            message=log_in.message,
            trace_id=log_in.traceId,
            fields=log_in.fields,
            attachments=prepared_attachments,
        )
    except HTTPException:
        _cleanup_saved_files(saved_files)
        raise
    except Exception:
        _cleanup_saved_files(saved_files)
        raise

    return _row_to_out(row)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/api/recent", response_model=list[LogOut])
def api_recent(limit: int = 200, app: str | None = None, level: str | None = None) -> list[LogOut]:
    rows = db.recent(limit=limit, app=app, level=level)
    return [_row_to_out(r) for r in rows]


@app.get("/api/query", response_model=list[LogOut])
def api_query(
    limit: int = 200,
    app: str | None = None,
    level: str | None = None,
    since: int | None = None,
    until: int | None = None,
    contains: str | None = None,
    traceId: str | None = None,
) -> list[LogOut]:
    rows = db.query(
        limit=limit,
        app=app,
        level=level,
        since=since,
        until=until,
        contains=contains,
        trace_id=traceId,
    )
    return [_row_to_out(r) for r in rows]


@app.get("/api/apps", response_model=list[AppInfo])
def api_apps(limit: int = 500) -> list[AppInfo]:
    rows = db.apps(limit=limit)
    return [AppInfo.model_validate(r) for r in rows]


@app.get("/api/info")
def api_info() -> dict[str, str]:
    return {"name": app.title, "version": app.version}


@app.get("/api/attachments/{attachment_id}", response_model=AttachmentOut)
def api_attachment(attachment_id: int) -> AttachmentOut:
    row = db.attachment(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return _attachment_to_out(row)


@app.get("/api/attachments/{attachment_id}/file", include_in_schema=False)
def api_attachment_file(attachment_id: int) -> FileResponse:
    row = db.attachment(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    if row.kind != "image" or not row.blob_path:
        raise HTTPException(status_code=400, detail="attachment is not an image")

    path = settings.blob_dir / row.blob_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="attachment file not found")

    return FileResponse(path, media_type=row.mime_type or "application/octet-stream", filename=row.name)


@app.post("/api/purge")
def api_purge() -> dict[str, int]:
    deleted = db.purge()
    return {"deleted": int(deleted)}

