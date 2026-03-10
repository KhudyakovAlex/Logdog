from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from logdog.config import load_settings
from logdog.db import LogRow, LogdogDB
from logdog.models import AppInfo, LogIn, LogOut


settings = load_settings()
db = LogdogDB(
    settings.db_path,
    db_max_bytes=settings.db_max_bytes,
    retention_target_fraction=settings.retention_target_fraction,
    retention_check_interval_s=settings.retention_check_interval_s,
)

app = FastAPI(title="Logdog ingest", version="0.1.0")

UI_DIR = Path(__file__).resolve().parent / "ui"
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


def _row_to_out(row: LogRow) -> LogOut:
    return LogOut(
        id=row.id,
        ts=row.ts,
        level=row.level,
        app=row.app,
        message=row.message,
        traceId=row.trace_id,
        fields=row.fields,
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

    row = db.insert(
        ts=log_in.ts,
        level=log_in.level,
        app=log_in.app,
        message=log_in.message,
        trace_id=log_in.traceId,
        fields=log_in.fields,
    )

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

