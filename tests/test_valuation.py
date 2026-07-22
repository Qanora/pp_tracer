"""Tests for pure valuation and ordered balance scoring."""

import math
from datetime import date, timedelta

import pytest

from ppt.valuation import (
    BalanceScore,
    balance_score,
    bucket_values,
    bucket_weights,
    currency_split,
    current_holdings_backtest,
    current_holdings_bucket_history,
    equal_target_weights,
    is_corridor_breached,
    portfolio_snapshot,
    ticker_values_cny,
    total_value,
)

PRICES = {
    "SPYM": 100.0,
    "AVUV": 100.0,
    "VGIT": 100.0,
    "GLDM": 100.0,
    "SGOV": 100.0,
    "518880.SS": 1.0,
    "511360.SS": 1.0,
}


def balanced_holdings() -> dict[str, int]:
    return {
        "SPYM": 50,
        "AVUV": 50,
        "VGIT": 100,
        "GLDM": 50,
        "518880.SS": 5000,
        "SGOV": 50,
        "511360.SS": 5000,
    }


def test_ticker_and_bucket_values_use_native_currency() -> None:
    holdings = balanced_holdings()
    values = ticker_values_cny(holdings, PRICES, usdcny=2.0)

    assert values["SPYM"] == 10_000.0
    assert values["518880.SS"] == 5_000.0
    buckets = bucket_values(values)
    assert buckets == {
        "stock": 20_000.0,
        "bond": 20_000.0,
        "gold": 15_000.0,
        "cash": 15_000.0,
    }
    assert total_value(buckets) == 70_000.0


def test_equal_bucket_and_intra_score_is_zero() -> None:
    score = balance_score(balanced_holdings(), PRICES, usdcny=1.0)

    assert isinstance(score, BalanceScore)
    assert score.bucket_max == pytest.approx(0.0)
    assert score.bucket_total == pytest.approx(0.0)
    assert score.intra_max == pytest.approx(0.0)
    assert score.intra_total == pytest.approx(0.0)
    # With stock and bond entirely USD, equal dual-ticker buckets imply 75/25.
    assert score.currency == pytest.approx(0.25)


def test_bucket_weights_and_targets_have_stable_order() -> None:
    weights = bucket_weights({"stock": 4, "bond": 3, "gold": 2, "cash": 1})

    assert list(weights) == ["stock", "bond", "gold", "cash"]
    assert weights == pytest.approx({"stock": 0.4, "bond": 0.3, "gold": 0.2, "cash": 0.1})
    assert equal_target_weights() == {
        "stock": 0.25,
        "bond": 0.25,
        "gold": 0.25,
        "cash": 0.25,
    }


def test_currency_split_is_cny_valued() -> None:
    split = currency_split(balanced_holdings(), PRICES, usdcny=1.0)

    assert split == {"usd": 30_000.0, "cny": 10_000.0, "total": 40_000.0}


def test_empty_portfolio_has_explicit_deviations() -> None:
    score = balance_score({}, PRICES, usdcny=1.0)

    assert score.as_tuple() == pytest.approx((0.25, 1.0, 0.0, 0.0, 0.5))


def test_portfolio_snapshot_contains_ticker_bucket_currency_and_score_facts() -> None:
    snapshot = portfolio_snapshot(balanced_holdings(), PRICES, usdcny=1.0)

    assert snapshot.total_value_cny == pytest.approx(40_000.0)
    assert [row.ticker for row in snapshot.tickers] == [
        "SPYM",
        "AVUV",
        "VGIT",
        "GLDM",
        "518880.SS",
        "SGOV",
        "511360.SS",
    ]
    assert [row.weight for row in snapshot.buckets] == pytest.approx([0.25] * 4)
    assert [row.weight for row in snapshot.currencies] == pytest.approx([0.75, 0.25])
    assert snapshot.score.as_tuple() == pytest.approx((0.0, 0.0, 0.0, 0.0, 0.25))
    assert snapshot.corridor_breached is False


def test_empty_portfolio_snapshot_marks_undefined_weights() -> None:
    snapshot = portfolio_snapshot({}, PRICES, usdcny=1.0)

    assert snapshot.total_value_cny == 0.0
    assert all(row.portfolio_weight is None for row in snapshot.tickers)
    assert all(row.bucket_weight is None for row in snapshot.tickers)
    assert all(row.weight is None and row.corridor is None for row in snapshot.buckets)
    assert all(row.weight is None for row in snapshot.currencies)
    assert snapshot.corridor_breached is False


def test_corridor_boundaries_are_inclusive() -> None:
    assert is_corridor_breached({"stock": 15, "bond": 35, "gold": 25, "cash": 25}) is False
    assert is_corridor_breached({"stock": 14, "bond": 36, "gold": 25, "cash": 25}) is True
    assert is_corridor_breached({"stock": 0, "bond": 0, "gold": 0, "cash": 0}) is False


def test_current_holdings_backtest_replays_fixed_cny_and_usd_holdings() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(30)]
    usd_prices = [100.0, 90.0, *([95.0] * 28)]
    cny_prices = [10.0, 9.0, *([9.5] * 28)]
    result = current_holdings_backtest(
        {"SPYM": 1, "518880.SS": 10},
        {
            "SPYM": dict(zip(days, usd_prices, strict=True)),
            "518880.SS": dict(zip(days, cny_prices, strict=True)),
        },
        dict.fromkeys(days, 7.0),
    )

    assert result.observations == 30
    assert result.maximum_drawdown == pytest.approx(-0.1)
    assert result.current_drawdown == pytest.approx(-0.05)
    assert result.maximum_runup == pytest.approx(95.0 / 90.0 - 1.0)


