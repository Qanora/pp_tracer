"""Pure portfolio valuation and balance scoring.

The target portfolio has three strictly ordered objectives:

1. four buckets at 25% each;
2. equal market value for the tickers inside each bucket;
3. total USD/CNY market value at 50%/50%.

This module deliberately contains no IO, configuration, or presentation code.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from ppt.constants import (
    BUCKET_ORDER,
    BUCKET_TICKERS,
    CNY_TICKERS,
    CORRIDOR_LOWER,
    CORRIDOR_UPPER,
    HISTORY_WINDOW_DAYS,
    TARGET_BUCKET_WEIGHT,
    TARGET_CURRENCY_WEIGHT,
    TICKER_CURRENCY,
    USD_TICKERS,
)


@dataclass(frozen=True)
class BalanceScore:
    """Lexicographic balance score; lower is better.

    ``bucket_*`` is priority one, ``intra_*`` priority two, and
    ``currency`` priority three.  The paired max/total components make each
    priority deterministic without allowing a later priority to compensate
    for an earlier one.
    """

    bucket_max: float
    bucket_total: float
    intra_max: float
    intra_total: float
    currency: float

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return (
            self.bucket_max,
            self.bucket_total,
            self.intra_max,
            self.intra_total,
            self.currency,
        )


@dataclass(frozen=True)
class TickerSnapshot:
    """One fixed holding valued in CNY with its current allocation weights."""

    bucket: str
    ticker: str
    currency: str
    shares: int | float
    price: float
    value_cny: float
    portfolio_weight: float | None
    bucket_weight: float | None
    bucket_target: float


@dataclass(frozen=True)
class BucketSnapshot:
    """One strategic bucket's current value, target, and corridor position."""

    bucket: str
    value_cny: float
    weight: float | None
    target: float
    deviation: float | None
    corridor: str | None


@dataclass(frozen=True)
class CurrencySnapshot:
    """One currency's CNY-equivalent value and allocation weight."""

    currency: str
    value_cny: float
    weight: float | None
    target: float
    deviation: float | None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Complete read-only current portfolio state for terminal presentation."""

    total_value_cny: float
    usdcny: float
    tickers: tuple[TickerSnapshot, ...]
    buckets: tuple[BucketSnapshot, ...]
    currencies: tuple[CurrencySnapshot, ...]
    score: BalanceScore
    corridor_breached: bool


@dataclass(frozen=True)
class HoldingsBacktestSummary:
    """Drawdown and run-up of today's fixed holdings over 30 trading days."""

    current_drawdown: float | None
    maximum_drawdown: float | None
    maximum_runup: float | None
    observations: int


def current_holdings_backtest(
    holdings: Mapping[str, int | float],
    history: Mapping[str, Mapping[date, float]],
    usdcny_history: Mapping[date, float],
) -> HoldingsBacktestSummary:
    """Replay today's holdings over the latest 30 exact common trading dates.

    Share counts remain fixed throughout the replay.  Each USD close is
    converted using that date's USD/CNY rate.  Missing required history or
    fewer than 30 common observations make the result unavailable; malformed
    or non-finite financial data is rejected.
    """

    selected = _positive_holdings(holdings)
    if not selected:
        return _unavailable_backtest(0)

    value_history = _fixed_holdings_history(selected, history, usdcny_history)
    if value_history is None:
        return _unavailable_backtest(0)
    available_observations = len(value_history)
    if available_observations < HISTORY_WINDOW_DAYS:
        return _unavailable_backtest(available_observations)
    values = [
        value_history[day]
        for day in sorted(value_history)[-HISTORY_WINDOW_DAYS:]
    ]

    peak = values[0]
    trough = values[0]
    drawdowns: list[float] = []
    runups: list[float] = []
    for value in values:
        peak = max(peak, value)
        trough = min(trough, value)
        drawdown = value / peak - 1.0
        runup = value / trough - 1.0
        if not math.isfinite(drawdown) or not math.isfinite(runup):
            raise ValueError("non-finite backtest metric")
        drawdowns.append(min(0.0, drawdown))
        runups.append(max(0.0, runup))

    return HoldingsBacktestSummary(
        current_drawdown=drawdowns[-1],
        maximum_drawdown=min(drawdowns),
        maximum_runup=max(runups),
        observations=len(values),
    )


def current_holdings_bucket_history(
    holdings: Mapping[str, int | float],
    history: Mapping[str, Mapping[date, float]],
    usdcny_history: Mapping[date, float],
) -> dict[str, dict[date, float]]:
    """Return each current bucket's dated CNY value curve.

    Empty buckets and buckets missing any required close or historical FX
    series are returned as empty mappings so advisory diagnostics can degrade
    independently. Malformed supplied financial data remains an error.
    """

    selected = _positive_holdings(holdings)
    by_bucket: dict[str, dict[date, float]] = {}
    for bucket in BUCKET_ORDER:
        bucket_tickers = set(BUCKET_TICKERS[bucket])
        bucket_holdings = tuple(
            (ticker, shares)
            for ticker, shares in selected
            if ticker in bucket_tickers
        )
        curve = _fixed_holdings_history(
            bucket_holdings,
            history,
            usdcny_history,
        )
        by_bucket[bucket] = curve or {}
    return by_bucket


