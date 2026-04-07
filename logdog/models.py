from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


Level = Literal["debug", "info", "warn", "error"]
AttachmentKind = Literal["md", "json", "image"]
ImageMime = Literal["image/jpeg", "image/png", "image/webp"]


def _parse_ts_to_ms(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return int(s)
        # Accept ISO8601 (best-effort).
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    raise TypeError("Invalid ts type")


class LogIn(BaseModel):
    ts: Optional[int] = Field(default=None, description="epoch milliseconds (optional)")
    level: Level
    app: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=200_000)
    traceId: Optional[str] = Field(default=None, min_length=1, max_length=200)
    fields: Optional[dict[str, Any]] = None
    attachments: list["AttachmentIn"] = Field(default_factory=list, max_length=32)

    @field_validator("ts", mode="before")
    @classmethod
    def _coerce_ts(cls, v: Any) -> Any:
        return _parse_ts_to_ms(v)


class AttachmentIn(BaseModel):
    kind: AttachmentKind
    name: str = Field(min_length=1, max_length=300)
    content: Optional[str] = Field(default=None, min_length=1)
    mime: Optional[ImageMime] = None
    contentBase64: Optional[str] = Field(default=None, min_length=1)
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_content(self) -> "AttachmentIn":
        if self.kind == "image":
            if self.content is not None:
                raise ValueError("image attachment must not include content")
            if not self.mime:
                raise ValueError("image attachment requires mime")
            if not self.contentBase64:
                raise ValueError("image attachment requires contentBase64")
            try:
                decoded = base64.b64decode(self.contentBase64, validate=True)
            except Exception as e:
                raise ValueError(f"invalid image base64 content: {e}") from e
            if not decoded:
                raise ValueError("image attachment content is empty")
            return self

        if self.contentBase64 is not None:
            raise ValueError("text attachment must not include contentBase64")
        if self.mime is not None:
            raise ValueError("text attachment must not include mime")
        if self.width is not None or self.height is not None:
            raise ValueError("text attachment must not include width/height")
        if self.content is None:
            raise ValueError("text attachment requires content")

        if self.kind == "json":
            try:
                json.loads(self.content)
            except Exception as e:
                raise ValueError(f"invalid json attachment content: {e}") from e
        return self


class AttachmentRef(BaseModel):
    id: int
    kind: AttachmentKind
    name: str
    sizeBytes: int
    mime: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class AttachmentOut(AttachmentRef):
    logId: int
    content: Optional[str] = None
    downloadUrl: Optional[str] = None


class LogOut(BaseModel):
    id: int
    ts: int
    level: str
    app: str
    message: str
    traceId: Optional[str] = None
    fields: Optional[dict[str, Any]] = None
    attachments: list[AttachmentRef] = Field(default_factory=list)


class AppInfo(BaseModel):
    app: str
    count: int
    lastTs: int

