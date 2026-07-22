"""Smoke tests for the small set of terminal components."""

from ppt import display


def test_status_output_contains_current_holdings_and_allocations():
    with display.console.capture() as capture:
        display.show_status(
            total_value=40_000.0,
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
        )

    output = capture.get()
    assert "组合当前状态" in output
    assert "当前持仓" in output
    assert "SPYM" in output
    assert "518880.SS" in output
    assert "USD $100" in output
    assert "CNY ¥1" in output
    assert "四桶配置" in output
    assert "币种配置" in output
    assert "三级最大偏差" in output
    assert "¥40,000.00" in output


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
            diagnostics=["股票趋势：上行"],
            command="ppt buy SPYM#2@75.0 SGOV#-1@100.0",
        )

    output = capture.get()
    assert "未使用金额" in output
    assert "四桶最大偏差" in output
    assert "股票趋势：上行" in output
    assert "ppt buy SPYM#2@75.0 SGOV#-1@100.0" in output


def test_history_keeps_signed_shares_and_reverse_batch_order():
    batches = [
        {
            "executed_at": "2026-01-01T00:00:00+08:00",
            "net_cny": 100.0,
            "trades": [{"ticker": "SPYM", "shares": 1, "price": 10.0}],
        },
        {
            "executed_at": "2026-01-02T00:00:00+08:00",
            "net_cny": -50.0,
            "trades": [{"ticker": "SPYM", "shares": -1, "price": 11.0}],
        },
    ]
    with display.console.capture() as capture:
        display.show_history(
            cash_in=100.0,
            cash_out=50.0,
            market_value=0.0,
            profit=-50.0,
            return_rate=-0.5,
            batches=batches,
        )

    output = capture.get()
    assert output.index("2026-01-02") < output.index("2026-01-01")
    assert "+1" in output
    assert "-1" in output
    assert "-50.00%" in output