def _unavailable_backtest(observations: int) -> HoldingsBacktestSummary:
    return HoldingsBacktestSummary(
        current_drawdown=None,
        maximum_drawdown=None,
        maximum_runup=None,
        observations=observations,
    )


def _positive_holdings(
    holdings: Mapping[str, int | float],
) -> tuple[tuple[str, float], ...]:
    selected: list[tuple[str, float]] = []
    for ticker, shares in holdings.items():
        if ticker not in TICKER_CURRENCY:
            raise ValueError(f"unknown holding ticker: {ticker}")
        if (
            isinstance(shares, bool)
            or not isinstance(shares, (int, float))
            or not math.isfinite(shares)
            or shares < 0
        ):
            raise ValueError(f"invalid holding shares for {ticker}: {shares}")
        if shares > 0:
            selected.append((ticker, float(shares)))
    return tuple(selected)


def _fixed_holdings_history(
    selected: tuple[tuple[str, float], ...],
    history: Mapping[str, Mapping[date, float]],
    usdcny_history: Mapping[date, float],
) -> dict[date, float] | None:
    if not selected:
        return {}

    required: list[Mapping[date, float]] = []
    for ticker, _shares in selected:
        prices = history.get(ticker)
        if not isinstance(prices, Mapping) or not prices:
            return None
        _validate_dated_values(prices, f"historical price for {ticker}")
        required.append(prices)

    needs_fx = any(TICKER_CURRENCY[ticker] == "USD" for ticker, _ in selected)
    if needs_fx:
        if not isinstance(usdcny_history, Mapping) or not usdcny_history:
            return None
        _validate_dated_values(usdcny_history, "historical USD/CNY rate")
        required.append(usdcny_history)

    common_dates = set(required[0])
    for series in required[1:]:
        common_dates.intersection_update(series)

    values: dict[date, float] = {}
    for day in sorted(common_dates):
        components = [
            shares
            * float(history[ticker][day])
            * (
                float(usdcny_history[day])
                if TICKER_CURRENCY[ticker] == "USD"
                else 1.0
            )
            for ticker, shares in selected
        ]
        value = math.fsum(components)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"invalid portfolio value on {day.isoformat()}: {value}")
        values[day] = value
    return values


def _validate_dated_values(values: Mapping[date, float], label: str) -> None:
    for day, value in values.items():
        if type(day) is not date:
            raise ValueError(f"invalid date in {label}: {day}")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"invalid {label} on {day.isoformat()}: {value}")


def ticker_values_cny(
    holdings: dict[str, int | float],
    prices: dict[str, float],
    usdcny: float,
) -> dict[str, float]:
    """Return the CNY market value of every fixed ticker.

    Validation belongs at the boundary that creates a market snapshot.  This
    pure primitive assumes positive, finite prices and exchange rate.
    """

    return {
        ticker: holdings.get(ticker, 0)
        * prices[ticker]
        * (1.0 if ticker in CNY_TICKERS else usdcny)
        for bucket in BUCKET_ORDER
        for ticker in BUCKET_TICKERS[bucket]
    }


def bucket_values(ticker_vals: dict[str, float]) -> dict[str, float]:
    """Aggregate ticker values into the four buckets in stable order."""

    return {
        bucket: sum(ticker_vals.get(ticker, 0.0) for ticker in BUCKET_TICKERS[bucket])
        for bucket in BUCKET_ORDER
    }


def total_value(bucket_vals: dict[str, float]) -> float:
    """Return total portfolio market value."""

    return sum(bucket_vals.get(bucket, 0.0) for bucket in BUCKET_ORDER)


def bucket_weights(bucket_vals: dict[str, float]) -> dict[str, float]:
    """Return bucket weights, or four zeros for an empty portfolio."""

    total = total_value(bucket_vals)
    if total <= 0:
        return {bucket: 0.0 for bucket in BUCKET_ORDER}
    return {bucket: bucket_vals.get(bucket, 0.0) / total for bucket in BUCKET_ORDER}


def equal_target_weights() -> dict[str, float]:
    """Return the immutable four-bucket strategic target."""

    return {bucket: TARGET_BUCKET_WEIGHT for bucket in BUCKET_ORDER}


def is_corridor_breached(bucket_vals: dict[str, float]) -> bool:
    """Return whether a non-empty portfolio is outside the fixed 15%-35% corridor."""

    if total_value(bucket_vals) <= 0:
        return False
    weights = bucket_weights(bucket_vals)
    return any(
        weights[bucket] < CORRIDOR_LOWER or weights[bucket] > CORRIDOR_UPPER
        for bucket in BUCKET_ORDER
    )


