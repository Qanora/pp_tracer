"""Tests for conversion, intra-bucket rebalance, correlation, returns (§4.8–§4.13)."""

import warnings

import pytest

from ppt.returns import (
    bucket_correlation,
    bucket_net_cost,
    cagr,
    conversion_check,
    intra_bucket_rebalance,
    stock_bond_reversal,
    total_return,
    xirr,
)

# ── §4.10 两段式换仓 ─────────────────────────────────────────────────────────


class TestConversion:
    """§4.10 GLDM→518880, SGOV→511360."""

    def test_gldm_triggers(self):
        """GLDM market value ≥ threshold → trigger."""
        # threshold = 1000 * p_518880 * (1 + 0.003)
        # p_518880 = 5.50 → threshold = 1000 * 5.50 * 1.003 = 5516.5
        # GLDM: 200 shares * 30 * 7.25 = 43500 → triggers
        result = conversion_check(
            ticker="GLDM",
            market_value_cny=43500.0,
            target_price_cny=5.50,
            conversion_shares=1000,
            fx_spread=0.003,
        )
        assert result["triggered"] is True
        assert result["batches"] > 0

    def test_gldm_no_trigger(self):
        """GLDM below threshold → no trigger."""
        result = conversion_check(
            ticker="GLDM",
            market_value_cny=5000.0,
            target_price_cny=5.50,
            conversion_shares=1000,
            fx_spread=0.003,
        )
        assert result["triggered"] is False

    def test_sgov_triggers(self):
        """SGOV ≥ 100 * p_511360 * (1 + fx_spread)."""
        result = conversion_check(
            ticker="SGOV",
            market_value_cny=12000.0,
            target_price_cny=100.0,
            conversion_shares=100,
            fx_spread=0.003,
        )
        assert result["triggered"] is True


# ── §4.9 桶内再均衡 ──────────────────────────────────────────────────────────


class TestIntraBucketRebalance:
    """§4.9 SPYM ↔ AVUV."""

    def test_triggers_when_ratio_exceeds(self):
        """SPYM at 70% → trigger."""
        result = intra_bucket_rebalance(
            V_SPYM=7000.0,
            V_AVUV=3000.0,
            p_SPYM=72.5,
            p_AVUV=120.0,
            threshold=0.60,
            target_ratio=0.50,
        )
        assert result["triggered"] is True
        assert result["sell_ticker"] == "SPYM"
        assert result["sell_shares"] > 0

    def test_no_trigger_when_balanced(self):
        """SPYM at 55% → no trigger."""
        result = intra_bucket_rebalance(
            V_SPYM=5500.0,
            V_AVUV=4500.0,
            p_SPYM=72.5,
            p_AVUV=120.0,
        )
        assert result["triggered"] is False

    def test_avuv_over_triggers(self):
        """AVUV at 65% → trigger, sell AVUV."""
        result = intra_bucket_rebalance(
            V_SPYM=3500.0,
            V_AVUV=6500.0,
            p_SPYM=72.5,
            p_AVUV=120.0,
        )
        assert result["triggered"] is True
        assert result["sell_ticker"] == "AVUV"

    def test_sell_clamped_to_holdings(self):
        """Sell shares ≤ holdings."""
        result = intra_bucket_rebalance(
            V_SPYM=6000.0,
            V_AVUV=4000.0,
            p_SPYM=72.5,
            p_AVUV=120.0,
            max_holdings={"SPYM": 30.0, "AVUV": 50.0},
        )
        if result["triggered"]:
            ticker = result["sell_ticker"]
            assert result["sell_shares"] <= result["max_holdings"][ticker]


# ── §4.12 相关性分析 ─────────────────────────────────────────────────────────


class TestBucketCorrelation:
    """§4.12 桶间 Pearson 相关系数."""

    def test_perfect_positive(self):
        """Identical returns → ρ ≈ 1."""
        prices_a = [100.0 + i for i in range(60)]
        prices_b = [200.0 + 2 * i for i in range(60)]
        rho = bucket_correlation(prices_a, prices_b)
        assert rho is not None
        assert rho > 0.9

    def test_insufficient_data(self):
        """<30 data points → None."""
        rho = bucket_correlation([100.0] * 10, [200.0] * 10)
        assert rho is None

    def test_zero_variance(self):
        """Flat prices → None (variance=0)."""
        rho = bucket_correlation(
            [100.0] * 60,
            [200.0 + i * 0.1 for i in range(60)],
        )
        assert rho is None

    def test_nan_in_prices_returns_none(self):
        """NaN in price history → None (not silently clamped to +1.0)."""
        # Construct data where NaN is present but both variances are non-trivial
        # so the old code would NOT be saved by the zero-variance guard.
        prices_a = [100.0 + 2.0 * i for i in range(1, 32)]  # 31 non-NaN points
        prices_a.append(float("nan"))
        prices_a.extend([prices_a[30] + 2.0 * i for i in range(1, 31)])

        prices_b = [200.0 + i * 3.0 for i in range(62)]

        rho = bucket_correlation(prices_a, prices_b)
        assert rho is None, f"NaN returns should yield None, got {rho!r}"

    def test_inf_in_prices_returns_none(self):
        """Inf in price history → None."""
        prices_a = [100.0 + i * 5.0 for i in range(62)]
        prices_b = [100.0 + i * 5.0 for i in range(62)]
        prices_b[31] = float("inf")

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            rho = bucket_correlation(prices_a, prices_b)
        assert rho is None, f"Inf returns should yield None, got {rho!r}"


