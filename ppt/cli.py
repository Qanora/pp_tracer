"""Thin command-line orchestration for the four target commands."""

from __future__ import annotations

import math
from collections.abc import Sequence

import click

from ppt.constants import BUCKET_ORDER, BUCKET_TICKERS, CNY_TICKERS, TICKER_ORDER
from ppt.display import (
    confirm_reset,
    show_error,
    show_history,
    show_initialized,
    show_plan,
    show_recorded,
)
from ppt.holdings import (
    HoldingsStore,
    LedgerNotInitializedError,
    Trade,
    batch_net_investment,
    derive_holdings,
    ledger_batches,
)
from ppt.prices import fetch_market
from ppt.rebalance import build_plan
from ppt.returns import diagnostics, history_summary
from ppt.valuation import bucket_values, ticker_values_cny, total_value


def _store() -> HoldingsStore:
    return HoldingsStore.from_environment()


def _require_ledger(store: HoldingsStore) -> dict:
    ledger = store.load()
    if ledger is None:
        raise LedgerNotInitializedError("账本尚未初始化，请先运行 ppt init")
    return ledger


def _parse_trade(argument: str) -> Trade:
    try:
        ticker, shares_and_price = argument.split("#")
        shares_text, price_text = shares_and_price.split("@")
        shares = int(shares_text)
        price = float(price_text)
    except (TypeError, ValueError) as exc:
        raise click.BadParameter(
            f"格式错误：{argument}；应为 代码#有符号整数股数@正数价格"
        ) from exc
    return Trade(ticker=ticker, shares=shares, price=price)


def _parse_batch(arguments: Sequence[str]) -> tuple[Trade, ...]:
    trades = tuple(_parse_trade(argument) for argument in arguments)
    tickers = [trade.ticker for trade in trades]
    if len(tickers) != len(set(tickers)):
        raise click.BadParameter("同一批次中每个标的只能出现一次")
    return trades


def _bucket_history(ticker_history: dict[str, Sequence[float]]) -> dict[str, Sequence[float]]:
    """Use each bucket's first fixed ticker as its stable diagnostic proxy."""
    return {
        bucket: ticker_history.get(BUCKET_TICKERS[bucket][0], ())
        for bucket in BUCKET_ORDER
    }


def _diagnostic_lines(ticker_history: dict[str, Sequence[float]]) -> list[str]:
    result = diagnostics(_bucket_history(ticker_history))
    direction_text = {"up": "上行", "down": "下行", "flat": "横盘", None: "数据不足"}
    lines = [
        f"{bucket} 趋势：{direction_text[result.trends[bucket]]}"
        for bucket in BUCKET_ORDER
    ]
    lines.extend(
        f"{warning.first}/{warning.second} 相关性异常：{warning.correlation:+.2f}"
        for warning in result.correlations
    )
    if not result.correlations:
        lines.append("未发现相关性异常（数据不足时不作判断）")
    return lines


def _trade_rows(trades: dict[str, int], prices: dict[str, float], usdcny: float) -> list[dict]:
    rows: list[dict] = []
    for ticker in TICKER_ORDER:
        shares = trades.get(ticker, 0)
        if shares == 0:
            continue
        multiplier = 1.0 if ticker in CNY_TICKERS else usdcny
        rows.append(
            {
                "ticker": ticker,
                "shares": shares,
                "price": prices[ticker],
                "amount_cny": shares * prices[ticker] * multiplier,
            }
        )
    return rows


def _buy_command(trades: dict[str, int], prices: dict[str, float]) -> str | None:
    parts = [
        f"'{ticker}#{trades[ticker]:+d}@{prices[ticker]!r}'"
        for ticker in TICKER_ORDER
        if trades.get(ticker, 0) != 0
    ]
    return "ppt buy " + " ".join(parts) if parts else None


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """永久投资组合 OSS 账本与三级优先均衡计划器。"""


@main.command()
@click.option("--yes", is_flag=True, help="跳过重置确认")
def init(yes: bool) -> None:
    """备份现有账本并重置持仓和交易历史。"""
    if not yes and not confirm_reset():
        return
    backup_path = _store().initialize()
    show_initialized(backup_path)


@main.command(context_settings={"ignore_unknown_options": True})
@click.argument("trade_arguments", metavar="代码#股数@价格", nargs=-1, required=True)
def buy(trade_arguments: tuple[str, ...]) -> None:
    """原子记录多个交易；正股数买入，负股数卖出。"""
    trades = _parse_batch(trade_arguments)
    market = fetch_market()
    store = _store()
    batch = store.record_batch(trades, market.usdcny)
    ledger = _require_ledger(store)
    presentation = batch.to_dict()
    presentation["net_cny"] = batch_net_investment(batch)
    show_recorded(presentation, derive_holdings(ledger))


@main.command()
@click.argument("amount", type=float, required=True)
def plan(amount: float) -> None:
    """合并自动均衡与指定人民币新增金额，输出一条净交易命令。"""
    if not math.isfinite(amount) or amount <= 0:
        raise click.BadParameter("人民币金额必须是正的有限数值", param_hint="amount")
    store = _store()
    ledger = _require_ledger(store)
    holdings = derive_holdings(ledger)
    market = fetch_market()
    result = build_plan(holdings, market.prices, market.usdcny, amount)
    show_plan(
        trades=_trade_rows(result.trades, market.prices, market.usdcny),
        budget=amount,
        buy_cost=result.buy_cost,
        sell_proceeds=result.sell_proceeds,
        unused_amount=result.unused_amount,
        before={
            "bucket": result.before_score.bucket_max,
            "intra": result.before_score.intra_max,
            "currency": result.before_score.currency,
        },
        after={
            "bucket": result.after_score.bucket_max,
            "intra": result.after_score.intra_max,
            "currency": result.after_score.currency,
        },
        diagnostics=_diagnostic_lines(market.history),
        command=_buy_command(result.trades, market.prices),
    )


@main.command()
def history() -> None:
    """展示收益汇总和按批次倒序的有符号交易记录。"""
    store = _store()
    ledger = _require_ledger(store)
    holdings = derive_holdings(ledger)
    market = fetch_market()
    current_value = total_value(
        bucket_values(ticker_values_cny(holdings, market.prices, market.usdcny))
    )
    summary = history_summary(ledger["batches"], current_value)
    batches = []
    for batch in ledger_batches(ledger):
        item = batch.to_dict()
        item["net_cny"] = batch_net_investment(batch)
        batches.append(item)
    show_history(
        cash_in=summary.invested,
        cash_out=summary.withdrawn,
        market_value=summary.current_value,
        profit=summary.profit,
        return_rate=summary.return_rate,
        batches=batches,
    )


def run() -> None:
    """Run Click with one consistent non-zero failure path."""
    try:
        main(standalone_mode=False)
    except click.exceptions.Exit as exc:
        raise SystemExit(exc.exit_code) from None
    except click.ClickException as exc:
        show_error(exc.format_message())
        raise SystemExit(exc.exit_code) from None
    except KeyboardInterrupt:
        show_error("已中断")
        raise SystemExit(130) from None
    except Exception as exc:
        show_error(str(exc) or exc.__class__.__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
