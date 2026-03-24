"""Unit tests for the PriceRingBuffer."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.price_ring_buffer import (
    PriceRingBuffer,
    PriceTick,
    get_shared_buffer,
    set_shared_buffer,
)

_NOW = datetime.now(tz=UTC)


def _tick(
    market_id: str = "m1",
    yes_mid: str = "0.65",
    ts: datetime | None = None,
) -> PriceTick:
    return PriceTick(
        market_id=market_id,
        market_title="Test Market",
        category="nba",
        category_type="sport",
        yes_mid=Decimal(yes_mid),
        baseline_yes=Decimal("0.60"),
        deviation_pct=8.33,
        spread=Decimal("0.02"),
        timestamp=ts or _NOW,
        book_depth_bids=5,
        book_depth_asks=3,
    )


def test_push_and_get_latest() -> None:
    """Push a tick and verify get_latest returns it."""
    buf = PriceRingBuffer()
    t = _tick()
    buf.push(t)
    latest = buf.get_latest()
    assert "m1" in latest
    assert latest["m1"].yes_mid == Decimal("0.65")


def test_get_latest_returns_most_recent() -> None:
    """When multiple ticks exist, get_latest returns the last one."""
    buf = PriceRingBuffer()
    buf.push(_tick(yes_mid="0.60", ts=_NOW - timedelta(seconds=10)))
    buf.push(_tick(yes_mid="0.70", ts=_NOW))
    latest = buf.get_latest()
    assert latest["m1"].yes_mid == Decimal("0.70")


def test_get_history_order() -> None:
    """History returns ticks in chronological order (oldest first)."""
    buf = PriceRingBuffer()
    t1 = _tick(yes_mid="0.60", ts=_NOW - timedelta(seconds=20))
    t2 = _tick(yes_mid="0.65", ts=_NOW - timedelta(seconds=10))
    t3 = _tick(yes_mid="0.70", ts=_NOW)
    buf.push(t1)
    buf.push(t2)
    buf.push(t3)
    history = buf.get_history("m1")
    assert len(history) == 3
    assert history[0].yes_mid == Decimal("0.60")
    assert history[2].yes_mid == Decimal("0.70")


def test_maxlen_eviction() -> None:
    """Buffer evicts oldest ticks when maxlen is exceeded."""
    buf = PriceRingBuffer(max_per_market=3)
    for i in range(5):
        buf.push(
            _tick(
                yes_mid=f"0.{60 + i}",
                ts=_NOW + timedelta(seconds=i),
            )
        )
    history = buf.get_history("m1")
    assert len(history) == 3
    # Oldest surviving should be index 2 (0.62)
    assert history[0].yes_mid == Decimal("0.62")


def test_empty_buffer() -> None:
    """Empty buffer returns empty results."""
    buf = PriceRingBuffer()
    assert buf.get_latest() == {}
    assert buf.get_history("nonexistent") == []
    assert buf.market_count() == 0


def test_multiple_markets() -> None:
    """Buffer tracks multiple markets independently."""
    buf = PriceRingBuffer()
    buf.push(_tick(market_id="m1", yes_mid="0.60"))
    buf.push(_tick(market_id="m2", yes_mid="0.70"))
    assert buf.market_count() == 2
    latest = buf.get_latest()
    assert latest["m1"].yes_mid == Decimal("0.60")
    assert latest["m2"].yes_mid == Decimal("0.70")


def test_thread_safety() -> None:
    """Concurrent pushes do not corrupt the buffer."""
    buf = PriceRingBuffer(max_per_market=1000)
    errors: list[Exception] = []

    def push_batch(start: int) -> None:
        try:
            for i in range(100):
                buf.push(
                    _tick(
                        market_id="m1",
                        yes_mid=f"0.{50 + (start + i) % 50:02d}",
                        ts=_NOW + timedelta(seconds=start + i),
                    )
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=push_batch, args=(i * 100,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    history = buf.get_history("m1")
    assert len(history) == 400


def test_shared_buffer_singleton() -> None:
    """Module-level singleton accessors work correctly."""
    buf = PriceRingBuffer()
    set_shared_buffer(buf)
    assert get_shared_buffer() is buf


def test_none_baseline() -> None:
    """Tick with None baseline_yes is stored correctly."""
    tick = PriceTick(
        market_id="m1",
        market_title="No Baseline",
        category="crypto",
        category_type="crypto",
        yes_mid=Decimal("0.50"),
        baseline_yes=None,
        deviation_pct=0.0,
        spread=Decimal("0.01"),
        timestamp=_NOW,
        book_depth_bids=0,
        book_depth_asks=0,
    )
    buf = PriceRingBuffer()
    buf.push(tick)
    latest = buf.get_latest()
    assert latest["m1"].baseline_yes is None
