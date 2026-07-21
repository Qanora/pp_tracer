"""Integration tests for CLI commands (§5)."""

import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner

from ppt.cli import _sell_ticker_for_bucket, main


def cli(*args):
    """Run ppt CLI and return CompletedProcess."""
    return subprocess.run(
        [sys.executable, "-m", "ppt"] + list(args),
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestCLIHelp:
    def test_help(self):
        r = cli("--help")
        assert r.returncode == 0
        assert "ppt" in r.stdout

    def test_plan_help(self):
        r = cli("plan", "--help")
        assert r.returncode == 0

    def test_status_help(self):
        r = cli("status", "--help")
        assert r.returncode == 0

    def test_buy_help(self):
        r = cli("buy", "--help")
        assert r.returncode == 0

    def test_sell_help(self):
        r = cli("sell", "--help")
        assert r.returncode == 0

    def test_undo_help(self):
        r = cli("undo", "--help")
        assert r.returncode == 0

    def test_history_help(self):
        r = cli("history", "--help")
        assert r.returncode == 0

    def test_config_help(self):
        r = cli("config", "--help")
        assert r.returncode == 0

    def test_init_help(self):
        r = cli("init", "--help")
        assert r.returncode == 0

    def test_custom_help(self):
        r = cli("help", "--help")
        assert r.returncode == 0


class TestCLIErrorHandling:
    def test_invalid_command(self):
        r = cli("nonexistent")
        assert r.returncode != 0

    def test_buy_no_args(self):
        r = cli("buy")
        assert r.returncode != 0

    def test_bad_format(self):
        # Requires initialized state first; format error caught at parse time
        r = cli("buy", "NOT#VALID")
        # Should fail because format is wrong (state isn't checked if args parse fails)
        assert "Error" in r.stderr or r.returncode != 0


class TestCLIStatusWithoutInit:
    """Status with no data → graceful empty state."""

    def test_status_no_data(self):
        # Global flags go before subcommand
        r = cli("--offline", "status")
        assert r.returncode in (0, 1)

    def test_plan_no_data(self):
        r = cli("--offline", "plan", "10000")
        assert r.returncode in (0, 1)


class _MemoryStore:
    def __init__(self):
        self.state = {
            "holdings": {
                "SPYM": 100.0,
                "AVUV": 0.0,
                "VGIT": 0.0,
                "GLDM": 0.0,
                "518880.SS": 0.0,
                "SGOV": 0.0,
                "511360.SS": 0.0,
            },
            "cash_in": 10000.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        }

    def load(self):
        return self.state

    def load_price_history(self):
        return []

    def add_transaction(self, txn):
        self.add_transactions([txn])

    def add_transactions(self, transactions):
        for txn in transactions:
            self._apply_transaction(txn)

    def _apply_transaction(self, txn):
        record = txn.to_dict()
        for trade in record["trades"]:
            ticker = trade["ticker"]
            delta = trade["shares"] if record["type"] == "buy" else -trade["shares"]
            self.state["holdings"][ticker] += delta
        self.state["transactions"].append(record)


class TestCLIRebalance:
    def test_sell_ticker_uses_market_value_with_usd_tie_break(self):
        prices = {"GLDM": 100.0, "518880.SS": 5.0}
        assert (
            _sell_ticker_for_bucket(
                "gold",
                {"GLDM": 1.0, "518880.SS": 200.0},
                prices,
                7.0,
            )
            == "518880.SS"
        )
        assert (
            _sell_ticker_for_bucket(
                "gold",
                {"GLDM": 1.0, "518880.SS": 140.0},
                prices,
                7.0,
            )
            == "GLDM"
        )

    def test_full_rebalance_records_internal_transactions_without_cash_flow(self):
        store = _MemoryStore()
        prices = {
            "SPYM": 100.0,
            "AVUV": 100.0,
            "VGIT": 100.0,
            "GLDM": 100.0,
            "518880.SS": 5.0,
            "SGOV": 100.0,
            "511360.SS": 1.0,
        }
        runner = CliRunner()

        with (
            patch("ppt.cli.HoldingsStore", return_value=store),
            patch("ppt.cli.fetch_prices", return_value={"prices": prices, "usdcny": 1.0}),
        ):
            result = runner.invoke(main, ["--yes", "rebalance", "--full"])

        assert result.exit_code == 0, result.output
        assert store.state["transactions"]
        assert all(txn["internal"] is True for txn in store.state["transactions"])
        assert store.state["cash_in"] == 10000.0
        assert store.state["cash_out"] == 0.0
        sells = sum(
            trade["shares"] * trade["price"]
            for txn in store.state["transactions"]
            if txn["type"] == "sell"
            for trade in txn["trades"]
        )
        buys = sum(
            trade["shares"] * trade["price"]
            for txn in store.state["transactions"]
            if txn["type"] == "buy"
            for trade in txn["trades"]
        )
        assert buys <= sells

    def test_full_rebalance_sells_the_held_alternate_ticker(self):
        store = _MemoryStore()
        store.state["holdings"]["SPYM"] = 0.0
        store.state["holdings"]["AVUV"] = 100.0
        prices = {ticker: 100.0 for ticker in store.state["holdings"]}
        runner = CliRunner()

        with (
            patch("ppt.cli.HoldingsStore", return_value=store),
            patch("ppt.cli.fetch_prices", return_value={"prices": prices, "usdcny": 1.0}),
        ):
            result = runner.invoke(main, ["--yes", "rebalance", "--full"])

        assert result.exit_code == 0, result.output
        sold = [
            trade
            for txn in store.state["transactions"]
            if txn["type"] == "sell"
            for trade in txn["trades"]
        ]
        assert sold and sold[0]["ticker"] == "AVUV"
        assert all(shares >= 0 for shares in store.state["holdings"].values())
