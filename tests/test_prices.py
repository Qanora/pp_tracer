"""Tests for strict current and advisory historical market data."""

import json
import math
from datetime import date
from unittest.mock import MagicMock

import pytest

from ppt.constants import TICKER_ORDER
from ppt.prices import MarketDataError, fetch_market, validate_market


def _prices(value: float = 100.0) -> dict[str, float]:
    return {ticker: value for ticker in TICKER_ORDER}


class _Series:
    def __init__(self, points):
        self._points = points

    def items(self):
        return iter(self._points)


class _Frame:
    def __init__(self, points_by_symbol):
        self._close = {
            symbol: _Series(points) for symbol, points in points_by_symbol.items()
        }

    def __getitem__(self, field):
        if field != "Close":
            raise KeyError(field)
        return self._close


def _frame(**overrides) -> _Frame:
    points = {
        ticker: [
            (date(2026, 4, 3), 100.0),
            (date(2026, 4, 1), 90.0),
            (date(2026, 4, 2), math.nan),
        ]
        for ticker in TICKER_ORDER
    }
    points["CNY=X"] = [
        (date(2026, 4, 2), 7.1),
        (date(2026, 4, 3), 7.2),
    ]
    points.update(overrides)
    return _Frame(points)


class TestValidateMarket:
    def test_requires_all_fixed_prices_and_positive_fx(self):
        prices, usdcny = validate_market(_prices(), 7.25)
        assert tuple(prices) == TICKER_ORDER
        assert usdcny == 7.25

    def test_identical_prices_are_valid(self):
        prices, _ = validate_market(_prices(123.45), 12.0)
        assert set(prices.values()) == {123.45}

    def test_missing_price_is_fatal(self):
        prices = _prices()
        del prices["AVUV"]
        with pytest.raises(MarketDataError, match="missing current prices: AVUV"):
            validate_market(prices, 7.25)

    @pytest.mark.parametrize(
        "value",
        [True, "100", 0, -1, float("nan"), float("inf")],
    )
    def test_invalid_price_is_fatal(self, value):
        prices = _prices()
        prices["SPYM"] = value
        with pytest.raises(MarketDataError, match="price for SPYM"):
            validate_market(prices, 7.25)

    @pytest.mark.parametrize(
        "value",
        [None, True, "7.25", 0, -1, float("nan"), float("inf")],
    )
    def test_invalid_fx_is_fatal(self, value):
        with pytest.raises(MarketDataError, match="USD/CNY"):
            validate_market(_prices(), value)


