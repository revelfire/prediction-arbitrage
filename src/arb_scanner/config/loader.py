"""Configuration loader with YAML parsing and environment variable interpolation."""

import os
import re
from pathlib import Path
from typing import Any, Callable

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

    if not resolved.exists() and path is None and "ARB_SCANNER_CONFIG" not in os.environ:
        logger.info("config_file_missing_using_env_defaults", path=str(resolved))
        return _settings_from_env()

    raw_text = resolved.read_text(encoding="utf-8")
    raw_data: Any = yaml.safe_load(raw_text)

    if not isinstance(raw_data, dict):
        raise ValueError(f"Expected a YAML mapping at top level, got {type(raw_data).__name__}")

    interpolated: Any = _interpolate_env_vars(raw_data)

    settings = Settings.model_validate(interpolated)
    _apply_auto_exec_env_overrides(settings)
    logger.info("config_loaded", path=str(resolved))
    return settings


def _settings_from_env() -> Settings:
    """Build Settings from environment variables when no config file exists.

    Uses DATABASE_URL from the environment and default fee schedules.
    Useful for CI and minimal deployments.
    """
    from decimal import Decimal

    from arb_scanner.models.config import FeeSchedule, FeesConfig, StorageConfig

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise ValueError("No config.yaml found and DATABASE_URL not set")

    settings = Settings(
        storage=StorageConfig(database_url=db_url),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="percent_winnings"),
            kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
        ),
    )
    _apply_auto_exec_env_overrides(settings)
    return settings


def _apply_auto_exec_env_overrides(settings: Settings) -> None:
    """Apply explicit env overrides for auto-exec watchdog/probe controls."""
    _set_env_float(
        "AUTO_FAILURE_PROBE_COOLDOWN_MIN_SECONDS",
        lambda v: _set_auto_exec_override(settings, "failure_probe_cooldown_min_seconds", v),
    )
    _set_env_float(
        "AUTO_FAILURE_PROBE_COOLDOWN_MAX_SECONDS",
        lambda v: _set_auto_exec_override(settings, "failure_probe_cooldown_max_seconds", v),
    )
    _set_env_float(
        "AUTO_FAILURE_PROBE_BACKOFF_MULTIPLIER",
        lambda v: _set_auto_exec_override(settings, "failure_probe_backoff_multiplier", v),
    )
    _set_env_float(
        "AUTO_FAILURE_PROBE_RECOVERY_MULTIPLIER",
        lambda v: _set_auto_exec_override(settings, "failure_probe_recovery_multiplier", v),
    )
    _set_env_int(
        "AUTO_EXIT_PENDING_STALE_SECONDS",
        lambda v: _set_auto_exec_override(settings, "exit_pending_stale_seconds", v),
    )
    _set_env_int(
        "AUTO_EXIT_RETRY_MAX_ATTEMPTS",
        lambda v: _set_auto_exec_override(settings, "exit_retry_max_attempts", v),
    )
    _set_env_float(
        "AUTO_EXIT_REPRICE_PCT",
        lambda v: _set_auto_exec_override(settings, "exit_retry_reprice_pct", v),
    )
    _set_env_float(
        "AUTO_EXIT_RETRY_MIN_PRICE",
        lambda v: _set_auto_exec_override(settings, "exit_retry_min_price", v),
    )


def _set_env_int(name: str, setter: Callable[[int], None]) -> None:
    """Parse integer env var and apply via setter when present."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return
    try:
        setter(int(raw.strip()))
    except ValueError:
        logger.warning("invalid_env_override_int", var=name, value=raw)


def _set_env_float(name: str, setter: Callable[[float], None]) -> None:
    """Parse float env var and apply via setter when present."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return
    try:
        setter(float(raw.strip()))
    except ValueError:
        logger.warning("invalid_env_override_float", var=name, value=raw)


def _set_auto_exec_override(settings: Settings, field: str, value: int | float) -> None:
    """Apply value to root auto_exec config and flip_overrides."""
    setattr(settings.auto_execution, field, value)
    settings.auto_execution.flip_overrides[field] = value
