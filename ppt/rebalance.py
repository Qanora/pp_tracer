"""Rebalancing engine (§4.6–§4.7, §4.11).

Pure calculation layer — no IO, no print, no side effects.
"""

import math
from typing import Dict, List, Optional, Tuple

from ppt.constants import (
    BUCKET_TICKERS,
    BUCKETS,
    CNY_TICKERS,
    EPSILON,
    MIN_TRADE_AMOUNT,
    TICKER_LOT_SIZE,
)
from ppt.valuation import bucket_values, bucket_weights, ticker_values_cny, total_value

# ── §4.6 强制再平衡：联立方程求解 ────────────────────────────────────────────


def single_over_rebalance(
    V_b: float,
    w_star: float,
    V: float,
    price: float,
    max_shares: Optional[float] = None,
) -> float:
    """Single overweight bucket analytic solution.

    s = (V_b - w*_b * V) / (p * (1 - w*_b))

    Shares rounded UP (ceil), clamped to max_shares (holdings).
    Returns 0 if bucket is not over or price=0.
    """
    if price < EPSILON:
        return 0.0
    excess = V_b - w_star * V
    if excess <= EPSILON * V:
        return 0.0
    denom = price * (1.0 - w_star)
    if abs(denom) < EPSILON:
        return 0.0
    s = math.ceil(excess / denom - EPSILON)
    s = max(s, 0.0)
    if max_shares is not None:
        s = min(s, max_shares)
    return float(s)


def multi_over_rebalance(
    over: Dict[str, Dict[str, float]],
    V: float,
) -> Dict[str, float]:
    """Multi-bucket simultaneous over-rebalance (simultaneous equations).

    over: {bucket: {V_b, w_star, price}}

    S = (sum V_i - V * sum w*_i) / (1 - sum w*_i)

    Special: if sum(w*_i) ≈ 1 (all buckets over, degenerate),
    each bucket solved independently.
    """
    if not over:
        return {}

    sum_w = sum(v["w_star"] for v in over.values())
    sum_V = sum(v["V_b"] for v in over.values())

    # Degenerate: all buckets over
    if abs(1.0 - sum_w) < EPSILON:
        result: Dict[str, float] = {}
        for b, data in over.items():
            s = single_over_rebalance(data["V_b"], data["w_star"], V, data["price"])
            if s > 0:
                result[b] = s
        return result

    S = (sum_V - V * sum_w) / (1.0 - sum_w)
    if S <= 0:
        return {}

    result = {}
    for b, data in over.items():
        sell_amount = data["V_b"] - data["w_star"] * (V - S)
        if sell_amount > EPSILON:
            s = math.ceil(sell_amount / data["price"] - EPSILON)
            if s > 0:
                result[b] = float(s)
    return result


def multi_under_rebalance(
    under: Dict[str, Dict[str, float]],
    V: float,
) -> Dict[str, float]:
    """Multi-bucket simultaneous under-rebalance.

    under: {bucket: {V_b, w_star, price}}

    B = (V * sum w*_i - sum V_i) / (1 - sum w*_i)
    """
    if not under:
        return {}

    sum_w = sum(v["w_star"] for v in under.values())
    sum_V = sum(v["V_b"] for v in under.values())

    if abs(1.0 - sum_w) < EPSILON:
        return {}

    B = (V * sum_w - sum_V) / (1.0 - sum_w)
    if B <= 0:
        return {}

    result = {}
    for b, data in under.items():
        buy_amount = data["w_star"] * (V + B) - data["V_b"]
        if buy_amount > EPSILON:
            s = math.floor(buy_amount / data["price"] + EPSILON)
            if s > 0:
                result[b] = float(s)
    return result


# ── §4.7 增量分配（定投）────────────────────────────────────────────────────


