"""Pure portfolio-performance summaries and market diagnostics."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from itertools import combinations

from ppt.constants import (
    BUCKET_ORDER,
    CORRELATION_MIN_POINTS,
    CORRELATION_WARNING,
    HISTORY_WINDOW_DAYS,
    TREND_LONG_WINDOW,
    TREND_SHORT_WINDOW,
)


@dataclass(frozen=True)
class PerformanceSummary:
    """Cash-flow and simple-return facts for the current portfolio."""

    invested: float
    withdrawn: float
    net_invested: float
    current_value: float
    profit: float
    return_rate: float | None


@dataclass(frozen=True)
class CorrelationWarning:
    """One pair whose absolute recent correlation exceeds the threshold."""

    first: str
    second: str
    correlation: float


@dataclass(frozen=True)
class Diagnostics:
    """Structured, display-independent trend and correlation result."""

    trends: dict[str, str | None]
    correlations: tuple[CorrelationWarning, ...]
    correlation_pairs: int


def performance_summary(
    invested: float,
    withdrawn: float,
    current_value: float,
) -> PerformanceSummary:
    """Return cumulative cash-flow, profit, and simple-return facts.

    The denominator is cumulative investment rather than net investment.  This
    keeps withdrawals from making a remaining portfolio's simple return
    explode or change sign.
    """

    values = {
        "invested": invested,
        "withdrawn": withdrawn,
        "current value": current_value,
    }
    normalized: dict[str, float] = {}
    for name, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"invalid {name}: {value}")
        normalized[name] = float(value)

    invested_value = normalized["invested"]
    withdrawn_value = normalized["withdrawn"]
    current = normalized["current value"]
    net_invested = invested_value - withdrawn_value
    profit = current + withdrawn_value - invested_value
    return PerformanceSummary(
        invested=invested_value,
        withdrawn=withdrawn_value,
        net_invested=net_invested,
        current_value=current,
        profit=profit,
        return_rate=profit / invested_value if invested_value > 0 else None,
    )


def trend_signal(
    prices: Mapping[date, float],
    short_window: int = TREND_SHORT_WINDOW,
    long_window: int = TREND_LONG_WINDOW,
) -> float | None:
    """Return short-MA/long-MA minus one, or ``None`` when unavailable."""

    if short_window <= 0 or long_window <= short_window:
        raise ValueError("trend windows must satisfy 0 < short < long")
    ordered = _ordered_prices(prices)
    if ordered is None or len(ordered) < long_window:
        return None
    short_mean = sum(ordered[-short_window:]) / short_window
    long_mean = sum(ordered[-long_window:]) / long_window
    return short_mean / long_mean - 1.0


def trend_direction(
    prices: Mapping[date, float],
    threshold: float = 0.01,
    short_window: int = TREND_SHORT_WINDOW,
    long_window: int = TREND_LONG_WINDOW,
) -> str | None:
    """Return ``up``, ``down``, ``flat``, or ``None`` for insufficient data."""

    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold < 0
    ):
        raise ValueError("trend threshold must be finite and non-negative")
    signal = trend_signal(prices, short_window, long_window)
    if signal is None:
        return None
    if signal > threshold:
        return "up"
    if signal < -threshold:
        return "down"
    return "flat"


def bucket_correlation(
    prices_a: Mapping[date, float],
    prices_b: Mapping[date, float],
    min_days: int = CORRELATION_MIN_POINTS,
) -> float | None:
    """Return Pearson correlation of returns on exact common dates."""

    if isinstance(min_days, bool) or not isinstance(min_days, int) or min_days < 2:
        raise ValueError("min_days must be at least two")
    if not _valid_price_history(prices_a) or not _valid_price_history(prices_b):
        return None

    common_dates = sorted(set(prices_a).intersection(prices_b))[-HISTORY_WINDOW_DAYS:]
    if len(common_dates) < min_days + 1:
        return None
    a = [float(prices_a[day]) for day in common_dates]
    b = [float(prices_b[day]) for day in common_dates]
    returns_a = [(a[index] / a[index - 1]) - 1.0 for index in range(1, len(a))]
    returns_b = [(b[index] / b[index - 1]) - 1.0 for index in range(1, len(b))]
    mean_a = sum(returns_a) / len(returns_a)
    mean_b = sum(returns_b) / len(returns_b)
    centered_a = [value - mean_a for value in returns_a]
    centered_b = [value - mean_b for value in returns_b]
    variance_a = sum(value * value for value in centered_a)
    variance_b = sum(value * value for value in centered_b)
    if variance_a <= 0 or variance_b <= 0:
        return None
    covariance = sum(x * y for x, y in zip(centered_a, centered_b))
    correlation = covariance / math.sqrt(variance_a * variance_b)
    return max(-1.0, min(1.0, correlation))


def correlation_warnings(
    history: Mapping[str, Mapping[date, float]],
    threshold: float = CORRELATION_WARNING,
    min_days: int = CORRELATION_MIN_POINTS,
) -> tuple[CorrelationWarning, ...]:
    """Return all stable-order bucket pairs above an absolute threshold."""

    _validate_correlation_threshold(threshold)
    return tuple(
        CorrelationWarning(first, second, correlation)
        for first, second, correlation in _available_correlations(history, min_days)
        if abs(correlation) > threshold
    )


def diagnostics(
    history: Mapping[str, Mapping[date, float]],
    trend_threshold: float = 0.01,
    correlation_threshold: float = CORRELATION_WARNING,
    correlation_min_days: int = CORRELATION_MIN_POINTS,
) -> Diagnostics:
    """Build trend directions and correlation warnings without affecting plans."""

    trends = {
        bucket: trend_direction(history.get(bucket, {}), threshold=trend_threshold)
        for bucket in BUCKET_ORDER
    }
    _validate_correlation_threshold(correlation_threshold)
    available = _available_correlations(history, correlation_min_days)
    return Diagnostics(
        trends=trends,
        correlations=tuple(
            CorrelationWarning(first, second, correlation)
            for first, second, correlation in available
            if abs(correlation) > correlation_threshold
        ),
        correlation_pairs=len(available),
    )


def _available_correlations(
    history: Mapping[str, Mapping[date, float]],
    min_days: int,
) -> tuple[tuple[str, str, float], ...]:
    available: list[tuple[str, str, float]] = []
    for first, second in combinations(BUCKET_ORDER, 2):
        correlation = bucket_correlation(
            history.get(first, {}), history.get(second, {}), min_days=min_days
        )
        if correlation is not None:
            available.append((first, second, correlation))
    return tuple(available)


def _validate_correlation_threshold(threshold: float) -> None:
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
    ):
        raise ValueError("correlation threshold must be between zero and one")


def _ordered_prices(prices: Mapping[date, float]) -> list[float] | None:
    if not _valid_price_history(prices):
        return None
    return [float(prices[day]) for day in sorted(prices)]


def _valid_price_history(prices: object) -> bool:
    if not isinstance(prices, Mapping):
        return False
    return all(
        type(day) is date
        and not isinstance(price, bool)
        and isinstance(price, (int, float))
        and math.isfinite(price)
        and price > 0
        for day, price in prices.items()
    )
