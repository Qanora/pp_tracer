"""Tests for asset configuration constants (§1, §9)."""

from ppt.constants import (
    A_SHARE_TICKERS,
    BUCKET_TICKERS,
    BUCKETS,
    CNY_TICKERS,
    PRIMARY_TICKER,
    TICKER_CURRENCY,
    TICKER_LOT_SIZE,
    TICKER_MARKET,
    TICKER_WHITELIST,
    USD_TICKERS,
)


class TestAssetConfig:
    """Tests for §1 asset configuration."""

    def test_buckets(self):
        """Four buckets: stock, bond, gold, cash."""
        assert set(BUCKETS) == {"stock", "bond", "gold", "cash"}

    def test_bucket_tickers(self):
        """Each bucket has correct tickers."""
        assert "SPYM" in BUCKET_TICKERS["stock"]
        assert "AVUV" in BUCKET_TICKERS["stock"]
        assert BUCKET_TICKERS["bond"] == ("VGIT",)
        assert "GLDM" in BUCKET_TICKERS["gold"]
        assert "518880.SS" in BUCKET_TICKERS["gold"]
        assert "SGOV" in BUCKET_TICKERS["cash"]
        assert "511360.SS" in BUCKET_TICKERS["cash"]

    def test_primary_ticker(self):
        """Primary tickers for volatility/trend calculation."""
        assert PRIMARY_TICKER["stock"] == "SPYM"
        assert PRIMARY_TICKER["bond"] == "VGIT"
        assert PRIMARY_TICKER["gold"] == "GLDM"
        assert PRIMARY_TICKER["cash"] == "SGOV"

    def test_whitelist(self):
        """Exactly 7 tickers in whitelist."""
        assert len(TICKER_WHITELIST) == 7
        expected = {"SPYM", "AVUV", "VGIT", "GLDM", "518880.SS", "SGOV", "511360.SS"}
        assert TICKER_WHITELIST == expected

    def test_ticker_market(self):
        """SPYM/VGIT/GLDM/AVUV/SGOV = US; 518880.SS/511360.SS = A."""
        assert TICKER_MARKET["SPYM"] == "US"
        assert TICKER_MARKET["518880.SS"] == "A"
        assert TICKER_MARKET["511360.SS"] == "A"

    def test_lot_sizes(self):
        """US tickers = 1 share; A-share tickers = 100 shares."""
        assert TICKER_LOT_SIZE["SPYM"] == 1
        assert TICKER_LOT_SIZE["518880.SS"] == 100
        assert TICKER_LOT_SIZE["511360.SS"] == 100

    def test_currency(self):
        """USD tickers = USD; CNY tickers = CNY."""
        assert TICKER_CURRENCY["SPYM"] == "USD"
        assert TICKER_CURRENCY["518880.SS"] == "CNY"

    def test_usd_cny_sets(self):
        """USD_TICKERS and CNY_TICKERS partition the whitelist."""
        assert USD_TICKERS | CNY_TICKERS == TICKER_WHITELIST
        assert len(USD_TICKERS & CNY_TICKERS) == 0
        assert "SPYM" in USD_TICKERS
        assert "518880.SS" in CNY_TICKERS

    def test_a_share_tickers(self):
        """A-share tickers are subset of CNY tickers."""
        assert A_SHARE_TICKERS == {"518880.SS", "511360.SS"}
