"""All user-facing terminal components."""

from collections.abc import Mapping, Sequence
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_BUCKET_LABELS = {
    "stock": "股票",
    "bond": "债券",
    "gold": "黄金",
    "cash": "现金",
}
_CURRENCY_LABELS = {"USD": "美元", "CNY": "人民币"}
_CORRIDOR_LABELS = {
    "below": "低于 15%",
    "within": "走廊内",
    "above": "高于 35%",
}


def show_error(message: str) -> None:
    console.print(Text(f"错误：{message}", style="bold red"))


def show_success(message: str) -> None:
    console.print(Text(message, style="bold green"))


def show_info(message: str) -> None:
    console.print(Text(message, style="cyan"))


def confirm_reset() -> bool:
    """Ask for the destructive reset confirmation from the display layer."""
    console.print(Text("init 会先备份现有 OSS 账本，再清空持仓和交易历史。", style="yellow"))
    return click.confirm("确认重置", default=False)


def show_initialized(backup_path: str | None) -> None:
    lines = ["持仓与交易历史已重置。"]
    if backup_path:
        lines.append(f"备份：{backup_path}")
    else:
        lines.append("此前没有账本，已直接创建。")
    console.print(Panel("\n".join(lines), title="初始化完成", border_style="green"))


def show_recorded(batch: Mapping[str, Any], holdings: Mapping[str, int]) -> None:
    table = Table(title="已记录交易批次", show_lines=False)
    table.add_column("标的")
    table.add_column("股数", justify="right")
    table.add_column("成交价", justify="right")
    table.add_column("持仓", justify="right")
    for trade in batch["trades"]:
        ticker = str(trade["ticker"])
        table.add_row(
            ticker,
            f"{int(trade['shares']):+d}",
            _price(ticker, float(trade["price"])),
            str(int(holdings[ticker])),
        )
    console.print(table)
    console.print(Text(f"批次净投入：¥{float(batch['net_cny']):,.2f}", style="green"))


