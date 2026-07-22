"""Tests for pure valuation and ordered balance scoring."""

import pytest

from ppt.valuation import (
    BalanceScore,
    balance_score,
    bucket_values,
    bucket_weights,
    currency_split,
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
