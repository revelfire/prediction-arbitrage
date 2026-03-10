"""Tests for Polymarket CSV trade history importer."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arb_scanner.backtesting.csv_importer import parse_csv, parse_csv_bytes
from arb_scanner.models.backtesting import TradeAction

VALID_CSV = (
    '"marketName","action","usdcAmount","tokenAmount",'
    '"tokenName","timestamp","hash"\n'
    '"BTC above $80k?","Buy","10.5","50","Yes","1772704987",'
    '"0xabc123"\n'
    '"BTC above $80k?","Sell","15.0","50","Yes","1772749535",'
    '"0xdef456"\n'
)

DEPOSIT_CSV = (
    '"marketName","action","usdcAmount","tokenAmount",'
    '"tokenName","timestamp","hash"\n'
    '"Deposited funds","Deposit","1000","1000","USDC","1772235109",'
    '"0x7e41dbbc"\n'
)


class TestParseValidCSV:
    def test_parses_buy_and_sell(self) -> None:
        trades = parse_csv_bytes(VALID_CSV.encode())
        assert len(trades) == 2
        assert trades[0].action == TradeAction.Buy
        assert trades[1].action == TradeAction.Sell

    def test_decimal_amounts(self) -> None:
        trades = parse_csv_bytes(VALID_CSV.encode())
        from decimal import Decimal

        assert trades[0].usdc_amount == Decimal("10.5")
        assert trades[0].token_amount == Decimal("50")

    def test_timestamp_conversion(self) -> None:
        trades = parse_csv_bytes(VALID_CSV.encode())
        assert trades[0].timestamp.tzinfo == UTC
        assert trades[0].timestamp == datetime.fromtimestamp(1772704987, tz=UTC)

    def test_market_name_and_hash(self) -> None:
        trades = parse_csv_bytes(VALID_CSV.encode())
        assert trades[0].market_name == "BTC above $80k?"
        assert trades[0].tx_hash == "0xabc123"


class TestDepositWithdraw:
    def test_deposit_parsed(self) -> None:
        trades = parse_csv_bytes(DEPOSIT_CSV.encode())
        assert len(trades) == 1
        assert trades[0].action == TradeAction.Deposit
        assert trades[0].token_name == "USDC"


class TestMalformedRows:
    def test_missing_column(self) -> None:
        bad_csv = '"marketName","action"\n"Test","Buy"\n'
        with pytest.raises((ValueError, KeyError)):
            parse_csv_bytes(bad_csv.encode())

    def test_bad_timestamp(self) -> None:
        bad_csv = (
            '"marketName","action","usdcAmount","tokenAmount",'
            '"tokenName","timestamp","hash"\n'
            '"Test","Buy","10","50","Yes","not_a_number","0xabc"\n'
        )
        with pytest.raises(ValueError, match="1 row"):
            parse_csv_bytes(bad_csv.encode())

    def test_unknown_action(self) -> None:
        bad_csv = (
            '"marketName","action","usdcAmount","tokenAmount",'
            '"tokenName","timestamp","hash"\n'
            '"Test","Transfer","10","50","Yes","1772704987","0xabc"\n'
        )
        with pytest.raises(ValueError, match="unknown action"):
            parse_csv_bytes(bad_csv.encode())


class TestNonStandardTokenNames:
    def test_tcu_horned_frogs(self) -> None:
        csv_data = (
            '"marketName","action","usdcAmount","tokenAmount",'
            '"tokenName","timestamp","hash"\n'
            '"Spread: Texas Tech","Buy","19.38","37.83",'
            '"TCU Horned Frogs","1772558001","0xc40f"\n'
        )
        trades = parse_csv_bytes(csv_data.encode())
        assert trades[0].token_name == "TCU Horned Frogs"


class TestEmptyFile:
    def test_empty_bytes(self) -> None:
        assert parse_csv_bytes(b"") == []

    def test_header_only(self) -> None:
        header = '"marketName","action","usdcAmount","tokenAmount","tokenName","timestamp","hash"\n'
        assert parse_csv_bytes(header.encode()) == []


class TestBOMHandling:
    def test_utf8_bom(self) -> None:
        bom = b"\xef\xbb\xbf"
        trades = parse_csv_bytes(bom + VALID_CSV.encode())
        assert len(trades) == 2


class TestRealCSV:
    """Parse the real Polymarket export and verify row counts."""

    REAL_CSV = Path("/Users/cmathias/Downloads/Polymarket-History-2026-03-06.csv")

    @pytest.mark.skipif(
        not Path("/Users/cmathias/Downloads/Polymarket-History-2026-03-06.csv").exists(),
        reason="Real CSV not present",
    )
    def test_parse_real_csv(self) -> None:
        trades = parse_csv(self.REAL_CSV)
        assert len(trades) == 37

        counts = Counter(t.action for t in trades)
        assert counts[TradeAction.Buy] == 28
        assert counts[TradeAction.Sell] == 8
        assert counts[TradeAction.Deposit] == 1

    @pytest.mark.skipif(
        not Path("/Users/cmathias/Downloads/Polymarket-History-2026-03-06.csv").exists(),
        reason="Real CSV not present",
    )
    def test_all_hashes_unique(self) -> None:
        trades = parse_csv(self.REAL_CSV)
        hashes = [t.tx_hash for t in trades]
        assert len(hashes) == len(set(hashes))


class TestFileNotFound:
    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_csv(Path("/nonexistent/trades.csv"))
