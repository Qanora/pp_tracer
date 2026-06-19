"""CLI entry point and command implementations (§5).

Orchestration layer — ties together calculation, IO, and display layers.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import click

from ppt.config import DEFAULT_CONFIG, Config
from ppt.constants import (
    BUCKETS,
    CNY_TICKERS,
    PRIMARY_TICKER,
    TICKER_CURRENCY,
    TICKER_WHITELIST,
)
from ppt.display import (
    Color,
    cmd_hint,
    currency_badge,
    dev_tone,
    empty_state,
    kpi_row,
    kv,
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
    validate_transaction_input,
)
from ppt.prices import PriceCache, PriceFetcher
from ppt.rebalance import dca_allocate, dca_minimum_plan
from ppt.returns import (
    bucket_net_cost,
    conversion_check,
    intra_bucket_rebalance,
    total_return,
)
from ppt.valuation import (
    bucket_values,
    bucket_weights,
    equal_target_weights,
    ticker_values_cny,
    total_value,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _load_state(store: HoldingsStore) -> Optional[dict]:
    """Load holdings, or show empty state and return None."""
    state = store.load_local()
    if state is None:
        empty_state(message="未初始化。请运行 ppt init 或从 OSS 同步。")
    return state


def _get_prices(fresh: bool = False, offline: bool = False) -> Optional[dict]:
    """Fetch prices, or show error and return None."""
    cache_dir = Path.home() / ".pp"
    cache = PriceCache(path=cache_dir / "price_cache.json", ttl=300)
    fetcher = PriceFetcher(cache=cache)
    try:
        return fetcher.fetch(force=fresh, offline=offline)
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


def _compute_portfolio(holdings: dict, prices: dict, usdcny: float) -> dict:
    """Compute full portfolio snapshot."""
    tv = ticker_values_cny(holdings, prices, usdcny)
    bv = bucket_values(tv)
    V = total_value(bv)
    w = bucket_weights(bv) if V > 0 else {b: 0.0 for b in BUCKETS}
    return {"ticker_values": tv, "bucket_values": bv, "total_value": V, "weights": w}


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

    today = datetime.now().strftime("%Y-%m-%d")

    if amount is None:
        # Minimum plan (§4.11)
        plan_state = {
            "holdings": state["holdings"],
            "prices": prices,
            "usdcny": usdcny,
            "target_weights": target_weights,
        }
        tolerance = cfg["rebalance"]["tolerance"]
        C, plan_result = dca_minimum_plan(plan_state, tolerance=tolerance)

        rule(f"定投达标方案 — {today}")
        if C == 0:
            success_banner("当前持仓已达标，无需投入。")
            return

        note(f"最小达标投入额: ¥{C:,.0f}")
        if plan_result:
            lines = [f"[{Color.fg_muted}]代码     股数     单价        金额[/]"]
            total = 0.0
            for ticker, shares in plan_result.items():
                p_cny = prices.get(ticker, 0) * (usdcny if ticker not in CNY_TICKERS else 1)
                amt = shares * p_cny
                total += amt
                lines.append(
                    f"{ticker_display(ticker):<8} "
                    f"{shares:>6.0f}{ticker_unit(ticker)} "
                    f"{price_str(ticker, prices.get(ticker, 0)):>10} "
                    f"¥{amt:>10,.0f}"
                )
            lines.append(f"─── 合计 ¥{total:,.0f}")
            panel("买入建议", lines, accent=Color.accent, border=Color.border_ok)
    else:
        # DCA plan (§4.7)
        plan_state = {
            "holdings": state["holdings"],
            "prices": prices,
            "usdcny": usdcny,
            "target_weights": target_weights,
        }
        elasticity = cfg["advanced"]["gap_elasticity"]
        plan_result = dca_allocate(C=amount, state=plan_state, elasticity=elasticity)

        rule(f"定投方案 ¥{amount:,.0f} — {today}")
        if plan_result:
            lines = [f"[{Color.fg_muted}]代码     股数     单价        金额[/]"]
            total = 0.0
            for ticker, shares in plan_result.items():
                p_cny = prices.get(ticker, 0) * (usdcny if ticker not in CNY_TICKERS else 1)
                amt = shares * p_cny
                total += amt
                lines.append(
                    f"{ticker_display(ticker):<8} "
                    f"{shares:>6.0f}{ticker_unit(ticker)} "
                    f"{price_str(ticker, prices.get(ticker, 0)):>10} "
                    f"¥{amt:>10,.0f}"
                )
            lines.append(f"─── 合计 ¥{total:,.0f}")
            panel("分配方案", lines, accent=Color.accent, border=Color.border_ok)

            # Show execution commands
            buy_cmds = [
                cmd_hint(
                    f"ppt buy {ticker}#{int(shares)}@"
                    f"{prices.get(ticker, 0):.2f}"
                )
                for ticker, shares in plan_result.items()
            ]
            if buy_cmds:
                panel("执行命令", buy_cmds, border=Color.border_info)
        else:
            warn_card("无法生成分配方案")


# ── buy / sell ────────────────────────────────────────────────────────────────


def _record_trade(
    ctx: click.Context,
    txn_type: str,
    trade_args: List[str],
):
    """Shared buy/sell logic."""
    # Parse and validate trades FIRST (before state check)
    trades = []
    for arg in trade_args:
        ticker, shares, price = _parse_trade_arg(arg)
        errors = validate_transaction_input(ticker, shares, price)
        if errors:
            for e in errors:
                warn_card(e, icon="❌")
            raise SystemExit(1)
        trades.append({
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "currency": TICKER_CURRENCY[ticker],
        })

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
        panel(f"确认{action}", preview_lines, accent=Color.warn, border=Color.border_warn)
        choice = input(f"[{Color.fg_muted}]确认? (y/N) [/]").strip().lower()
        if choice != "y":
            note("已取消")
            return

    # Execute
    store.add_transaction(txn)

    # Show result
    updated = store.load_local()
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

    pf = _compute_portfolio(state["holdings"], prices, usdcny)
    V = pf["total_value"]
    w = pf["weights"]

    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    rule(f"持仓全景 — {today}  USD/CNY={usdcny}")

    # ── 持仓卡片 ──
    lines = [f"[{Color.fg_muted}]代码       股数      单价        人民币[/]"]
    for ticker, shares in state["holdings"].items():
        if shares <= 0:
            continue
        p = prices.get(ticker, 0)
        p_cny = p * (usdcny if ticker not in CNY_TICKERS else 1)
        lines.append(
            f"{ticker_display(ticker):<8} "
            f"{shares:>8.0f}{ticker_unit(ticker)} "
            f"{price_str(ticker, p):>10} "
            f"{currency_badge(ticker):>8} "
            f"¥{shares * p_cny:>10,.0f}"
        )
    lines.append(f"─── 总资产: ¥{V:,.0f}")
    panel("持仓", lines, accent=Color.accent, border=Color.border_ok)

    # ── 权重卡片 ──
    target_weights = equal_target_weights()
    wt_lines = []
    for b in BUCKETS:
        dev = w[b] - target_weights[b]
        tone = dev_tone(dev)
        bar = progress_bar(w[b], target_weights[b], L=0.10, U=0.40)
        wt_lines.append(
            f"{b:<6} {w[b]:>5.1%} → {target_weights[b]:.0%} "
            f"{bar} {status_badge(tone)}"
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

    # Append price history
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
    alerts = _health_check(state, prices, usdcny)
    if alerts:
        panel("体检", alerts, accent=Color.warn, border=Color.border_warn)
    else:
        panel("体检", ["✓ 无异常"], accent=Color.profit, border=Color.border_ok)


def _health_check(state: dict, prices: dict, usdcny: float) -> List[str]:
    """Generate health check alerts (§4.12, §4.9, §4.10)."""
    alerts = []

    # Conversion triggers
    tv = ticker_values_cny(state["holdings"], prices, usdcny)

    # Check GLDM → 518880
    gldm_val = tv.get("GLDM", 0)
    result = conversion_check("GLDM", gldm_val, prices.get("518880.SS", 5.50), 1000)
    if result["triggered"]:
        alerts.append(
            f"{status_badge('info')} 黄金换仓: GLDM → 518880 "
            f"({result['batches']} 批)"
        )

    # Check SGOV → 511360
    sgov_val = tv.get("SGOV", 0)
    result = conversion_check("SGOV", sgov_val, prices.get("511360.SS", 100), 100)
    if result["triggered"]:
        alerts.append(
            f"{status_badge('info')} 现金换仓: SGOV → 511360 "
            f"({result['batches']} 批)"
        )

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
            alerts.append(
                f"{status_badge('warn')} 桶内再均衡: "
                f"卖 {ticker_display(rb['sell_ticker'])} {rb['sell_shares']:.0f}股"
            )

    return alerts


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
    for txn in reversed(transactions):
        txn_type = txn["type"]
        badge_str = status_badge("ok") if txn_type == "buy" else status_badge("warn")
        lines = [f"日期: {txn['date']}  {badge_str}"]
        for t in txn.get("trades", []):
            lines.append(
                f"  {ticker_display(t['ticker'])} "
                f"{t['shares']}{ticker_unit(t['ticker'])} @ "
                f"{price_str(t['ticker'], t['price'])}"
            )
        lines.append(f"  总额: ¥{txn.get('amount_cny', 0):,.2f}")
        panel(f"{txn_type.upper()} #{txn['id'][:8]}", lines, border=Color.border_dim)


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
        panel("撤销预览", preview_lines, accent=Color.warn, border=Color.border_warn)
        choice = input(f"[{Color.fg_muted}]确认撤销? (y/N) [/]").strip().lower()
        if choice != "y":
            note("已取消")
            return

    removed = store.undo_last()
    if removed:
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
    data = {
        "holdings": {t: 0.0 for t in TICKER_WHITELIST},
        "cash_in": 0.0,
        "cash_out": 0.0,
        "transactions": [],
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    store.save_local(data)
    success_banner("已初始化")


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
        "ppt help          此帮助",
    ]
    panel("核心命令", core_cmds, accent=Color.accent, border=Color.border_ok)
    panel("配置工具", util_cmds, accent=Color.info, border=Color.border_info)
    note("--fresh 忽略缓存  --offline 离线模式  --yes 跳过确认")


if __name__ == "__main__":
    main()