class TestPriceFile:
    def test_file_precedes_network_and_loads_optional_history(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "market.json"
        path.write_text(
            json.dumps(
                {
                    "prices": _prices(),
                    "usdcny": 7.25,
                    "history": {
                        "SPYM": {
                            "2026-04-03": 100.0,
                            "2026-04-01": 98.0,
                            "2026-04-02": 99.0,
                        },
                        "VGIT": {"2026-04-01": 80.0, "2026-04-02": 81.0},
                    },
                    "usdcny_history": {
                        "2026-04-02": 7.2,
                        "2026-04-01": 7.1,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PP_PRICE_FILE", str(path))
        download = MagicMock()

        snapshot = fetch_market(download_fn=download, with_history=True)

        download.assert_not_called()
        assert snapshot.prices == _prices()
        assert snapshot.usdcny == 7.25
        assert snapshot.history["SPYM"] == {
            date(2026, 4, 1): 98.0,
            date(2026, 4, 2): 99.0,
            date(2026, 4, 3): 100.0,
        }
        assert snapshot.usdcny_history == {
            date(2026, 4, 1): 7.1,
            date(2026, 4, 2): 7.2,
        }

    def test_missing_history_is_an_empty_mapping(self, tmp_path, monkeypatch):
        path = tmp_path / "market.json"
        path.write_text(
            json.dumps({"prices": _prices(), "usdcny": 7.25}),
            encoding="utf-8",
        )
        monkeypatch.setenv("PP_PRICE_FILE", str(path))

        snapshot = fetch_market(with_history=True)

        assert snapshot.history == {}
        assert snapshot.usdcny_history == {}

    def test_default_fetch_ignores_optional_history(self, tmp_path, monkeypatch):
        path = tmp_path / "market.json"
        path.write_text(
            json.dumps(
                {
                    "prices": _prices(),
                    "usdcny": 7.25,
                    "history": {"SPYM": {"2026-04-01": 98.0}},
                    "usdcny_history": {"2026-04-01": 7.1},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PP_PRICE_FILE", str(path))

        snapshot = fetch_market()

        assert snapshot.history == {}
        assert snapshot.usdcny_history == {}

    def test_invalid_history_is_ignored_without_weakening_current_data(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "market.json"
        path.write_text(
            json.dumps(
                {
                    "prices": _prices(),
                    "usdcny": 7.25,
                    "history": {
                        "SPYM": {"2026-04-01": 100.0},
                        "VGIT": {"2026-04-01": 0.0},
                    },
                    "usdcny_history": {"not-a-date": 7.1},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PP_PRICE_FILE", str(path))

        snapshot = fetch_market(with_history=True)

        assert snapshot.prices["SPYM"] == 100.0
        assert snapshot.history == {"SPYM": {date(2026, 4, 1): 100.0}}
        assert snapshot.usdcny_history == {}

    def test_undated_array_history_is_not_supported(self, tmp_path, monkeypatch):
        path = tmp_path / "market.json"
        path.write_text(
            json.dumps(
                {
                    "prices": _prices(),
                    "usdcny": 7.25,
                    "history": {"SPYM": [98.0, 99.0, 100.0]},
                    "usdcny_history": [7.1, 7.2],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PP_PRICE_FILE", str(path))

        snapshot = fetch_market(with_history=True)

        assert snapshot.history == {}
        assert snapshot.usdcny_history == {}

    @pytest.mark.parametrize(
        "payload,error",
        [
            ("not json", "not valid JSON"),
            (json.dumps([]), "root must be an object"),
            (json.dumps({"prices": {}, "usdcny": 7.25}), "missing current prices"),
            (json.dumps({"prices": _prices(), "usdcny": 0}), "USD/CNY"),
        ],
    )
    def test_invalid_file_is_fatal(self, payload, error, tmp_path, monkeypatch):
        path = tmp_path / "market.json"
        path.write_text(payload, encoding="utf-8")
        monkeypatch.setenv("PP_PRICE_FILE", str(path))

        with pytest.raises(MarketDataError, match=error):
            fetch_market()

    def test_missing_file_is_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PP_PRICE_FILE", str(tmp_path / "missing.json"))
        with pytest.raises(MarketDataError, match="does not exist"):
            fetch_market()


class TestYFinanceMarket:
    def test_default_download_uses_short_period_and_returns_no_history(
        self, monkeypatch
    ):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)
        download = MagicMock(return_value=_frame())

        snapshot = fetch_market(download_fn=download)

        symbols = download.call_args.args[0]
        assert symbols == [*TICKER_ORDER, "CNY=X"]
        assert download.call_args.kwargs["period"] == "5d"
        assert download.call_args.kwargs["interval"] == "1d"
        assert snapshot.prices == _prices()
        assert snapshot.usdcny == 7.2
        assert snapshot.history == {}
        assert snapshot.usdcny_history == {}

    def test_history_download_preserves_dates_and_sorts_points(self, monkeypatch):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)
        download = MagicMock(return_value=_frame())

        snapshot = fetch_market(download_fn=download, with_history=True)

        assert download.call_args.kwargs["period"] == "60d"
        assert snapshot.prices == _prices()
        assert snapshot.usdcny == 7.2
        assert snapshot.history["SPYM"] == {
            date(2026, 4, 1): 90.0,
            date(2026, 4, 3): 100.0,
        }
        assert set(snapshot.history) == set(TICKER_ORDER)
        assert snapshot.usdcny_history == {
            date(2026, 4, 2): 7.1,
            date(2026, 4, 3): 7.2,
        }

    def test_missing_ticker_is_fatal(self, monkeypatch):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)
        frame = _frame()
        del frame._close["AVUV"]

        with pytest.raises(MarketDataError, match="AVUV"):
            fetch_market(download_fn=MagicMock(return_value=frame))

    def test_missing_fx_never_uses_a_fallback(self, monkeypatch):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)
        frame = _frame()
        del frame._close["CNY=X"]

        with pytest.raises(MarketDataError, match="CNY=X"):
            fetch_market(download_fn=MagicMock(return_value=frame))

    def test_invalid_latest_current_value_is_fatal(self, monkeypatch):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)
        with pytest.raises(MarketDataError, match="price for SPYM"):
            fetch_market(
                download_fn=MagicMock(
                    return_value=_frame(
                        SPYM=[
                            (date(2026, 4, 1), 90.0),
                            (date(2026, 4, 3), -1.0),
                        ]
                    )
                )
            )

    def test_download_with_an_undated_point_is_fatal(self, monkeypatch):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)

        with pytest.raises(MarketDataError, match="invalid date for SPYM"):
            fetch_market(
                download_fn=MagicMock(
                    return_value=_frame(
                        SPYM=[
                            (0, 90.0),
                            (date(2026, 4, 3), 100.0),
                        ]
                    )
                )
            )

    def test_download_failure_is_explicit(self, monkeypatch):
        monkeypatch.delenv("PP_PRICE_FILE", raising=False)

        def fail(*_args, **_kwargs):
            raise ConnectionError("network unavailable")

        with pytest.raises(MarketDataError, match="network unavailable"):
            fetch_market(download_fn=fail)
