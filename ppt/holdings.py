"""Holdings I/O (§2) — OSS-only storage, undo, transaction history."""

import logging
import math
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ppt.constants import (
    BUCKETS,
    OSS_BACKUP_PATH,
    OSS_HOLDINGS_PATH,
    OSS_PRICE_HISTORY_PATH,
    TICKER_CURRENCY,
    TICKER_LOT_SIZE,
    TICKER_WHITELIST,
)
from ppt.storage import IStorageBackend, OssBackend

logger = logging.getLogger(__name__)


def is_nan(val) -> bool:
    """Check whether a value is float NaN."""
    return isinstance(val, float) and math.isnan(val)


# ── Validation ────────────────────────────────────────────────────────────────


def validate_transaction_input(
    ticker: str,
    shares: float,
    price: float,
) -> list[str]:
    """Validate user input for buy/sell command (§5). Returns list of errors."""
    errors = []

    if ticker not in TICKER_WHITELIST:
        errors.append(f"Unknown ticker: {ticker}")
        return errors

    shares_is_number = isinstance(shares, (int, float)) and not isinstance(shares, bool)
    price_is_number = isinstance(price, (int, float)) and not isinstance(price, bool)
    if not shares_is_number:
        errors.append(f"Shares must be numeric: {shares}")
    elif not math.isfinite(shares):
        errors.append(f"Shares must be finite: {shares}")
    elif shares <= 0:
        errors.append(f"Shares must be positive: {shares}")
    if not price_is_number:
        errors.append(f"Price must be numeric: {price}")
    elif not math.isfinite(price):
        errors.append(f"Price must be finite: {price}")
    elif price <= 0:
        errors.append(f"Price must be positive: {price}")

    lot = TICKER_LOT_SIZE[ticker]
    if shares_is_number and math.isfinite(shares) and shares != int(shares):
        errors.append(f"Shares must be integer: {shares}")
    elif shares_is_number and math.isfinite(shares) and int(shares) % lot != 0:
        if lot == 100:
            errors.append(f"A-share shares must be multiple of 100: {shares}")
        else:
            errors.append(f"USD shares must be whole shares: {shares}")

    return errors


def validate_holdings(data: dict) -> bool:
    """Validate holdings JSON structure."""
    if not isinstance(data, dict):
        return False
    required = {"holdings", "cash_in", "cash_out", "transactions", "created_at"}
    if not all(k in data for k in required):
        return False
    if not isinstance(data["holdings"], dict):
        return False
    cash_values = (data["cash_in"], data["cash_out"])
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in cash_values
    ):
        return False
    if any(
        isinstance(shares, bool)
        or not isinstance(shares, (int, float))
        or not math.isfinite(shares)
        or shares < 0
        for shares in data["holdings"].values()
    ):
        return False
    if not isinstance(data["transactions"], list):
        return False
    if not isinstance(data["created_at"], str):
        return False
    return True


# ── Transaction ───────────────────────────────────────────────────────────────


@dataclass
class Transaction:
    """A single buy/sell transaction (§2.1)."""

    txn_id: str
    date: str
    txn_type: str  # "buy" | "sell"
    trades: list[dict[str, Any]]
    usdcny: float
    internal: bool = False

    def to_dict(self) -> dict:
        amount_cny = 0.0
        for trade in self.trades:
            trade_amount = trade["shares"] * trade["price"]
            if trade["currency"] == "USD":
                trade_amount *= self.usdcny
            amount_cny += trade_amount
        return {
            "id": self.txn_id,
            "date": self.date,
            "type": self.txn_type,
            "trades": self.trades,
            "usdcny": self.usdcny,
            "amount_cny": round(amount_cny, 2),
            "internal": self.internal,
        }


# ── Holdings Store ────────────────────────────────────────────────────────────


