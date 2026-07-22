"""Smoke tests for the small set of terminal components."""

from ppt import display


def test_trade_prices_use_fixed_market_precision():
    assert display.format_trade_price("SGOV", 100.60009765625) == "100.60"
    assert display.format_trade_price("SPYM", 88.13500213623047) == "88.14"
    assert display.format_trade_price("518880.SS", 8.5600004196167) == "8.560"


def test_status_output_contains_current_holdings_and_allocations():
    with display.console.capture() as capture:
        display.show_status(
            usdcny=1.0,
            tickers=[
                {
                    "bucket": "stock",
                    "ticker": "SPYM",
                    "currency": "USD",
                    "shares": 50,
                    "price": 100.0,
                    "value_cny": 5_000.0,
                    "portfolio_weight": 0.125,
                    "bucket_weight": 0.5,
                    "bucket_target": 0.5,
                },
                {
                    "bucket": "gold",
                    "ticker": "518880.SS",
                    "currency": "CNY",
                    "shares": 5000,
                    "price": 1.0,
                    "value_cny": 5_000.0,
                    "portfolio_weight": 0.125,
                    "bucket_weight": 0.5,
                    "bucket_target": 0.5,
                },
            ],
            buckets=[
                {
                    "bucket": bucket,
                    "value_cny": 10_000.0,
                    "weight": 0.25,
                    "target": 0.25,
                    "deviation": 0.0,
                    "corridor": "within",
                }
                for bucket in ("stock", "bond", "gold", "cash")
            ],
            currencies=[
                {
                    "currency": "USD",
                    "value_cny": 30_000.0,
                    "weight": 0.75,
                    "target": 0.5,
                    "deviation": 0.25,
                },
                {
                    "currency": "CNY",
                    "value_cny": 10_000.0,
                    "weight": 0.25,
                    "target": 0.5,
                    "deviation": -0.25,
                },
            ],
            deviations={"bucket": 0.0, "intra": 0.0, "currency": 0.25},
            corridor_breached=False,
            performance={
                "invested": 35_000.0,
                "withdrawn": 0.0,
                "net_invested": 35_000.0,
                "current_value": 40_000.0,
                "profit": 5_000.0,
                "return_rate": 1 / 7,
            },
            backtest={
                "current_drawdown": -0.05,
                "maximum_drawdown": -0.12,
                "maximum_runup": 0.18,
                "observations": 30,
            },
            diagnostics={
                "trends": {
                    "stock": "up",
                    "bond": "flat",
                    "gold": "down",
                    "cash": None,
                },
                "correlation_pairs": 3,
                "correlations": [],
            },
        )

    output = capture.get()
    assert "组合当前状态" in output
    assert "当前持仓" in output
    assert "SPYM" in output
    assert "518880.SS" in output
    assert "USD $100.00" in output
    assert "CNY ¥1.000" in output
    assert "四桶配置" in output
    assert "币种配置" in output
    assert "三级最大偏差" in output
    assert "¥40,000.00" in output
    assert "累计盈亏" in output
    assert "+14.29%" in output
    assert "当前回撤" in output
    assert "-5.00%" in output
    assert "最大回撤" in output
    assert "-12.00%" in output
    assert "最大涨幅" in output
    assert "18.00%" in output
    assert "股票趋势：上行" in output
    assert "相关性覆盖：3/6 组，其余数据不足" in output


def test_diagnostics_distinguish_unavailable_from_no_warning():
    trends = {bucket: None for bucket in ("stock", "bond", "gold", "cash")}

    unavailable = display._diagnostic_lines(
        {"trends": trends, "correlation_pairs": 0, "correlations": []}
    )
    clear = display._diagnostic_lines(
        {"trends": trends, "correlation_pairs": 6, "correlations": []}
    )

    assert "相关性：数据不足" in unavailable
    assert "未发现相关性异常" not in unavailable
    assert "未发现相关性异常" in clear
    assert "相关性：数据不足" not in clear


def test_plan_output_contains_cash_scores_and_exact_command():
    with display.console.capture() as capture:
        display.show_plan(
            trades=[
                {
                    "ticker": "SPYM",
                    "shares": 2,
                    "price": 75.0,
                    "amount_cny": 1080.0,
                },
                {
                    "ticker": "SGOV",
                    "shares": -1,
                    "price": 100.0,
                    "amount_cny": -720.0,
                },
            ],
            budget=1000.0,
            buy_cost=1080.0,
            sell_proceeds=720.0,
            unused_amount=640.0,
            before={"bucket": 0.2, "intra": 0.1, "currency": 0.3},
            after={"bucket": 0.1, "intra": 0.05, "currency": 0.2},
            command="ppt buy SPYM#2@75.0 SGOV#-1@100.0",
        )

    output = capture.get()
    assert "未使用金额" in output
    assert "四桶最大偏差" in output
    assert "趋势与相关性提示" not in output
    assert "ppt buy SPYM#2@75.0 SGOV#-1@100.0" in output


def test_history_keeps_signed_shares_and_reverse_batch_order():
    batches = [
        {
            "executed_at": "2026-01-01T00:00:00+08:00",
            "net_cny": 100.0,
            "trades": [{"ticker": "SPYM", "shares": 1, "price": 10.0}],
        },
        {
            "executed_at": "2026-01-02T00:00:00.123456+08:00",
            "net_cny": -50.0,
            "trades": [{"ticker": "SPYM", "shares": -1, "price": 11.0}],
        },
    ]
    with display.console.capture() as capture:
        display.show_history(
            cash_in=100.0,
            cash_out=50.0,
            net_invested=50.0,
            batches=batches,
        )

    output = capture.get()
    assert "2026-01-02T00:00:00.123456+08:00" in output
    assert output.index("2026-01-02") < output.index("2026-01-01")
    assert "+1" in output
    assert "-1" in output
    assert "资金流汇总" in output
    assert "净投入" in output
    assert "收益率" not in output
