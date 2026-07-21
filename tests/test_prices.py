"""Tests for price fetching and caching (§3)."""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ppt.constants import YFINANCE_TICKERS
from ppt.prices import (
    PriceCache,
    PriceFetcher,
    PriceValidator,
)

# ── Price Validator (§3 constraints) ─────────────────────────────────────────


class TestPriceValidator:
    def test_all_positive(self):
        """All prices > 0 passes."""
        errors = PriceValidator.validate(
            prices={"SPYM": 72.5, "VGIT": 58.92, "GLDM": 30.0},
            usdcny=7.25,
        )
        assert len(errors) == 0

    def test_negative_price_rejected(self):
        """Price ≤ 0 → error."""
        errors = PriceValidator.validate(
            prices={"SPYM": -72.5, "VGIT": 58.92},
            usdcny=7.25,
        )
        assert len(errors) > 0
        # negative price should trigger validation error
        pass  # len(errors) > 0 already asserted above

    def test_usdcny_out_of_range(self):
        """usdcny outside [5.0, 10.0] → warning (not error)."""
        errors = PriceValidator.validate(
            prices={"SPYM": 72.5},
            usdcny=15.0,
        )
        assert len(errors) > 0

    def test_usdcny_in_range(self):
        """usdcny within [5.0, 10.0] passes."""
        errors = PriceValidator.validate(
            prices={"SPYM": 72.5},
            usdcny=7.25,
        )
        assert all("usdcny" not in e.lower() for e in errors)

    def test_duplicate_placeholder_prices(self):
        """If unique price count ≤ total/3 → yfinance placeholder detected."""
        # 7 tickers, all same price → 1 unique ≤ 7/3 ≈ 2.3 → warning
        bad_prices = {t: 123.45 for t in YFINANCE_TICKERS if t != "CNY=X"}
        errors = PriceValidator.validate(prices=bad_prices, usdcny=7.25)
        assert any("unique" in e.lower() or "placeholder" in e.lower() for e in errors)

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_non_finite_price_is_blocking(self, value):
        errors = PriceValidator.validate(prices={"SPYM": value}, usdcny=7.25)
        assert any("not finite" in error for error in errors)


# ── Price Cache ───────────────────────────────────────────────────────────────


class TestPriceCache:
    def temp_cache_path(self):
        tmp = tempfile.mkdtemp()
        return Path(tmp) / "price_cache.json"

    def test_save_and_load(self):
        """Round-trip save → load preserves data."""
        path = self.temp_cache_path()
        cache = PriceCache(path=path, ttl=300)
        data = {
            "timestamp": "2025-06-19 12:00:00",
            "prices": {"SPYM": 72.5, "VGIT": 58.92},
            "usdcny": 7.25,
        }
        cache.save(data)
        loaded = cache.load()
        assert loaded["prices"] == data["prices"]
        assert loaded["usdcny"] == data["usdcny"]

    def test_expired(self):
        """Cache older than TTL → is_fresh returns False."""
        path = self.temp_cache_path()
        cache = PriceCache(path=path, ttl=1)
        # Write manually to bypass save()'s timestamp override
        path.write_text(
            json.dumps(
                {
                    "timestamp": "2020-01-01 00:00:00",
                    "prices": {"SPYM": 72.5},
                    "usdcny": 7.25,
                }
            )
        )
        assert cache.is_fresh() is False

    def test_fresh(self):
        """Cache within TTL → is_fresh returns True."""
        path = self.temp_cache_path()
        cache = PriceCache(path=path, ttl=99999)
        cache.save(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "prices": {"SPYM": 72.5},
                "usdcny": 7.25,
            }
        )
        assert cache.is_fresh() is True

    def test_empty_cache_not_fresh(self):
        """No cache file → is_fresh returns False."""
        path = self.temp_cache_path()
        cache = PriceCache(path=path, ttl=300)
        assert cache.is_fresh() is False

    def test_non_finite_cache_is_rejected(self):
        path = self.temp_cache_path()
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "prices": {"SPYM": float("inf")},
                    "usdcny": 7.25,
                }
            )
        )
        assert PriceCache(path=path).load() is None


# ── Price Fetcher (with mock yfinance) ────────────────────────────────────────


class TestPriceFetcher:
    def test_cache_hit_skips_fetch(self):
        """When cache is fresh, no yfinance call."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "price_cache.json"
            cache = PriceCache(path=cache_path, ttl=99999)
            cache.save(
                {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "prices": {
                        "SPYM": 72.5,
                        "VGIT": 58.92,
                        "AVUV": 120.0,
                        "GLDM": 30.0,
                        "SGOV": 100.0,
                        "518880.SS": 5.50,
                        "511360.SS": 100.0,
                    },
                    "usdcny": 7.25,
                }
            )

            fetcher = PriceFetcher(cache=cache)
            mock_download = MagicMock()
            result = fetcher.fetch(download_fn=mock_download, force=False)
            mock_download.assert_not_called()
            assert result["usdcny"] == 7.25

    def test_force_bypasses_cache(self):
        """--fresh forces re-fetch."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "price_cache.json"
            cache = PriceCache(path=cache_path, ttl=99999)
            cache.save({"timestamp": "2020-01-01", "prices": {}, "usdcny": 7.0})

            fetcher = PriceFetcher(cache=cache)
            mock_df = _make_mock_dataframe()
            result = fetcher.fetch(download_fn=lambda _: mock_df, force=True)
            assert result is not None

    def test_offline_fails_without_cache(self):
        """--offline without cache → raises."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "nonexistent.json"
            cache = PriceCache(path=cache_path, ttl=300)
            fetcher = PriceFetcher(cache=cache)
            with pytest.raises(RuntimeError):
                fetcher.fetch(download_fn=None, offline=True)


def _make_mock_dataframe():
    """Create a minimal mock yfinance response."""
    import pandas as pd

    df = pd.DataFrame()
    for t in ["SPYM", "AVUV", "VGIT", "GLDM", "SGOV", "518880.SS", "511360.SS"]:
        df[t] = [72.5]
    df["CNY=X"] = [7.25]
    return df
