"""CLI contract tests for the four-command surface."""

import uuid
from copy import deepcopy

from click.testing import CliRunner

from ppt.cli import main
from ppt.holdings import Trade, TradeBatch
from ppt.prices import MarketSnapshot
from ppt.rebalance import PlanResult
from ppt.valuation import BalanceScore

PRICES = {
    "SPYM": 100.0,
    "AVUV": 100.0,
    "VGIT": 100.0,
    "GLDM": 100.0,
    "518880.SS": 1.0,
    "SGOV": 100.0,
    "511360.SS": 1.0,
}


def empty_ledger():
    return {"initialized_at": "2026-01-01T00:00:00+00:00", "batches": []}


class _Store:
    def __init__(self, ledger=None):
        self.ledger = deepcopy(ledger)
        self.initialized = False
        self.recorded = []

    def initialize(self):
        self.initialized = True
        self.ledger = empty_ledger()
        return "oss://test/backups/old.json"

    def load(self):
        return deepcopy(self.ledger)

    def record_batch(self, trades, usdcny):
        batch = TradeBatch(
            batch_id=str(uuid.uuid4()),
            executed_at="2026-01-02T00:00:00+00:00",
            usdcny=usdcny,
            trades=tuple(trades),
        )
        self.ledger["batches"].append(batch.to_dict())
        self.recorded.append(batch)
        return batch


def _market(history=None):
    return MarketSnapshot(PRICES, 1.0, history or {})


def test_help_exposes_exactly_four_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    command_lines = {
        line.strip().split()[0]
        for line in result.output.splitlines()
        if line.startswith("  ") and line.strip().split()[0] in {"init", "buy", "plan", "history"}
    }
    assert command_lines == {"init", "buy", "plan", "history"}
    for removed in ("sell", "status", "undo", "rebalance", "config", "clean-history"):
        assert removed not in result.output


def test_init_yes_backs_up_and_resets(monkeypatch):
    store = _Store(empty_ledger())
    monkeypatch.setattr("ppt.cli._store", lambda: store)

    result = CliRunner().invoke(main, ["init", "--yes"])

    assert result.exit_code == 0
    assert store.initialized is True
    assert "oss://test/backups/old.json" in result.output


def test_buy_records_signed_multi_ticker_batch(monkeypatch):
    store = _Store(empty_ledger())
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda: _market())

    result = CliRunner().invoke(
        main,
        ["buy", "SPYM#2@100", "518880.SS#100@1"],
    )

    assert result.exit_code == 0, result.output
    assert [(trade.ticker, trade.shares) for trade in store.recorded[0].trades] == [
        ("SPYM", 2),
        ("518880.SS", 100),
    ]
    assert "+2" in result.output
    assert "+100" in result.output


def test_buy_rejects_duplicate_ticker_before_storage(monkeypatch):
    store = _Store(empty_ledger())
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda: _market())

    result = CliRunner().invoke(main, ["buy", "SPYM#1@100", "SPYM#2@101"])

    assert result.exit_code != 0
    assert not store.recorded


def test_plan_outputs_one_exact_signed_buy_command_without_writing(monkeypatch):
    store = _Store(empty_ledger())
    score_before = BalanceScore(0.2, 0.4, 0.1, 0.2, 0.3)
    score_after = BalanceScore(0.1, 0.2, 0.05, 0.1, 0.2)
    planned = PlanResult(
        trades={"SPYM": 2, "SGOV": -1},
        before_score=score_before,
        after_score=score_after,
        buy_cost=200.0,
        sell_proceeds=100.0,
        unused_amount=900.0,
        final_holdings={ticker: 0 for ticker in PRICES},
        corridor_breached=True,
    )
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda: _market())
    monkeypatch.setattr("ppt.cli.build_plan", lambda *_args: planned)

    result = CliRunner().invoke(main, ["plan", "1000"])

    assert result.exit_code == 0, result.output
    assert "ppt buy 'SPYM#+2@100.0' 'SGOV#-1@100.0'" in result.output
    assert store.recorded == []
    assert store.ledger == empty_ledger()


def test_history_shows_summary_and_reverse_batches(monkeypatch):
    store = _Store(empty_ledger())
    first = store.record_batch((Trade("SPYM", 2, 80.0),), 1.0)
    second = store.record_batch((Trade("SPYM", -1, 120.0),), 1.0)
    assert first.executed_at == second.executed_at
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda: _market())

    result = CliRunner().invoke(main, ["history"])

    assert result.exit_code == 0, result.output
    assert "累计投入" in result.output
    assert "累计取出" in result.output
    assert "+2" in result.output
    assert "-1" in result.output