def currency_split(
    holdings: dict[str, int | float],
    prices: dict[str, float],
    usdcny: float,
) -> dict[str, float]:
    """Return CNY-valued USD/CNY holdings and total value."""

    ticker_vals = ticker_values_cny(holdings, prices, usdcny)
    usd = sum(ticker_vals[ticker] for ticker in USD_TICKERS)
    cny = sum(ticker_vals[ticker] for ticker in CNY_TICKERS)
    return {"usd": usd, "cny": cny, "total": usd + cny}


def balance_score(
    holdings: dict[str, int | float],
    prices: dict[str, float],
    usdcny: float,
) -> BalanceScore:
    """Calculate the three ordered deviations for a portfolio.

    Intra-bucket deviations are normalized by total portfolio value, not by
    the bucket itself.  This keeps empty buckets well-defined and ensures that
    the score represents actual CNY imbalance.  Single-ticker buckets have no
    intra-bucket deviation.
    """

    ticker_vals = ticker_values_cny(holdings, prices, usdcny)
    buckets = bucket_values(ticker_vals)
    total = total_value(buckets)
    weights = bucket_weights(buckets)

    bucket_deviations = [
        abs(weights[bucket] - TARGET_BUCKET_WEIGHT) for bucket in BUCKET_ORDER
    ]

    intra_deviations: list[float] = []
    if total > 0:
        for bucket in BUCKET_ORDER:
            tickers = BUCKET_TICKERS[bucket]
            if len(tickers) < 2:
                continue
            equal_value = buckets[bucket] / len(tickers)
            intra_deviations.extend(
                abs(ticker_vals[ticker] - equal_value) / total for ticker in tickers
            )

    split = currency_split(holdings, prices, usdcny)
    currency_deviation = (
        abs(split["usd"] / split["total"] - TARGET_CURRENCY_WEIGHT)
        if split["total"] > 0
        else TARGET_CURRENCY_WEIGHT
    )

    return BalanceScore(
        bucket_max=max(bucket_deviations, default=0.0),
        bucket_total=sum(bucket_deviations),
        intra_max=max(intra_deviations, default=0.0),
        intra_total=sum(intra_deviations),
        currency=currency_deviation,
    )


def portfolio_snapshot(
    holdings: dict[str, int | float],
    prices: dict[str, float],
    usdcny: float,
) -> PortfolioSnapshot:
    """Build the complete current allocation snapshot without IO or planning."""

    ticker_vals = ticker_values_cny(holdings, prices, usdcny)
    bucket_vals = bucket_values(ticker_vals)
    total = total_value(bucket_vals)
    weights = bucket_weights(bucket_vals)

    ticker_rows: list[TickerSnapshot] = []
    for bucket in BUCKET_ORDER:
        bucket_total = bucket_vals[bucket]
        target = 1.0 / len(BUCKET_TICKERS[bucket])
        for ticker in BUCKET_TICKERS[bucket]:
            value = ticker_vals[ticker]
            ticker_rows.append(
                TickerSnapshot(
                    bucket=bucket,
                    ticker=ticker,
                    currency=TICKER_CURRENCY[ticker],
                    shares=holdings.get(ticker, 0),
                    price=prices[ticker],
                    value_cny=value,
                    portfolio_weight=value / total if total > 0 else None,
                    bucket_weight=value / bucket_total if bucket_total > 0 else None,
                    bucket_target=target,
                )
            )
    bucket_rows = tuple(
        BucketSnapshot(
            bucket=bucket,
            value_cny=bucket_vals[bucket],
            weight=weights[bucket] if total > 0 else None,
            target=TARGET_BUCKET_WEIGHT,
            deviation=weights[bucket] - TARGET_BUCKET_WEIGHT if total > 0 else None,
            corridor=(
                None
                if total <= 0
                else "below"
                if weights[bucket] < CORRIDOR_LOWER
                else "above"
                if weights[bucket] > CORRIDOR_UPPER
                else "within"
            ),
        )
        for bucket in BUCKET_ORDER
    )

    split = currency_split(holdings, prices, usdcny)
    currency_rows = tuple(
        CurrencySnapshot(
            currency=currency,
            value_cny=split[currency.lower()],
            weight=split[currency.lower()] / split["total"] if split["total"] > 0 else None,
            target=TARGET_CURRENCY_WEIGHT,
            deviation=(
                split[currency.lower()] / split["total"] - TARGET_CURRENCY_WEIGHT
                if split["total"] > 0
                else None
            ),
        )
        for currency in ("USD", "CNY")
    )

    return PortfolioSnapshot(
        total_value_cny=total,
        usdcny=usdcny,
        tickers=tuple(ticker_rows),
        buckets=bucket_rows,
        currencies=currency_rows,
        score=balance_score(holdings, prices, usdcny),
        corridor_breached=is_corridor_breached(bucket_vals),
    )
