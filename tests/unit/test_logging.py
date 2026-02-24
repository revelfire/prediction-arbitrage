"""Unit tests for the logging configuration module."""

from __future__ import annotations

import logging

from arb_scanner.utils.logging import get_logger, setup_logging


class TestSetupLogging:
    """Tests for setup_logging."""

    def test_json_format(self) -> None:
        """JSON format configures structlog without errors."""
        setup_logging(level="DEBUG", json_format=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) > 0

    def test_console_format(self) -> None:
        """Console format configures structlog without errors."""
        setup_logging(level="WARNING", json_format=False)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_default_level(self) -> None:
        """Default level is INFO."""
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO


class TestGetLogger:
    """Tests for get_logger."""

    def test_returns_logger(self) -> None:
        """Returns a structlog logger instance."""
        log = get_logger("test_module")
        # structlog.get_logger returns a lazy proxy; verify it is usable
        assert hasattr(log, "info")
        assert hasattr(log, "error")
