"""CLI contract tests for the five-command surface."""

import uuid
from copy import deepcopy
from datetime import date, timedelta

from click.testing import CliRunner

from ppt.cli import main
from ppt.holdings import Trade, TradeBatch
from ppt.prices import MarketDataError, MarketSnapshot
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


def _market(history=None, usdcny_history=None):
    return MarketSnapshot(PRICES, 1.0, history or {}, usdcny_history or {})


def test_help_exposes_exactly_five_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    command_lines = {
        line.strip().split()[0]
        for line in result.output.splitlines()
        if line.startswith("  ")
        and line.strip().split()[0] in {"init", "buy", "status", "plan", "history"}
    }
    assert command_lines == {"init", "buy", "status", "plan", "history"}
    for removed in ("sell", "undo", "rebalance", "config", "clean-history"):
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
    monkeypatch.setattr("ppt.cli.fetch_market", lambda **_kwargs: _market())

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
    monkeypatch.setattr("ppt.cli.fetch_market", lambda **_kwargs: _market())

    result = CliRunner().invoke(main, ["buy", "SPYM#1@100", "SPYM#2@101"])

    assert result.exit_code != 0
    assert not store.recorded


def test_status_shows_complete_current_snapshot_without_writing(monkeypatch):
    store = _Store(empty_ledger())
    store.record_batch(
        (
            Trade("SPYM", 50, 100.0),
            Trade("AVUV", 50, 100.0),
            Trade("VGIT", 100, 100.0),
            Trade("GLDM", 50, 100.0),
            Trade("518880.SS", 5000, 1.0),
            Trade("SGOV", 50, 100.0),
            Trade("511360.SS", 5000, 1.0),
        ),
        1.0,
    )
    store.recorded.clear()
    before = deepcopy(store.ledger)
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda **_kwargs: _market())

    result = CliRunner().invoke(main, ["status"])

    assert result.exit_code == 0, result.output
    for ticker in PRICES:
        assert ticker in result.output
    assert "¥40,000.00" in result.output
    assert "四桶配置" in result.output
    assert "币种配置" in result.output
    assert "75.00%" in result.output
    assert "25.00%" in result.output
    assert "100%/100%" in result.output
    assert "…" not in result.output
    assert "三级最大偏差" in result.output
    assert "累计盈亏" in result.output
    assert "简单收益率" in result.output
    assert "30 日持仓回测" in result.output
    assert "趋势与相关性提示" in result.output
    assert "ppt buy" not in result.output
    assert store.ledger == before
    assert store.recorded == []


