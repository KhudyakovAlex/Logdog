from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    blob_dir: Path
    http_max_bytes: int
    db_max_bytes: int
    retention_target_fraction: float
    retention_check_interval_s: int


def load_settings() -> Settings:
    db_path = Path(os.getenv("LOGDOG_DB_PATH", "./data/logdog.db"))
    blob_dir_raw = os.getenv("LOGDOG_BLOB_DIR")
    blob_dir = Path(blob_dir_raw) if blob_dir_raw else (db_path.parent / "blobs")
    http_max_bytes = _env_int("LOGDOG_HTTP_MAX_BYTES", 4_194_304)
    db_max_bytes = _env_int("LOGDOG_DB_MAX_BYTES", 1_073_741_824)
    retention_target_fraction = float(os.getenv("LOGDOG_DB_TARGET_FRACTION", "0.9"))
    retention_check_interval_s = _env_int("LOGDOG_RETENTION_CHECK_INTERVAL_S", 10)

    return Settings(
        db_path=db_path,
        blob_dir=blob_dir,
        http_max_bytes=http_max_bytes,
        db_max_bytes=db_max_bytes,
        retention_target_fraction=retention_target_fraction,
        retention_check_interval_s=retention_check_interval_s,
    )

