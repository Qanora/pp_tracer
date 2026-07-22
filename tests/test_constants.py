"""Tests for the fixed portfolio metadata."""

from ppt.constants import (
    BUCKET_ORDER,
    BUCKET_TICKERS,
    CNY_TICKERS,
    CORRIDOR_LOWER,
    CORRIDOR_UPPER,
    INTRA_BUCKET_SELL_THRESHOLD,
    TARGET_BUCKET_WEIGHT,
    TARGET_CURRENCY_WEIGHT,
    TICKER_CURRENCY,
    TICKER_LOT_SIZE,
    TICKER_ORDER,
    TICKER_WHITELIST,
    USD_TICKERS,
)


def test_fixed_bucket_and_ticker_order_is_complete_and_unique():
    assert BUCKET_ORDER == ("stock", "bond", "gold", "cash")
    assert BUCKET_TICKERS == {
        "stock": ("SPYM", "AVUV"),
        "bond": ("VGIT",),
        "gold": ("GLDM", "518880.SS"),
        "cash": ("SGOV", "511360.SS"),
    }
    assert len(TICKER_ORDER) == len(set(TICKER_ORDER)) == 7
    assert TICKER_WHITELIST == frozenset(TICKER_ORDER)


def test_currency_and_lot_metadata_cover_every_ticker():
    assert set(TICKER_CURRENCY) == set(TICKER_ORDER)
    assert set(TICKER_LOT_SIZE) == set(TICKER_ORDER)
    assert USD_TICKERS | CNY_TICKERS == TICKER_WHITELIST
    assert USD_TICKERS & CNY_TICKERS == set()
    assert CNY_TICKERS == {"518880.SS", "511360.SS"}
    assert TICKER_LOT_SIZE["518880.SS"] == 100
    assert TICKER_LOT_SIZE["511360.SS"] == 100
    assert all(TICKER_LOT_SIZE[ticker] == 1 for ticker in USD_TICKERS)


def test_strategy_constants_are_fixed():
    assert TARGET_BUCKET_WEIGHT == 0.25
    assert (CORRIDOR_LOWER, CORRIDOR_UPPER) == (0.15, 0.35)
    assert INTRA_BUCKET_SELL_THRESHOLD == 0.60
    assert TARGET_CURRENCY_WEIGHT == 0.50