def dca_allocate(
    C: float,
    state: dict,
    tolerance: float = 0.005,
    elasticity: float = 1.5,
    min_trade: float = MIN_TRADE_AMOUNT,
) -> Dict[str, float]:
    """Incremental DCA allocation.

    1. Gap identification: only buckets with gap > tolerance * (V+C)
    2. Elastic weighting: weight_b = gap_b^elasticity
    3. Fee filtering: iteratively remove allocations < min_trade
    4. Discretization: Hamilton method (max remainder)
    5. In-bucket ticker selection: pick lower market-cap ticker

    Returns: {ticker: shares} (already discretized to lot sizes)
    """
    holdings: Dict[str, float] = state["holdings"]
    prices: Dict[str, float] = state["prices"]
    usdcny: float = state["usdcny"]
    target_weights: Dict[str, float] = state["target_weights"]

    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    V_new = V + C

    # Step 1: Gap identification
    gaps: Dict[str, float] = {}
    for b in BUCKETS:
        target_val = V_new * target_weights[b]
        gap = target_val - bv[b]
        threshold = V_new * tolerance
        if gap > threshold:
            gaps[b] = gap

    # Degenerate: total=0 (first investment) → equal split
    if V < EPSILON and not gaps:
        return _equal_split_first_buy(C, prices, usdcny)

    # Degenerate: all within tolerance → proportional to relative gap
    if not gaps:
        for b in BUCKETS:
            target_val = V_new * target_weights[b]
            gap = target_val - bv[b]
            if gap > EPSILON:
                gaps[b] = gap

    # Step 2: Elastic weighting
    weights = {b: g**elasticity for b, g in gaps.items()}
    total_w = sum(weights.values())
    if total_w < EPSILON:
        return {}

    alloc = {b: C * weights[b] / total_w for b in weights}

    # Step 3: Fee filtering — iteratively remove below min_trade
    alloc = _min_trade_filter(alloc, gaps, prices, usdcny, min_trade)

    # Step 4: Discretization with Hamilton method
    result = _discretize_hamilton(alloc, prices, usdcny, holdings)

    return result


def _equal_split_first_buy(
    C: float,
    prices: Dict[str, float],
    usdcny: float,
) -> Dict[str, float]:
    """First investment: equal 25% split across 4 buckets."""
    per_bucket = C / 4.0
    result: Dict[str, float] = {}
    for bucket, tickers in BUCKET_TICKERS.items():
        # Pick primary ticker for single-ticker buckets
        ticker = tickers[0]
        price_cny = prices.get(ticker, 0.0) * (usdcny if ticker not in CNY_TICKERS else 1.0)
        if price_cny < EPSILON:
            continue
        lot = TICKER_LOT_SIZE[ticker]
        shares = math.floor(per_bucket / price_cny / lot) * lot
        if shares > 0:
            result[ticker] = float(shares)
    return result


def _min_trade_filter(
    alloc: Dict[str, float],
    gaps: Dict[str, float],
    prices: Dict[str, float],
    usdcny: float,
    min_trade: float,
) -> Dict[str, float]:
    """Iteratively remove bucket allocations below min_trade."""
    if not alloc:
        return alloc

    while len(alloc) >= 1:
        # Find bucket with smallest allocation amount
        min_bucket = None
        min_amount = float("inf")
        min_price_cny = 0.0
        for b, amount in alloc.items():
            ticker = BUCKET_TICKERS[b][0]
            price_cny = prices.get(ticker, 0.0) * (usdcny if ticker not in CNY_TICKERS else 1.0)
            if amount < min_amount - EPSILON:
                min_amount = amount
                min_bucket = b
                min_price_cny = price_cny
            elif abs(amount - min_amount) < EPSILON and price_cny > min_price_cny:
                min_bucket = b
                min_price_cny = price_cny

        if min_bucket is None or min_amount >= min_trade - EPSILON:
            break

        if len(alloc) == 1:
            # Last bucket below min_trade → drop it
            return {}

        # Remove and redistribute
        removed = alloc.pop(min_bucket)
        total_remaining = sum(alloc.values())
        if total_remaining > EPSILON:
            for b in alloc:
                alloc[b] += removed * alloc[b] / total_remaining

    return alloc


def _discretize_hamilton(
    alloc: Dict[str, float],
    prices: Dict[str, float],
    usdcny: float,
    holdings: Dict[str, float] = None,
) -> Dict[str, float]:
    """Discretize allocation to lot-size units using Hamilton (max remainder) method."""
    # Map bucket allocations to ticker shares
    ticker_alloc: Dict[str, float] = {}
    for bucket, amount in alloc.items():
        tickers = BUCKET_TICKERS[bucket]
        if len(tickers) == 1:
            ticker = tickers[0]
        else:
            # Pick lower market-cap ticker for buy side (§1)
            if holdings:
                t1, t2 = tickers[0], tickers[1]
                v1 = holdings.get(t1, 0) * prices.get(t1, 0) * (usdcny if t1 not in CNY_TICKERS else 1)
                v2 = holdings.get(t2, 0) * prices.get(t2, 0) * (usdcny if t2 not in CNY_TICKERS else 1)
                ticker = t1 if v1 <= v2 else t2
            else:
                ticker = tickers[0]
        price_cny = prices.get(ticker, 0.0) * (usdcny if ticker not in CNY_TICKERS else 1.0)
        if price_cny < EPSILON:
            continue
        ticker_alloc[ticker] = amount / price_cny

    # Floor to lot sizes
    result: Dict[str, float] = {}
    remainders: List[Tuple[str, float]] = []
    total_floor_value = 0.0

    for ticker, exact_shares in ticker_alloc.items():
        lot = TICKER_LOT_SIZE[ticker]
        price_cny = prices.get(ticker, 0.0) * (usdcny if ticker not in CNY_TICKERS else 1.0)
        floored = math.floor(exact_shares / lot) * lot
        result[ticker] = float(floored)
        total_floor_value += floored * price_cny
        remainder = (exact_shares - floored) * price_cny
        remainders.append((ticker, remainder))

    # Distribute remaining funds via max remainder
    remaining = sum(alloc.values()) - total_floor_value
    if remaining > 0:
        remainders.sort(key=lambda x: x[1], reverse=True)
        for ticker, _ in remainders:
            lot = TICKER_LOT_SIZE[ticker]
            price_cny = prices.get(ticker, 0.0) * (usdcny if ticker not in CNY_TICKERS else 1.0)
            if price_cny < EPSILON:
                continue
            one_lot_value = lot * price_cny
            if remaining >= one_lot_value - EPSILON:
                result[ticker] += float(lot)
                remaining -= one_lot_value
            else:
                break

    # Remove zero-share entries
    return {t: s for t, s in result.items() if s > 0}


