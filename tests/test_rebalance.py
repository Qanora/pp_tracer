"""Tests for the unified lexicographic planner."""

import math

import pytest

from ppt.constants import BUCKET_TICKERS, CNY_TICKERS, TICKER_LOT_SIZE
from ppt.rebalance import PlanResult, build_plan

PRICES = {
    "SPYM": 100.0,
    "AVUV": 100.0,
    "VGIT": 100.0,
    "GLDM": 100.0,
    "SGOV": 100.0,
    "518880.SS": 1.0,
    "511360.SS": 1.0,
}


def holdings_for_bucket_values(
    stock: int,
    bond: int,
    gold: int,
    cash: int,
) -> dict[str, int]:
    """Create internally equal holdings; values must be multiples of 200."""

    assert all(value % 200 == 0 for value in (stock, bond, gold, cash))
    return {
        "SPYM": stock // 200,
        "AVUV": stock // 200,
        "VGIT": bond // 100,
        "GLDM": gold // 200,
        "518880.SS": gold // 2,
        "SGOV": cash // 200,
        "511360.SS": cash // 2,
    }


def bucket_trade_value(trades: dict[str, int], bucket: str) -> float:
    return sum(
        trades.get(ticker, 0)
        * PRICES[ticker]
        * (1.0 if ticker in CNY_TICKERS else 1.0)
        for ticker in BUCKET_TICKERS[bucket]
    )


def assert_legal_and_funded(result: PlanResult, budget: float) -> None:
    assert result.buy_cost <= budget + result.sell_proceeds + 1e-7
    assert result.unused_amount == pytest.approx(
        budget + result.sell_proceeds - result.buy_cost
    )
    assert len(result.trades) == len(set(result.trades))
    for ticker, shares in result.final_holdings.items():
        assert shares >= 0
        assert shares % TICKER_LOT_SIZE[ticker] == 0
    for ticker, delta in result.trades.items():
        assert delta != 0
        assert delta % TICKER_LOT_SIZE[ticker] == 0


def test_empty_portfolio_gets_one_unified_fully_funded_plan() -> None:
    result = build_plan({}, PRICES, usdcny=1.0, budget=40_000.0)

    assert isinstance(result, PlanResult)
    assert result.corridor_breached is False
    assert result.buy_cost == pytest.approx(40_000.0)
    assert result.sell_proceeds == 0.0
    assert result.unused_amount == 0.0
    assert result.after_score.bucket_max == pytest.approx(0.0)
    assert result.after_score.intra_max == pytest.approx(0.0)
    assert all(delta > 0 for delta in result.trades.values())
    assert_legal_and_funded(result, 40_000.0)


def test_inside_corridor_never_has_cross_bucket_net_sale() -> None:
    holdings = holdings_for_bucket_values(30_000, 30_000, 20_000, 20_000)
    result = build_plan(holdings, PRICES, usdcny=1.0, budget=2_000.0)

    assert result.corridor_breached is False
    for bucket in BUCKET_TICKERS:
        assert bucket_trade_value(result.trades, bucket) >= -1e-7
    assert_legal_and_funded(result, 2_000.0)


def test_corridor_breach_allows_overweight_bucket_to_fund_others() -> None:
    holdings = holdings_for_bucket_values(40_000, 20_000, 20_000, 20_000)
    result = build_plan(holdings, PRICES, usdcny=1.0, budget=100.0)

    assert result.corridor_breached is True
    assert bucket_trade_value(result.trades, "stock") < 0
    assert any(
        bucket_trade_value(result.trades, bucket) > 0
        for bucket in ("bond", "gold", "cash")
    )
    assert result.after_score.bucket_max < result.before_score.bucket_max
    assert_legal_and_funded(result, 100.0)


def test_low_corridor_breach_can_draw_from_buckets_above_target() -> None:
    holdings = holdings_for_bucket_values(30_000, 30_000, 30_000, 10_000)
    result = build_plan(holdings, PRICES, usdcny=1.0, budget=100.0)

    assert result.corridor_breached is True
    assert bucket_trade_value(result.trades, "cash") > 0
    assert any(
        bucket_trade_value(result.trades, bucket) < 0
        for bucket in ("stock", "bond", "gold")
    )
    assert_legal_and_funded(result, 100.0)


def test_bucket_priority_beats_currency_priority() -> None:
    holdings = holdings_for_bucket_values(6_000, 8_000, 8_000, 8_000)
    result = build_plan(holdings, PRICES, usdcny=1.0, budget=100.0)

    # The only priority-one improvement is an additional USD stock share.
    assert bucket_trade_value(result.trades, "stock") == pytest.approx(100.0)
    assert all(ticker in {"SPYM", "AVUV"} for ticker in result.trades)
    assert result.after_score.bucket_max < result.before_score.bucket_max


def test_intra_bucket_priority_beats_currency_even_when_currency_worsens() -> None:
    holdings = holdings_for_bucket_values(10_000, 10_000, 10_000, 10_000)
    holdings["GLDM"] = 20
    holdings["518880.SS"] = 8_000
    result = build_plan(holdings, PRICES, usdcny=1.0, budget=1_600.0)

    assert result.trades["GLDM"] > 0
    # CNY gold may not be sold for a reverse active conversion.
    assert "518880.SS" not in result.trades
    for bucket in BUCKET_TICKERS:
        assert bucket_trade_value(result.trades, bucket) == pytest.approx(400.0)
    assert result.after_score.bucket_max == pytest.approx(result.before_score.bucket_max)
    assert result.after_score.intra_max < result.before_score.intra_max
    assert result.after_score.currency > result.before_score.currency
    assert_legal_and_funded(result, 1_600.0)


@pytest.mark.parametrize(
    ("source", "target"),
    (("GLDM", "518880.SS"), ("SGOV", "511360.SS")),
)
def test_usd_to_cny_conversion_is_part_of_the_single_net_plan(
    source: str,
    target: str,
) -> None:
    holdings = holdings_for_bucket_values(10_000, 10_000, 10_000, 10_000)
    holdings[source] = 80
    holdings[target] = 2_000
    result = build_plan(holdings, PRICES, usdcny=1.0, budget=100.0)

    assert result.trades[source] < 0
    assert result.trades[target] > 0
    assert result.after_score.intra_max < result.before_score.intra_max
    assert_legal_and_funded(result, 100.0)


@pytest.mark.parametrize(
    ("field", "value"),
    (("budget", 0.0), ("budget", math.inf), ("usdcny", 0.0)),
)
def test_invalid_scalar_inputs_are_rejected(field: str, value: float) -> None:
    kwargs = {"holdings": {}, "prices": PRICES, "usdcny": 1.0, "budget": 100.0}
    kwargs[field] = value

    with pytest.raises(ValueError):
        build_plan(**kwargs)


def test_missing_or_invalid_prices_and_illegal_holdings_are_rejected() -> None:
    missing = dict(PRICES)
    missing.pop("VGIT")
    with pytest.raises(ValueError, match="missing prices"):
        build_plan({}, missing, 1.0, 100.0)

    invalid = dict(PRICES, VGIT=float("nan"))
    with pytest.raises(ValueError, match="invalid price"):
        build_plan({}, invalid, 1.0, 100.0)

    with pytest.raises(ValueError, match="invalid holdings"):
        build_plan({"518880.SS": 50}, PRICES, 1.0, 100.0)
