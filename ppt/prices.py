"""Strict market-data IO.

Current prices are required for every configured ticker and for USD/CNY.  A
deterministic JSON snapshot can be supplied with ``PP_PRICE_FILE``; otherwise
the data is downloaded from yfinance.  Historical data is advisory, so an
absent or unusable history never weakens validation of the current snapshot.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any

from ppt.constants import TICKER_ORDER

FX_TICKER = "CNY=X"
PRICE_FILE_ENV = "PP_PRICE_FILE"


class MarketDataError(RuntimeError):
    """Raised when a usable current market snapshot cannot be produced."""


@dataclass(frozen=True)
class MarketSnapshot:
    """Validated current quotes and optional original-currency close history."""

    prices: dict[str, float]
    usdcny: float
    history: dict[str, tuple[float, ...]]


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise MarketDataError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise MarketDataError(f"{label} must be finite and greater than zero")
    return result


def validate_market(
    prices: object,
    usdcny: object,
) -> tuple[dict[str, float], float]:
    """Validate and normalize the complete current market snapshot.

    Extra quote keys are ignored.  Missing or invalid required values are
    fatal; in particular, USD/CNY is never synthesized from a fallback.
    """

    if not isinstance(prices, Mapping):
        raise MarketDataError("prices must be an object")

    missing = [ticker for ticker in TICKER_ORDER if ticker not in prices]
    if missing:
        raise MarketDataError("missing current prices: " + ", ".join(missing))

    normalized = {
        ticker: _positive_number(prices[ticker], f"price for {ticker}")
        for ticker in TICKER_ORDER
    }
    return normalized, _positive_number(usdcny, "USD/CNY")


def _normalize_optional_history(raw_history: object) -> dict[str, tuple[float, ...]]:
    """Keep only complete, positive ticker series from an optional fixture."""

    if not isinstance(raw_history, Mapping):
        return {}

    history: dict[str, tuple[float, ...]] = {}
    for ticker in TICKER_ORDER:
        raw_values = raw_history.get(ticker)
        if (
            not isinstance(raw_values, Sequence)
            or isinstance(raw_values, (str, bytes, bytearray))
            or not raw_values
        ):
            continue
        try:
            values = tuple(
                _positive_number(value, f"history price for {ticker}")
                for value in raw_values
            )
        except MarketDataError:
            continue
        history[ticker] = values
    return history


def _read_price_file(path: Path) -> MarketSnapshot:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MarketDataError(f"price file does not exist: {path}") from exc
    except OSError as exc:
        raise MarketDataError(f"cannot read price file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MarketDataError(f"price file is not valid JSON: {path}") from exc

    if not isinstance(raw, Mapping):
        raise MarketDataError("price file root must be an object")

    prices, usdcny = validate_market(raw.get("prices"), raw.get("usdcny"))
    return MarketSnapshot(
        prices=prices,
        usdcny=usdcny,
        history=_normalize_optional_history(raw.get("history")),
    )


def _series_values(close: object, ticker: str) -> list[float]:
    try:
        series = close[ticker]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise MarketDataError(f"download is missing Close data for {ticker}") from exc

    if hasattr(series, "tolist"):
        raw_values = series.tolist()
    elif isinstance(series, Sequence) and not isinstance(series, (str, bytes, bytearray)):
        raw_values = list(series)
    else:
        raw_values = [series]

    values: list[float] = []
    for raw_value in raw_values:
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        values.append(value)
    if not values:
        raise MarketDataError(f"download has no current Close for {ticker}")
    return values


def _snapshot_from_dataframe(frame: object) -> MarketSnapshot:
    try:
        close = frame["Close"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise MarketDataError("download is missing the Close field") from exc

    series_by_symbol = {
        symbol: _series_values(close, symbol) for symbol in (*TICKER_ORDER, FX_TICKER)
    }
    prices, usdcny = validate_market(
        {ticker: series_by_symbol[ticker][-1] for ticker in TICKER_ORDER},
        series_by_symbol[FX_TICKER][-1],
    )

    history: dict[str, tuple[float, ...]] = {}
    for ticker in TICKER_ORDER:
        usable = tuple(
            value
            for value in series_by_symbol[ticker]
            if math.isfinite(value) and value > 0
        )
        if usable:
            history[ticker] = usable

    return MarketSnapshot(prices=prices, usdcny=usdcny, history=history)


def _yfinance_download(symbols: list[str], **kwargs: Any) -> object:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - installation failure
        raise MarketDataError("yfinance is not installed") from exc
    return yf.download(symbols, **kwargs)


def fetch_market(
    download_fn: Callable[..., object] | None = None,
) -> MarketSnapshot:
    """Return a strict market snapshot or raise :class:`MarketDataError`.

    ``PP_PRICE_FILE`` always takes precedence.  The optional ``download_fn`` is
    an IO seam for tests; production callers leave it unset.
    """

    fixture_path = os.environ.get(PRICE_FILE_ENV)
    if fixture_path:
        return _read_price_file(Path(fixture_path).expanduser())

    downloader = download_fn or _yfinance_download
    symbols = [*TICKER_ORDER, FX_TICKER]
    try:
        frame = downloader(
            symbols,
            period="3mo",
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
        return _snapshot_from_dataframe(frame)
    except MarketDataError:
        raise
    except Exception as exc:
        raise MarketDataError(f"market download failed: {exc}") from exc
