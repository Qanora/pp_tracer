"""Price fetching, caching, and validation (§3).

IO layer — handles yfinance, local JSON cache, price validation.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ppt.constants import YFINANCE_TICKERS

logger = logging.getLogger(__name__)


# ── Price Validator ───────────────────────────────────────────────────────────


class PriceValidator:
    """Validate fetched prices against README §3 constraints."""

    USDCNY_MIN = 5.0
    USDCNY_MAX = 10.0

    @classmethod
    def validate(
        cls,
        prices: Dict[str, float],
        usdcny: float,
    ) -> List[str]:
        """Return list of validation errors (empty = all good)."""
        errors: List[str] = []

        # Check usdcny range (warning, not blocking)
        if not (cls.USDCNY_MIN <= usdcny <= cls.USDCNY_MAX):
            errors.append(
                f"[PRICE] usdcny={usdcny} outside [{cls.USDCNY_MIN}, {cls.USDCNY_MAX}]"
            )

        # Check all prices > 0 (blocking error)
        for ticker, price in prices.items():
            if ticker == "CNY=X":
                continue
            if price <= 0:
                errors.append(f"[PRICE] {ticker} price={price} ≤ 0")

        # Check unique price count — anti-placeholder detection
        non_fx_prices = [p for t, p in prices.items() if t != "CNY=X"]
        if non_fx_prices:
            unique_count = len(set(non_fx_prices))
            total_count = len(non_fx_prices)
            if unique_count <= total_count / 3:
                errors.append(
                    f"[PRICE] only {unique_count} unique prices out of {total_count} "
                    f"— possible yfinance placeholder data"
                )

        return errors


# ── Price Cache ───────────────────────────────────────────────────────────────


def price_cache_key() -> str:
    """Cache key for local price cache."""
    return "prices"


class PriceCache:
    """Local JSON file cache for prices (§3)."""

    def __init__(self, path: Path, ttl: int = 300):
        self.path = path
        self.ttl = ttl

    def is_fresh(self) -> bool:
        """Check if cache exists and is within TTL."""
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ts_str = data.get("timestamp", "")
            if not ts_str:
                return False
            cached_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            age = (datetime.now() - cached_time).total_seconds()
            return age < self.ttl
        except (json.JSONDecodeError, ValueError, KeyError):
            return False

    def load(self) -> Optional[dict]:
        """Load cached data. Returns None if cache is missing/corrupt."""
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, data: dict) -> None:
        """Write data to cache file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Price Fetcher ─────────────────────────────────────────────────────────────


