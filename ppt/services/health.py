"""Health check & conversion-trade builders (§4.10, §4.12).

Extracted from cli.py to reduce God Module size and enable unit testing.
"""

import math
from typing import Dict, List, Tuple

from ppt.constants import BUCKETS
from ppt.display import cmd_hint, status_badge, ticker_display
from ppt.rebalance import intra_bucket_rebalance
from ppt.returns import bucket_correlation, conversion_check, stock_bond_reversal
from ppt.valuation import ticker_values_cny


def build_conversion_trades(
    holdings: dict, prices: dict, usdcny: float, cfg: dict
) -> Tuple[dict, dict, list]:
    """Build conversion sell/buy plans for GLDM→518880 and SGOV→511360 (§4.10).

    Returns: (conversion_sells, conversion_buys, conversion_info)
    """
    tv = ticker_values_cny(holdings, prices, usdcny)
    conv = cfg["conversion"]

    conversion_sells: dict = {}
    conversion_buys: dict = {}
    conversion_info: list = []

    # GLDM → 518880.SS
    gldm_val = tv.get("GLDM", 0)
    p_518880 = prices.get("518880.SS", 0)
    if p_518880 > 0:
        result = conversion_check("GLDM", gldm_val, p_518880, conv["gldm_shares"])
        if result["triggered"] and result["buy_units"] > 0:
            buy_shares = float(result["buy_units"])
            buy_amount_cny = buy_shares * p_518880
            p_gldm_cny = prices.get("GLDM", 0) * usdcny
            if p_gldm_cny > 0:
                sell_shares = min(
                    math.ceil(buy_amount_cny / p_gldm_cny),
                    holdings.get("GLDM", 0),
                )
                conversion_sells["GLDM"] = float(sell_shares)
                conversion_buys["518880.SS"] = buy_shares
                conversion_info.append({
                    "bucket": "gold",
                    "sell": "GLDM", "buy": "518880.SS",
                    "sell_shares": float(sell_shares), "buy_shares": buy_shares,
                    "batches": result["batches"],
                })

    # SGOV → 511360.SS
    sgov_val = tv.get("SGOV", 0)
    p_511360 = prices.get("511360.SS", 0)
    if p_511360 > 0:
        result = conversion_check("SGOV", sgov_val, p_511360, conv["sgov_shares"])
        if result["triggered"] and result["buy_units"] > 0:
            buy_shares = float(result["buy_units"])
            buy_amount_cny = buy_shares * p_511360
            p_sgov_cny = prices.get("SGOV", 0) * usdcny
            if p_sgov_cny > 0:
                sell_shares = min(
                    math.ceil(buy_amount_cny / p_sgov_cny),
                    holdings.get("SGOV", 0),
                )
                conversion_sells["SGOV"] = float(sell_shares)
                conversion_buys["511360.SS"] = buy_shares
                conversion_info.append({
                    "bucket": "cash",
                    "sell": "SGOV", "buy": "511360.SS",
                    "sell_shares": float(sell_shares), "buy_shares": buy_shares,
                    "batches": result["batches"],
                })

    return conversion_sells, conversion_buys, conversion_info


def health_check(
    state: dict,
    prices: dict,
    usdcny: float,
    price_history: list = None,
    prices_by_bucket: dict = None,
) -> List[str]:
    """Generate health check alerts (§4.12, §4.9, §4.10). Sorted by severity."""
    alerts: list = []  # (severity: 0=crit, 1=warn, 2=info, msg)

    # Conversion triggers
    tv = ticker_values_cny(state["holdings"], prices, usdcny)

    # Check GLDM → 518880
    gldm_val = tv.get("GLDM", 0)
    result = conversion_check("GLDM", gldm_val, prices.get("518880.SS", 5.50), 1000)
    if result["triggered"]:
        alerts.append((
            2,
            f"{status_badge('info')} 黄金换仓: GLDM → 518880 "
            f"({result['batches']} 批, ¥{result['threshold_cny']:,.0f} 触发线)"
            f"\n  {cmd_hint(f'ppt sell GLDM + ppt buy 518880')}"
        ))

    # Check SGOV → 511360
    sgov_val = tv.get("SGOV", 0)
    result = conversion_check("SGOV", sgov_val, prices.get("511360.SS", 100), 100)
    if result["triggered"]:
        alerts.append((
            2,
            f"{status_badge('info')} 现金换仓: SGOV → 511360 "
            f"({result['batches']} 批, ¥{result['threshold_cny']:,.0f} 触发线)"
            f"\n  {cmd_hint(f'ppt sell SGOV + ppt buy 511360')}"
        ))

    # Intra-bucket rebalance
    V_SPYM = tv.get("SPYM", 0)
    V_AVUV = tv.get("AVUV", 0)
    if V_SPYM + V_AVUV > 0:
        rb = intra_bucket_rebalance(
            V_SPYM=V_SPYM, V_AVUV=V_AVUV,
            p_SPYM=prices.get("SPYM", 0), p_AVUV=prices.get("AVUV", 0),
            max_holdings=state["holdings"],
        )
        if rb["triggered"]:
            alerts.append((
                1,
                f"{status_badge('warn')} 桶内再均衡: "
                f"卖 {ticker_display(rb['sell_ticker'])} {rb['sell_shares']:.0f}股"
                f" → 买 {ticker_display(rb['buy_ticker'])} {rb['buy_shares']:.0f}股"
            ))

    # Correlation checks (§4.12)
    if prices_by_bucket and price_history:
        bucket_list = list(BUCKETS)
        for i in range(len(bucket_list)):
            for j in range(i + 1, len(bucket_list)):
                b1, b2 = bucket_list[i], bucket_list[j]
                rho = bucket_correlation(prices_by_bucket[b1], prices_by_bucket[b2])
                if rho is not None and abs(rho) > 0.7:
                    alerts.append((
                        1,
                        f"{status_badge('warn')} {b1}-{b2} 相关性过高: ρ={rho:+.2f}"
                    ))

        # Stock-bond reversal
        reversal = stock_bond_reversal(
            prices_by_bucket["stock"], prices_by_bucket["bond"]
        )
        if reversal["reversal"]:
            alerts.append((
                0,
                f"{status_badge('crit')} 股债相关性反转: ρ前={reversal['rho_first']:+.2f} → ρ後={reversal['rho_second']:+.2f}"
            ))

    # Sort by severity (0=crit first, 1=warn, 2=info last)
    alerts.sort(key=lambda x: x[0])
    return [msg for _, msg in alerts]