# ── §4.11 定投达标方案 ───────────────────────────────────────────────────────


def dca_minimum_plan(
    state: dict,
    tolerance: float = 0.005,
) -> Tuple[float, Dict[str, float]]:
    """Minimum investment to bring all buckets within tolerance.

    1. Gate: max_dev < tolerance → return (0, {})
    2. Theoretical minimum (underweight buckets only)
    3. Feasibility: ensure at least 1 lot per bucket
    4. Round up to nearest 100 CNY
    5. Over-shoot protection: verify max_dev after allocation < before

    Returns: (C_min, plan) where plan = {ticker: shares}
    """
    holdings: Dict[str, float] = state["holdings"]
    prices: Dict[str, float] = state["prices"]
    usdcny: float = state["usdcny"]
    target_weights: Dict[str, float] = state["target_weights"]

    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    w = bucket_weights(bv)

    # Deviation check
    max_dev = max(abs(w[b] - target_weights[b]) for b in BUCKETS)
    if max_dev < tolerance:
        return (0.0, {})

    # Identify underweight buckets
    under_sum_w = 0.0
    under_sum_V = 0.0
    under_buckets = []
    for b in BUCKETS:
        target_val = V * target_weights[b]
        if bv[b] < target_val - EPSILON:
            under_buckets.append(b)
            under_sum_w += target_weights[b]
            under_sum_V += bv[b]

    if not under_buckets:
        return (0.0, {})

    # Theoretical minimum
    denom = 1.0 - under_sum_w
    if abs(denom) < EPSILON:
        # All buckets under → degenerate
        max_gap = max(
            target_weights[b] * V - bv[b] for b in BUCKETS
        )
        k = len(BUCKETS)
        C = max(max_gap * k / (k - 1), MIN_TRADE_AMOUNT * k)
    else:
        C = (under_sum_w * V - under_sum_V) / denom

    # Feasibility: ensure at least 1 lot in each under bucket
    for b in under_buckets:
        ticker = BUCKET_TICKERS[b][0]
        price_cny = prices.get(ticker, 0.0) * (usdcny if ticker not in CNY_TICKERS else 1.0)
        lot = TICKER_LOT_SIZE[ticker]
        min_cost = lot * price_cny
        alloc_to_b = C * (target_weights[b] * V - bv[b])
        sum_gaps = sum(max(target_weights[x] * V - bv[x], 0.0) for x in under_buckets)
        if sum_gaps > EPSILON:
            alloc_to_b = C * max(target_weights[b] * V - bv[b], 0.0) / sum_gaps
        if alloc_to_b < min_cost:
            C = max(C, min_cost * sum_gaps / max(target_weights[b] * V - bv[b], EPSILON))

    # Round up to nearest 100 CNY
    C = max(math.ceil(C / 100.0) * 100.0, 100.0)

    # Generate plan (gap^1.0 per §4.11 step 6, no elasticity amplification)
    plan = dca_allocate(C, state, tolerance=tolerance, elasticity=1.0)

    # Over-shoot protection
    if plan:
        # Simulate post-allocation
        new_holdings = dict(holdings)
        for t, s in plan.items():
            new_holdings[t] = new_holdings.get(t, 0.0) + s
        new_tv = ticker_values_cny(new_holdings, prices, usdcny)
        new_bv = bucket_values(new_tv)
        new_w = bucket_weights(new_bv)
        new_max_dev = max(abs(new_w[b] - target_weights[b]) for b in BUCKETS)
        if new_max_dev >= max_dev - EPSILON:
            return (0.0, {})

    return (C, plan)
