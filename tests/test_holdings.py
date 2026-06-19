"""Tests for holdings I/O (§2)."""

import tempfile
import uuid
from pathlib import Path

from ppt.holdings import (
    HoldingsStore,
    Transaction,
    validate_holdings,
    validate_transaction_input,
)

# ── Validation ────────────────────────────────────────────────────────────────


class TestValidateTransactionInput:
    """Input format: TICKER#shares@price."""

    def test_valid_usd(self):
        """SPYM#10@72.50 is valid."""
        result = validate_transaction_input("SPYM", 10, 72.50)
        assert len(result) == 0

    def test_valid_a_share(self):
        """518880.SS#1000@5.50 is valid (100-lot)."""
        result = validate_transaction_input("518880.SS", 1000, 5.50)
        assert len(result) == 0

    def test_invalid_ticker(self):
        """Non-whitelist ticker → error."""
        result = validate_transaction_input("AAPL", 10, 150.0)
        assert len(result) > 0

    def test_fractional_usd_shares(self):
        """USD shares must be integer."""
        result = validate_transaction_input("SPYM", 10.5, 72.50)
        assert len(result) > 0

    def test_a_share_not_multiple_of_100(self):
        """A-share shares must be multiple of 100."""
        result = validate_transaction_input("518880.SS", 150, 5.50)
        assert len(result) > 0

    def test_zero_or_negative_shares(self):
        """Shares ≤ 0 → error."""
        result = validate_transaction_input("SPYM", 0, 72.50)
        assert len(result) > 0

    def test_zero_or_negative_price(self):
        """Price ≤ 0 → error."""
        result = validate_transaction_input("SPYM", 10, 0)
        assert len(result) > 0


class TestValidateHoldings:
    """Full holdings state validation."""

    def test_valid_state(self):
        result = validate_holdings({
            "holdings": {"SPYM": 30, "VGIT": 50},
            "cash_in": 100000.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        })
        assert result is True

    def test_missing_field(self):
        result = validate_holdings({"holdings": {}})
        assert result is False

    def test_negative_cash(self):
        result = validate_holdings({
            "holdings": {}, "cash_in": -100.0, "cash_out": 0.0,
            "transactions": [], "created_at": "2025-01-01",
        })
        assert result is False


# ── Holdings Store ────────────────────────────────────────────────────────────


class TestHoldingsStore:
    def temp_dir(self):
        return Path(tempfile.mkdtemp())

    def make_store(self, local_dir=None):
        if local_dir is None:
            local_dir = self.temp_dir()
        return HoldingsStore(local_dir=local_dir)

    def test_save_and_load_local(self):
        """Round-trip local save → load."""
        store = self.make_store()
        data = {
            "holdings": {"SPYM": 30.0, "VGIT": 50.0},
            "cash_in": 100000.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-06-19",
        }
        store.save_local(data)
        loaded = store.load_local()
        assert loaded is not None
        assert loaded["holdings"]["SPYM"] == 30.0
        assert loaded["cash_in"] == 100000.0

    def test_backup_on_save(self):
        """Re-saving creates a backup of the previous version."""
        store = self.make_store()
        data = {
            "holdings": {"SPYM": 10.0},
            "cash_in": 50000.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        }
        store.save_local(data)  # first save — no previous file to back up
        data["cash_in"] = 60000.0
        store.save_local(data)  # second save — backups previous
        backup = store.local_dir / "pp_holdings.backup.json"
        assert backup.exists()

    def test_add_transaction(self):
        """Adding a transaction updates holdings."""
        store = self.make_store()
        # Fresh state
        tickers = ["SPYM", "AVUV", "VGIT", "GLDM", "518880.SS", "SGOV", "511360.SS"]
        store.save_local({
            "holdings": {t: 0.0 for t in tickers},
            "cash_in": 0.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        })
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type="buy",
            trades=[{"ticker": "SPYM", "shares": 10, "price": 72.50, "currency": "USD"}],
            usdcny=7.25,
        )
        store.add_transaction(txn)
        state = store.load_local()
        assert state is not None
        assert state["holdings"]["SPYM"] == 10.0

    def test_undo_last_transaction(self):
        """Undo reverts the last transaction."""
        store = self.make_store()
        store.save_local({
            "holdings": {"SPYM": 10.0, "AVUV": 0, "VGIT": 20.0, "GLDM": 0,
                         "518880.SS": 0, "SGOV": 50.0, "511360.SS": 0},
            "cash_in": 50000.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        })
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type="sell",
            trades=[{"ticker": "SPYM", "shares": 5, "price": 80.0, "currency": "USD"}],
            usdcny=7.30,
        )
        store.add_transaction(txn)
        before_undo = store.load_local()
        assert before_undo["holdings"]["SPYM"] == 5.0  # 10 - 5

        store.undo_last()
        after_undo = store.load_local()
        assert after_undo["holdings"]["SPYM"] == 10.0  # restored

    def test_undo_empty_history(self):
        """Undo with no transactions → no error."""
        store = self.make_store()
        store.save_local({
            "holdings": {}, "cash_in": 0, "cash_out": 0,
            "transactions": [], "created_at": "2025-01-01",
        })
        store.undo_last()  # should not raise

    def test_undo_clamp_negative(self):
        """Undo that would cause negative holdings → clamp to 0 + warning."""
        store = self.make_store()
        store.save_local({
            "holdings": {"SPYM": 0.0, "AVUV": 0, "VGIT": 0, "GLDM": 0,
                         "518880.SS": 0, "SGOV": 0, "511360.SS": 0},
            "cash_in": 0, "cash_out": 0, "transactions": [], "created_at": "2025-01-01",
        })
        # Buy 10 SPYM
        txn = Transaction(
            txn_id=str(uuid.uuid4()), date="2025-06-19", txn_type="buy",
            trades=[{"ticker": "SPYM", "shares": 10, "price": 72.50, "currency": "USD"}],
            usdcny=7.25,
        )
        store.add_transaction(txn)
        # Manually corrupt holdings to 0
        state = store.load_local()
        state["holdings"]["SPYM"] = 0.0
        store.save_local(state)
        # Undo should clamp
        store.undo_last()
        after = store.load_local()
        assert after["holdings"]["SPYM"] >= 0  # not negative

    def test_price_history_update(self):
        """price_history is appended/updated."""
        store = self.make_store()
        entry = {
            "date": "2025-06-19",
            "prices_cny": {"stock": 525.0, "bond": 425.0, "gold": 217.5, "cash": 725.0},
        }
        store.update_price_history(entry)
        history = store.load_price_history()
        assert len(history) >= 1
        assert history[-1]["date"] == "2025-06-19"

    def test_price_history_trims_to_max(self):
        """History trimmed to 120 entries."""
        store = self.make_store()
        for i in range(150):
            store.update_price_history({
                "date": f"2025-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
                "prices_cny": {"stock": 500.0, "bond": 400.0, "gold": 200.0, "cash": 700.0},
            })
        history = store.load_price_history()
        assert len(history) <= 120