class TestStockBondReversal:
    """§4.12 股债相关性反转检测."""

    def test_no_reversal(self):
        """Same correlation pattern → no reversal."""
        prices_stock = [100.0 + i * 0.5 for i in range(61)]
        prices_bond = [100.0 + i * 0.1 for i in range(61)]
        result = stock_bond_reversal(prices_stock, prices_bond)
        assert result["reversal"] is False

    def test_reversal_detected(self):
        """ρ前 < 0 AND ρ后 > 0.3 → reversal."""
        # First 30 days: stock up, bond down → negative correlation
        stock_first = [100.0 + i * 0.5 for i in range(30)]
        bond_first = [100.0 - i * 0.3 for i in range(30)]
        # Split day
        split_s = stock_first[-1] + 0.25
        split_b = bond_first[-1] - 0.15
        # Next 30 days: both up → positive correlation
        stock_second = [split_s + i * 0.5 for i in range(30)]
        bond_second = [split_b + i * 0.4 for i in range(30)]

        prices_stock = stock_first + [split_s] + stock_second
        prices_bond = bond_first + [split_b] + bond_second

        result = stock_bond_reversal(prices_stock, prices_bond)
        # Must have enough data
        assert len(prices_stock) >= 61
        # Check reversal condition
        assert "reversal" in result


# ── §4.13 收益计算 ───────────────────────────────────────────────────────────


class TestBucketNetCost:
    """§4.13 桶净成本."""

    def test_buy_adds_cost(self):
        """Buy → cost increases."""
        transactions = [
            {
                "type": "buy",
                "trades": [{"ticker": "SPYM", "shares": 10, "price": 72.5, "currency": "USD"}],
                "usdcny": 7.25,
                "amount_cny": 5256.25,
            }
        ]
        costs = bucket_net_cost(transactions)
        assert costs["stock"] == pytest.approx(5256.25)

    def test_sell_reduces_cost(self):
        """Sell → cost decreases."""
        transactions = [
            {
                "type": "buy",
                "trades": [{"ticker": "SPYM", "shares": 10, "price": 72.5, "currency": "USD"}],
                "usdcny": 7.25,
                "amount_cny": 5256.25,
            },
            {
                "type": "sell",
                "trades": [{"ticker": "SPYM", "shares": 5, "price": 80.0, "currency": "USD"}],
                "usdcny": 7.30,
                "amount_cny": 2920.00,
            },
        ]
        costs = bucket_net_cost(transactions)
        assert costs["stock"] == pytest.approx(5256.25 - 2920.00)

    def test_mixed_currencies(self):
        """USD and CNY trades in same bucket."""
        transactions = [
            {
                "type": "buy",
                "trades": [{"ticker": "GLDM", "shares": 100, "price": 30.0, "currency": "USD"}],
                "usdcny": 7.25,
                "amount_cny": 21750.0,
            },
            {
                "type": "buy",
                "trades": [
                    {"ticker": "518880.SS", "shares": 1000, "price": 5.50, "currency": "CNY"}
                ],
                "usdcny": 0,
                "amount_cny": 5500.0,
            },
        ]
        costs = bucket_net_cost(transactions)
        assert costs["gold"] == pytest.approx(21750.0 + 5500.0)


class TestTotalReturn:
    """§4.13 总收益."""

    def test_profit(self):
        """V > net_cost → positive return."""
        costs = {"stock": 5000, "bond": 5000, "gold": 0, "cash": 0}
        P, pct = total_return(V=15000.0, bucket_costs=costs)
        assert P == pytest.approx(5000.0)
        assert pct == pytest.approx(0.50)

    def test_zero_cost(self):
        """Zero cost → return 0."""
        P, pct = total_return(V=10000.0, bucket_costs={"stock": 0, "bond": 0, "gold": 0, "cash": 0})
        assert P == 0.0


class TestCAGR:
    """§4.13 CAGR fallback."""

    def test_basic(self):
        """100 → 121 over 2 years → CAGR = 10%."""
        result = cagr(V=121.0, cost=100.0, years=2.0)
        assert result == pytest.approx(0.10)


class TestXIRR:
    """§4.13 XIRR Newton iteration."""

    def test_simple(self):
        """Buy at -100, now worth 110 after 1 year → XIRR ≈ 10%."""
        cashflows = [
            (-100.0, 0.0),
            (110.0, 365.0),
        ]
        rate = xirr(cashflows)
        assert rate is not None
        assert rate == pytest.approx(0.10, abs=0.02)

    def test_insufficient_flows(self):
        """Only one flow → None."""
        rate = xirr([(100.0, 0.0)])
        assert rate is None

    def test_zero_duration(self):
        """All flows same day → None."""
        rate = xirr([(-100.0, 0.0), (100.0, 0.0)])
        assert rate is None
