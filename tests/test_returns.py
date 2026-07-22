"""Tests for signed-history summaries and read-only diagnostics."""

import math

import pytest

from ppt.returns import (
    CorrelationWarning,
    bucket_correlation,
    correlation_warnings,
    diagnostics,
    history_summary,
    trend_direction,
    trend_signal,
)


def test_history_summary_uses_net_batch_cash_flow() -> None:
    transactions = [
        {
            "date": "2026-01-01T10:00:00+08:00",
            "usdcny": 2.0,
            "trades": [{"ticker": "SPYM", "shares": 10, "price": 100.0}],
        },
        {
            "date": "2026-02-01T10:00:00+08:00",
            "usdcny": 2.0,
            "trades": [
                {"ticker": "GLDM", "shares": -5, "price": 100.0},
                {"ticker": "518880.SS", "shares": 1000, "price": 1.0},
            ],
        },
        {
            "date": "2026-03-01T10:00:00+08:00",
            "usdcny": 2.0,
            "trades": [{"ticker": "SPYM", "shares": -2, "price": 120.0}],
        },
    ]

    summary = history_summary(transactions, current_value=2_000.0)

    assert summary.invested == pytest.approx(2_000.0)
    assert summary.withdrawn == pytest.approx(480.0)
    assert summary.net_invested == pytest.approx(1_520.0)
    assert summary.profit == pytest.approx(480.0)
    # The agreed denominator is cumulative investment, not net investment.
    assert summary.return_rate == pytest.approx(0.24)


def test_history_summary_without_investment_has_no_return_rate() -> None:
    summary = history_summary([], current_value=0.0)

    assert summary.invested == 0.0
    assert summary.withdrawn == 0.0
    assert summary.profit == 0.0
    assert summary.return_rate is None


@pytest.mark.parametrize("current_value", (-1.0, math.nan, math.inf, True))
def test_history_summary_rejects_invalid_current_value(current_value) -> None:
    with pytest.raises(ValueError):
        history_summary([], current_value)


def test_history_summary_rejects_invalid_trade_data() -> None:
    with pytest.raises(ValueError, match="invalid historical shares"):
        history_summary(
            [
                {
                    "usdcny": 7.0,
                    "trades": [{"ticker": "SPYM", "shares": 0, "price": 100.0}],
                }
            ],
            0.0,
        )


def test_trend_signal_and_direction() -> None:
    rising = [100.0 * 1.02**index for index in range(30)]
    falling = list(reversed(rising))
    flat = [100.0] * 30

    assert trend_signal(rising) is not None
    assert trend_direction(rising) == "up"
    assert trend_direction(falling) == "down"
    assert trend_direction(flat) == "flat"


def test_trend_is_unavailable_for_short_or_invalid_history() -> None:
    assert trend_signal([100.0] * 10) is None
    assert trend_direction([100.0] * 19) is None
    invalid = [100.0] * 20
    invalid[-1] = float("nan")
    assert trend_signal(invalid) is None


def test_bucket_correlation_detects_aligned_returns() -> None:
    first = [100.0 + index + (index % 3) for index in range(40)]
    second = [value * 2.0 for value in first]

    assert bucket_correlation(first, second) == pytest.approx(1.0)
    assert bucket_correlation(first[:20], second[:20]) is None
    assert bucket_correlation([100.0] * 40, second) is None


def test_bucket_correlation_rejects_nonfinite_history_without_warning() -> None:
    first = [100.0 + index for index in range(40)]
    second = list(first)
    second[20] = float("inf")

    assert bucket_correlation(first, second) is None


def test_correlation_warnings_are_structured_and_stable() -> None:
    stock = [100.0 + index + (index % 4) for index in range(40)]
    history = {
        "stock": stock,
        "bond": [value * 1.5 for value in stock],
        "gold": [200.0 + index * 0.2 + (index % 5) for index in range(40)],
        "cash": [100.0] * 40,
    }

    warnings = correlation_warnings(history)

    assert warnings
    assert isinstance(warnings[0], CorrelationWarning)
    assert (warnings[0].first, warnings[0].second) == ("stock", "bond")
    assert warnings[0].correlation == pytest.approx(1.0)


def test_diagnostics_combines_hints_without_a_plan_input() -> None:
    rising = [100.0 + index + (index % 4) for index in range(40)]
    report = diagnostics(
        {
            "stock": rising,
            "bond": [value * 2 for value in rising],
            "gold": list(reversed(rising)),
            "cash": [100.0] * 40,
        }
    )

    assert list(report.trends) == ["stock", "bond", "gold", "cash"]
    assert report.trends["stock"] == "up"
    assert report.trends["gold"] == "down"
    assert report.trends["cash"] == "flat"
    assert any(
        warning.first == "stock" and warning.second == "bond"
        for warning in report.correlations
    )
