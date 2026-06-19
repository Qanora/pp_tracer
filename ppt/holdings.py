"""Holdings I/O (§2) — OSS, local cache, undo, transaction history."""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ppt.constants import (
    OSS_BACKUP_PATH,
    OSS_HOLDINGS_PATH,
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
    """Local holdings state manager with OSS sync capability."""

    HOLDINGS_FILE = "pp_holdings.json"
    BACKUP_FILE = "pp_holdings.backup.json"
    HISTORY_FILE = "price_history.json"

    def __init__(self, local_dir: Optional[Path] = None):
        self.local_dir = local_dir or Path.home() / ".pp"
        self.local_dir.mkdir(parents=True, exist_ok=True)

    @property
    def holdings_path(self) -> Path:
        return self.local_dir / self.HOLDINGS_FILE

    @property
    def backup_path(self) -> Path:
        return self.local_dir / self.BACKUP_FILE

    @property
    def history_path(self) -> Path:
        return self.local_dir / self.HISTORY_FILE

    # ── Local I/O ─────────────────────────────────────────────────────────

    def load_local(self) -> Optional[dict]:
        """Load holdings from local file."""
        if not self.holdings_path.exists():
            return None
        try:
            data = json.loads(self.holdings_path.read_text(encoding="utf-8"))
            if validate_holdings(data):
                return data
            logger.error("Invalid holdings data")
            return None
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load holdings: {e}")
            return None

    def save_local(self, data: dict) -> None:
        """Save holdings locally with backup."""
        # Backup existing
        if self.holdings_path.exists():
            shutil.copy2(self.holdings_path, self.backup_path)
        self.holdings_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── OSS I/O ──────────────────────────────────────────────────────────

    def pull_from_oss(self) -> Optional[dict]:
        """Download holdings from OSS via ossutil."""
        import subprocess

        ossutil = self._ossutil_path()
        try:
            result = subprocess.run(
                [ossutil, "cp", OSS_HOLDINGS_PATH, str(self.holdings_path), "-f"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"ossutil pull failed: {result.stderr}")
                return None
            return self.load_local()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"OSS pull error: {e}")
            return None

    def push_to_oss(self) -> bool:
        """Upload holdings to OSS via ossutil."""
        import subprocess

        ossutil = self._ossutil_path()
        # Backup on OSS first
        self._oss_backup()
        try:
            result = subprocess.run(
                [ossutil, "cp", str(self.holdings_path), OSS_HOLDINGS_PATH, "-f"],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"OSS push error: {e}")
            return False

    def _oss_backup(self) -> None:
        """Create backup on OSS."""
        import subprocess

        ossutil = self._ossutil_path()
        subprocess.run(
            [ossutil, "cp", OSS_HOLDINGS_PATH, OSS_BACKUP_PATH, "-f"],
            capture_output=True, timeout=30,
        )

    def _ossutil_path(self) -> str:
        import os
        return os.environ.get("OSSUTIL_PATH", "ossutil")

    # ── Transactions ─────────────────────────────────────────────────────

    def add_transaction(self, txn: Transaction) -> None:
        """Add a transaction and update holdings."""
        state = self.load_local()
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
        self.save_local(state)

    def undo_last(self) -> Optional[dict]:
        """Undo the most recent transaction. Returns the removed transaction or None."""
        state = self.load_local()
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
                    f"Undo clamped {ticker} from {state['holdings'][ticker]} to 0"
                )
                state["holdings"][ticker] = 0.0

        if reverse_type == "sell":
            state["cash_out"] += last["amount_cny"]
        else:
            state["cash_in"] -= last["amount_cny"]

        self.save_local(state)
        return last

    # ── Price history ────────────────────────────────────────────────────

    def load_price_history(self) -> List[dict]:
        """Load bucket price history."""
        if not self.history_path.exists():
            return []
        try:
            return json.loads(self.history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def update_price_history(self, entry: dict) -> None:
        """Append/update today's bucket price entry. Trim to max 120."""
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

        self.history_path.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