def test_status_handles_empty_initialized_ledger_without_fake_weights(monkeypatch):
    store = _Store(empty_ledger())
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda **_kwargs: _market())

    result = CliRunner().invoke(main, ["status"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, result.output
    for ticker in PRICES:
        assert ticker in result.output
    assert "¥0.00" in result.output
    assert "暂无持仓" in result.output
    assert "—" in result.output
    assert "不可计算" in result.output


def test_status_shows_current_profit_and_fixed_holdings_drawdown(monkeypatch):
    store = _Store(empty_ledger())
    store.record_batch((Trade("SPYM", 1, 100.0),), 1.0)
    store.recorded.clear()
    start = date(2026, 1, 1)
    days = [start + timedelta(days=index) for index in range(30)]
    history_prices = [100.0, 80.0, *([90.0] * 28)]
    prices = {**PRICES, "SPYM": 90.0}
    market = MarketSnapshot(
        prices,
        1.0,
        {"SPYM": dict(zip(days, history_prices, strict=True))},
        {day: 1.0 for day in days},
    )
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda **_kwargs: market)

    result = CliRunner().invoke(main, ["status"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, result.output
    assert "¥-10.00" in result.output
    assert "-10.00%" in result.output
    assert "-20.00%" in result.output
    assert "12.50%" in result.output
    assert "共同有效交易日：30/30" in result.output


def test_status_diagnostics_use_actual_current_bucket_holdings(monkeypatch):
    store = _Store(empty_ledger())
    store.record_batch((Trade("518880.SS", 100, 1.0),), 1.0)
    store.recorded.clear()
    start = date(2026, 1, 1)
    days = [start + timedelta(days=index) for index in range(30)]
    held_gold = [100.0 * 0.98**index for index in range(30)]
    unused_proxy = [100.0 * 1.02**index for index in range(30)]
    market = MarketSnapshot(
        PRICES,
        1.0,
        {
            "518880.SS": dict(zip(days, held_gold, strict=True)),
            "GLDM": dict(zip(days, unused_proxy, strict=True)),
        },
        {},
    )
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr("ppt.cli.fetch_market", lambda **_kwargs: market)

    result = CliRunner().invoke(main, ["status"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, result.output
    assert "黄金趋势：下行" in result.output
    assert "黄金趋势：上行" not in result.output


def test_status_propagates_invalid_market_without_writing(monkeypatch):
    store = _Store(empty_ledger())
    before = deepcopy(store.ledger)
    monkeypatch.setattr("ppt.cli._store", lambda: store)

    def fail_market(**_kwargs):
        raise MarketDataError("missing current prices: AVUV")

    monkeypatch.setattr("ppt.cli.fetch_market", fail_market)

    result = CliRunner().invoke(main, ["status"])

    assert result.exit_code != 0
    assert isinstance(result.exception, MarketDataError)
    assert store.ledger == before
    assert store.recorded == []


def test_plan_outputs_one_exact_signed_buy_command_without_writing(monkeypatch):
    store = _Store(empty_ledger())
    prices = {
        **PRICES,
        "SPYM": 88.04000091552734,
        "518880.SS": 8.5600004196167,
        "SGOV": 100.60009765625,
    }
    score_before = BalanceScore(0.2, 0.4, 0.1, 0.2, 0.3)
    score_after = BalanceScore(0.1, 0.2, 0.05, 0.1, 0.2)
    planned = PlanResult(
        trades={"SPYM": 2, "518880.SS": 100, "SGOV": -1},
        before_score=score_before,
        after_score=score_after,
        buy_cost=200.0,
        sell_proceeds=100.0,
        unused_amount=900.0,
        final_holdings={ticker: 0 for ticker in PRICES},
        corridor_breached=True,
    )
    monkeypatch.setattr("ppt.cli._store", lambda: store)
    monkeypatch.setattr(
        "ppt.cli.fetch_market", lambda: MarketSnapshot(prices, 1.0, {}, {})
    )

    def build_plan_with_raw_prices(_holdings, received_prices, _usdcny, _amount):
        assert received_prices == prices
        assert received_prices["SGOV"] == 100.60009765625
        assert received_prices["518880.SS"] == 8.5600004196167
        return planned

    monkeypatch.setattr("ppt.cli.build_plan", build_plan_with_raw_prices)

    result = CliRunner().invoke(main, ["plan", "1000"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, result.output
    assert (
        "ppt buy 'SPYM#+2@88.04' '518880.SS#+100@8.560' 'SGOV#-1@100.60'"
        in result.output
    )
    assert "88.04000091552734" not in result.output
    assert "8.5600004196167" not in result.output
    assert "100.60009765625" not in result.output
    assert "趋势与相关性提示" not in result.output
    assert store.recorded == []
    assert store.ledger == empty_ledger()


def test_history_shows_summary_and_reverse_batches(monkeypatch):
    store = _Store(empty_ledger())
    first = store.record_batch((Trade("SPYM", 2, 80.0),), 1.0)
    second = store.record_batch((Trade("SPYM", -1, 120.0),), 1.0)
    assert first.executed_at == second.executed_at
    monkeypatch.setattr("ppt.cli._store", lambda: store)

    def unexpected_market_call(*_args, **_kwargs):
        raise AssertionError("history must not fetch market data")

    monkeypatch.setattr("ppt.cli.fetch_market", unexpected_market_call)

    result = CliRunner().invoke(main, ["history"])

    assert result.exit_code == 0, result.output
    assert "累计投入" in result.output
    assert "累计取出" in result.output
    assert "净投入" in result.output
    assert "当前市值" not in result.output
    assert "收益率" not in result.output
    assert "+2" in result.output
    assert "-1" in result.output
