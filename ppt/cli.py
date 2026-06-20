"""CLI entry point and command implementations (§5).

Orchestration layer — ties together calculation, IO, and display layers.
"""

import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import click

from ppt.config import DEFAULT_CONFIG, Config
from ppt.constants import (
    BUCKET_ORDER,
    BUCKET_TICKERS,
    BUCKETS,
    CNY_TICKERS,
    EPSILON,
    PRIMARY_TICKER,
    TICKER_CURRENCY,
    TICKER_WHITELIST,
)
from ppt.display import (
    Color,
    cmd_hint,
    confirm_card,
    currency_badge,
    dev_change,
    dev_tone,
    cols,
    empty_state,
    kpi_row,
    kv,
    kv_table,
    note,
    panel,
    price_str,
    progress_bar,
    rule,
    status_badge,
    success_banner,
    ticker_display,
    ticker_unit,
    warn_card,
)
from ppt.holdings import (
    HoldingsStore,
    Transaction,
    is_nan,
    validate_transaction_input,
)
from ppt.prices import fetch_prices
from ppt.rebalance import (
    dca_allocate,
    dca_minimum_plan,
    multi_over_rebalance,
    multi_under_rebalance,
    single_over_rebalance,
)
from ppt.services.health import build_conversion_trades, health_check
from ppt.services.portfolio import (
    build_allocation_table,
    compute_portfolio,
    portfolio_snapshot,
)
from ppt.returns import (
    bucket_correlation,
    bucket_net_cost,
    cagr,
    conversion_check,
    intra_bucket_rebalance,
    net_conversion_with_dca,
    stock_bond_reversal,
    total_return,
    xirr,
)
from ppt.valuation import (
    bucket_values,
    bucket_weights,
    corridor_bounds,
    currency_split,
    equal_target_weights,
    risk_parity_weights,
    trend_adjusted_corridor,
    ticker_values_cny,
    total_value,
    trend_signal,
    volatility,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _load_state(store: HoldingsStore) -> Optional[dict]:
    """Load holdings from OSS, or show empty state and return None."""
    state = store.load()
    if state is None:
        empty_state(message="未初始化。请运行 ppt init。")
    return state


def _get_prices(fresh: bool = False, offline: bool = False) -> Optional[dict]:
    """Fetch prices via canonical fetch_prices(), with UI error handling."""
    try:
        return fetch_prices(force=fresh, offline=offline)
    except RuntimeError as e:
        warn_card(f"价格获取失败: {e}", icon="❌")
        return None


def _parse_trade_arg(arg: str) -> Tuple[str, int, float]:
    """Parse 'TICKER#shares@price' format."""
    try:
        ticker, rest = arg.split("#")
        shares_str, price_str_val = rest.split("@")
        shares = int(shares_str)
        price = float(price_str_val)
        return ticker, shares, price
    except ValueError:
        raise click.BadParameter(f"格式错误: {arg}，应为 TICKER#shares@price")


def compute_portfolio(holdings: dict, prices: dict, usdcny: float) -> dict:
    """Compute full portfolio snapshot."""
    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    w = bucket_weights(bv) if V > 0 else {b: 0.0 for b in BUCKETS}
    return {"ticker_values": tv, "bucket_values": bv, "total_value": V, "weights": w}


def portfolio_snapshot(holdings: dict, prices: dict, usdcny: float, target_weights: dict) -> dict:
    """Compute portfolio weights, deviations, and total value."""
    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    w = bucket_weights(bv) if V > 0 else {b: 0.0 for b in BUCKETS}
    devs = {b: w[b] - target_weights[b] for b in BUCKETS}
    return {"weights": w, "deviations": devs, "total_value": V, "bucket_values": bv}
# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


@click.group()
@click.option("--fresh", is_flag=True, help="忽略价格缓存，强制拉取")
@click.option("--offline", is_flag=True, help="只读本地缓存")
@click.option("--yes", "-y", is_flag=True, help="跳过交互确认")
@click.pass_context
def main(ctx: click.Context, fresh: bool, offline: bool, yes: bool):
    """ppt — 永久投资组合辅助工具"""
    ctx.ensure_object(dict)
    ctx.obj["fresh"] = fresh
    ctx.obj["offline"] = offline
    ctx.obj["yes"] = yes

    # Load config
    config_path = Path.home() / ".pp" / "pp_config.json"
    ctx.obj["config"] = Config.from_file(config_path)
    ctx.obj["store"] = HoldingsStore()


# ── plan ──────────────────────────────────────────────────────────────────────


@main.command()
@click.argument("amount", required=False, type=float)
@click.pass_context
def plan(ctx: click.Context, amount: Optional[float]):
    """生成买入建议。无参数则计算达标最小投入额。"""
    store: HoldingsStore = ctx.obj["store"]
    state = _load_state(store)
    if state is None:
        return

    prices_data = _get_prices(fresh=ctx.obj["fresh"], offline=ctx.obj["offline"])
    if prices_data is None:
        return
    prices = prices_data["prices"]
    usdcny = prices_data["usdcny"]

    cfg = ctx.obj["config"].data
    target_weights = equal_target_weights()
    tolerance = cfg["rebalance"]["tolerance"]
    holdings = state["holdings"]

    today = datetime.now().strftime("%Y-%m-%d")

    # ── Compute DCA plan ──
    plan_state = {
        "holdings": holdings,
        "prices": prices,
        "usdcny": usdcny,
        "target_weights": target_weights,
    }

    if amount is None:
        # Minimum plan (§4.11)
        C, dca_plan = dca_minimum_plan(plan_state, tolerance=tolerance)
        rule(f"定投达标方案 — {today}")
        if C == 0 and not dca_plan:
            success_banner("当前持仓已达标，无需投入。")
            return
        if C > 0 and not dca_plan:
            if C > 10000:
                warn_card(
                    f"过冲保护触发：计算投入额 ¥{C:,.0f} 但无法改善最大偏差\n"
                    f"  {cmd_hint('尝试 ppt plan <金额> 手动指定投入额，或等待价格变动')}"
                )
            else:
                # C is too small for even 1 lot in the underweight bucket
                note(
                    f"最小投入额 ¥{C:,.0f} 不足以购买 1 手目标标的\n"
                    f"  {cmd_hint('当前已接近达标，或尝试 ppt plan <金额> 手动指定较大金额')}"
                )
            return
        note(f"最小达标投入额: ¥{C:,.0f}")
    else:
        # DCA plan (§4.7)
        elasticity = cfg["advanced"]["gap_elasticity"]
        C = amount
        dca_plan = dca_allocate(C=C, state=plan_state, elasticity=elasticity)
        rule(f"定投方案 ¥{C:,.0f} — {today}")

    if not dca_plan:
        warn_card(
            f"无法生成分配方案：投入额 ¥{C:,.0f} 过小或当前偏差已很小\n"
            f"  {cmd_hint('尝试增大投入额 或 使用 ppt rebalance --full')}"
        )
        return

    # ── Build conversion plan (§4.8, §4.10) ──
    # Simulate post-DCA holdings so conversion thresholds see the larger
    # GLDM/SGOV positions after the DCA purchase.
    post_dca_holdings = dict(holdings)
    for t, s in dca_plan.items():
        post_dca_holdings[t] = post_dca_holdings.get(t, 0) + s
    conv_sells, conv_buys, conv_info = build_conversion_trades(
        post_dca_holdings, prices, usdcny, cfg
    )

    # Net merge: cancel overlapping DCA buys with conversion sells
    prices_cny_all = {
        t: prices.get(t, 0) * (usdcny if t not in CNY_TICKERS else 1)
        for t in set(list(dca_plan.keys()) + list(conv_sells.keys()))
    }
    adjusted_dca, adjusted_sells = net_conversion_with_dca(
        dca_plan, conv_sells, conv_buys, prices_cny_all
    )

    # Count saved transactions
    saved_txns = 0
    for t in conv_sells:
        if t in dca_plan and dca_plan[t] > 0:
            saved_txns += 1

    # ── Before portfolio snapshot ──
    before = portfolio_snapshot(holdings, prices, usdcny, target_weights)

    # ── Simulate after portfolio ──
    new_holdings = dict(holdings)
    for t, s in adjusted_sells.items():
        new_holdings[t] = new_holdings.get(t, 0) - s
    for t, s in adjusted_dca.items():
        new_holdings[t] = new_holdings.get(t, 0) + s
    for t, s in conv_buys.items():
        new_holdings[t] = new_holdings.get(t, 0) + s

    after = portfolio_snapshot(new_holdings, prices, usdcny, target_weights)

    # ── Card 1: 分配 ──
    # Combine all buys: DCA buys + conversion buys
    all_buys = dict(adjusted_dca)
    for t, s in conv_buys.items():
        all_buys[t] = all_buys.get(t, 0) + s

    alloc_lines, alloc_total = build_allocation_table(all_buys, prices, usdcny)
    panel("分配方案", alloc_lines, accent=Color.accent, border=Color.border_ok)

    # ── Card 2: 变化 (before → after) ──
    changes_lines: list = []
    for b in BUCKETS:
        w_before = before["weights"][b]
        w_after = after["weights"][b]
        dev_before = before["deviations"][b]
        dev_after = after["deviations"][b]
        tone = dev_tone(dev_after)
        bar = progress_bar(w_after, target_weights[b], L=0.10, U=0.40)
        change_str = dev_change(dev_before, dev_after)
        changes_lines.append(
            f"{b:<6} {w_before:>5.1%} → {w_after:>5.1%} "
            f"{bar} {change_str} {status_badge(tone)}"
        )
    panel("权重变化", changes_lines, accent=Color.accent, border=Color.border_ok)

    # ── Card 3: 效果 ──
    effect_lines: list = []

    max_dev_before = max(abs(before["deviations"][b]) for b in BUCKETS)
    max_dev_after = max(abs(after["deviations"][b]) for b in BUCKETS)
    change = dev_change(
        max_dev_before if max_dev_before > 0.001 else 0.001,
        max_dev_after if max_dev_after > 0.001 else 0.001,
    )
    effect_lines.append(kv("最大偏差", f"{max_dev_before:.1%} → {max_dev_after:.1%}  {change}"))

    # Remaining issues
    over_buckets = [b for b in BUCKETS if after["deviations"][b] > tolerance]
    under_buckets = [b for b in BUCKETS if after["deviations"][b] < -tolerance]
    if over_buckets:
        effect_lines.append(
            kv("剩余超配", ", ".join(f"{b} (+{after['deviations'][b]:.1%})" for b in over_buckets))
        )
    else:
        effect_lines.append(kv("剩余超配", f"[{Color.profit}]✓ 无[/]"))
    if under_buckets:
        effect_lines.append(
            kv("剩余低配", ", ".join(f"{b} ({after['deviations'][b]:.1%})" for b in under_buckets))
        )
    else:
        effect_lines.append(kv("剩余低配", f"[{Color.profit}]✓ 无[/]"))

    # Conversion details
    if conv_info:
        for ci in conv_info:
            # Check if this conversion was adjusted by netting
            sell_shares = adjusted_sells.get(ci["sell"], ci["sell_shares"])
            if sell_shares > 0:
                effect_lines.append(
                    kv(
                        f"换仓 {ci['bucket']}",
                        f"卖 {ticker_display(ci['sell'])} {sell_shares:.0f}股"
                        f" → 买 {ticker_display(ci['buy'])} {ci['buy_shares']:.0f}份"
                    )
                )
            else:
                effect_lines.append(
                    kv(
                        f"换仓 {ci['bucket']}",
                        f"定投已覆盖 {ticker_display(ci['sell'])} 买入，直接买 "
                        f"{ticker_display(ci['buy'])} {ci['buy_shares']:.0f}份"
                    )
                )
    if saved_txns > 0:
        effect_lines.append(kv("节省交易", f"{saved_txns} 笔 (定投与换仓合并)"))

    # Bucket distribution (post-plan)
    bar_len = 16
    bucket_bars = []
    for b in BUCKET_ORDER:
        bv = after.get("bucket_values", {}).get(b, 0)
        pct = bv / after["total_value"] if after["total_value"] > 0 else 0.0
        bars = max(1, round(pct * bar_len))
        bucket_bars.append(f"{b:<6} {'█' * bars}{'░' * (bar_len - bars)}  {pct:>5.1%}")
    effect_lines.append(kv("桶分布", "\n".join(bucket_bars)))

    # CNY/USD balance
    cur = currency_split(new_holdings, prices, usdcny)
    if cur["total"] > 0:
        usd_pct = cur["usd"] / cur["total"]
        cny_pct = cur["cny"] / cur["total"]
        usd_bars = max(1, round(usd_pct * bar_len))
        cny_bars = bar_len - usd_bars
        usd_bar = "█" * usd_bars
        cny_bar = "░" * cny_bars
        effect_lines.append(
            kv("货币均衡", f"{usd_bar}{cny_bar}  "
                          f"${usd_pct:.0%} / ¥{cny_pct:.0%}")
        )

    panel("效果评估", effect_lines, accent=Color.info, border=Color.border_info)

    # ── Card 4: 执行命令 ──
    exec_lines: list = []
    # Sell commands (merged into one line)
    sell_parts: list[str] = []
    for t, s in adjusted_sells.items():
        if s > 0:
            sell_parts.append(f"{ticker_display(t)}#{int(s)}@"
                            f"{prices.get(t, 0):.2f}")
    if sell_parts:
        exec_lines.append(cmd_hint(f"ppt sell {' '.join(sell_parts)}"))
    # Buy commands (merged into one line)
    buy_parts: list[str] = []
    for t, s in all_buys.items():
        if s > 0:
            buy_parts.append(f"{ticker_display(t)}#{int(s)}@"
                           f"{prices.get(t, 0):.2f}")
    if buy_parts:
        exec_lines.append(cmd_hint(f"ppt buy {' '.join(buy_parts)}"))
    if exec_lines:
        panel("执行命令", exec_lines, border=Color.border_info)
    else:
        note("无需执行交易")


# ── buy / sell ────────────────────────────────────────────────────────────────


def _record_trade(
    ctx: click.Context,
    txn_type: str,
    trade_args: List[str],
):
    """Shared buy/sell logic."""
    # Parse and validate trades FIRST (before state check)
    raw_trades = []
    for arg in trade_args:
        ticker, shares, price = _parse_trade_arg(arg)
        errors = validate_transaction_input(ticker, shares, price)
        if errors:
            for e in errors:
                warn_card(e, icon="❌")
            raise SystemExit(1)
        raw_trades.append({
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "currency": TICKER_CURRENCY[ticker],
        })

    # Merge same-ticker trades with weighted average price (§5)
    merged: dict = {}
    for t in raw_trades:
        tk = t["ticker"]
        if tk in merged:
            prev = merged[tk]
            total_shares = prev["shares"] + t["shares"]
            prev["price"] = (
                (prev["price"] * prev["shares"] + t["price"] * t["shares"])
                / total_shares
            )
            prev["shares"] = total_shares
        else:
            merged[tk] = dict(t)
    trades = list(merged.values())

    store: HoldingsStore = ctx.obj["store"]
    state = _load_state(store)
    if state is None:
        return

    # Pre-check: sell pre-validation
    if txn_type == "sell":
        for t in trades:
            current = state["holdings"].get(t["ticker"], 0)
            if current < t["shares"]:
                warn_card(
                    f"{ticker_display(t['ticker'])} 持仓不足: "
                    f"需 {t['shares']}{ticker_unit(t['ticker'])}，"
                    f"持 {current}{ticker_unit(t['ticker'])}",
                    icon="❌",
                )
                raise SystemExit(1)

    # Get price data for display
    prices_data = _get_prices(fresh=ctx.obj["fresh"], offline=ctx.obj["offline"])
    usdcny = prices_data["usdcny"] if prices_data else 7.25

    # Build transaction
    txn = Transaction(
        txn_id=str(uuid.uuid4()),
        date=datetime.now().strftime("%Y-%m-%d"),
        txn_type=txn_type,
        trades=trades,
        usdcny=usdcny,
    )

    # Show confirmation card
    total_cny = txn.to_dict()["amount_cny"]
    action = "买入" if txn_type == "buy" else "卖出"
    preview_lines = [f"汇率 USD/CNY: {usdcny}"]
    for t in trades:
        preview_lines.append(
            f"{ticker_display(t['ticker'])} "
            f"{t['shares']}{ticker_unit(t['ticker'])} @ "
            f"{price_str(t['ticker'], t['price'])} "
            f"{currency_badge(t['ticker'])}"
        )
    preview_lines.append(f"─── {action}总额: ¥{total_cny:,.2f}")

    if not ctx.obj["yes"]:
        confirm_card(f"确认{action}", "\n".join(preview_lines))
        choice = input(f"[{Color.fg_muted}]确认? (y/N) [/]").strip().lower()
        if choice != "y":
            note("已取消")
            return

    # Sell second validation: re-check holdings after confirmation (§5)
    if txn_type == "sell":
        updated_state = store.load()
        if updated_state is None:
            warn_card("持仓数据丢失，无法完成卖出", icon="❌")
            raise SystemExit(1)
        for t in trades:
            current = updated_state["holdings"].get(t["ticker"], 0)
            if current < t["shares"]:
                warn_card(
                    f"{ticker_display(t['ticker'])} 持仓不足(二次校验): "
                    f"需 {t['shares']}{ticker_unit(t['ticker'])}，"
                    f"持 {current}{ticker_unit(t['ticker'])}",
                    icon="❌",
                )
                raise SystemExit(1)

    # Execute
    store.add_transaction(txn)

    # Show result
    updated = store.load()
    if updated:
        success_banner(f"{action}完成")
        result_lines = []
        for t in trades:
            new_holdings = updated["holdings"].get(t["ticker"], 0)
            result_lines.append(
                f"{ticker_display(t['ticker'])}: "
                f"{new_holdings}{ticker_unit(t['ticker'])} "
                f"({action} {t['shares']}{ticker_unit(t['ticker'])})"
            )
        panel("持仓变化", result_lines, accent=Color.accent)


# ── rebalance ────────────────────────────────────────────────────────────────────


@main.command()
@click.option("--full", is_flag=True, help="执行强制再均衡（卖超买欠）")
@click.option("--dry-run", is_flag=True, help="仅显示方案不执行")
@click.pass_context
def rebalance(ctx: click.Context, full: bool, dry_run: bool):
    """强制再均衡。--full 卖出超配、买入欠配。默认仅诊断。"""
    store: HoldingsStore = ctx.obj["store"]
    state = _load_state(store)
    if state is None:
        return

    prices_data = _get_prices(fresh=ctx.obj["fresh"], offline=ctx.obj["offline"])
    if prices_data is None:
        return
    prices = prices_data["prices"]
    usdcny = prices_data["usdcny"]
    cfg = ctx.obj["config"].data

    holdings = state["holdings"]
    pf = compute_portfolio(holdings, prices, usdcny)
    tv = pf["ticker_values"]
    bv = pf["bucket_values"]
    V = pf["total_value"]
    w = pf["weights"]

    target_weights = equal_target_weights()
    tolerance = cfg["rebalance"]["tolerance"]

    # ── Diagnostic: identify over/under buckets ──
    over = {}
    under = {}
    for b in BUCKETS:
        dev = w[b] - target_weights[b]
        if dev > tolerance:
            primary = PRIMARY_TICKER[b]
            p_cny = prices.get(primary, 0) * (usdcny if primary not in CNY_TICKERS else 1)
            over[b] = {"V_b": bv[b], "w_star": target_weights[b], "price": p_cny}
            rule(f"{status_badge('warn')} {b}: {w[b]:.1%} > {target_weights[b]:.0%} 超配")
        elif dev < -tolerance:
            primary = PRIMARY_TICKER[b]
            p_cny = prices.get(primary, 0) * (usdcny if primary not in CNY_TICKERS else 1)
            under[b] = {"V_b": bv[b], "w_star": target_weights[b], "price": p_cny}
            rule(f"{status_badge('info')} {b}: {w[b]:.1%} < {target_weights[b]:.0%} 欠配")

    if not over and not under:
        success_banner("所有桶均在目标区间内，无需再均衡")
        return

    if not full:
        note("使用 --full 执行强制再均衡交易")
        return

    # ── Build rebalance plan ──
    sell_plan = {}
    buy_plan = {}

    if over:
        if len(over) == 1:
            sell_plan = single_over_rebalance(
                list(over.values())[0]["V_b"],
                list(over.values())[0]["w_star"],
                V,
                list(over.values())[0]["price"],
            )
            if sell_plan > 0:
                b = list(over.keys())[0]
                sell_plan = {PRIMARY_TICKER[b]: sell_plan}
        else:
            sell_plan = multi_over_rebalance(over, V)
            # Map bucket → primary ticker
            sell_plan = {PRIMARY_TICKER[b]: s for b, s in sell_plan.items()
                        if b in over}

    if under:
        buy_plan = multi_under_rebalance(under, V)
        buy_plan = {PRIMARY_TICKER[b]: s for b, s in buy_plan.items()
                    if b in under}

    if not sell_plan and not buy_plan:
        warn_card("无法生成再均衡方案（可能无可行解）")
        return

    # ── Display plan ──
    lines = []
    if sell_plan:
        alloc_lines, sell_total = build_allocation_table(sell_plan, prices, usdcny)
        lines.append(f"[{Color.fg_muted}]─── 卖出 ──[/]")
        lines.extend(alloc_lines)
    if buy_plan:
        alloc_lines, buy_total = build_allocation_table(buy_plan, prices, usdcny)
        lines.append(f"[{Color.fg_muted}]─── 买入 ──[/]")
        lines.extend(alloc_lines)

    panel("强制再均衡方案", lines, accent=Color.accent)

    if dry_run:
        note("dry-run: 未执行交易")
        return

    # ── Confirm & execute ──
    if not ctx.obj["yes"]:
        confirm_card("执行以上交易？ [Y/n]")
        try:
            ans = input()
            if ans.lower() not in ("", "y", "yes"):
                note("已取消")
                return
        except (EOFError, KeyboardInterrupt):
            note("已取消")
            return

    for ticker, shares in {**sell_plan, **buy_plan}.items():
        p_cny = prices.get(ticker, 0) * (usdcny if ticker not in CNY_TICKERS else 1)
        is_buy = ticker in buy_plan
        txn = Transaction(
            ticker=ticker, shares=shares, price=p_cny,
            direction="buy" if is_buy else "sell",
            usdcny=usdcny,
        )
        store.add_transaction(txn)
        action = "买入" if is_buy else "卖出"
        note(f"{action} {ticker} {shares:.0f}股 @ {p_cny:.2f}")

    success_banner("强制再均衡完成")


@main.command(context_settings=dict(ignore_unknown_options=True))
@click.argument("trades", nargs=-1, required=True)
@click.pass_context
def buy(ctx: click.Context, trades: Tuple[str, ...]):
    """记录买入。格式: TICKER#shares@price"""
    _record_trade(ctx, "buy", list(trades))


@main.command(context_settings=dict(ignore_unknown_options=True))
@click.argument("trades", nargs=-1, required=True)
@click.pass_context
def sell(ctx: click.Context, trades: Tuple[str, ...]):
    """记录卖出。格式: TICKER#shares@price"""
    _record_trade(ctx, "sell", list(trades))


# ── status ────────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def status(ctx: click.Context):
    """持仓全景: 持仓 → 权重 → 收益 → 体检."""
    store: HoldingsStore = ctx.obj["store"]
    state = _load_state(store)
    if state is None:
        return

    prices_data = _get_prices(fresh=ctx.obj["fresh"], offline=ctx.obj["offline"])
    if prices_data is None:
        return
    prices = prices_data["prices"]
    usdcny = prices_data["usdcny"]
    cfg = ctx.obj["config"].data

    pf = compute_portfolio(state["holdings"], prices, usdcny)
    V = pf["total_value"]
    w = pf["weights"]
    tv = pf["ticker_values"]

    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    rule(f"持仓全景 — {today}  USD/CNY={usdcny:.4f}")

    # ── 持仓卡片: 明细 + 汇总 ──
    lines = [f"[{Color.fg_muted}]{cols(('代码',8,'left'), ('股数',9,'right'), ('单价',10,'right'), ('币种',4,'left'), ('金额',11,'right'))}[/]"]
    _ticker_to_bucket: dict = {}
    for bucket, tickers in BUCKET_TICKERS.items():
        for t in tickers:
            _ticker_to_bucket[t] = bucket

    bucket_vals = {b: 0.0 for b in BUCKET_ORDER}
    for bucket in BUCKET_ORDER:
        for ticker in BUCKET_TICKERS[bucket]:
            shares = state["holdings"].get(ticker, 0)
            if shares <= 0:
                continue
            p = prices.get(ticker, 0) or 0.0
            p_cny = p * (usdcny if ticker not in CNY_TICKERS else 1)
            val_cny = shares * p_cny
            bucket_vals[bucket] += val_cny
            is_sub = ticker != BUCKET_TICKERS[bucket][0]
            prefix = "  ╰─" if is_sub else "  "
            curr = "USD" if ticker not in CNY_TICKERS else "CNY"
            lines.append(cols(
                (f"{prefix}{ticker_display(ticker)}", 8, 'left'),
                (f"{shares:.0f}{ticker_unit(ticker)}", 9, 'right'),
                (price_str(ticker, p), 10, 'right'),
                (curr, 4, 'left'),
                (f"¥{val_cny:,.0f}", 11, 'right'),
            ))

    # ── 汇总区: 桶分布 + 总资产 + 货币均衡 ──
    lines.append("")
    bar_len = 16
    for b in BUCKET_ORDER:
        bv = bucket_vals[b]
        pct = bv / V if V > 0 else 0.0
        bars = max(1, round(pct * bar_len))
        bar = "█" * bars + "░" * (bar_len - bars)
        lines.append(f"  {b:<6} {bar}  ¥{bv:,.0f}  {pct:>5.1%}")
    lines.append(f"  {'─' * 40}")
    lines.append(f"  总资产  ¥{V:,.0f}")

    cur = currency_split(state["holdings"], prices, usdcny)
    if cur["total"] > 0:
        usd_pct = cur["usd"] / cur["total"]
        cny_pct = cur["cny"] / cur["total"]
        lines.append(f"  货币均衡  ${usd_pct:.0%} / ¥{cny_pct:.0%}")

    panel("持仓", lines, accent=Color.accent, border=Color.border_ok)

    # ── 权重卡片 (含走廊/趋势) ──
    # Load price history for vol/trend/corridor
    price_history = store.load_price_history()
    prices_by_bucket: dict = {b: [] for b in BUCKETS}
    for entry in price_history:
        for b in BUCKETS:
            v = entry["prices_cny"].get(b, 0)
            if v == 0 or is_nan(v):
                continue
            prices_by_bucket[b].append(v)

    # Compute target weights: risk_parity or equal
    weighting_mode = cfg["advanced"].get("weighting_mode", "equal")
    if weighting_mode == "risk_parity":
        sigmas = {b: volatility(prices_by_bucket[b]) for b in BUCKETS
                  if len(prices_by_bucket[b]) >= 2}
        if len(sigmas) == len(BUCKETS):
            target_weights = risk_parity_weights(
                sigmas,
                cap=cfg["advanced"].get("rp_weight_cap", 0.40),
                floor=cfg["advanced"].get("rp_weight_floor", 0.10),
            )
        else:
            target_weights = equal_target_weights()
    else:
        target_weights = equal_target_weights()

    wt_lines = []
    k = cfg["advanced"]["corridor_k"]
    lam = cfg["advanced"]["trend_sensitivity"]
    for b in BUCKETS:
        dev = w[b] - target_weights[b]
        tone = dev_tone(dev)
        bar = progress_bar(w[b], target_weights[b], L=0.10, U=0.40)

        # Corridor & trend
        sigma = volatility(prices_by_bucket[b]) if len(prices_by_bucket[b]) >= 2 else None
        trend = trend_signal(prices_by_bucket[b])
        L, U = corridor_bounds(target_weights[b], sigma, k)
        L_adj, U_adj = trend_adjusted_corridor(target_weights[b], sigma, trend, k, lam)

        trend_str = (
            f"[{Color.profit}]↑[/]" if trend > 0.01 else
            f"[{Color.loss}]↓[/]" if trend < -0.01 else
            f"[{Color.fg_muted}]─[/]"
        )
        base_corridor = f"[{L:.0%}, {U:.0%}]"
        # Show adjusted corridor only when trend shifts boundaries
        if abs(L_adj - L) > EPSILON or abs(U_adj - U) > EPSILON:
            adjusted = f"[{Color.fg_muted}]→ [{L_adj:.0%}, {U_adj:.0%}][/]"
        else:
            adjusted = ""

        wt_lines.append(
            f"{b:<6} {w[b]:>5.1%} → {target_weights[b]:.0%} "
            f"{bar} {trend_str} [{Color.fg_muted}]{base_corridor}[/]{adjusted} {status_badge(tone)}"
        )

        # Intra-bucket sub-items for stock
        if b == "stock":
            V_stock = tv.get("SPYM", 0) + tv.get("AVUV", 0)
            if V_stock > 0:
                r_SPYM = tv.get("SPYM", 0) / V_stock
                r_AVUV = tv.get("AVUV", 0) / V_stock
                wt_lines.append(
                    f"  ╰─SPYM  {r_SPYM:.0%}  AVUV {r_AVUV:.0%}  [{Color.fg_muted}]触发线 60%[/]"
                )
    panel("权重", wt_lines, accent=Color.accent, border=Color.border_ok)

    # ── 收益卡片 ──
    bucket_costs = bucket_net_cost(state["transactions"])
    P, pct = total_return(V, bucket_costs)
    pct_str = f"+{pct:.1%}" if pct >= 0 else f"{pct:.1%}"
    value_style = Color.profit if P >= 0 else Color.loss

    kpi_row([
        ("总收益", f"¥{P:,.0f}", value_style),
        ("收益率", pct_str, value_style),
        ("累计投入", f"¥{state['cash_in']:,.0f}", Color.fg_default),
    ])

    # XIRR
    xirr_str = "N/A"
    if state["transactions"]:
        cashflows: list = []
        for txn in state["transactions"]:
            sign = -1 if txn["type"] == "buy" else 1
            amount = txn.get("amount_cny", 0)
            d = datetime.strptime(txn["date"], "%Y-%m-%d")
            day_num = (d - datetime(2025, 1, 1)).days
            cashflows.append((sign * amount, float(day_num)))
        today_num = (datetime.now() - datetime(2025, 1, 1)).days
        cashflows.append((V, float(today_num)))
        xirr_val = xirr(cashflows)
        if xirr_val is not None:
            xirr_str = f"{xirr_val:.1%}"
        else:
            # Fallback to CAGR
            net_cost = sum(bucket_costs.values())
            years = max((datetime.now() - datetime.strptime(state["created_at"], "%Y-%m-%d")).days / 365.25, 0.01)
            xirr_val = cagr(V, net_cost, years)
            if xirr_val:
                xirr_str = f"{xirr_val:.1%} (CAGR)"

    # Per-bucket returns
    bv = pf["bucket_values"]
    bucket_return_lines: list = []
    for b in BUCKETS:
        cost = bucket_costs.get(b, 0)
        val = bv.get(b, 0)
        b_P = val - abs(cost)
        b_pct = b_P / abs(cost) if abs(cost) > 0.001 else 0.0
        sign = "+" if b_pct >= 0 else ""
        bucket_return_lines.append(
            f"{b:<6} 成本 ¥{cost:,.0f}  市值 ¥{val:,.0f}  "
            f"[{Color.profit if b_P >= 0 else Color.loss}]{sign}{b_pct:.1%}[/]"
        )

    panel(
        "收益",
        [f"[{Color.fg_muted}]XIRR: [/][{value_style}]{xirr_str}[/]", ""] + bucket_return_lines,
        accent=Color.accent,
        border=Color.border_ok,
    )

    # Append price history — skip if any raw price is NaN
    raw_ok = True
    for b in BUCKETS:
        raw = prices.get(PRIMARY_TICKER[b], 0)
        if is_nan(raw):
            raw_ok = False
            break
    if raw_ok:
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "prices_cny": {
                b: (
                    prices.get(PRIMARY_TICKER[b], 0)
                    * (usdcny if PRIMARY_TICKER[b] not in CNY_TICKERS else 1)
                )
                for b in BUCKETS
            },
        }
        store.update_price_history(entry)

    # ── 体检卡片 ──
    alerts = health_check(state, prices, usdcny, price_history, prices_by_bucket)
    if alerts:
        panel("体检", alerts, accent=Color.warn, border=Color.border_warn)
    else:
        panel("体检", ["✓ 无异常"], accent=Color.profit, border=Color.border_ok)