class HoldingsStore:
    """OSS-only holdings state manager. No local data files (§2.1)."""

    def __init__(
        self,
        local_dir: Path | None = None,
        backend: IStorageBackend | None = None,
    ):
        # local_dir kept for config/logs only (no data files)
        self.local_dir = local_dir or Path.home() / ".pp"
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.backend = backend or OssBackend()

    # ── OSS core I/O — delegated to backend ────────────────────────────────

    def _oss_backup(self) -> None:
        """Create backup of holdings on OSS."""
        if isinstance(self.backend, OssBackend) and not self.backend.copy(
            OSS_HOLDINGS_PATH, OSS_BACKUP_PATH
        ):
            raise RuntimeError("Failed to back up holdings on OSS")

    def _mutation_lock(self):
        if isinstance(self.backend, OssBackend):
            return self.backend.lock(OSS_HOLDINGS_PATH)
        return nullcontext()

    # ── Holdings (public API) ─────────────────────────────────────────────

    def load(self) -> dict | None:
        """Load holdings from OSS."""
        data = self.backend.read(OSS_HOLDINGS_PATH)
        if data and validate_holdings(data):
            return data
        if data:
            logger.error("Invalid holdings data on OSS")
        return None

    def _save_unlocked(self, data: dict, *, backup: bool = True) -> None:
        if not validate_holdings(data):
            raise ValueError("Refusing to save invalid holdings state")
        if backup:
            self._oss_backup()
        if not self.backend.write(OSS_HOLDINGS_PATH, data):
            raise RuntimeError("Failed to save holdings to OSS")

    def save(self, data: dict, *, backup: bool = True) -> None:
        """Save holdings to OSS with backup."""
        with self._mutation_lock():
            self._save_unlocked(data, backup=backup)

    # ── Transactions ─────────────────────────────────────────────────────

    def add_transaction(self, txn: Transaction) -> None:
        """Add a transaction and update holdings."""
        self.add_transactions([txn])

    def add_transactions(self, transactions: list[Transaction]) -> None:
        """Validate and persist one atomic transaction batch."""
        if not transactions:
            return
        with self._mutation_lock():
            state = self.load()
            if state is None:
                raise RuntimeError("No holdings state. Run init first.")
            state = deepcopy(state)

            for txn in transactions:
                if txn.txn_type not in {"buy", "sell"}:
                    raise ValueError(f"Invalid transaction type: {txn.txn_type}")
                if (
                    isinstance(txn.usdcny, bool)
                    or not isinstance(txn.usdcny, (int, float))
                    or not math.isfinite(txn.usdcny)
                    or txn.usdcny <= 0
                ):
                    raise ValueError(f"Invalid USD/CNY rate: {txn.usdcny}")
                if not txn.trades:
                    raise ValueError("Transaction must contain at least one trade")

                for trade in txn.trades:
                    ticker = trade.get("ticker")
                    shares = trade.get("shares")
                    price = trade.get("price")
                    if not isinstance(shares, (int, float)) or not isinstance(price, (int, float)):
                        raise ValueError("Trade shares and price must be numeric")
                    errors = validate_transaction_input(ticker, shares, price)
                    if errors:
                        raise ValueError("; ".join(errors))
                    if trade.get("currency") != TICKER_CURRENCY[ticker]:
                        raise ValueError(f"Invalid currency for {ticker}: {trade.get('currency')}")

                    current = state["holdings"].get(ticker, 0.0)
                    if txn.txn_type == "sell" and shares > current:
                        raise ValueError(
                            f"Insufficient holdings for {ticker}: need {shares}, have {current}"
                        )
                    delta = shares if txn.txn_type == "buy" else -shares
                    state["holdings"][ticker] = current + delta

                record = txn.to_dict()
                if not txn.internal:
                    if txn.txn_type == "buy":
                        state["cash_in"] += record["amount_cny"]
                    else:
                        state["cash_out"] += record["amount_cny"]
                state["transactions"].append(record)

            self._save_unlocked(state)

    def undo_last(self) -> dict | None:
        """Undo the most recent transaction. Returns the removed transaction or None."""
        with self._mutation_lock():
            state = self.load()
            if state is None or not state["transactions"]:
                logger.info("Nothing to undo")
                return None
            state = deepcopy(state)

            last = state["transactions"].pop()
            for trade in last["trades"]:
                ticker = trade["ticker"]
                delta = -trade["shares"] if last["type"] == "buy" else trade["shares"]
                state["holdings"][ticker] = state["holdings"].get(ticker, 0.0) + delta
                if state["holdings"][ticker] < 0:
                    logger.warning(
                        "Undo clamped %s from %s to 0",
                        ticker,
                        state["holdings"][ticker],
                    )
                    state["holdings"][ticker] = 0.0

            if not last.get("internal", False):
                cash_key = "cash_in" if last["type"] == "buy" else "cash_out"
                state[cash_key] = max(0.0, state[cash_key] - last["amount_cny"])

            self._save_unlocked(state)
            return last

    # ── Price history ────────────────────────────────────────────────────

    def load_price_history(self) -> list[dict]:
        """Load bucket price history from OSS."""
        return self.backend.read_list(OSS_PRICE_HISTORY_PATH)

    def _save_price_history(self, history: list[dict]) -> None:
        """Save price history to OSS."""
        self.backend.write(OSS_PRICE_HISTORY_PATH, history)

    def clean_price_history(self) -> int:
        """Remove entries where any bucket price is NaN. Returns count removed.

        Mirrors the NaN guard in cli.py status() — an entry is removed if
        *any* of its stock/bond/gold/cash values is NaN.
        """
        history = self.load_price_history()

        def _entry_has_nan(entry: dict) -> bool:
            prices = entry.get("prices_cny", {})
            return any(is_nan(prices.get(b, 0)) for b in BUCKETS)

        cleaned = [e for e in history if not _entry_has_nan(e)]
        removed = len(history) - len(cleaned)
        if removed > 0:
            self._save_price_history(cleaned)
        return removed

    def update_price_history(self, entry: dict) -> None:
        """Append/update today's bucket price entry. Trim to max 120.

        If history ≤ 30 entries after update, attempt to backfill ~60 days (§2.2).
        """
        history = self.load_price_history()
        today = entry["date"]

        # Overwrite if same date exists
        updated = False
        for i, h in enumerate(history):
            if h["date"] == today:
                history[i] = entry
                updated = True
                break
        if not updated:
            history.append(entry)

        # Sort by date ascending
        history.sort(key=lambda x: x["date"])

        # Trim to max 120
        if len(history) > 120:
            history = history[-120:]

        # Backfill if ≤ 30 entries (§2.2)
        if len(history) <= 30:
            history = self._backfill_history(history)

        self._save_price_history(history)

    def _backfill_history(self, history: list[dict]) -> list[dict]:
        """Backfill ~3 months (~60 trading days) of bucket prices via yfinance."""
        try:
            import yfinance as yf

            from ppt.constants import BUCKETS as _BUCKETS
            from ppt.constants import CNY_TICKERS, PRIMARY_TICKER

            # Determine earliest date needed
            if history:
                earliest = history[0]["date"]
            else:
                from datetime import datetime

                earliest = datetime.now().strftime("%Y-%m-%d")

            # Fetch 3 months of daily data for primary tickers + USDCNY
            tickers_set = set()
            for b in _BUCKETS:
                t = PRIMARY_TICKER[b]
                tickers_set.add(t)
            tickers_set.add("CNY=X")  # USDCNY rate

            tickers_list = sorted(tickers_set)
            df = yf.download(tickers_list, period="3mo", progress=False)
            if df.empty:
                return history

            close = df["Close"]
            existing_dates = {h["date"] for h in history}

            # Use iterrows() for cleaner row iteration (avoid repeated iloc lookups)
            for row_date_idx, row_data in close.iterrows():
                row_date = (
                    str(row_date_idx.date())
                    if hasattr(row_date_idx, "date")
                    else str(row_date_idx)[:10]
                )
                if row_date >= earliest or row_date in existing_dates:
                    continue

                # Extract USDCNY rate for this row
                usdcny_row = 7.25
                try:
                    usdcny_row = float(row_data.get("CNY=X", 7.25))
                except (TypeError, ValueError):
                    pass

                entry_cny = {}
                for b in _BUCKETS:
                    t = PRIMARY_TICKER[b]
                    try:
                        val = float(row_data[t])
                    except (KeyError, TypeError):
                        val = None
                    if val is not None:
                        # Convert USD tickers to CNY
                        if t not in CNY_TICKERS:
                            val = val * usdcny_row
                        entry_cny[b] = val

                if len(entry_cny) == len(_BUCKETS):
                    history.append(
                        {
                            "date": row_date,
                            "prices_cny": entry_cny,
                        }
                    )
                    existing_dates.add(row_date)

            history.sort(key=lambda x: x["date"])
            logger.info("Backfilled %d price history entries", len(history))
        except Exception as e:
            logger.debug("Price history backfill skipped: %s", e)

        return history