def show_status(
    *,
    usdcny: float,
    tickers: Sequence[Mapping[str, Any]],
    buckets: Sequence[Mapping[str, Any]],
    currencies: Sequence[Mapping[str, Any]],
    deviations: Mapping[str, float],
    corridor_breached: bool,
    performance: Mapping[str, Any],
    backtest: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> None:
    """Show the current valuation, allocation, performance, and risk."""

    current_value = float(performance["current_value"])
    overview = Table(title="组合当前状态", show_header=False)
    overview.add_column("项目")
    overview.add_column("当前值", justify="right")
    overview.add_row("当前市值", f"¥{current_value:,.2f}")
    overview.add_row("累计投入", f"¥{float(performance['invested']):,.2f}")
    overview.add_row("累计取出", f"¥{float(performance['withdrawn']):,.2f}")
    overview.add_row("净投入", f"¥{float(performance['net_invested']):,.2f}")
    overview.add_row("累计盈亏", f"¥{float(performance['profit']):+,.2f}")
    return_rate = performance["return_rate"]
    overview.add_row(
        "简单收益率",
        "不可计算" if return_rate is None else f"{float(return_rate):+.2%}",
    )
    overview.add_row("USD/CNY", f"{usdcny:.4f}".rstrip("0").rstrip("."))
    overview.add_row(
        "跨桶走廊",
        "暂无持仓" if current_value <= 0 else "已越界" if corridor_breached else "走廊内",
    )
    console.print(overview)

    holdings = Table(title="当前持仓", show_lines=False)
    holdings.add_column("桶")
    holdings.add_column("标的")
    holdings.add_column("持仓", justify="right")
    holdings.add_column("原币现价", justify="right")
    holdings.add_column("人民币市值", justify="right")
    holdings.add_column("组合占比", justify="right")
    holdings.add_column("桶内/目标", justify="right")
    for row in tickers:
        ticker = str(row["ticker"])
        holdings.add_row(
            _BUCKET_LABELS[str(row["bucket"])],
            ticker,
            str(int(row["shares"])),
            f"{row['currency']} {_price(ticker, float(row['price']))}",
            f"¥{float(row['value_cny']):,.2f}",
            _optional_percent(row["portfolio_weight"]),
            (
                f"{_optional_compact_percent(row['bucket_weight'])}/"
                f"{_compact_percent(float(row['bucket_target']))}"
            ),
        )
    console.print(holdings)

    bucket_table = Table(title="四桶配置")
    bucket_table.add_column("桶")
    bucket_table.add_column("人民币市值", justify="right")
    bucket_table.add_column("当前占比", justify="right")
    bucket_table.add_column("目标", justify="right")
    bucket_table.add_column("偏差", justify="right")
    bucket_table.add_column("走廊状态")
    for row in buckets:
        corridor = row["corridor"]
        bucket_table.add_row(
            _BUCKET_LABELS[str(row["bucket"])],
            f"¥{float(row['value_cny']):,.2f}",
            _optional_percent(row["weight"]),
            _percent(float(row["target"])),
            _optional_signed_percent(row["deviation"]),
            "暂无持仓" if corridor is None else _CORRIDOR_LABELS[str(corridor)],
        )
    console.print(bucket_table)

    currency_table = Table(title="币种配置")
    currency_table.add_column("币种")
    currency_table.add_column("人民币市值", justify="right")
    currency_table.add_column("当前占比", justify="right")
    currency_table.add_column("目标", justify="right")
    currency_table.add_column("偏差", justify="right")
    for row in currencies:
        currency = str(row["currency"])
        currency_table.add_row(
            f"{_CURRENCY_LABELS[currency]} ({currency})",
            f"¥{float(row['value_cny']):,.2f}",
            _optional_percent(row["weight"]),
            _percent(float(row["target"])),
            _optional_signed_percent(row["deviation"]),
        )
    console.print(currency_table)

    scores = Table(title="三级最大偏差", show_header=False)
    scores.add_column("优先级")
    scores.add_column("偏差", justify="right")
    scores.add_row("四桶最大偏差", _percent(float(deviations["bucket"])))
    scores.add_row("桶内最大偏差", _percent(float(deviations["intra"])))
    scores.add_row("美元/人民币偏差", _percent(float(deviations["currency"])))
    console.print(scores)

    backtest_table = Table(title="30 日持仓回测", show_header=False)
    backtest_table.add_column("指标")
    backtest_table.add_column("幅度", justify="right")
    for label, key in (
        ("当前回撤", "current_drawdown"),
        ("最大回撤", "maximum_drawdown"),
        ("最大涨幅", "maximum_runup"),
    ):
        value = backtest[key]
        backtest_table.add_row(
            label,
            "不可计算" if value is None else _percent(float(value)),
        )
    observations = int(backtest["observations"])
    if observations:
        backtest_table.caption = f"共同有效交易日：{observations}/30"
    console.print(backtest_table)

    hints = _diagnostic_lines(diagnostics)
    if hints:
        console.print(Panel("\n".join(hints), title="趋势与相关性提示", border_style="yellow"))


def show_plan(
    *,
    trades: Sequence[Mapping[str, Any]],
    budget: float,
    buy_cost: float,
    sell_proceeds: float,
    unused_amount: float,
    before: Mapping[str, float],
    after: Mapping[str, float],
    command: str | None,
) -> None:
    console.print(Text("自动均衡交易方案", style="bold cyan"))
    table = Table(show_lines=False)
    table.add_column("标的")
    table.add_column("净股数", justify="right")
    table.add_column("成交价", justify="right")
    table.add_column("预计人民币金额", justify="right")
    for trade in trades:
        ticker = str(trade["ticker"])
        shares = int(trade["shares"])
        amount = float(trade["amount_cny"])
        table.add_row(
            ticker,
            f"{shares:+d}",
            _price(ticker, float(trade["price"])),
            f"{amount:+,.2f}",
        )
    if trades:
        console.print(table)
    else:
        show_info("当前约束下无需交易。")

    cash = Table(title="预计人民币收支", show_header=False)
    cash.add_column("项目")
    cash.add_column("金额", justify="right")
    cash.add_row("新增金额", f"¥{budget:,.2f}")
    cash.add_row("卖出收入", f"¥{sell_proceeds:,.2f}")
    cash.add_row("买入支出", f"¥{buy_cost:,.2f}")
    cash.add_row("未使用金额", f"¥{unused_amount:,.2f}")
    console.print(cash)

    scores = Table(title="三级偏差（越小越好）")
    scores.add_column("优先级")
    scores.add_column("买入前", justify="right")
    scores.add_column("买入后", justify="right")
    scores.add_row("四桶最大偏差", _percent(before["bucket"]), _percent(after["bucket"]))
    scores.add_row("桶内最大偏差", _percent(before["intra"]), _percent(after["intra"]))
    scores.add_row("美元/人民币偏差", _percent(before["currency"]), _percent(after["currency"]))
    console.print(scores)

    if command:
        console.print(Panel(Text(command, style="bold cyan"), title="执行命令"))


def show_history(
    *,
    cash_in: float,
    cash_out: float,
    net_invested: float,
    batches: Sequence[Mapping[str, Any]],
) -> None:
    summary = Table(title="资金流汇总", show_header=False)
    summary.add_column("项目")
    summary.add_column("金额", justify="right")
    summary.add_row("累计投入", f"¥{cash_in:,.2f}")
    summary.add_row("累计取出", f"¥{cash_out:,.2f}")
    summary.add_row("净投入", f"¥{net_invested:,.2f}")
    console.print(summary)

    if not batches:
        show_info("暂无交易记录。")
        return
    for batch in reversed(batches):
        table = Table(title=str(batch["executed_at"]), show_lines=False, expand=True)
        table.add_column("标的")
        table.add_column("股数", justify="right")
        table.add_column("成交价", justify="right")
        for trade in batch["trades"]:
            ticker = str(trade["ticker"])
            table.add_row(
                ticker,
                f"{int(trade['shares']):+d}",
                _price(ticker, float(trade["price"])),
            )
        table.caption = f"批次净投入 ¥{float(batch['net_cny']):+,.2f}"
        console.print(table)


def _price(ticker: str, value: float) -> str:
    symbol = "¥" if ticker.endswith(".SS") else "$"
    return f"{symbol}{format_trade_price(ticker, value)}"


def format_trade_price(ticker: str, value: float) -> str:
    """Format a trade price at the fixed precision of its market."""
    decimals = 3 if ticker.endswith(".SS") else 2
    return f"{value:.{decimals}f}"


def _diagnostic_lines(report: Mapping[str, Any]) -> list[str]:
    direction_labels = {
        "up": "上行",
        "down": "下行",
        "flat": "横盘",
        None: "数据不足",
    }
    trends = report["trends"]
    lines = [
        f"{_BUCKET_LABELS[bucket]}趋势：{direction_labels[trends[bucket]]}"
        for bucket in _BUCKET_LABELS
    ]
    correlations = report["correlations"]
    lines.extend(
        (
            f"{_BUCKET_LABELS[str(item['first'])]}/"
            f"{_BUCKET_LABELS[str(item['second'])]}相关性异常："
            f"{float(item['correlation']):+.2f}"
        )
        for item in correlations
    )
    available_pairs = int(report["correlation_pairs"])
    total_pairs = len(_BUCKET_LABELS) * (len(_BUCKET_LABELS) - 1) // 2
    if available_pairs == 0:
        lines.append("相关性：数据不足")
    elif not correlations:
        lines.append("未发现相关性异常")
    if 0 < available_pairs < total_pairs:
        lines.append(f"相关性覆盖：{available_pairs}/{total_pairs} 组，其余数据不足")
    return lines


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _optional_percent(value: object) -> str:
    return "—" if value is None else _percent(float(value))


def _optional_signed_percent(value: object) -> str:
    return "—" if value is None else f"{float(value):+.2%}"


def _compact_percent(value: float) -> str:
    percentage = value * 100
    return f"{percentage:.0f}%" if percentage.is_integer() else f"{percentage:.2f}%"


def _optional_compact_percent(value: object) -> str:
    return "—" if value is None else _compact_percent(float(value))