# ── history ───────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def history(ctx: click.Context):
    """交易历史，按天倒序."""
    store: HoldingsStore = ctx.obj["store"]
    state = _load_state(store)
    if state is None:
        return

    transactions = state.get("transactions", [])
    if not transactions:
        empty_state(message="暂无交易记录")
        return

    rule("交易历史")

    # Group transactions by date, then merge same-ticker trades
    from collections import defaultdict
    by_date: dict = defaultdict(list)
    for txn in transactions:
        by_date[txn["date"]].append(txn)

    for date_str in sorted(by_date.keys(), reverse=True):
        day_txns = by_date[date_str]
        # Collect all trades for this day, merging same ticker
        day_trades: dict = {}  # ticker → {type, shares, total_price_weight, currency}
        day_types = set()
        day_total_cny = 0.0
        for txn in day_txns:
            day_types.add(txn["type"])
            day_total_cny += txn.get("amount_cny", 0)
            for t in txn.get("trades", []):
                tk = t["ticker"]
                if tk in day_trades:
                    prev = day_trades[tk]
                    prev["shares"] += t["shares"]
                    prev["total_weighted"] += t["shares"] * t["price"]
                else:
                    day_trades[tk] = {
                        "shares": t["shares"],
                        "total_weighted": t["shares"] * t["price"],
                        "currency": t.get("currency", "USD"),
                    }

        # Build display
        type_label = "/".join(sorted(day_types))
        badge_str = (
            status_badge("ok") if "buy" in day_types and "sell" not in day_types
            else status_badge("warn") if "sell" in day_types and "buy" not in day_types
            else status_badge("info")
        )
        lines = [f"日期: {date_str}  {badge_str}"]
        for tk, info in sorted(day_trades.items()):
            avg_price = info["total_weighted"] / info["shares"] if info["shares"] > 0 else 0
            lines.append(
                f"  {ticker_display(tk)} "
                f"{info['shares']:.0f}{ticker_unit(tk)} @ "
                f"{price_str(tk, avg_price)} (均价)"
            )
        lines.append(f"  总额: ¥{day_total_cny:,.2f}")
        panel(f"{type_label} — {date_str}", lines, border=Color.border_dim)


