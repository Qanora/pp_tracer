"""Pure, unified portfolio planner.

The planner chooses one legal final holding vector and derives signed trades
from it.  DCA, cross-bucket rebalancing, intra-bucket rebalancing, currency
balancing, and the GLDM/SGOV CNY conversions therefore cannot produce
contradictory buy and sell instructions for the same ticker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product

from ppt.constants import (
    BUCKET_ORDER,
    BUCKET_TICKERS,
    CNY_TICKERS,
    INTRA_BUCKET_SELL_THRESHOLD,
    TARGET_BUCKET_WEIGHT,
    TICKER_LOT_SIZE,
    TICKER_ORDER,
    TICKER_WHITELIST,
)
from ppt.valuation import (
    BalanceScore,
    balance_score,
    bucket_values,
    bucket_weights,
    is_corridor_breached,
    ticker_values_cny,
    total_value,
)

_VALUE_EPSILON = 1e-7
_SCORE_DIGITS = 12
_TICKERS = TICKER_ORDER


@dataclass(frozen=True)
class PlanResult:
    """Complete result of a side-effect-free planning run."""

    trades: dict[str, int]
    before_score: BalanceScore
    after_score: BalanceScore
    buy_cost: float
    sell_proceeds: float
    unused_amount: float
    final_holdings: dict[str, int]
    corridor_breached: bool


@dataclass(frozen=True)
class _PlanningContext:
    current: dict[str, int]
    prices: dict[str, float]
    prices_cny: dict[str, float]
    usdcny: float
    budget: float
    cross_sell_buckets: frozenset[str]
    intra_sell_tickers: frozenset[str]


def build_plan(
    holdings: dict[str, int | float],
    prices: dict[str, float],
    usdcny: float,
    budget: float,
) -> PlanResult:
    """Return the lexicographically best legal plan found for ``budget``.

    The search starts from the continuous water-filling solution, enumerates a
    finite legal-lot neighbourhood around it, then performs deterministic
    one-lot and paired-swap improvement.  Every candidate is compared by the
    full ``bucket -> intra-bucket -> currency`` score, followed only by unused
    money and turnover tie-breakers.

    The 15%-35% corridor is a permission gate for *net* cross-bucket sales.
    Inside it, a bucket may receive new money or perform an eligible internal
    switch, but may not fund another bucket.  Once any bucket breaches the
    corridor, buckets initially above 25% may fund the rest of the portfolio.
    """

    current, clean_prices, clean_rate, clean_budget = _validate_inputs(
        holdings, prices, usdcny, budget
    )
    prices_cny = {
        ticker: clean_prices[ticker] * (1.0 if ticker in CNY_TICKERS else clean_rate)
        for ticker in _TICKERS
    }
    before_ticker_values = ticker_values_cny(current, clean_prices, clean_rate)
    before_values = bucket_values(before_ticker_values)
    before_weights = bucket_weights(before_values)
    corridor_breached = is_corridor_breached(before_values)
    cross_sell_buckets = frozenset(
        bucket
        for bucket in BUCKET_ORDER
        if corridor_breached and before_weights[bucket] > TARGET_BUCKET_WEIGHT
    )
    intra_sell_tickers = _intra_sell_sources(current, before_ticker_values, before_values)
    ctx = _PlanningContext(
        current=current,
        prices=clean_prices,
        prices_cny=prices_cny,
        usdcny=clean_rate,
        budget=clean_budget,
        cross_sell_buckets=cross_sell_buckets,
        intra_sell_tickers=intra_sell_tickers,
    )

    desired_bucket_values = _desired_bucket_values(before_values, clean_budget, corridor_breached)
    per_bucket_candidates = [
        _bucket_candidates(bucket, desired_bucket_values[bucket], ctx)
        for bucket in BUCKET_ORDER
    ]

    best = dict(current)
    best_key = _candidate_key(best, ctx)
    for bucket_vectors in product(*per_bucket_candidates):
        candidate = {
            ticker: shares
            for vector in bucket_vectors
            for ticker, shares in vector.items()
        }
        if not _is_feasible(candidate, ctx):
            continue
        candidate_key = _candidate_key(candidate, ctx)
        if candidate_key < best_key:
            best = candidate
            best_key = candidate_key

    best = _improve_by_legal_lots(best, best_key, ctx)
    trades = {
        ticker: best[ticker] - current[ticker]
        for ticker in _TICKERS
        if best[ticker] != current[ticker]
    }
    buy_cost, sell_proceeds = _cash_totals(best, ctx)
    unused = clean_budget + sell_proceeds - buy_cost
    if abs(unused) < _VALUE_EPSILON:
        unused = 0.0

    return PlanResult(
        trades=trades,
        before_score=balance_score(current, clean_prices, clean_rate),
        after_score=balance_score(best, clean_prices, clean_rate),
        buy_cost=buy_cost,
        sell_proceeds=sell_proceeds,
        unused_amount=unused,
        final_holdings=best,
        corridor_breached=corridor_breached,
    )


def _validate_inputs(
    holdings: dict[str, int | float],
    prices: dict[str, float],
    usdcny: float,
    budget: float,
) -> tuple[dict[str, int], dict[str, float], float, float]:
    if not isinstance(holdings, dict):
        raise ValueError("holdings must be a mapping")
    unknown = set(holdings) - TICKER_WHITELIST
    if unknown:
        raise ValueError(f"unknown holdings tickers: {', '.join(sorted(unknown))}")

    current: dict[str, int] = {}
    for ticker in _TICKERS:
        shares = holdings.get(ticker, 0)
        if (
            isinstance(shares, bool)
            or not isinstance(shares, (int, float))
            or not math.isfinite(shares)
            or shares < 0
            or shares != int(shares)
            or int(shares) % TICKER_LOT_SIZE[ticker] != 0
        ):
            raise ValueError(f"invalid holdings for {ticker}: {shares}")
        current[ticker] = int(shares)

    if not isinstance(prices, dict):
        raise ValueError("prices must be a mapping")
    missing = TICKER_WHITELIST - set(prices)
    if missing:
        raise ValueError(f"missing prices: {', '.join(sorted(missing))}")
    clean_prices: dict[str, float] = {}
    for ticker in _TICKERS:
        price = prices[ticker]
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
        ):
            raise ValueError(f"invalid price for {ticker}: {price}")
        clean_prices[ticker] = float(price)

    if (
        isinstance(usdcny, bool)
        or not isinstance(usdcny, (int, float))
        or not math.isfinite(usdcny)
        or usdcny <= 0
    ):
        raise ValueError(f"invalid USD/CNY rate: {usdcny}")
    if (
        isinstance(budget, bool)
        or not isinstance(budget, (int, float))
        or not math.isfinite(budget)
        or budget <= 0
    ):
        raise ValueError(f"invalid budget: {budget}")

    return current, clean_prices, float(usdcny), float(budget)


def _intra_sell_sources(
    holdings: dict[str, int],
    ticker_vals: dict[str, float],
    bucket_vals: dict[str, float],
) -> frozenset[str]:
    """Return initially dominant tickers allowed to fund an internal switch."""

    allowed_sources = {
        "stock": frozenset(BUCKET_TICKERS["stock"]),
        "gold": frozenset({"GLDM"}),
        "cash": frozenset({"SGOV"}),
    }
    sources: set[str] = set()
    for bucket in BUCKET_ORDER:
        tickers = BUCKET_TICKERS[bucket]
        total = bucket_vals[bucket]
        if len(tickers) < 2 or total <= 0:
            continue
        dominant = max(tickers, key=lambda ticker: ticker_vals[ticker])
        if (
            holdings[dominant] > 0
            and dominant in allowed_sources.get(bucket, frozenset())
            and ticker_vals[dominant] / total > INTRA_BUCKET_SELL_THRESHOLD
        ):
            sources.add(dominant)
    return frozenset(sources)


def _desired_bucket_values(
    current_values: dict[str, float],
    budget: float,
    corridor_breached: bool,
) -> dict[str, float]:
    """Continuous priority-one solution before legal-lot discretization."""

    current_total = total_value(current_values)
    if corridor_breached:
        target = (current_total + budget) / len(BUCKET_ORDER)
        return {bucket: target for bucket in BUCKET_ORDER}

    low = min(current_values.values(), default=0.0)
    high = max(current_values.values(), default=0.0) + budget
    for _ in range(80):
        level = (low + high) / 2.0
        required = sum(max(level - current_values[bucket], 0.0) for bucket in BUCKET_ORDER)
        if required <= budget:
            low = level
        else:
            high = level
    return {bucket: max(current_values[bucket], low) for bucket in BUCKET_ORDER}


def _nearby_share_counts(target_value: float, unit_value: float, lot: int) -> set[int]:
    exact_lots = max(target_value, 0.0) / unit_value
    base = math.floor(exact_lots)
    return {max(base + offset, 0) * lot for offset in range(-2, 4)}


def _bucket_candidates(
    bucket: str,
    desired_value: float,
    ctx: _PlanningContext,
) -> tuple[dict[str, int], ...]:
    """Build a small, diverse legal-lot neighbourhood for one bucket."""

    tickers = BUCKET_TICKERS[bucket]
    share_options: dict[str, set[int]] = {}
    for ticker in tickers:
        lot = TICKER_LOT_SIZE[ticker]
        unit_value = lot * ctx.prices_cny[ticker]
        targets = [desired_value / len(tickers), desired_value]
        for other in tickers:
            if other != ticker:
                targets.append(
                    desired_value - ctx.current[other] * ctx.prices_cny[other]
                )
        options = {ctx.current[ticker]}
        for target in targets:
            options.update(_nearby_share_counts(target, unit_value, lot))
        share_options[ticker] = options

    raw: list[dict[str, int]] = []
    for counts in product(*(sorted(share_options[ticker]) for ticker in tickers)):
        vector = dict(zip(tickers, counts))
        if _bucket_vector_allowed(bucket, vector, ctx):
            raw.append(vector)

    current_vector = {ticker: ctx.current[ticker] for ticker in tickers}
    if current_vector not in raw:
        raw.append(current_vector)

    def local_components(vector: dict[str, int]) -> tuple[float, float, float, float]:
        values = {
            ticker: vector[ticker] * ctx.prices_cny[ticker] for ticker in tickers
        }
        bucket_value = sum(values.values())
        equal_value = bucket_value / len(tickers)
        intra = sum(abs(value - equal_value) for value in values.values())
        cny_value = sum(values[ticker] for ticker in tickers if ticker in CNY_TICKERS)
        turnover = sum(
            abs(vector[ticker] - ctx.current[ticker]) * ctx.prices_cny[ticker]
            for ticker in tickers
        )
        return (
            abs(bucket_value - desired_value),
            intra,
            abs(cny_value - bucket_value / 2),
            turnover,
        )

    selected: list[dict[str, int]] = []

    def add(vectors: list[dict[str, int]]) -> None:
        for vector in vectors:
            if vector not in selected:
                selected.append(vector)

    add(sorted(raw, key=local_components)[:10])
    add(
        sorted(
            raw,
            key=lambda vector: (
                local_components(vector)[0],
                local_components(vector)[1],
                sum(
                    vector[ticker] * ctx.prices_cny[ticker]
                    for ticker in tickers
                    if ticker in CNY_TICKERS
                ),
            ),
        )[:3]
    )
    add(
        sorted(
            raw,
            key=lambda vector: (
                local_components(vector)[0],
                local_components(vector)[1],
                -sum(
                    vector[ticker] * ctx.prices_cny[ticker]
                    for ticker in tickers
                    if ticker in CNY_TICKERS
                ),
            ),
        )[:3]
    )
    add([current_vector])
    return tuple(selected)


def _bucket_vector_allowed(
    bucket: str,
    vector: dict[str, int],
    ctx: _PlanningContext,
) -> bool:
    tickers = BUCKET_TICKERS[bucket]
    delta_value = sum(
        (vector[ticker] - ctx.current[ticker]) * ctx.prices_cny[ticker]
        for ticker in tickers
    )
    if delta_value < -_VALUE_EPSILON and bucket not in ctx.cross_sell_buckets:
        return False

    for ticker in tickers:
        if vector[ticker] < 0 or vector[ticker] % TICKER_LOT_SIZE[ticker] != 0:
            return False
        if vector[ticker] >= ctx.current[ticker]:
            continue
        if bucket in ctx.cross_sell_buckets:
            continue
        if ticker not in ctx.intra_sell_tickers:
            return False
    return True


def _cash_totals(
    final: dict[str, int],
    ctx: _PlanningContext,
) -> tuple[float, float]:
    buy_cost = 0.0
    sell_proceeds = 0.0
    for ticker in _TICKERS:
        delta = final[ticker] - ctx.current[ticker]
        amount = abs(delta) * ctx.prices_cny[ticker]
        if delta > 0:
            buy_cost += amount
        elif delta < 0:
            sell_proceeds += amount
    return buy_cost, sell_proceeds


def _is_feasible(final: dict[str, int], ctx: _PlanningContext) -> bool:
    if set(final) != set(_TICKERS):
        return False
    for ticker in _TICKERS:
        shares = final[ticker]
        if shares < 0 or shares % TICKER_LOT_SIZE[ticker] != 0:
            return False
    for bucket in BUCKET_ORDER:
        vector = {ticker: final[ticker] for ticker in BUCKET_TICKERS[bucket]}
        if not _bucket_vector_allowed(bucket, vector, ctx):
            return False
    buy_cost, sell_proceeds = _cash_totals(final, ctx)
    return buy_cost <= ctx.budget + sell_proceeds + _VALUE_EPSILON


def _candidate_key(final: dict[str, int], ctx: _PlanningContext) -> tuple:
    score = balance_score(final, ctx.prices, ctx.usdcny)
    buy_cost, sell_proceeds = _cash_totals(final, ctx)
    unused = ctx.budget + sell_proceeds - buy_cost
    turnover = buy_cost + sell_proceeds
    trade_count = sum(final[ticker] != ctx.current[ticker] for ticker in _TICKERS)
    return (
        *(round(component, _SCORE_DIGITS) for component in score.as_tuple()),
        round(max(unused, 0.0), 6),
        round(turnover, 6),
        trade_count,
        *(final[ticker] for ticker in _TICKERS),
    )


def _improve_by_legal_lots(
    initial: dict[str, int],
    initial_key: tuple,
    ctx: _PlanningContext,
) -> dict[str, int]:
    """Finish the finite seed search with deterministic legal-lot moves.

    Paired moves are important: a sale may be harmful in isolation but useful
    when it immediately funds another bucket or an intra-bucket conversion,
    including GLDM -> 518880.SS and SGOV -> 511360.SS.
    """

    best = dict(initial)
    best_key = initial_key
    for _ in range(256):
        next_best = best
        next_key = best_key
        moves: list[tuple[tuple[str, int], ...]] = []
        for ticker in _TICKERS:
            lot = TICKER_LOT_SIZE[ticker]
            moves.append(((ticker, lot),))
            moves.append(((ticker, -lot),))
        for sell_ticker in _TICKERS:
            for buy_ticker in _TICKERS:
                if sell_ticker == buy_ticker:
                    continue
                moves.append(
                    (
                        (sell_ticker, -TICKER_LOT_SIZE[sell_ticker]),
                        (buy_ticker, TICKER_LOT_SIZE[buy_ticker]),
                    )
                )

        for move in moves:
            candidate = dict(best)
            for ticker, delta in move:
                candidate[ticker] += delta
            if not _is_feasible(candidate, ctx):
                continue
            candidate_key = _candidate_key(candidate, ctx)
            if candidate_key < next_key:
                next_best = candidate
                next_key = candidate_key

        if next_key >= best_key:
            break
        best = next_best
        best_key = next_key
    return best
