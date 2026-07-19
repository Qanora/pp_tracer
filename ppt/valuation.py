"""Pure calculation layer (§4.1–§4.5).

No IO, no print, no side effects. All configurable parameters
are injected via function arguments (not read from files/env).
"""

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from ppt.constants import (
    BUCKET_TICKERS,
    BUCKETS,
    CNY_TICKERS,
    CORRIDOR_HARD_CAP,
    CORRIDOR_HARD_FLOOR,
    CORRIDOR_HMIN,
    EPSILON,
    VOL_FALLBACK,
    VOL_FLOOR,
)

# ── §4.1 权重计算 ────────────────────────────────────────────────────────────


def ticker_values_cny(
    holdings: Dict[str, float],
    prices: Dict[str, float],
    usdcny: float,
) -> Dict[str, float]:
    """ticker → CNY market value.

    USD tickers: holdings * price * usdcny
    CNY tickers: holdings * price (direct)
    """
    result: Dict[str, float] = {}
    for t, shares in holdings.items():
        if shares == 0:
            result[t] = 0.0
            continue
        price = prices.get(t, 0.0)
        if t in CNY_TICKERS:
            result[t] = shares * price
        else:
            result[t] = shares * price * usdcny
    return result


def bucket_values(ticker_vals: Dict[str, float]) -> Dict[str, float]:
    """Sum ticker CNY values into bucket values."""
    bv: Dict[str, float] = {b: 0.0 for b in BUCKETS}
    for bucket, tickers in BUCKET_TICKERS.items():
        bv[bucket] = sum(ticker_vals.get(t, 0.0) for t in tickers)
    return bv


def bucket_weights(bucket_vals: Dict[str, float]) -> Dict[str, float]:
    """Bucket weight = bucket_value / total."""
    total = sum(bucket_vals.values())
    if abs(total) < EPSILON:
        return {b: 0.0 for b in BUCKETS}
    return {b: v / total for b, v in bucket_vals.items()}


def total_value(bucket_vals: Dict[str, float]) -> float:
    """Sum of all bucket values."""
    return sum(bucket_vals.values())


def currency_split(
    holdings: Dict[str, float],
    prices: Dict[str, float],
    usdcny: float,
) -> Dict[str, float]:
    """Compute USD/CNY value split from ticker-level holdings.

    Returns: {"usd": usd_total_cny, "cny": cny_total, "total": total}
    """
    from ppt.constants import USD_TICKERS

    usd_total = 0.0
    cny_total = 0.0
    for ticker, shares in holdings.items():
        if shares <= 0:
            continue
        p_cny = prices.get(ticker, 0.0)
        if ticker in USD_TICKERS:
            val = shares * p_cny * usdcny
            usd_total += val
        else:
            val = shares * p_cny
            cny_total += val
    return {"usd": usd_total, "cny": cny_total, "total": usd_total + cny_total}


# ── §4.2 目标权重 ────────────────────────────────────────────────────────────


def equal_target_weights() -> Dict[str, float]:
    """Equal-weight: each bucket = 0.25."""
    return {b: 0.25 for b in BUCKETS}


def risk_parity_weights(
    sigmas: Dict[str, float],
    cap: float = 0.40,
    floor: float = 0.10,
    max_iter: int = 20,
) -> Dict[str, float]:
    """Risk parity with cap/floor via iterative clipping algorithm.

    1. w*_b ∝ 1/σ_b
    2. Repeatedly pin overshooting buckets to cap/floor,
       redistribute excess to free buckets proportionally.
    3. Normalize to sum=1.
    """
    buckets = list(BUCKETS)

    # Step 1: raw inverse-vol weights
    inv_vol = {b: 1.0 / max(sigmas.get(b, 0.01), EPSILON) for b in buckets}
    total_inv = sum(inv_vol.values())
    w = {b: inv_vol[b] / total_inv for b in buckets}

    # Step 2: iterative clipping
    for _ in range(max_iter):
        pinned: Dict[str, float] = {}
        free: List[str] = []
        excess = 0.0

        for b in buckets:
            if w[b] >= cap - EPSILON:
                pinned[b] = cap
                excess += w[b] - cap
            elif w[b] <= floor + EPSILON:
                pinned[b] = floor
                excess += w[b] - floor
            else:
                free.append(b)

        if not free or abs(excess) < EPSILON:
            break

        # Redistribute excess among free buckets proportionally
        free_weight_sum = sum(w[b] for b in free)
        if free_weight_sum > EPSILON:
            for b in free:
                w[b] += excess * (w[b] / free_weight_sum)
        else:
            # All buckets pinned — special case: cap-limited stay at cap,
            # rest get equal share of remaining
            remaining = 1.0 - sum(pinned.get(b, 0.0) for b in buckets if b not in free)
            if free:
                equal_share = remaining / len(free)
                for b in free:
                    w[b] = max(equal_share, floor)

        for b, val in pinned.items():
            w[b] = val

    # Step 3: normalize
    total = sum(w.values())
    if total > EPSILON:
        w = {b: v / total for b, v in w.items()}

    return w


