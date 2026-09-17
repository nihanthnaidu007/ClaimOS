"""Storage providers for claim documents (spec Tier 3).

The interface is the seam: routes depend on `StorageProvider`, deployments
choose a driver. LocalFsProvider (default) writes inside a configured root
directory; an S3-compatible driver is the named upgrade path and plugs in
behind the same protocol without touching route code.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
MAX_FILENAME_LEN = 120


@dataclass(frozen=True)
class StoredDocument:
    """Result of persisting one document blob."""

    storage_key: str
    size_bytes: int
    content_type: str


class StorageProvider(Protocol):
    """Storage seam for claim documents — async, key-addressed."""

    async def save(self, *, key: str, content: bytes, content_type: str) -> StoredDocument:
        """Persist `content` under `key`; must reject key traversal escapes."""
        ...  # pragma: no cover

    async def open(self, storage_key: str) -> bytes:
        """Read back a stored blob by key."""
        ...  # pragma: no cover


def sanitize_filename(name: str | None) -> str:
    """Reduce an untrusted filename to a safe basename.

    Strips directory components (both separators), leading dots, and any
    character outside [A-Za-z0-9._-]; caps length while preserving the
    extension. Never returns a path separator, traversal, or empty string.
    """
    if not name:
        return "upload"
    # Basename only, whatever separator style arrived.
    base = str(name).replace("\\", "/").split("/")[-1]
    base = base.lstrip(".")
    base = _UNSAFE_CHARS.sub("_", base).strip("._")
    if not base:
        return "upload"
    stem, dot, ext = base.rpartition(".")
    if not dot or not ext:
        # No usable extension — treat the whole string as the stem.
        return base[:MAX_FILENAME_LEN]
    keep = MAX_FILENAME_LEN - len(ext) - 1
    return f"{stem[:keep]}.{ext}"


class LocalFsProvider:
    """Writes documents under a root directory, refusing key escapes."""

    def __init__(self, root: Path):
        self._root = Path(root)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ValueError(f"invalid storage key: {key!r}")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    async def save(self, *, key: str, content: bytes, content_type: str) -> StoredDocument:
        path = self._resolve(key)
        # Direct write, not asyncio.to_thread: uploads are capped (default
        # 10 MB) and to_thread on the TestClient's portal loop races loop
        # teardown in tests. Blocking for a bounded local write matches how
        # the rest of this codebase handles small sync work in routes.
        self._write(path, content)
        return StoredDocument(
            storage_key=key, size_bytes=len(content), content_type=content_type
        )

    async def open(self, storage_key: str) -> bytes:
        return self._resolve(storage_key).read_bytes()

    def _write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


_provider: StorageProvider | None = None


def get_provider() -> StorageProvider:
    """Process-wide provider, built from settings on first use."""
    global _provider
    if _provider is None:
        from app.config import settings

        _provider = LocalFsProvider(root=Path(settings.upload_dir))
    return _provider


def new_storage_key(claim_id: str, file_name: str) -> str:
    """Collision-proof key: one folder per claim, content never overwrites."""
    return f"{claim_id}/{uuid.uuid4().hex}-{file_name}"
