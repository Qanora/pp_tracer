"""Portfolio computation & allocation table formatting.

Extracted from cli.py to reduce God Module size and enable unit testing.
"""

from typing import Dict, List, Tuple

from ppt.constants import BUCKETS, CNY_TICKERS
from ppt.display import Color, cols, price_str, ticker_display, ticker_unit
from ppt.valuation import bucket_values, bucket_weights, ticker_values_cny, total_value


def compute_portfolio(holdings: dict, prices: dict, usdcny: float) -> dict:
    """Compute full portfolio snapshot."""
    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    w = bucket_weights(bv) if V > 0 else {b: 0.0 for b in BUCKETS}
    return {"ticker_values": tv, "bucket_values": bv, "total_value": V, "weights": w}


def portfolio_snapshot(
    holdings: dict, prices: dict, usdcny: float, target_weights: dict
) -> dict:
    """Compute portfolio weights, deviations, and total value."""
    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    w = bucket_weights(bv) if V > 0 else {b: 0.0 for b in BUCKETS}
    devs = {b: w[b] - target_weights[b] for b in BUCKETS}
    return {"weights": w, "deviations": devs, "total_value": V, "bucket_values": bv}


def build_allocation_table(
    ticker_shares: dict, prices: dict, usdcny: float
) -> Tuple[list, float]:
    """Build formatted allocation table lines. Returns (lines, total_cny)."""
    # Header and rows via CJK-aware cols()
    lines = [f"[{Color.fg_muted}]{cols(('代码',8,'left'), ('股数',9,'right'), ('单价',10,'right'), ('金额',11,'right'))}[/]"]
    total = 0.0
    for ticker, shares in ticker_shares.items():
        if shares <= 0:
            continue
        p_cny = prices.get(ticker, 0) * (usdcny if ticker not in CNY_TICKERS else 1)
        amt = shares * p_cny
        total += amt
        lines.append(cols(
            (ticker_display(ticker), 8, 'left'),
            (f"{shares:.0f}{ticker_unit(ticker)}", 9, 'right'),
            (price_str(ticker, prices.get(ticker, 0)), 10, 'right'),
            (f"¥{amt:>10,.0f}", 11, 'right'),
        ))
    lines.append(f"─── 合计 ¥{total:,.0f}")
    return lines, total