# ── §4.3 波动率估计 ──────────────────────────────────────────────────────────


def volatility(
    prices: List[float],
    fallback: Optional[float] = None,
) -> float:
    """60-day rolling annualized volatility from price sequence.

    r_t = (P_t - P_{t-1}) / P_{t-1}
    sigma = std(r) * sqrt(252)

    Returns fallback if <20 returns available; floor at VOL_FLOOR.
    """
    if len(prices) < 2:
        return fallback if fallback is not None else VOL_FALLBACK["stock"]

    arr = np.asarray(prices, dtype=np.float64)
    returns = np.diff(arr) / arr[:-1]

    if len(returns) < 20:
        return fallback if fallback is not None else VOL_FALLBACK["stock"]

    sigma = float(np.std(returns, ddof=1) * np.sqrt(252))

    return max(sigma, VOL_FLOOR)


# ── §4.4 自适应走廊 ──────────────────────────────────────────────────────────


def corridor_bounds(
    w_star: float,
    sigma: Optional[float],
    k: float = 2.5,
) -> Tuple[float, float]:
    """Adaptive rebalancing corridor.

    h = max(k * sigma / sqrt(12), h_min)
    L = max(w* - h, hard_floor)
    U = min(w* + h, hard_cap)

    If sigma is None → fallback fixed thresholds [0.15, 0.35].
    """
    if sigma is None:
        return (0.15, 0.35)

    h = max(k * sigma / math.sqrt(12), CORRIDOR_HMIN)
    L = max(w_star - h, CORRIDOR_HARD_FLOOR)
    U = min(w_star + h, CORRIDOR_HARD_CAP)
    return (L, U)


# ── §4.5 趋势信号与走廊调整 ──────────────────────────────────────────────────


def trend_signal(
    prices: List[float],
    S: int = 10,
    L: int = 20,
) -> float:
    """Trend signal: MA_S / MA_L - 1.

    Returns 0.0 if insufficient data (< L prices).
    """
    if len(prices) < L:
        return 0.0

    arr = np.asarray(prices, dtype=np.float64)
    ma_short = float(np.mean(arr[-S:]))
    ma_long = float(np.mean(arr[-L:]))

    if abs(ma_long) < EPSILON:
        return 0.0

    return ma_short / ma_long - 1.0


def trend_adjusted_corridor(
    w_star: float,
    sigma: Optional[float],
    trend: float,
    k: float = 2.5,
    lam: float = 0.5,
) -> Tuple[float, float]:
    """Adjust corridor bounds based on trend signal.

    - Weak bucket (trend < 0): raise upper bound (delay selling)
    - Strong bucket (trend > 0): lower floor (delay buying)
    - Only one boundary moves, corridor never narrows.

    Δ = lam * |trend| * (U - L)
    """
    L, U = corridor_bounds(w_star, sigma, k)
    delta = lam * abs(trend) * (U - L)

    if trend < -EPSILON:
        # Weak: raise upper
        U = min(U + delta, CORRIDOR_HARD_CAP)
    elif trend > EPSILON:
        # Strong: lower floor
        L = max(L - delta, CORRIDOR_HARD_FLOOR)

    return (L, U)
