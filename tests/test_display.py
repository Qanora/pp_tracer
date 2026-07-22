"""Smoke tests for the small set of terminal components."""

from ppt import display


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
