"""T018 - Unit tests for the YAML config loader."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from arb_scanner.config.loader import load_config
from arb_scanner.models.config import Settings

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _valid_yaml() -> str:
    """Return a minimal valid YAML config string."""
    return """\
storage:
  database_url: "postgresql://localhost/testdb"
fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
"""


def _full_yaml() -> str:
    """Return a full YAML config string with all sections."""
    return """\
venues:
  polymarket:
    gamma_base_url: "https://gamma-api.polymarket.com"
    clob_base_url: "https://clob.polymarket.com"
    enabled: true
    rate_limit_per_sec: 10
  kalshi:
    base_url: "https://api.elections.kalshi.com/trade-api/v2"
    enabled: true
    rate_limit_per_sec: 20
    max_relevant_events: 80
    rate_limit_cooldown_seconds: 4.5

claude:
  model: "claude-sonnet-4-20250514"
  api_key: "test-key"
  batch_size: 5
  match_cache_ttl_hours: 24

scanning:
  interval_seconds: 60
  mode: "continuous"

arb_thresholds:
  min_net_spread_pct: 0.02
  min_size_usd: 10
  thin_liquidity_threshold: 50

notifications:
  slack_webhook: ""
  discord_webhook: ""
  enabled: true
  min_spread_to_notify_pct: 0.02

storage:
  database_url: "postgresql://localhost/testdb"

logging:
  level: "INFO"
  format: "json"

fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
    fee_cap: 0.07
"""


def _write_config(tmp_path: Path, content: str) -> Path:
    """Write config content to a temporary YAML file and return the path."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(content, encoding="utf-8")
    return config_file


# ---------------------------------------------------------------------------
# Successful loading
# ---------------------------------------------------------------------------


class TestLoadConfigSuccess:
    """Tests for successful YAML config loading."""

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        """Verify loading a minimal config produces a valid Settings."""
        cfg_path = _write_config(tmp_path, _valid_yaml())
        settings = load_config(str(cfg_path))
        assert isinstance(settings, Settings)
        assert settings.storage.database_url == "postgresql://localhost/testdb"

    def test_load_full_config(self, tmp_path: Path) -> None:
        """Verify loading a full config populates all sections."""
        cfg_path = _write_config(tmp_path, _full_yaml())
        settings = load_config(str(cfg_path))
        assert settings.claude.api_key == "test-key"
        assert settings.venues.kalshi.rate_limit_per_sec == 20
        assert settings.venues.kalshi.max_relevant_events == 80
        assert settings.venues.kalshi.rate_limit_cooldown_seconds == 4.5
        assert settings.fees.kalshi.fee_cap is not None

    def test_defaults_applied(self, tmp_path: Path) -> None:
        """Verify default values are applied for missing optional sections."""
        cfg_path = _write_config(tmp_path, _valid_yaml())
        settings = load_config(str(cfg_path))
        assert settings.venues.polymarket.enabled is True
        assert settings.scanning.interval_seconds == 60
        assert settings.claude.batch_size == 5
        assert settings.logging.level == "INFO"
        assert settings.arb_thresholds.min_size_usd is not None


# ---------------------------------------------------------------------------
# Environment variable interpolation
# ---------------------------------------------------------------------------


