"""Conversion, intra-bucket rebalance, correlation, returns (§4.8–§4.13).

Pure calculation layer — no IO, no print, no side effects.
"""

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from ppt.constants import (
    BUCKET_TICKERS,
    BUCKETS,
    CORR_MIN_DAYS,
    EPSILON,
    FX_SPREAD,
    INTRA_BUCKET_TARGET,
    INTRA_BUCKET_THRESHOLD,
    STOCK_BOND_REVERSAL_THRESHOLD,
)

# ── §4.10 两段式换仓 ─────────────────────────────────────────────────────────


def conversion_check(
    ticker: str,
    market_value_cny: float,
    target_price_cny: float,
    conversion_shares: int,
    fx_spread: float = FX_SPREAD,
) -> dict:
    """Check if a conversion (USD→A-share) is triggered.

    GLDM → 518880.SS: threshold = 1000 * p_518880 * (1 + fx_spread)
    SGOV → 511360.SS: threshold = 100 * p_511360 * (1 + fx_spread)

    Returns: {triggered, batches, threshold_cny, buy_units}
    """
    threshold = conversion_shares * target_price_cny * (1.0 + fx_spread)
    triggered = market_value_cny >= threshold - EPSILON

    batches = 0
    buy_units = 0
    if triggered and threshold > EPSILON:
        batches = int(market_value_cny // threshold)
        buy_units = batches * conversion_shares

    return {
        "triggered": triggered,
        "batches": batches,
        "threshold_cny": threshold,
        "buy_units": buy_units,
    }


# ── §4.8 定投+换仓统一规划（净额合并）────────────────────────────────────────


def net_conversion_with_dca(
    dca_plan: Dict[str, float],
    conversion_sells: Dict[str, float],
    conversion_buys: Dict[str, float],
    prices_cny: Dict[str, float],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Net conversion against DCA plan.

    If DCA plan buys the same ticker conversion would sell:
    - DCA buy ≥ conversion sell: cancel DCA buy, conversion buys directly
    - DCA buy < conversion sell: zero DCA, reduce conversion sell

    Returns: (adjusted_dca, adjusted_conversion_sells)
    """
    adjusted_dca = dict(dca_plan)
    adjusted_sells = dict(conversion_sells)

    for ticker, sell_shares in list(conversion_sells.items()):
        if ticker in adjusted_dca:
            dca_shares = adjusted_dca[ticker]
            if dca_shares >= sell_shares - EPSILON:
                # DCA covers it → cancel DCA portion, sell nothing extra
                adjusted_dca[ticker] = dca_shares - sell_shares
                adjusted_sells[ticker] = 0.0
            else:
                # DCA partially covers → zero DCA, reduce sell
                adjusted_dca[ticker] = 0.0
                adjusted_sells[ticker] = sell_shares - dca_shares

    # Clean zero entries
    adjusted_dca = {t: s for t, s in adjusted_dca.items() if s > 0}
    adjusted_sells = {t: s for t, s in adjusted_sells.items() if s > 0}

    return adjusted_dca, adjusted_sells


# ── §4.9 桶内再均衡 ──────────────────────────────────────────────────────────


def intra_bucket_rebalance(
    V_SPYM: float,
    V_AVUV: float,
    p_SPYM: float,
    p_AVUV: float,
    threshold: float = INTRA_BUCKET_THRESHOLD,
    target_ratio: float = INTRA_BUCKET_TARGET,
    max_holdings: Optional[Dict[str, float]] = None,
) -> dict:
    """SPYM ↔ AVUV intra-bucket rebalance.

    Trigger: max(r_SPYM, r_AVUV) > threshold where r = V / V_stock.
    Target: return to 50:50.

    Returns: {triggered, sell_ticker, buy_ticker, sell_shares, buy_shares}
    """
    V_stock = V_SPYM + V_AVUV
    if V_stock < EPSILON:
        return {"triggered": False}

    r_SPYM = V_SPYM / V_stock
    r_AVUV = V_AVUV / V_stock
    max_ratio = max(r_SPYM, r_AVUV)

    if max_ratio <= threshold + EPSILON:
        return {"triggered": False}

    if r_SPYM > r_AVUV:
        over_ticker = "SPYM"
        under_ticker = "AVUV"
        V_over = V_SPYM
        p_sell = p_SPYM
        p_buy = p_AVUV
    else:
        over_ticker = "AVUV"
        under_ticker = "SPYM"
        V_over = V_AVUV
        p_sell = p_AVUV
        p_buy = p_SPYM

    # Transfer amount = V_over - target * V_stock
    transfer = V_over - target_ratio * V_stock
    if transfer <= 0 or p_sell < EPSILON:
        return {"triggered": False}

    sell_shares = math.ceil(transfer / p_sell - EPSILON)
    if max_holdings and over_ticker in max_holdings:
        sell_shares = min(sell_shares, int(max_holdings[over_ticker]))

    sell_proceeds = sell_shares * p_sell
    buy_shares = math.floor(sell_proceeds / p_buy + EPSILON) if p_buy > EPSILON else 0

    return {
        "triggered": True,
        "sell_ticker": over_ticker,
        "buy_ticker": under_ticker,
        "sell_shares": float(sell_shares),
        "buy_shares": float(buy_shares),
        "transfer_amount": transfer,
        "max_holdings": max_holdings or {},
    }


# ── §4.12 相关性分析 ─────────────────────────────────────────────────────────


def bucket_correlation(
    prices_a: List[float],
    prices_b: List[float],
    min_days: int = CORR_MIN_DAYS,
) -> Optional[float]:
    """Pearson correlation of daily returns between two buckets.

    Returns None if < min_days data or either variance is zero.
    """
    if len(prices_a) < min_days + 1 or len(prices_b) < min_days + 1:
        return None

    n = min(len(prices_a), len(prices_b)) - 1
    arr_a = np.asarray(prices_a[-n - 1:], dtype=np.float64)
    arr_b = np.asarray(prices_b[-n - 1:], dtype=np.float64)
    ret_a = np.diff(arr_a) / arr_a[:-1]
    ret_b = np.diff(arr_b) / arr_b[:-1]

    # Guard: NaN/Inf returns → reject early
    if np.any(np.isnan(ret_a)) or np.any(np.isinf(ret_a)) or \
       np.any(np.isnan(ret_b)) or np.any(np.isinf(ret_b)):
        return None

    if np.std(ret_a) < EPSILON or np.std(ret_b) < EPSILON:
        return None

    rho = float(np.corrcoef(ret_a, ret_b)[0, 1])

    if math.isnan(rho):
        return None
    return max(-1.0, min(1.0, rho))


def stock_bond_reversal(
    prices_stock: List[float],
    prices_bond: List[float],
    window: int = 30,
    threshold: float = STOCK_BOND_REVERSAL_THRESHOLD,
) -> dict:
    """Detect stock-bond correlation reversal.

    Split 61 days into first 30 + 1 split + last 30.
    Alert if rho_first < 0 AND rho_second > threshold.

    Returns: {reversal, rho_first, rho_second}
    """
    total = 2 * window + 1  # 61
    if len(prices_stock) < total or len(prices_bond) < total:
        return {"reversal": False, "rho_first": None, "rho_second": None}

    # Use last `total` data points
    stock = prices_stock[-total:]
    bond = prices_bond[-total:]

    rho_first = bucket_correlation(stock[: window + 1], bond[: window + 1])
    rho_second = bucket_correlation(stock[window:], bond[window:])

    if rho_first is None or rho_second is None:
        return {"reversal": False, "rho_first": rho_first, "rho_second": rho_second}

    reversal = rho_first < 0.0 and rho_second > threshold
    return {
        "reversal": reversal,
        "rho_first": rho_first,
        "rho_second": rho_second,
    }


# ── §4.13 收益计算 ───────────────────────────────────────────────────────────

_TICKER_TO_BUCKET: Dict[str, str] = {
    t: bucket for bucket, tickers in BUCKET_TICKERS.items() for t in tickers
}


def bucket_net_cost(transactions: List[dict]) -> Dict[str, float]:
    """Net cost per bucket from transaction history.

    Buy adds cost (+), sell reduces cost (-).
    USD transactions converted at historic usdcny rate.
    """
    costs: Dict[str, float] = {b: 0.0 for b in BUCKETS}

    for txn in transactions:
        for trade in txn.get("trades", []):
            ticker = trade["ticker"]
            bucket = _TICKER_TO_BUCKET.get(ticker)
            if bucket is None:
                continue
            # Use amount_cny as the authoritative CNY amount for this trade
            # Amounts from transactions are pre-calculated
            trade_amount = (
                trade["shares"] * trade["price"] * txn.get("usdcny", 0)
                if trade["currency"] == "USD"
                else trade["shares"] * trade["price"]
            )
            if txn["type"] == "buy":
                costs[bucket] += trade_amount
            else:
                costs[bucket] -= trade_amount

    return costs


def total_return(
    V: float,
    bucket_costs: Dict[str, float],
) -> Tuple[float, float]:
    """Total return P&L.

    P = V - sum(costs), pct = P / net_cost.
    Returns (0, 0) if net_cost = 0.
    """
    net_cost = sum(bucket_costs.values())
    if abs(net_cost) < EPSILON:
        return (0.0, 0.0)
    P = V - net_cost
    return (P, P / net_cost)


def cagr(V: float, cost: float, years: float) -> float:
    """Compound Annual Growth Rate: (V/cost)^(1/years) - 1."""
    if abs(cost) < EPSILON or years <= 0:
        return 0.0
    if V <= 0:
        return -1.0
    return (V / cost) ** (1.0 / years) - 1.0


def _npv(rate: float, cashflows: List[Tuple[float, float]], durations: List[float]) -> float:
    """Net present value of cashflows discounted at `rate`."""
    return sum(cf / (1.0 + rate) ** t for (cf, _), t in zip(cashflows, durations))


def xirr(
    cashflows: List[Tuple[float, float]],
    guess: float = 0.1,
    tol: float = 1e-6,
    max_iter: int = 200,
) -> Optional[float]:
    """XIRR via Newton iteration.

    cashflows: [(amount, day_number)] — negative=investment, positive=return.
    Returns None if insufficient data or convergence failure.
    """
    if len(cashflows) < 2:
        return None

    d0 = cashflows[0][1]
    durations = [(d - d0) / 365.0 for _, d in cashflows]

    if max(durations) < EPSILON:
        return None

    def dnpv(rate: float) -> float:
        return sum(-t * cf / (1.0 + rate) ** (t + 1.0) for (cf, _), t in zip(cashflows, durations))

    rate = guess
    for _ in range(max_iter):
        f = _npv(rate, cashflows, durations)
        df = dnpv(rate)

        if abs(f) < tol:
            return rate

        if abs(df) < EPSILON:
            # Fallback: bisection
            return _xirr_bisection(cashflows, durations)

        new_rate = rate - f / df

        # Boundary protection
        if new_rate <= -1.0:
            new_rate = (rate - 1.0) / 2.0
        elif new_rate > 10.0:
            new_rate = (rate + 10.0) / 2.0

        if abs(new_rate - rate) < tol:
            return new_rate

        rate = new_rate

    # Newton failed → fallback bisection
    return _xirr_bisection(cashflows, durations)


def _xirr_bisection(
    cashflows: List[Tuple[float, float]],
    durations: List[float],
    max_iter: int = 300,
) -> Optional[float]:
    """Bisection fallback for XIRR."""

    lo, hi = -0.99, 10.0
    f_lo = _npv(lo, cashflows, durations)
    f_hi = _npv(hi, cashflows, durations)

    if f_lo * f_hi > 0:
        return None  # No sign change

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = _npv(mid, cashflows, durations)

        if abs(f_mid) < 1e-6 or (hi - lo) < 1e-8:
            return mid

        if f_mid * f_lo < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid

    return (lo + hi) / 2.0
