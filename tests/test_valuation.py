"""Tests for valuation core (§4.1–§4.5)."""

import math

import pytest

from ppt.constants import VOL_FLOOR
from ppt.valuation import (
    bucket_values,
    bucket_weights,
    corridor_bounds,
    equal_target_weights,
    risk_parity_weights,
    ticker_values_cny,
    total_value,
    trend_adjusted_corridor,
    trend_signal,
    volatility,
)

# ── §4.1 权重计算 ────────────────────────────────────────────────────────────


class TestTickerValuesCNY:
    """ticker → CNY value mapping."""

    def test_usd_ticker_converted(self):
        """USD ticker value = holdings * price * usdcny."""
        holdings = {"SPYM": 10}
        prices = {"SPYM": 72.5}
        usdcny = 7.2
        result = ticker_values_cny(holdings, prices, usdcny)
        assert result["SPYM"] == pytest.approx(10 * 72.5 * 7.2)

    def test_cny_ticker_direct(self):
        """CNY ticker value = holdings * price (no conversion)."""
        holdings = {"518880.SS": 500}
        prices = {"518880.SS": 5.50}
        result = ticker_values_cny(holdings, prices, usdcny=7.2)
        assert result["518880.SS"] == pytest.approx(500 * 5.50)

    def test_mixed_tickers(self):
        """Mixed USD + CNY tickers in one call."""
        holdings = {"SPYM": 10, "518880.SS": 500}
        prices = {"SPYM": 72.5, "518880.SS": 5.50}
        result = ticker_values_cny(holdings, prices, usdcny=7.0)
        assert result["SPYM"] == pytest.approx(10 * 72.5 * 7.0)
        assert result["518880.SS"] == pytest.approx(500 * 5.50)


class TestBucketValues:
    """Ticker values rolled up to buckets."""

    def test_stock_bucket_sums_sub_tickers(self):
        tv = {"SPYM": 1000.0, "AVUV": 500.0, "VGIT": 2000.0}
        bv = bucket_values(tv)
        assert bv["stock"] == pytest.approx(1500.0)
        assert bv["bond"] == pytest.approx(2000.0)

    def test_missing_bucket_is_zero(self):
        tv = {"SPYM": 1000.0}
        bv = bucket_values(tv)
        assert bv["gold"] == 0.0
        assert bv["cash"] == 0.0


class TestBucketWeights:
    """Bucket weight = bucket_value / total."""

    def test_equal_split(self):
        bv = {"stock": 100.0, "bond": 100.0, "gold": 100.0, "cash": 100.0}
        w = bucket_weights(bv)
        for b in bv:
            assert w[b] == pytest.approx(0.25)

    def test_uneven(self):
        bv = {"stock": 300.0, "bond": 100.0, "gold": 0.0, "cash": 0.0}
        w = bucket_weights(bv)
        assert w["stock"] == pytest.approx(0.75)
        assert w["bond"] == pytest.approx(0.25)
        assert w["gold"] == 0.0

    def test_total_zero_returns_zeros(self):
        bv = {"stock": 0.0, "bond": 0.0, "gold": 0.0, "cash": 0.0}
        w = bucket_weights(bv)
        assert all(v == 0.0 for v in w.values())


class TestTotalValue:
    def test_simple_sum(self):
        bv = {"stock": 100.0, "bond": 200.0, "gold": 300.0, "cash": 400.0}
        assert total_value(bv) == pytest.approx(1000.0)


# ── §4.2 目标权重 ────────────────────────────────────────────────────────────


class TestEqualTarget:
    def test_all_quarters(self):
        w = equal_target_weights()
        for v in w.values():
            assert v == pytest.approx(0.25)
        assert sum(w.values()) == pytest.approx(1.0)


class TestRiskParity:
    def test_basic(self):
        sigmas = {"stock": 0.15, "bond": 0.10, "gold": 0.16, "cash": 0.02}
        w = risk_parity_weights(sigmas)
        assert sum(w.values()) == pytest.approx(1.0)
        # cash has lowest vol → highest weight
        assert w["cash"] > w["stock"]
        assert w["cash"] > w["gold"]

    def test_cap_and_floor(self):
        """Weights clamped to [floor, cap] via iterative clipping."""
        sigmas = {"stock": 0.01, "bond": 0.10, "gold": 0.16, "cash": 0.02}
        w = risk_parity_weights(sigmas, cap=0.40, floor=0.10)
        for v in w.values():
            assert 0.10 - 1e-9 <= v <= 0.40 + 1e-9
        assert sum(w.values()) == pytest.approx(1.0)

    def test_all_hit_cap(self):
        """Extreme case where uncapped weights all exceed cap."""
        sigmas = {"stock": 0.005, "bond": 0.006, "gold": 0.007, "cash": 0.008}
        w = risk_parity_weights(sigmas, cap=0.40, floor=0.10)
        assert sum(w.values()) == pytest.approx(1.0)


# ── §4.3 波动率估计 ──────────────────────────────────────────────────────────


