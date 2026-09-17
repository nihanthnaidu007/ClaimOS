"""structlog configuration: JSON logs in staging/production, console in development.

Stdlib loggers (agents.py still uses logging.getLogger) are routed through the
same ProcessorFormatter so every log line carries request_id and structured fields.
"""

import logging

import structlog

_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(environment: str = "development", level: int = logging.INFO) -> None:
    """Configure structlog + stdlib logging. Idempotent; safe to call per test session."""
    renderer: structlog.typing.Processor
    if environment == "development":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    # force=True replaces any pre-existing basicConfig handlers (idempotent re-init).
    logging.basicConfig(level=level, handlers=[handler], force=True)

    structlog.configure(
        processors=_SHARED_PROCESSORS
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
