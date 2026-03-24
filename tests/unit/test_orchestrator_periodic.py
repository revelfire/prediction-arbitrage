"""Tests for periodic task execution during stream timeout."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening.orchestrator import _LoopTimers


class TestOrchestratorPeriodicTimers:
    """Verify _LoopTimers includes all required fields."""

    def test_loop_timers_has_reconnect(self) -> None:
        """_LoopTimers includes last_reconnect field."""
        timers = _LoopTimers()
        assert hasattr(timers, "last_reconnect")
        assert timers.last_reconnect == 0.0

    def test_loop_timers_has_all_fields(self) -> None:
        """_LoopTimers has all required timer fields."""
        timers = _LoopTimers()
        assert hasattr(timers, "last_discovery")
        assert hasattr(timers, "last_persist")
        assert hasattr(timers, "last_tick_flush")
        assert hasattr(timers, "last_alert_flush")
        assert hasattr(timers, "stall_count")
        assert hasattr(timers, "last_stall_received")
        assert hasattr(timers, "last_drift_alert")
        assert hasattr(timers, "last_pending_exit_reconcile")
        assert timers.stall_count == 0


class TestPeriodicTaskExecution:
    """Verify periodic work runs during stream timeout."""

    @pytest.mark.asyncio
    async def test_timeout_triggers_periodic_tasks(self) -> None:
        """Stream timeout invokes _run_periodic_tasks."""
        from arb_scanner.flippening.orchestrator import _run_periodic_tasks

        timers = _LoopTimers()
        # Set old timestamps so periodic tasks trigger
        timers.last_tick_flush = 0.0
        timers.last_alert_flush = 0.0
        timers.last_discovery = 0.0
        timers.last_pending_exit_reconcile = 0.0

        config = MagicMock()
        config.flippening.tick_flush_interval_seconds = 1
        config.flippening.alert_batch_interval_seconds = 1
        config.flippening.ws_telemetry_persist_interval_seconds = 999

        tick_buffer = AsyncMock()
        alert_buffer = AsyncMock()
        book_cache = MagicMock()
        telemetry = MagicMock()
        telemetry.should_log.return_value = False

        with (
            patch(
                "arb_scanner.flippening.orchestrator.time.monotonic",
                return_value=999_999.0,
            ),
            patch(
                "arb_scanner.flippening.orchestrator._periodic_discovery",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_discovery,
            patch(
                "arb_scanner.flippening.orchestrator.check_telemetry",
                new_callable=AsyncMock,
                return_value=(0, 0, 0.0, 0.0),
            ),
            patch(
                "arb_scanner.flippening.orchestrator.reconcile_pending_db_positions",
                new_callable=AsyncMock,
            ) as mock_reconcile,
            patch(
                "arb_scanner.flippening.orchestrator.sweep_overtime_signals",
                new_callable=AsyncMock,
            ),
            patch(
                "arb_scanner.flippening.orchestrator.sweep_overtime_db_positions",
                new_callable=AsyncMock,
            ),
            patch(
                "arb_scanner.flippening.orchestrator.reconcile_open_positions_with_exchange",
                new_callable=AsyncMock,
            ),
            patch(
                "arb_scanner.flippening.orchestrator.retry_active_signals",
                new_callable=AsyncMock,
            ),
        ):
            await _run_periodic_tasks(
                timers,
                config,
                {},
                AsyncMock(),
                MagicMock(),
                MagicMock(),
                None,
                None,
                False,
                tick_buffer,
                alert_buffer,
                book_cache,
                telemetry,
            )

        tick_buffer.flush.assert_called()
        alert_buffer.flush.assert_called()
        mock_discovery.assert_called_once()
        mock_reconcile.assert_called_once()
