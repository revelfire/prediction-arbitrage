"""Structured logging configuration for the arb scanner."""

import logging
import sys

import structlog


def setup_logging(level: str = "INFO", json_format: bool = True) -> None:
    """Configure structlog with JSON or console output.

    Args:
        level: Log level string (e.g. "INFO", "DEBUG").
        json_format: If True, output JSON lines. If False, use console renderer.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_format:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Silence noisy HTTP request logs from httpx/httpcore
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(module: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger with the given module name.

    Args:
        module: The module name to bind to the logger.

    Returns:
        A structlog BoundLogger instance bound with the module name.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(module=module)
    return logger
