"""Tests for holdings I/O (§2) — OSS-only storage."""

import uuid
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock

import pytest

from ppt.holdings import (
    HoldingsStore,
    Transaction,
    validate_holdings,
    validate_transaction_input,
)
from ppt.storage import OssBackend


class _FakeBackend:
    """In-memory storage backend for testing — no ossutil required."""

    def __init__(self):
        self._store: dict[str, Any] = {}

    def read(self, path: str) -> dict[str, Any] | None:
        data = self._store.get(path)
        return data if isinstance(data, dict) else None

    def read_list(self, path: str) -> list[Any]:
        data = self._store.get(path)
        return data if isinstance(data, list) else []

    def write(self, path: str, data: Any) -> bool:
        self._store[path] = data
        return True


def _price(stock, bond, gold, cash):
    """Build a prices_cny dict with bucket keys."""
    return {"stock": stock, "bond": bond, "gold": gold, "cash": cash}


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

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_non_finite_values_are_rejected_without_crashing(self, value):
        assert validate_transaction_input("SPYM", value, 72.50)
        assert validate_transaction_input("SPYM", 10, value)


class TestValidateHoldings:
    """Full holdings state validation."""

    def test_valid_state(self):
        result = validate_holdings(
            {
                "holdings": {"SPYM": 30, "VGIT": 50},
                "cash_in": 100000.0,
                "cash_out": 0.0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        assert result is True

    def test_missing_field(self):
        result = validate_holdings({"holdings": {}})
        assert result is False

    def test_negative_cash(self):
        result = validate_holdings(
            {
                "holdings": {},
                "cash_in": -100.0,
                "cash_out": 0.0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        assert result is False

    @pytest.mark.parametrize("cash_in", ["100", float("nan"), float("inf")])
    def test_invalid_cash_type_or_value(self, cash_in):
        assert (
            validate_holdings(
                {
                    "holdings": {},
                    "cash_in": cash_in,
                    "cash_out": 0.0,
                    "transactions": [],
                    "created_at": "2025-01-01",
                }
            )
            is False
        )

    def test_invalid_holding_value(self):
        assert (
            validate_holdings(
                {
                    "holdings": {"SPYM": float("inf")},
                    "cash_in": 0.0,
                    "cash_out": 0.0,
                    "transactions": [],
                    "created_at": "2025-01-01",
                }
            )
            is False
        )


# ── HoldingsStore (OSS-mocked) ────────────────────────────────────────────────


class TestHoldingsStore:
    """Tests use in-memory store mocked over OSS I/O."""

    @staticmethod
    def make_store_with_memory():
        """Create a HoldingsStore with an in-memory fake backend."""
        return HoldingsStore(backend=_FakeBackend())

    def test_save_and_load(self):
        """Round-trip save → load via mocked OSS."""
        store = self.make_store_with_memory()
        data = {
            "holdings": {"SPYM": 30.0, "VGIT": 50.0},
            "cash_in": 100000.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-06-19",
        }
        store.save(data)
        loaded = store.load()
        assert loaded is not None
        assert loaded["holdings"]["SPYM"] == 30.0
        assert loaded["cash_in"] == 100000.0

    def test_load_empty_returns_none(self):
        """Loading from empty OSS returns None."""
        store = self.make_store_with_memory()
        assert store.load() is None

    def test_add_transaction(self):
        """Adding a transaction updates holdings."""
        store = self.make_store_with_memory()
        tickers = ["SPYM", "AVUV", "VGIT", "GLDM", "518880.SS", "SGOV", "511360.SS"]
        store.save(
            {
                "holdings": {t: 0.0 for t in tickers},
                "cash_in": 0.0,
                "cash_out": 0.0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type="buy",
            trades=[{"ticker": "SPYM", "shares": 10, "price": 72.50, "currency": "USD"}],
            usdcny=7.25,
        )
        store.add_transaction(txn)
        state = store.load()
        assert state is not None
        assert state["holdings"]["SPYM"] == 10.0

    def test_internal_transaction_does_not_change_external_cash_flow(self):
        store = self.make_store_with_memory()
        store.save(
            {
                "holdings": {"SPYM": 10.0},
                "cash_in": 1000.0,
                "cash_out": 0.0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type="sell",
            trades=[{"ticker": "SPYM", "shares": 2, "price": 72.50, "currency": "USD"}],
            usdcny=7.25,
            internal=True,
        )

        store.add_transaction(txn)
        state = store.load()

        assert state["holdings"]["SPYM"] == 8.0
        assert state["cash_in"] == 1000.0
        assert state["cash_out"] == 0.0
        assert state["transactions"][-1]["internal"] is True

    def test_undo_last_transaction(self):
        """Undo reverts the last transaction."""
        store = self.make_store_with_memory()
        store.save(
            {
                "holdings": {
                    "SPYM": 10.0,
                    "AVUV": 0,
                    "VGIT": 20.0,
                    "GLDM": 0,
                    "518880.SS": 0,
                    "SGOV": 50.0,
                    "511360.SS": 0,
                },
                "cash_in": 50000.0,
                "cash_out": 0.0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type="sell",
            trades=[{"ticker": "SPYM", "shares": 5, "price": 80.0, "currency": "USD"}],
            usdcny=7.30,
        )
        store.add_transaction(txn)
        before_undo = store.load()
        assert before_undo["holdings"]["SPYM"] == 5.0  # 10 - 5

        store.undo_last()
        after_undo = store.load()
        assert after_undo["holdings"]["SPYM"] == 10.0  # restored
        assert after_undo["cash_in"] == 50000.0
        assert after_undo["cash_out"] == 0.0

    @pytest.mark.parametrize("txn_type,cash_key", [("buy", "cash_in"), ("sell", "cash_out")])
    def test_undo_restores_external_cash_totals(self, txn_type, cash_key):
        store = self.make_store_with_memory()
        store.save(
            {
                "holdings": {"SPYM": 10.0},
                "cash_in": 0.0,
                "cash_out": 0.0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type=txn_type,
            trades=[{"ticker": "SPYM", "shares": 2, "price": 100.0, "currency": "USD"}],
            usdcny=7.0,
        )

        store.add_transaction(txn)
        assert store.load()[cash_key] == 1400.0
        store.undo_last()

        state = store.load()
        assert state["cash_in"] == 0.0
        assert state["cash_out"] == 0.0

    def test_batch_rejects_oversell_without_partial_update(self):
        store = self.make_store_with_memory()
        initial = {
            "holdings": {"SPYM": 10.0, "VGIT": 0.0},
            "cash_in": 0.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        }
        store.save(initial)
        transactions = [
            Transaction(
                str(uuid.uuid4()),
                "2025-06-19",
                "buy",
                [{"ticker": "VGIT", "shares": 1, "price": 50.0, "currency": "USD"}],
                7.0,
                internal=True,
            ),
            Transaction(
                str(uuid.uuid4()),
                "2025-06-19",
                "sell",
                [{"ticker": "SPYM", "shares": 11, "price": 100.0, "currency": "USD"}],
                7.0,
                internal=True,
            ),
        ]

        with pytest.raises(ValueError, match="Insufficient holdings"):
            store.add_transactions(transactions)

        assert store.load() == initial

    def test_backup_failure_prevents_primary_write(self, tmp_path, monkeypatch):
        backend = OssBackend("ossutil")
        monkeypatch.setattr(backend, "lock", lambda _: nullcontext())
        monkeypatch.setattr(backend, "copy", lambda _source, _destination: False)
        backend.write = MagicMock(return_value=True)
        store = HoldingsStore(local_dir=tmp_path, backend=backend)
        state = {
            "holdings": {},
            "cash_in": 0.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        }

        with pytest.raises(RuntimeError, match="back up"):
            store.save(state)

        backend.write.assert_not_called()

    def test_undo_empty_history(self):
        """Undo with no transactions → no error."""
        store = self.make_store_with_memory()
        store.save(
            {
                "holdings": {},
                "cash_in": 0,
                "cash_out": 0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        store.undo_last()  # should not raise

    def test_undo_clamp_negative(self):
        """Undo that would cause negative holdings → clamp to 0 + warning."""
        store = self.make_store_with_memory()
        store.save(
            {
                "holdings": {
                    "SPYM": 0.0,
                    "AVUV": 0,
                    "VGIT": 0,
                    "GLDM": 0,
                    "518880.SS": 0,
                    "SGOV": 0,
                    "511360.SS": 0,
                },
                "cash_in": 0,
                "cash_out": 0,
                "transactions": [],
                "created_at": "2025-01-01",
            }
        )
        txn = Transaction(
            txn_id=str(uuid.uuid4()),
            date="2025-06-19",
            txn_type="buy",
            trades=[{"ticker": "SPYM", "shares": 10, "price": 72.50, "currency": "USD"}],
            usdcny=7.25,
        )
        store.add_transaction(txn)
        # Manually corrupt holdings to 0
        state = store.load()
        state["holdings"]["SPYM"] = 0.0
        store.save(state)
        # Undo should clamp
        store.undo_last()
        after = store.load()
        assert after["holdings"]["SPYM"] >= 0  # not negative

    def test_price_history_update(self):
        """price_history is appended/updated."""
        store = self.make_store_with_memory()
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
        store = self.make_store_with_memory()
        for i in range(150):
            store.update_price_history(
                {
                    "date": f"2025-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
                    "prices_cny": {"stock": 500.0, "bond": 400.0, "gold": 200.0, "cash": 700.0},
                }
            )
        history = store.load_price_history()
        assert len(history) <= 120

    # ── clean_price_history ─────────────────────────────────────────────────

    def test_clean_price_history_removes_all_nan_entries(self):
        """Entries where every bucket price is NaN are removed."""
        store = self.make_store_with_memory()
        p = _price  # shorthand
        nan = float("nan")
        history = [
            {"date": "2025-01-01", "prices_cny": p(100, 200, 300, 400)},
            {"date": "2025-01-02", "prices_cny": p(nan, nan, nan, nan)},
            {"date": "2025-01-03", "prices_cny": p(101, 201, 301, 401)},
        ]
        store._save_price_history(history)
        removed = store.clean_price_history()
        assert removed == 1
        cleaned = store.load_price_history()
        assert len(cleaned) == 2
        assert cleaned[0]["date"] == "2025-01-01"
        assert cleaned[1]["date"] == "2025-01-03"

    def test_clean_price_history_removes_partial_nan_entries(self):
        """Entries with any NaN bucket price are removed."""
        store = self.make_store_with_memory()
        p = _price
        history = [
            {"date": "2025-01-01", "prices_cny": p(100, float("nan"), 300, 400)},
            {"date": "2025-01-02", "prices_cny": p(200, 200, 200, 200)},
        ]
        store._save_price_history(history)
        removed = store.clean_price_history()
        assert removed == 1
        cleaned = store.load_price_history()
        assert len(cleaned) == 1
        assert cleaned[0]["date"] == "2025-01-02"

    def test_clean_price_history_no_nan_entries(self):
        """No-op when there are no NaN entries."""
        store = self.make_store_with_memory()
        history = [
            {"date": "2025-01-01", "prices_cny": _price(100, 200, 300, 400)},
        ]
        store._save_price_history(history)
        removed = store.clean_price_history()
        assert removed == 0
        assert len(store.load_price_history()) == 1

    def test_clean_price_history_empty_history(self):
        """No-op on empty history."""
        store = self.make_store_with_memory()
        removed = store.clean_price_history()
        assert removed == 0
