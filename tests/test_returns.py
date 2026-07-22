"""Tests for portfolio-performance summaries and dated market diagnostics."""

import math
from datetime import date, timedelta

import pytest

from ppt.returns import (
    CorrelationWarning,
    PerformanceSummary,
    bucket_correlation,
    correlation_warnings,
    diagnostics,
    performance_summary,
    trend_direction,
    trend_signal,
)


def _dated(values: list[float], start: date = date(2026, 1, 1)) -> dict[date, float]:
    return {start + timedelta(days=index): value for index, value in enumerate(values)}


def test_performance_summary_uses_cumulative_investment_as_denominator() -> None:
    summary = performance_summary(
        invested=2_000.0,
        withdrawn=480.0,
        current_value=2_000.0,
    )

    assert isinstance(summary, PerformanceSummary)
    assert summary.invested == 2_000.0
    assert summary.withdrawn == 480.0
    assert summary.net_invested == 1_520.0
    assert summary.current_value == 2_000.0
    assert summary.profit == 480.0
    assert summary.return_rate == pytest.approx(0.24)


def test_performance_summary_without_investment_has_no_return_rate() -> None:
    summary = performance_summary(0, 25, 10)

    assert summary.net_invested == -25.0
    assert summary.profit == 35.0
    assert summary.return_rate is None


@pytest.mark.parametrize(
    ("invested", "withdrawn", "current_value"),
    [
        (True, 0.0, 0.0),
        (0.0, False, 0.0),
        (0.0, 0.0, True),
        (-1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 0.0, -1.0),
        (math.nan, 0.0, 0.0),
        (0.0, math.inf, 0.0),
        (0.0, 0.0, -math.inf),
    ],
)
def test_performance_summary_rejects_invalid_inputs(
    invested, withdrawn, current_value
) -> None:
    with pytest.raises(ValueError):
        performance_summary(invested, withdrawn, current_value)


def test_trend_signal_sorts_dated_prices() -> None:
    rising_values = [100.0 * 1.02**index for index in range(30)]
    rising = _dated(rising_values)
    reverse_insertion_order = dict(reversed(list(rising.items())))
    falling = _dated(list(reversed(rising_values)))
    flat = _dated([100.0] * 30)

    assert trend_signal(reverse_insertion_order) is not None
    assert trend_direction(reverse_insertion_order) == "up"
    assert trend_direction(falling) == "down"
    assert trend_direction(flat) == "flat"


def test_trend_is_unavailable_for_short_invalid_or_undated_history() -> None:
    assert trend_signal(_dated([100.0] * 10)) is None
    assert trend_direction(_dated([100.0] * 19)) is None
    invalid = _dated([100.0] * 20)
    invalid[max(invalid)] = float("nan")
    assert trend_signal(invalid) is None
    assert trend_signal([100.0] * 20) is None  # type: ignore[arg-type]


def test_bucket_correlation_aligns_only_exact_common_dates() -> None:
    start = date(2026, 1, 1)
    first = _dated(
        [100.0 + index + (index % 3) for index in range(40)],
        start=start,
    )
    second: dict[date, float] = {}
    for offset in range(-5, 45):
        day = start + timedelta(days=offset)
        second[day] = first[day] * 2.0 if day in first else 500.0 + offset**2

    assert bucket_correlation(first, second) == pytest.approx(1.0)
    assert bucket_correlation(dict(list(first.items())[:20]), second) is None
    assert bucket_correlation(_dated([100.0] * 40), second) is None


def test_bucket_correlation_uses_only_latest_30_common_days() -> None:
    first = _dated([100.0 + index + (index % 4) for index in range(40)])
    second = {
        day: (500.0 - value if index < 10 else value * 2.0)
        for index, (day, value) in enumerate(first.items())
    }

    assert bucket_correlation(first, second) == pytest.approx(1.0)


def test_bucket_correlation_rejects_nonfinite_or_undated_history() -> None:
    first = _dated([100.0 + index for index in range(40)])
    second = dict(first)
    second[list(second)[20]] = float("inf")

    assert bucket_correlation(first, second) is None
    assert bucket_correlation(list(first.values()), first) is None  # type: ignore[arg-type]


def test_correlation_warnings_are_structured_and_stable() -> None:
    stock = _dated([100.0 + index + (index % 4) for index in range(40)])
    history = {
        "stock": stock,
        "bond": {day: value * 1.5 for day, value in stock.items()},
        "gold": _dated([200.0 + index * 0.2 + (index % 5) for index in range(40)]),
        "cash": _dated([100.0] * 40),
    }

    warnings = correlation_warnings(history)

    assert warnings
    assert isinstance(warnings[0], CorrelationWarning)
    assert (warnings[0].first, warnings[0].second) == ("stock", "bond")
    assert warnings[0].correlation == pytest.approx(1.0)


def test_diagnostics_combines_hints_without_a_plan_input() -> None:
    rising = _dated([100.0 + index + (index % 4) for index in range(40)])
    report = diagnostics(
        {
            "stock": rising,
            "bond": {day: value * 2 for day, value in rising.items()},
            "gold": _dated(list(reversed(list(rising.values())))),
            "cash": _dated([100.0] * 40),
        }
    )

    assert list(report.trends) == ["stock", "bond", "gold", "cash"]
    assert report.trends["stock"] == "up"
    assert report.trends["gold"] == "down"
    assert report.trends["cash"] == "flat"
    assert report.correlation_pairs == 3
    assert any(
        warning.first == "stock" and warning.second == "bond"
        for warning in report.correlations
    )


def test_diagnostics_reports_when_no_correlation_pair_is_available() -> None:
    report = diagnostics({})

    assert report.correlation_pairs == 0
    assert report.correlations == ()
