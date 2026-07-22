"""Pure portfolio valuation and balance scoring.

The target portfolio has three strictly ordered objectives:

1. four buckets at 25% each;
2. equal market value for the tickers inside each bucket;
3. total USD/CNY market value at 50%/50%.

This module deliberately contains no IO, configuration, or presentation code.
"""

from __future__ import annotations

from dataclasses import dataclass

from ppt.constants import (
    BUCKET_ORDER,
    BUCKET_TICKERS,
    CNY_TICKERS,
    CORRIDOR_LOWER,
    CORRIDOR_UPPER,
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
