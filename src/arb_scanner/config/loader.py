"""Configuration loader with YAML parsing and environment variable interpolation."""

import os
import re
from pathlib import Path
from typing import Any

import structlog
import yaml

from arb_scanner.models.config import Settings

logger = structlog.get_logger()

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

_DEFAULT_CONFIG_PATH = "config.yaml"


def _interpolate_env_vars(value: object) -> object:
    """Recursively interpolate ``${VAR}`` and ``${VAR:default}`` patterns.

    Replaces occurrences with the corresponding environment variable value.
    Falls back to the inline default (after ``:``) or empty string.
    """
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(2) or ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_vars(item) for item in value]
    return value


def load_config(path: str | None = None) -> Settings:
    """Load application settings from a YAML configuration file.

    Resolution order for the config file path:
    1. Explicit ``path`` argument
    2. ``ARB_SCANNER_CONFIG`` environment variable
    3. Default ``config.yaml`` in the current working directory

    Environment variable references of the form ``${VAR}`` or
    ``${VAR:default}`` in string values are replaced with the corresponding
    ``os.environ`` value, falling back to the default (or empty string).
    """
    config_path = path or os.environ.get("ARB_SCANNER_CONFIG", _DEFAULT_CONFIG_PATH)
    resolved = Path(config_path)

    logger.info("loading_config", path=str(resolved))

    raw_text = resolved.read_text(encoding="utf-8")
    raw_data: Any = yaml.safe_load(raw_text)

    if not isinstance(raw_data, dict):
        raise ValueError(f"Expected a YAML mapping at top level, got {type(raw_data).__name__}")

    interpolated: Any = _interpolate_env_vars(raw_data)

    settings = Settings.model_validate(interpolated)
    logger.info("config_loaded", path=str(resolved))
    return settings
