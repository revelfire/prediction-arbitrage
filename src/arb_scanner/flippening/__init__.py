"""Flippening engine: mean reversion detection on live sports markets."""

from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.orchestrator import run_flip_watch
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector

__all__ = [
    "GameManager",
    "SignalGenerator",
    "SpikeDetector",
    "run_flip_watch",
]
