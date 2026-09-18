"""Shared upload validation: one error contract for every upload surface.

Extracted from the server.py adjuster route so the F4 customer portal upload
(app.portal_uploads) enforces byte-identical validation instead of a drifted
copy. Both surfaces reject the same bad input the same way: 415 on a
non-allowlisted content type, 413 over the size cap, 422 for an empty body.
"""

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.storage import sanitize_filename


def upload_content_type_allowlist() -> set[str]:
    """The configured allowlist, lowercased and whitespace-stripped."""
    return {
        part.strip().lower()
        for part in settings.upload_allowed_content_types.split(",")
        if part.strip()
    }


async def read_validated_upload(file: UploadFile) -> tuple[bytes, str, str]:
    """Read one multipart upload, enforcing the shared error contract.

    Returns (content, content_type, sanitized_filename). Validation fails
    closed before any storage call: 415 on a non-allowlisted content type,
    413 over the size cap (checked while streaming, so an oversized body is
    rejected before it is fully read), and 422 for an empty body.
    """
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in upload_content_type_allowlist():
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported content type: {content_type or 'unknown'}. "
            f"Allowed: {settings.upload_allowed_content_types}",
        )

    max_bytes = settings.upload_max_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {max_bytes} byte upload cap",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    if total == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    return content, content_type, sanitize_filename(file.filename)