# ── undo ──────────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def undo(ctx: click.Context):
    """撤销最近一笔交易."""
    store: HoldingsStore = ctx.obj["store"]
    state = _load_state(store)
    if state is None or not state.get("transactions"):
        empty_state(message="无交易可撤销")
        return

    last = state["transactions"][-1]
    preview_lines = [
        f"类型: {'买入' if last['type'] == 'buy' else '卖出'}",
        f"日期: {last['date']}",
        f"金额: ¥{last.get('amount_cny', 0):,.2f}",
    ]
    for t in last.get("trades", []):
        preview_lines.append(
            f"  {ticker_display(t['ticker'])} {t['shares']}{ticker_unit(t['ticker'])} "
            f"@ {price_str(t['ticker'], t['price'])}"
        )

    if not ctx.obj["yes"]:
        confirm_card("撤销预览", "\n".join(preview_lines), prompt="确认撤销? (y/N)")
        choice = input(f"[{Color.fg_muted}]确认撤销? (y/N) [/]").strip().lower()
        if choice != "y":
            note("已取消")
            return

    removed = store.undo_last()
    if removed:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            "Undo transaction #%s: %s ¥%s",
            last["id"][:8], last["type"], last.get("amount_cny", 0),
        )
        success_banner("已撤销")
    else:
        warn_card("撤销失败", icon="❌")


