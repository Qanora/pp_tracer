"""Pure transaction-history summaries and market diagnostics."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from ppt.constants import (
    BUCKET_ORDER,
    CORRELATION_MIN_POINTS,
    CORRELATION_WARNING,
    TICKER_CURRENCY,
    TICKER_WHITELIST,
    TREND_LONG_WINDOW,
    TREND_SHORT_WINDOW,
)


@dataclass(frozen=True)
class HistorySummary:
    """Cash-flow and simple-return summary shown above transaction history."""

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


def history_summary(transactions: list[dict], current_value: float) -> HistorySummary:
    """Summarize signed transaction batches in CNY.

    A batch may contain positive buys and negative sells.  Only its *net* cash
    flow is counted as new investment or withdrawal, so an internal rebalance
    does not inflate both totals.  USD trades use the exchange rate captured on
    that transaction batch.
    """

    if (
        isinstance(current_value, bool)
        or not isinstance(current_value, (int, float))
        or not math.isfinite(current_value)
        or current_value < 0
    ):
        raise ValueError(f"invalid current value: {current_value}")

    invested = 0.0
    withdrawn = 0.0
    for transaction in transactions:
        if not isinstance(transaction, dict):
            raise ValueError("transaction must be a mapping")
        trades = transaction.get("trades")
        if not isinstance(trades, list) or not trades:
            raise ValueError("transaction must contain trades")
        rate = transaction.get("usdcny")
        batch_cash_flow = 0.0
        for trade in trades:
            ticker = trade.get("ticker")
            shares = trade.get("shares")
            price = trade.get("price")
            if ticker not in TICKER_WHITELIST:
                raise ValueError(f"unknown ticker in history: {ticker}")
            if (
                isinstance(shares, bool)
                or not isinstance(shares, (int, float))
                or not math.isfinite(shares)
                or shares == 0
            ):
                raise ValueError(f"invalid historical shares for {ticker}: {shares}")
            if (
                isinstance(price, bool)
                or not isinstance(price, (int, float))
                or not math.isfinite(price)
                or price <= 0
            ):
                raise ValueError(f"invalid historical price for {ticker}: {price}")
            multiplier = 1.0
            if TICKER_CURRENCY[ticker] == "USD":
                if (
                    isinstance(rate, bool)
                    or not isinstance(rate, (int, float))
                    or not math.isfinite(rate)
                    or rate <= 0
                ):
                    raise ValueError(f"invalid historical USD/CNY rate: {rate}")
                multiplier = float(rate)
            batch_cash_flow += shares * price * multiplier

        if batch_cash_flow > 0:
            invested += batch_cash_flow
        elif batch_cash_flow < 0:
            withdrawn -= batch_cash_flow

    net_invested = invested - withdrawn
    profit = float(current_value) - net_invested
    return_rate = profit / invested if invested > 0 else None
    return HistorySummary(
        invested=invested,
        withdrawn=withdrawn,
        net_invested=net_invested,
        current_value=float(current_value),
        profit=profit,
        return_rate=return_rate,
    )


def trend_signal(
    prices: Sequence[float],
    short_window: int = TREND_SHORT_WINDOW,
    long_window: int = TREND_LONG_WINDOW,
) -> float | None:
    """Return short-MA/long-MA minus one, or ``None`` when unavailable."""

    if short_window <= 0 or long_window <= short_window:
        raise ValueError("trend windows must satisfy 0 < short < long")
    if len(prices) < long_window or not _valid_price_series(prices):
        return None
    short_mean = sum(prices[-short_window:]) / short_window
    long_mean = sum(prices[-long_window:]) / long_window
    return short_mean / long_mean - 1.0


def trend_direction(
    prices: Sequence[float],
    threshold: float = 0.01,
    short_window: int = TREND_SHORT_WINDOW,
    long_window: int = TREND_LONG_WINDOW,
) -> str | None:
    """Return ``up``, ``down``, ``flat``, or ``None`` for insufficient data."""

    if not math.isfinite(threshold) or threshold < 0:
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
    prices_a: Sequence[float],
    prices_b: Sequence[float],
    min_days: int = CORRELATION_MIN_POINTS,
) -> float | None:
    """Return Pearson correlation of aligned daily returns."""

    if min_days < 2:
        raise ValueError("min_days must be at least two")
    if (
        len(prices_a) < min_days + 1
        or len(prices_b) < min_days + 1
        or not _valid_price_series(prices_a)
        or not _valid_price_series(prices_b)
    ):
        return None

    count = min(len(prices_a), len(prices_b))
    a = prices_a[-count:]
    b = prices_b[-count:]
    returns_a = [(a[index] / a[index - 1]) - 1.0 for index in range(1, count)]
    returns_b = [(b[index] / b[index - 1]) - 1.0 for index in range(1, count)]
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
    history: dict[str, Sequence[float]],
    threshold: float = CORRELATION_WARNING,
    min_days: int = CORRELATION_MIN_POINTS,
) -> tuple[CorrelationWarning, ...]:
    """Return all stable-order bucket pairs above an absolute threshold."""

    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("correlation threshold must be between zero and one")
    warnings: list[CorrelationWarning] = []
    for first, second in combinations(BUCKET_ORDER, 2):
        correlation = bucket_correlation(
            history.get(first, ()), history.get(second, ()), min_days=min_days
        )
        if correlation is not None and abs(correlation) > threshold:
            warnings.append(CorrelationWarning(first, second, correlation))
    return tuple(warnings)


def diagnostics(
    history: dict[str, Sequence[float]],
    trend_threshold: float = 0.01,
    correlation_threshold: float = CORRELATION_WARNING,
    correlation_min_days: int = CORRELATION_MIN_POINTS,
) -> Diagnostics:
    """Build trend directions and correlation warnings without affecting plans."""

    trends = {
        bucket: trend_direction(history.get(bucket, ()), threshold=trend_threshold)
        for bucket in BUCKET_ORDER
    }
    return Diagnostics(
        trends=trends,
        correlations=correlation_warnings(
            history,
            threshold=correlation_threshold,
            min_days=correlation_min_days,
        ),
    )


def _valid_price_series(prices: Sequence[float]) -> bool:
    return all(
        not isinstance(price, bool)
        and isinstance(price, (int, float))
        and math.isfinite(price)
        and price > 0
        for price in prices
    )