class TestVolatility:
    """60-day rolling annualized volatility."""

    def make_prices(self, n, drift=0.0):
        """Generate price sequence of length n with optional drift."""
        prices = [100.0]
        for i in range(1, n):
            prices.append(prices[-1] * (1 + drift + 0.001 * math.sin(i)))
        return prices

    def test_zero_vol(self):
        """Constant prices → near-zero volatility (≥ floor)."""
        prices = [100.0] * 61
        sigma = volatility(prices)
        assert sigma >= VOL_FLOOR

    def test_positive_vol(self):
        """Volatile prices → positive sigma."""
        prices = self.make_prices(61, drift=0.0)
        sigma = volatility(prices)
        assert sigma > 0.005

    def test_fallback_when_few_returns(self):
        """<20 returns → fallback value."""
        prices = [100.0] * 10
        sigma = volatility(prices, fallback=0.15)
        assert sigma == 0.15

    def test_edge_exactly_20_returns(self):
        """21 prices = 20 returns → should compute (not fallback)."""
        prices = self.make_prices(21)
        sigma = volatility(prices, fallback=0.15)
        assert sigma > 0  # computed, not fallback


# ── §4.4 自适应走廊 ──────────────────────────────────────────────────────────


class TestCorridor:
    def test_basic_bounds(self):
        """h = max(k * sigma / sqrt(12), hmin)."""
        L, U = corridor_bounds(w_star=0.25, sigma=0.12, k=2.5)
        # h = max(2.5 * 0.12 / 3.464, 0.03) ≈ 0.0866
        h = max(2.5 * 0.12 / math.sqrt(12), 0.03)
        assert L == pytest.approx(max(0.25 - h, 0.10))
        assert U == pytest.approx(min(0.25 + h, 0.40))

    def test_hmin_kicks_in(self):
        """Low vol → hmin dominates."""
        L, U = corridor_bounds(w_star=0.25, sigma=0.005, k=2.5)
        # h = max(tiny, 0.03) = 0.03
        assert L == pytest.approx(0.22)  # 0.25 - 0.03
        assert U == pytest.approx(0.28)  # 0.25 + 0.03

    def test_hard_caps(self):
        """Bounds can't exceed [0.10, 0.40]."""
        L, U = corridor_bounds(w_star=0.05, sigma=0.50, k=5.0)
        assert L >= 0.10
        assert U <= 0.40

    def test_no_history_fallback(self):
        """None sigma → return fixed thresholds."""
        L, U = corridor_bounds(w_star=0.25, sigma=None, k=2.5)
        assert L == 0.15
        assert U == 0.35


# ── §4.5 趋势信号 ────────────────────────────────────────────────────────────


class TestTrendSignal:
    def test_neutral_when_flat(self):
        """Flat prices → trend ≈ 0."""
        prices = [100.0] * 30
        t = trend_signal(prices, S=10, L=20)
        assert t == pytest.approx(0.0)

    def test_uptrend_positive(self):
        """Rising prices → positive trend."""
        prices = [100.0 + i * 0.5 for i in range(30)]
        t = trend_signal(prices, S=10, L=20)
        assert t > 0

    def test_downtrend_negative(self):
        """Falling prices → negative trend."""
        prices = [100.0 - i * 0.5 for i in range(30)]
        t = trend_signal(prices, S=10, L=20)
        assert t < 0

    def test_insufficient_data_returns_zero(self):
        """< L days → neutral."""
        prices = [100.0 + i for i in range(15)]
        t = trend_signal(prices, S=10, L=20)
        assert t == 0.0


class TestTrendAdjustedCorridor:
    def test_weak_bucket_raises_upper(self):
        """Trend < 0 → upper bound moves up (delay selling)."""
        L, U = trend_adjusted_corridor(
            w_star=0.25,
            sigma=0.12,
            trend=-0.05,
            k=2.5,
            lam=0.5,
        )
        base_L, base_U = corridor_bounds(0.25, 0.12, 2.5)
        assert U > base_U  # upper relaxed
        assert L == pytest.approx(base_L)  # lower unchanged

    def test_strong_bucket_lowers_floor(self):
        """Trend > 0 → lower bound moves down (delay buying)."""
        L, U = trend_adjusted_corridor(
            w_star=0.25,
            sigma=0.12,
            trend=0.05,
            k=2.5,
            lam=0.5,
        )
        base_L, base_U = corridor_bounds(0.25, 0.12, 2.5)
        assert L < base_L  # lower relaxed
        assert U == pytest.approx(base_U)  # upper unchanged

    def test_neutral_trend_unchanged(self):
        """Trend = 0 → no adjustment."""
        L, U = trend_adjusted_corridor(
            w_star=0.25,
            sigma=0.12,
            trend=0.0,
            k=2.5,
            lam=0.5,
        )
        base_L, base_U = corridor_bounds(0.25, 0.12, 2.5)
        assert L == pytest.approx(base_L)
        assert U == pytest.approx(base_U)