# ── config ────────────────────────────────────────────────────────────────────


@main.group()
def config():
    """配置管理."""
    pass


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context):
    """查看当前配置."""
    cfg = ctx.obj["config"].data
    rule("当前配置")
    for section, items in cfg.items():
        lines = [kv(k, str(v)) for k, v in items.items()]
        panel(section, lines, accent=Color.info, border=Color.border_info)


@config.command("init")
@click.pass_context
def config_init(ctx: click.Context):
    """生成默认配置文件."""
    path = Path.home() / ".pp" / "pp_config.json"
    if path.exists() and not ctx.obj["yes"]:
        choice = input(f"[{Color.warn}]覆盖现有配置? (y/N) [/]").strip().lower()
        if choice != "y":
            return
    cfg = Config(data=DEFAULT_CONFIG)
    cfg.save(path)
    success_banner(f"已生成配置文件: {path}")


# ── init ──────────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def init(ctx: click.Context):
    """重置所有持仓数据."""
    if not ctx.obj["yes"]:
        warn_card("⚠ 初始化将清空所有持仓和交易记录！", icon="⚠")
        choice = input(f"[{Color.loss}]确认重置? (y/N) [/]").strip().lower()
        if choice != "y":
            note("已取消")
            return

    store: HoldingsStore = ctx.obj["store"]
    # Preserve created_at if already exists (§5)
    old_state = store.load()
    created_at = (
        old_state.get("created_at")
        if old_state and old_state.get("created_at")
        else datetime.now().strftime("%Y-%m-%d")
    )
    data = {
        "holdings": {t: 0.0 for t in TICKER_WHITELIST},
        "cash_in": 0.0,
        "cash_out": 0.0,
        "transactions": [],
        "created_at": created_at,
    }
    store.save(data)
    success_banner("已初始化")