class TestEnvVarInterpolation:
    """Tests for ${ENV_VAR} interpolation in config values."""

    def test_single_env_var(self, tmp_path: Path) -> None:
        """Verify a single ${VAR} is replaced from the environment."""
        yaml_content = """\
storage:
  database_url: "${TEST_DB_URL}"
fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
"""
        cfg_path = _write_config(tmp_path, yaml_content)
        with patch.dict(os.environ, {"TEST_DB_URL": "postgresql://envhost/envdb"}):
            settings = load_config(str(cfg_path))
        assert settings.storage.database_url == "postgresql://envhost/envdb"

    def test_multiple_env_vars(self, tmp_path: Path) -> None:
        """Verify multiple ${VAR} references in different fields are replaced."""
        yaml_content = """\
storage:
  database_url: "${TEST_DB_URL}"
notifications:
  slack_webhook: "${SLACK_URL}"
  discord_webhook: "${DISCORD_URL}"
fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
"""
        cfg_path = _write_config(tmp_path, yaml_content)
        env_vars = {
            "TEST_DB_URL": "postgresql://localhost/db",
            "SLACK_URL": "https://hooks.slack.com/test",
            "DISCORD_URL": "https://discord.com/api/test",
        }
        with patch.dict(os.environ, env_vars):
            settings = load_config(str(cfg_path))
        assert settings.notifications.slack_webhook == "https://hooks.slack.com/test"
        assert settings.notifications.discord_webhook == "https://discord.com/api/test"

    def test_missing_env_var_defaults_empty(self, tmp_path: Path) -> None:
        """Verify a missing env var resolves to empty string."""
        yaml_content = """\
storage:
  database_url: "postgresql://localhost/db"
notifications:
  slack_webhook: "${NONEXISTENT_VAR_12345}"
fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
"""
        cfg_path = _write_config(tmp_path, yaml_content)
        env_clean = {k: v for k, v in os.environ.items() if k != "NONEXISTENT_VAR_12345"}
        with patch.dict(os.environ, env_clean, clear=True):
            settings = load_config(str(cfg_path))
        assert settings.notifications.slack_webhook == ""

    def test_env_var_in_nested_list(self, tmp_path: Path) -> None:
        """Verify env var interpolation works in list values too."""
        yaml_content = """\
storage:
  database_url: "postgresql://localhost/db"
fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
claude:
  api_key: "${TEST_CLAUDE_KEY}"
"""
        cfg_path = _write_config(tmp_path, yaml_content)
        with patch.dict(os.environ, {"TEST_CLAUDE_KEY": "sk-ant-test123"}):
            settings = load_config(str(cfg_path))
        assert settings.claude.api_key == "sk-ant-test123"

    def test_auto_exit_watchdog_env_overrides_apply(self, tmp_path: Path) -> None:
        """Explicit env vars override auto-exec exit watchdog settings."""
        yaml_content = """\
storage:
  database_url: "postgresql://localhost/db"
fees:
  polymarket:
    taker_fee_pct: 0.0
    fee_model: "on_winnings"
  kalshi:
    taker_fee_pct: 0.07
    fee_model: "per_contract"
auto_execution:
  enabled: true
  mode: "auto"
  exit_pending_stale_seconds: 30
  exit_retry_max_attempts: 4
  exit_retry_reprice_pct: 0.02
  exit_retry_min_price: 0.01
"""
        cfg_path = _write_config(tmp_path, yaml_content)
        env = {
            "AUTO_FAILURE_PROBE_COOLDOWN_MIN_SECONDS": "20",
            "AUTO_FAILURE_PROBE_COOLDOWN_MAX_SECONDS": "240",
            "AUTO_FAILURE_PROBE_BACKOFF_MULTIPLIER": "1.8",
            "AUTO_FAILURE_PROBE_RECOVERY_MULTIPLIER": "0.70",
            "AUTO_EXIT_PENDING_STALE_SECONDS": "25",
            "AUTO_EXIT_RETRY_MAX_ATTEMPTS": "5",
            "AUTO_EXIT_REPRICE_PCT": "0.03",
            "AUTO_EXIT_RETRY_MIN_PRICE": "0.02",
        }
        with patch.dict(os.environ, env):
            settings = load_config(str(cfg_path))
        assert settings.auto_execution.failure_probe_cooldown_min_seconds == 20.0
        assert settings.auto_execution.failure_probe_cooldown_max_seconds == 240.0
        assert settings.auto_execution.failure_probe_backoff_multiplier == 1.8
        assert settings.auto_execution.failure_probe_recovery_multiplier == 0.70
        assert settings.auto_execution.exit_pending_stale_seconds == 25
        assert settings.auto_execution.exit_retry_max_attempts == 5
        assert settings.auto_execution.exit_retry_reprice_pct == 0.03
        assert settings.auto_execution.exit_retry_min_price == 0.02
        assert settings.auto_execution.flip_overrides["failure_probe_cooldown_min_seconds"] == 20.0
        assert settings.auto_execution.flip_overrides["failure_probe_cooldown_max_seconds"] == 240.0
        assert settings.auto_execution.flip_overrides["exit_pending_stale_seconds"] == 25
        assert settings.auto_execution.flip_overrides["exit_retry_max_attempts"] == 5


# ---------------------------------------------------------------------------
# Config path resolution
# ---------------------------------------------------------------------------


class TestConfigPathResolution:
    """Tests for config path resolution via env var and defaults."""

    def test_arb_scanner_config_env_override(self, tmp_path: Path) -> None:
        """Verify ARB_SCANNER_CONFIG env var overrides the default path."""
        cfg_path = _write_config(tmp_path, _valid_yaml())
        with patch.dict(os.environ, {"ARB_SCANNER_CONFIG": str(cfg_path)}):
            settings = load_config()
        assert isinstance(settings, Settings)

    def test_explicit_path_takes_priority(self, tmp_path: Path) -> None:
        """Verify an explicit path argument takes priority over env var."""
        explicit_cfg = _write_config(tmp_path, _valid_yaml())
        decoy = tmp_path / "decoy.yaml"
        decoy.write_text(_full_yaml(), encoding="utf-8")
        with patch.dict(os.environ, {"ARB_SCANNER_CONFIG": str(decoy)}):
            settings = load_config(str(explicit_cfg))
        # The minimal config has no claude.api_key set, so it defaults to ""
        assert settings.claude.api_key == ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestLoadConfigErrors:
    """Tests for error handling in config loading."""

    def test_missing_required_fields_raises_validation_error(self, tmp_path: Path) -> None:
        """Verify missing required fields (storage, fees) raise ValidationError."""
        incomplete_yaml = """\
venues:
  polymarket:
    enabled: true
"""
        cfg_path = _write_config(tmp_path, incomplete_yaml)
        with pytest.raises(ValidationError):
            load_config(str(cfg_path))

    def test_nonexistent_file_raises_error(self) -> None:
        """Verify loading from a nonexistent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_invalid_yaml_raises_error(self, tmp_path: Path) -> None:
        """Verify malformed YAML raises an appropriate error."""
        bad_yaml = "{{{{invalid yaml :::::\n  - broken: ["
        cfg_path = _write_config(tmp_path, bad_yaml)
        with pytest.raises(Exception):
            load_config(str(cfg_path))

    def test_non_mapping_yaml_raises_value_error(self, tmp_path: Path) -> None:
        """Verify YAML that is a list (not mapping) raises ValueError."""
        list_yaml = "- item1\n- item2\n"
        cfg_path = _write_config(tmp_path, list_yaml)
        with pytest.raises(ValueError, match="Expected a YAML mapping"):
            load_config(str(cfg_path))

    def test_scalar_yaml_raises_value_error(self, tmp_path: Path) -> None:
        """Verify YAML that is a scalar (not mapping) raises ValueError."""
        scalar_yaml = "just a string"
        cfg_path = _write_config(tmp_path, scalar_yaml)
        with pytest.raises(ValueError, match="Expected a YAML mapping"):
            load_config(str(cfg_path))
