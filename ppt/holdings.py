"""Holdings I/O (§2) — OSS-only storage, undo, transaction history."""

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ppt.constants import (
    OSS_BACKUP_PATH,
    OSS_HOLDINGS_PATH,
    OSS_PRICE_HISTORY_PATH,
    TICKER_LOT_SIZE,
    TICKER_WHITELIST,
)

logger = logging.getLogger(__name__)


# ── Validation ────────────────────────────────────────────────────────────────


def validate_transaction_input(
    ticker: str,
    shares: float,
    price: float,
) -> List[str]:
    """Validate user input for buy/sell command (§5). Returns list of errors."""
    errors = []

    if ticker not in TICKER_WHITELIST:
        errors.append(f"Unknown ticker: {ticker}")
        return errors

    if shares <= 0:
        errors.append(f"Shares must be positive: {shares}")
    if price <= 0:
        errors.append(f"Price must be positive: {price}")

    lot = TICKER_LOT_SIZE[ticker]
    if shares != int(shares):
        errors.append(f"Shares must be integer: {shares}")
    elif int(shares) % lot != 0:
        if lot == 100:
            errors.append(f"A-share shares must be multiple of 100: {shares}")
        else:
            errors.append(f"USD shares must be whole shares: {shares}")

    return errors


def validate_holdings(data: dict) -> bool:
    """Validate holdings JSON structure."""
    required = {"holdings", "cash_in", "cash_out", "transactions", "created_at"}
    if not all(k in data for k in required):
        return False
    if not isinstance(data["holdings"], dict):
        return False
    if data["cash_in"] < 0 or data["cash_out"] < 0:
        return False
    if not isinstance(data["transactions"], list):
        return False
    return True


# ── Transaction ───────────────────────────────────────────────────────────────


@dataclass
class Transaction:
    """A single buy/sell transaction (§2.1)."""
    txn_id: str
    date: str
    txn_type: str  # "buy" | "sell"
    trades: List[Dict[str, Any]]
    usdcny: float

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
        }


# ── Holdings Store ────────────────────────────────────────────────────────────