# ── clean-history ────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def clean_history(ctx: click.Context):
    """清理 price history 中的 NaN 脏数据."""
    store: HoldingsStore = ctx.obj["store"]
    removed = store.clean_price_history()
    if removed > 0:
        success_banner(f"已清理 {removed} 条 NaN 记录")
    else:
        note("未发现 NaN 记录")


# ── help ──────────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def help(ctx: click.Context):
    """结构化帮助."""
    rule("ppt — 永久投资组合辅助工具")
    core_cmds = [
        "ppt plan [金额]    生成买入建议",
        "ppt buy CODE#shares@price [...]   记录买入",
        "ppt sell CODE#shares@price [...]  记录卖出",
        "ppt status        持仓全景",
        "ppt history       交易历史",
        "ppt undo          撤销最近交易",
    ]
    util_cmds = [
        "ppt config show   查看配置",
        "ppt config init   生成默认配置",
        "ppt init          重置数据",
        "ppt clean-history 清理 NaN 价格记录",
        "ppt help          此帮助",
    ]
    panel("核心命令", core_cmds, accent=Color.accent, border=Color.border_ok)
    panel("配置工具", util_cmds, accent=Color.info, border=Color.border_info)
    note("--fresh 忽略缓存  --offline 离线模式  --yes 跳过确认")


if __name__ == "__main__":
    main()