class PriceFetcher:
    """Fetch prices via yfinance with caching and retry logic."""

    MAX_RETRY = 3
    RETRY_WAIT = 2

    def __init__(self, cache: PriceCache):
        self.cache = cache

    def fetch(
        self,
        download_fn: Optional[Callable] = None,
        force: bool = False,
        offline: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Fetch current prices for all tickers.

        Args:
            download_fn: yfinance-compatible download function (injectable for testing).
            force: Ignore cache (--fresh).
            offline: Only use cache (--offline).

        Returns: {timestamp, prices: {ticker: price}, usdcny: float}
        """
        # Try cache first
        if not force and self.cache.is_fresh():
            logger.debug("Using cached prices")
            return self.cache.load()

        if offline:
            if self.cache.is_fresh():
                return self.cache.load()
            cached = self.cache.load()
            if cached:
                logger.warning("Cache expired but --offline: using stale data")
                return cached
            raise RuntimeError("--offline mode: no valid cache available")

        # Fetch from yfinance
        prices, usdcny = self._download_with_retry(download_fn)

        if prices is None:
            # Total failure → try cache as fallback
            cached = self.cache.load()
            if cached:
                logger.warning("Fetch failed, using stale cache")
                return cached
            return None

        # Validate
        errors = PriceValidator.validate(prices, usdcny)
        blocking = []
        for e in errors:
            if "≤ 0" in e:
                blocking.append(e)
            else:
                logger.warning(e)
        if blocking:
            raise RuntimeError(
                "价格校验失败: " + "; ".join(blocking)
            )

        # Save to cache
        data = {
            "prices": prices,
            "usdcny": usdcny,
        }
        self.cache.save(data)
        return data

    def _download_with_retry(
        self,
        download_fn: Optional[Callable] = None,
    ) -> Tuple[Optional[Dict[str, float]], Optional[float]]:
        """Download prices with individual fallback and retry."""
        tickers = list(YFINANCE_TICKERS)

        # Batch download
        if download_fn is not None:
            try:
                df = download_fn(tickers)
                prices, usdcny = self._parse_dataframe(df)
                return prices, usdcny
            except Exception as e:
                logger.warning(f"Batch download failed: {e}")

        # Fallback: individual ticker download
        all_prices: Dict[str, float] = {}
        for ticker in tickers:
            if download_fn is None:
                continue
            for attempt in range(self.MAX_RETRY):
                try:
                    df = download_fn([ticker])
                    parsed, _ = self._parse_dataframe(df, single_ticker=ticker)
                    if parsed:
                        all_prices.update(parsed)
                    break
                except Exception as e:
                    if attempt < self.MAX_RETRY - 1:
                        time.sleep(self.RETRY_WAIT)
                    else:
                        logger.error(
                            f"Failed to fetch {ticker} after {self.MAX_RETRY} attempts: {e}"
                        )

        # Try real yfinance if no custom download_fn
        if download_fn is None:
            try:
                prices, usdcny = self._fetch_yfinance(tickers)
                return prices, usdcny
            except Exception as e:
                logger.error(f"yfinance fetch failed: {e}")
                return (None, None)

        if not all_prices:
            return (None, None)

        usdcny = all_prices.pop("CNY=X", 7.25)
        return (all_prices, usdcny)

    def _fetch_yfinance(self, tickers: List[str]) -> Tuple[Dict[str, float], float]:
        """Real yfinance download."""
        import yfinance as yf

        df = yf.download(tickers, period="5d", progress=False)
        return self._parse_dataframe(df)

    def _parse_dataframe(
        self,
        df,
        single_ticker: Optional[str] = None,
    ) -> Tuple[Dict[str, float], float]:
        """Extract close prices and USDCNY from yfinance DataFrame."""
        prices: Dict[str, float] = {}
        usdcny = 7.25

        try:
            close = df["Close"]
            # Multi-level columns: ('Close', 'SPYM')
            if hasattr(close, "columns"):
                for col in close.columns:
                    col_name = col[1] if isinstance(col, tuple) else col
                    series = close[col] if hasattr(close[col], "iloc") else close[col]
                    # Use last non-NaN value (yfinance may return NaN for today)
                    val = float(series.dropna().iloc[-1]) if hasattr(series, "dropna") else float(series)
                    if col_name == "CNY=X":
                        usdcny = val
                    else:
                        prices[col_name] = val
            elif single_ticker:
                s = close.dropna() if hasattr(close, "dropna") else close
                val = float(s.iloc[-1])
                if single_ticker == "CNY=X":
                    usdcny = val
                else:
                    prices[single_ticker] = val
        except (KeyError, IndexError, TypeError) as e:
            logger.debug(f"DataFrame parse warning: {e}")
            # Try fallback: use Adj Close or any available column
            try:
                if "Adj Close" in df:
                    close = df["Adj Close"]
                    if hasattr(close, "columns"):
                        for col in close.columns:
                            col_name = col[1] if isinstance(col, tuple) else col
                            series = close[col] if hasattr(close[col], "iloc") else close[col]
                            val = float(series.dropna().iloc[-1]) if hasattr(series, "dropna") else float(series)
                            if col_name == "CNY=X":
                                usdcny = val
                            else:
                                prices[col_name] = val
            except Exception as e:
                logger.warning(
                    f"Adj Close fallback parse failed for ticker={single_ticker}: {type(e).__name__}: {e}"
                )

        return (prices, usdcny)


# ── High-level API ────────────────────────────────────────────────────────────


def fetch_prices(
    cache_dir: Optional[Path] = None,
    force: bool = False,
    offline: bool = False,
) -> Optional[Dict[str, Any]]:
    """Convenience: fetch prices with local cache in ~/.pp/."""
    if cache_dir is None:
        cache_dir = Path.home() / ".pp"
    cache_path = cache_dir / "price_cache.json"
    cache = PriceCache(path=cache_path, ttl=300)
    fetcher = PriceFetcher(cache=cache)
    return fetcher.fetch(force=force, offline=offline)
