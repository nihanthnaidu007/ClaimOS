"""Request-ID middleware: accept or issue X-Request-ID and bind it to every log line."""

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Sanitized token check: reuse client-supplied IDs only when they are safe header
# values; anything else is replaced with a fresh ID.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

logger = structlog.get_logger("claimos.request")


class RequestIdMiddleware:
    """Pure-ASGI middleware (safe for SSE streaming) that:

    - reuses an incoming X-Request-ID when well-formed, else issues req_<uuid>,
    - echoes it back on the response,
    - binds it into structlog contextvars for the duration of the request,
    - emits one structured access-log line per request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next(
            (value for name, value in scope.get("headers", []) if name == b"x-request-id"),
            b"",
        ).decode("latin-1", errors="ignore")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else f"req_{uuid.uuid4().hex}"

        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_holder["status"],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            structlog.contextvars.clear_contextvars()