class HoldingsStore:
    """OSS-only holdings state manager. No local data files (§2.1)."""

    def __init__(self, local_dir: Optional[Path] = None):
        # local_dir kept for config/logs only (no data files)
        self.local_dir = local_dir or Path.home() / ".pp"
        self.local_dir.mkdir(parents=True, exist_ok=True)

    # ── OSS core I/O ─────────────────────────────────────────────────────

    def _ossutil_path(self) -> str:
        return os.environ.get("OSSUTIL_PATH", "ossutil")

    def _oss_read(self, oss_path: str) -> Optional[dict]:
        """Read JSON from OSS via `ossutil cat`."""
        ossutil = self._ossutil_path()
        try:
            result = subprocess.run(
                [ossutil, "cat", oss_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning("ossutil cat %s failed: %s", oss_path, result.stderr.strip())
                return None
            # Strip trailing timing line: "0.069200(s) elapsed"
            raw = result.stdout.strip()
            # Find the last '}' that ends the JSON object
            last_brace = raw.rfind("}")
            if last_brace >= 0:
                raw = raw[: last_brace + 1]
            return json.loads(raw)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("OSS read error (%s): %s", oss_path, e)
            return None

    def _oss_read_list(self, oss_path: str) -> list:
        """Read JSON list from OSS via `ossutil cat`."""
        ossutil = self._ossutil_path()
        try:
            result = subprocess.run(
                [ossutil, "cat", oss_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.debug("ossutil cat %s failed: %s", oss_path, result.stderr.strip())
                return []
            raw = result.stdout.strip()
            # Strip trailing timing line
            last_bracket = raw.rfind("]")
            if last_bracket >= 0:
                raw = raw[: last_bracket + 1]
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.debug("OSS read error (%s): %s", oss_path, e)
            return []

    def _oss_write(self, oss_path: str, data) -> bool:
        """Write JSON to OSS via temp file + `ossutil cp`."""
        ossutil = self._ossutil_path()
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="ppt_")
            os.close(fd)
            Path(tmp_path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                [ossutil, "cp", tmp_path, oss_path, "-f"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error("ossutil cp to %s failed: %s", oss_path, result.stderr.strip())
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.error("OSS write error (%s): %s", oss_path, e)
            return False
        finally:
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)

    def _oss_backup(self) -> None:
        """Create backup of holdings on OSS."""
        ossutil = self._ossutil_path()
        subprocess.run(
            [ossutil, "cp", OSS_HOLDINGS_PATH, OSS_BACKUP_PATH, "-f"],
            capture_output=True, timeout=30,
        )

    # ── Holdings (public API) ─────────────────────────────────────────────

    def load(self) -> Optional[dict]:
        """Load holdings from OSS."""
        data = self._oss_read(OSS_HOLDINGS_PATH)
        if data and validate_holdings(data):
            return data
        if data:
            logger.error("Invalid holdings data on OSS")
        return None

    def save(self, data: dict) -> None:
        """Save holdings to OSS with backup."""
        # Backup existing holdings on OSS first
        self._oss_backup()
        if not self._oss_write(OSS_HOLDINGS_PATH, data):
            raise RuntimeError("Failed to save holdings to OSS")

    # ── Transactions ─────────────────────────────────────────────────────

    def add_transaction(self, txn: Transaction) -> None:
        """Add a transaction and update holdings."""
        state = self.load()
        if state is None:
            raise RuntimeError("No holdings state. Run init first.")

        for trade in txn.trades:
            ticker = trade["ticker"]
            if txn.txn_type == "buy":
                state["holdings"][ticker] = state["holdings"].get(ticker, 0.0) + trade["shares"]
            else:
                state["holdings"][ticker] = state["holdings"].get(ticker, 0.0) - trade["shares"]

        if txn.txn_type == "buy":
            state["cash_in"] += txn.to_dict()["amount_cny"]
        else:
            state["cash_out"] += txn.to_dict()["amount_cny"]

        state["transactions"].append(txn.to_dict())
        self.save(state)

    def undo_last(self) -> Optional[dict]:
        """Undo the most recent transaction. Returns the removed transaction or None."""
        state = self.load()
        if state is None or not state["transactions"]:
            logger.info("Nothing to undo")
            return None

        last = state["transactions"].pop()
        # Reverse the trade
        reverse_type = "sell" if last["type"] == "buy" else "buy"
        for trade in last["trades"]:
            ticker = trade["ticker"]
            if reverse_type == "sell":
                state["holdings"][ticker] = state["holdings"].get(ticker, 0.0) - trade["shares"]
            else:
                state["holdings"][ticker] = state["holdings"].get(ticker, 0.0) + trade["shares"]
            # Clamp negative
            if state["holdings"][ticker] < 0:
                logger.warning(
                    "Undo clamped %s from %s to 0",
                    ticker, state["holdings"][ticker],
                )
                state["holdings"][ticker] = 0.0

        if reverse_type == "sell":
            state["cash_out"] += last["amount_cny"]
        else:
            state["cash_in"] -= last["amount_cny"]

        self.save(state)
        return last

    # ── Price history ────────────────────────────────────────────────────

    def load_price_history(self) -> List[dict]:
        """Load bucket price history from OSS."""
        return self._oss_read_list(OSS_PRICE_HISTORY_PATH)

    def _save_price_history(self, history: List[dict]) -> None:
        """Save price history to OSS."""
        self._oss_write(OSS_PRICE_HISTORY_PATH, history)

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

    def _backfill_history(self, history: List[dict]) -> List[dict]:
        """Backfill ~3 months (~60 trading days) of bucket prices via yfinance."""
        try:
            import yfinance as yf

            from ppt.constants import CNY_TICKERS, PRIMARY_TICKER
            from ppt.constants import BUCKETS as _BUCKETS

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

            for idx in range(len(close)):
                row_date = str(close.index[idx].date()) if hasattr(close.index[idx], "date") else str(close.index[idx])[:10]
                if row_date >= earliest or row_date in existing_dates:
                    continue

                # Extract USDCNY rate for this row
                usdcny_row = 7.25
                try:
                    usdcny_row = float(close["CNY=X"].iloc[idx])
                except (KeyError, IndexError, TypeError):
                    pass

                entry_cny = {}
                for b in _BUCKETS:
                    t = PRIMARY_TICKER[b]
                    try:
                        val = float(close[t].iloc[idx])
                    except (KeyError, IndexError):
                        val = None
                    if val is not None:
                        # Convert USD tickers to CNY
                        if t not in CNY_TICKERS:
                            val = val * usdcny_row
                        entry_cny[b] = val

                if len(entry_cny) == len(_BUCKETS):
                    history.append({
                        "date": row_date,
                        "prices_cny": entry_cny,
                    })
                    existing_dates.add(row_date)

            history.sort(key=lambda x: x["date"])
            logger.info("Backfilled %d price history entries", len(history))
        except Exception as e:
            logger.debug("Price history backfill skipped: %s", e)

        return history