def test_current_holdings_backtest_uses_historical_fx() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(30)]
    fx_rates = [7.0, 6.0, *([6.5] * 28)]
    result = current_holdings_backtest(
        {"SPYM": 2},
        {"SPYM": dict.fromkeys(days, 100.0)},
        dict(zip(days, fx_rates, strict=True)),
    )

    assert result.observations == 30
    assert result.maximum_drawdown == pytest.approx(6.0 / 7.0 - 1.0)
    assert result.current_drawdown == pytest.approx(6.5 / 7.0 - 1.0)
    assert result.maximum_runup == pytest.approx(6.5 / 6.0 - 1.0)


def test_current_holdings_backtest_uses_only_exact_common_dates() -> None:
    start = date(2026, 1, 1)
    first_days = [start + timedelta(days=index) for index in range(32)]
    second_days = [start + timedelta(days=index) for index in range(1, 33)]
    fx_days = [start + timedelta(days=index) for index in range(2, 34)]

    result = current_holdings_backtest(
        {"SPYM": 1, "518880.SS": 1},
        {
            "SPYM": dict.fromkeys(first_days, 100.0),
            "518880.SS": dict.fromkeys(second_days, 10.0),
        },
        dict.fromkeys(fx_days, 7.0),
    )

    assert result.observations == 30
    assert result.current_drawdown == pytest.approx(0.0)
    assert result.maximum_drawdown == pytest.approx(0.0)
    assert result.maximum_runup == pytest.approx(0.0)


def test_current_holdings_backtest_does_not_require_fx_for_cny_only() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(30)]
    prices = [10.0, *([9.0] * 29)]
    result = current_holdings_backtest(
        {"518880.SS": 10},
        {"518880.SS": dict(zip(days, prices, strict=True))},
        {},
    )

    assert result.maximum_drawdown == pytest.approx(-0.1)
    assert result.current_drawdown == pytest.approx(-0.1)
    assert result.maximum_runup == pytest.approx(0.0)
    assert result.observations == 30


def test_current_holdings_backtest_reports_unavailable_data() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(29)]

    empty = current_holdings_backtest({}, {}, {})
    missing = current_holdings_backtest({"SPYM": 1}, {}, {})
    incomplete = current_holdings_backtest(
        {"SPYM": 1},
        {"SPYM": dict.fromkeys(days, 100.0)},
        dict.fromkeys(days, 7.0),
    )

    assert empty == missing
    assert empty.current_drawdown is None
    assert empty.maximum_drawdown is None
    assert empty.maximum_runup is None
    assert empty.observations == 0
    assert incomplete.current_drawdown is None
    assert incomplete.maximum_drawdown is None
    assert incomplete.maximum_runup is None
    assert incomplete.observations == 29


def test_current_holdings_backtest_uses_only_latest_30_days() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(31)]
    prices = [1000.0, 100.0, 80.0, *([120.0] * 28)]

    result = current_holdings_backtest(
        {"518880.SS": 1},
        {"518880.SS": dict(zip(days, prices, strict=True))},
        {},
    )

    assert result.observations == 30
    assert result.current_drawdown == pytest.approx(0.0)
    assert result.maximum_drawdown == pytest.approx(-0.2)
    assert result.maximum_runup == pytest.approx(0.5)


def test_current_holdings_bucket_history_uses_actual_holdings_and_historical_fx() -> None:
    days = [date(2026, 1, 1), date(2026, 1, 2)]

    curves = current_holdings_bucket_history(
        {"SPYM": 1, "AVUV": 2, "518880.SS": 10},
        {
            "SPYM": dict(zip(days, [100.0, 100.0], strict=True)),
            "AVUV": dict(zip(days, [50.0, 60.0], strict=True)),
            "518880.SS": dict(zip(days, [10.0, 9.0], strict=True)),
        },
        dict(zip(days, [7.0, 6.0], strict=True)),
    )

    assert curves["stock"] == pytest.approx({days[0]: 1_400.0, days[1]: 1_320.0})
    assert curves["gold"] == pytest.approx({days[0]: 100.0, days[1]: 90.0})
    assert curves["bond"] == {}
    assert curves["cash"] == {}


def test_current_holdings_bucket_history_degrades_only_the_missing_bucket() -> None:
    days = [date(2026, 1, 1), date(2026, 1, 2)]

    curves = current_holdings_bucket_history(
        {"SPYM": 1, "518880.SS": 10},
        {"518880.SS": dict.fromkeys(days, 10.0)},
        {},
    )

    assert curves["stock"] == {}
    assert curves["gold"] == pytest.approx(dict.fromkeys(days, 100.0))


@pytest.mark.parametrize("invalid", [True, -1, math.nan, math.inf])
def test_current_holdings_backtest_rejects_invalid_shares(invalid) -> None:
    with pytest.raises(ValueError, match="invalid holding shares"):
        current_holdings_backtest({"SPYM": invalid}, {}, {})


@pytest.mark.parametrize("invalid", [True, 0, -1, math.nan, math.inf])
def test_current_holdings_backtest_rejects_invalid_required_market_data(invalid) -> None:
    days = [date(2026, 1, 1), date(2026, 1, 2)]
    prices = dict.fromkeys(days, 100.0)
    prices[days[-1]] = invalid

    with pytest.raises(ValueError, match="invalid historical price"):
        current_holdings_backtest(
            {"SPYM": 1},
            {"SPYM": prices},
            dict.fromkeys(days, 7.0),
        )


def test_current_holdings_backtest_rejects_nonfinite_fx() -> None:
    days = [date(2026, 1, 1), date(2026, 1, 2)]
    with pytest.raises(ValueError, match="invalid historical USD/CNY rate"):
        current_holdings_backtest(
            {"SPYM": 1},
            {"SPYM": dict.fromkeys(days, 100.0)},
            dict(zip(days, [7.0, math.inf])),
        )
