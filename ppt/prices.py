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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
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
    history: dict[str, dict[date, float]]
    usdcny_history: dict[date, float]


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


def _iso_date(value: object) -> date:
    if not isinstance(value, str):
        raise MarketDataError("history dates must be ISO date strings")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MarketDataError("history dates must be ISO date strings") from exc
    if value != parsed.isoformat():
        raise MarketDataError("history dates must be ISO date strings")
    return parsed


def _normalize_dated_history(
    raw_history: object,
    *,
    label: str,
) -> dict[date, float]:
    if not isinstance(raw_history, Mapping) or not raw_history:
        raise MarketDataError(f"{label} must be a non-empty object")

    dated = {
        _iso_date(raw_date): _positive_number(value, label)
        for raw_date, value in raw_history.items()
    }
    return dict(sorted(dated.items()))


def _normalize_optional_history(raw_history: object) -> dict[str, dict[date, float]]:
    """Normalize each optional ticker history independently."""

    if not isinstance(raw_history, Mapping):
        return {}

    history: dict[str, dict[date, float]] = {}
    for ticker in TICKER_ORDER:
        if ticker not in raw_history:
            continue
        try:
            history[ticker] = _normalize_dated_history(
                raw_history[ticker],
                label=f"history price for {ticker}",
            )
        except MarketDataError:
            continue
    return history


def _normalize_optional_usdcny_history(raw_history: object) -> dict[date, float]:
    """Normalize optional dated FX history, or ignore the whole block."""

    try:
        return _normalize_dated_history(raw_history, label="historical USD/CNY")
    except MarketDataError:
        return {}


def _read_price_file(path: Path, *, with_history: bool) -> MarketSnapshot:
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
        history=(
            _normalize_optional_history(raw.get("history")) if with_history else {}
        ),
        usdcny_history=(
            _normalize_optional_usdcny_history(raw.get("usdcny_history"))
            if with_history
            else {}
        ),
    )


def _index_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _series_points(close: object, ticker: str) -> list[tuple[date, float]]:
    try:
        series = close[ticker]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise MarketDataError(f"download is missing Close data for {ticker}") from exc

    if not hasattr(series, "items"):
        raise MarketDataError(f"download Close data is not a dated series for {ticker}")
    raw_points = list(series.items())

    points: list[tuple[date, float]] = []
    for raw_date, raw_value in raw_points:
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        day = _index_date(raw_date)
        if day is None:
            raise MarketDataError(f"download has invalid date for {ticker}: {raw_date}")
        points.append((day, value))
    if not points:
        raise MarketDataError(f"download has no current Close for {ticker}")
    points.sort(key=lambda point: point[0])
    return points


def _snapshot_from_dataframe(frame: object, *, with_history: bool) -> MarketSnapshot:
    try:
        close = frame["Close"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise MarketDataError("download is missing the Close field") from exc

    points_by_symbol = {
        symbol: _series_points(close, symbol)
        for symbol in (*TICKER_ORDER, FX_TICKER)
    }
    prices, usdcny = validate_market(
        {ticker: points_by_symbol[ticker][-1][1] for ticker in TICKER_ORDER},
        points_by_symbol[FX_TICKER][-1][1],
    )

    history: dict[str, dict[date, float]] = {}
    usdcny_history: dict[date, float] = {}
    if with_history:
        for ticker in TICKER_ORDER:
            usable = {
                raw_date: value
                for raw_date, value in points_by_symbol[ticker]
                if math.isfinite(value) and value > 0
            }
            if usable:
                history[ticker] = dict(sorted(usable.items()))
        usdcny_history = dict(
            sorted(
                (raw_date, value)
                for raw_date, value in points_by_symbol[FX_TICKER]
                if math.isfinite(value) and value > 0
            )
        )

    return MarketSnapshot(
        prices=prices,
        usdcny=usdcny,
        history=history,
        usdcny_history=usdcny_history,
    )


def _yfinance_download(symbols: list[str], **kwargs: Any) -> object:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - installation failure
        raise MarketDataError("yfinance is not installed") from exc
    return yf.download(symbols, **kwargs)


def fetch_market(
    download_fn: Callable[..., object] | None = None,
    *,
    with_history: bool = False,
) -> MarketSnapshot:
    """Return a strict market snapshot or raise :class:`MarketDataError`.

    ``PP_PRICE_FILE`` always takes precedence.  The optional ``download_fn`` is
    an IO seam for tests; production callers leave it unset.
    """

    fixture_path = os.environ.get(PRICE_FILE_ENV)
    if fixture_path:
        return _read_price_file(
            Path(fixture_path).expanduser(), with_history=with_history
        )

    downloader = download_fn or _yfinance_download
    symbols = [*TICKER_ORDER, FX_TICKER]
    try:
        frame = downloader(
            symbols,
            period="60d" if with_history else "5d",
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
        return _snapshot_from_dataframe(frame, with_history=with_history)
    except MarketDataError:
        raise
    except Exception as exc:
        raise MarketDataError(f"market download failed: {exc}") from exc
