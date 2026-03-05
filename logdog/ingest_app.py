from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from logdog.config import load_settings
from logdog.db import LogdogDB
from logdog.models import LogIn, LogOut


settings = load_settings()
db = LogdogDB(
    settings.db_path,
    db_max_bytes=settings.db_max_bytes,
    retention_target_fraction=settings.retention_target_fraction,
    retention_check_interval_s=settings.retention_check_interval_s,
)

app = FastAPI(title="Logdog ingest", version="0.1.0")


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

    return LogOut(
        id=row.id,
        ts=row.ts,
        level=row.level,
        app=row.app,
        message=row.message,
        traceId=row.trace_id,
        fields=row.fields,
    )

