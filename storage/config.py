"""Stage 3 limits. Operational capacity values are provisional."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class UploadConfig:
    root: Path
    secret: str
    max_bytes: int = 50 * 1024**3
    chunk_bytes: int = 8 * 1024**2
    block_bytes: int = 1024**2
    min_free_bytes: int = 10 * 1024**3
    quota_bytes: int = 100 * 1024**3
    max_chunks: int = 10000
    idle_seconds: int = 72 * 3600
    lifetime_seconds: int = 7 * 86400
    request_seconds: int = 120
    maintenance_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.root.is_absolute() or len(self.secret) < 32:
            raise ValueError("Absolute private root and stable random secret (32+ characters) required.")
        if any(v <= 0 for v in (self.max_bytes, self.chunk_bytes, self.block_bytes,
                self.quota_bytes, self.max_chunks, self.idle_seconds, self.lifetime_seconds,
                self.request_seconds, self.maintenance_seconds)) or self.min_free_bytes < 0:
            raise ValueError("Invalid upload resource limits.")
        if self.chunk_bytes > 8 * 1024**2 or self.block_bytes > 1024**2:
            raise ValueError("Chunk/block maxima are 8 MiB / 1 MiB in Stage 3.")


def from_env() -> tuple[UploadConfig, dict[str, str]]:
    config = UploadConfig(
        root=Path(os.environ["CVAT_UPLOAD_DIR"]), secret=os.environ["CVAT_UPLOAD_SECRET"],
        **{field: int(os.environ[env]) for field, env in {
            "max_bytes": "CVAT_MAX_UPLOAD_BYTES", "chunk_bytes": "CVAT_CHUNK_BYTES",
            "min_free_bytes": "CVAT_MIN_FREE_BYTES", "quota_bytes": "CVAT_STORAGE_QUOTA_BYTES",
            "idle_seconds": "CVAT_UPLOAD_IDLE_SECONDS", "lifetime_seconds": "CVAT_UPLOAD_LIFETIME_SECONDS",
        }.items() if env in os.environ},
    )
    owners = json.loads(os.environ["CVAT_UPLOAD_OWNER_KEYS_JSON"])
    validate_owners(owners)
    return config, owners


def validate_owners(owners: dict[str, str]) -> None:
    if not isinstance(owners, dict) or not owners or any(
        not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner)
        or not isinstance(key, str) or len(key) < 32 for owner, key in owners.items()
    ) or len(set(owners.values())) != len(owners):
        raise ValueError("Configure unique random owner keys (32+ characters), never public credentials.")
