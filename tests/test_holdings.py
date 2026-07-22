"""Tests for the signed, replay-only OSS ledger."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from ppt.holdings import (
    HoldingsStore,
    InsufficientHoldingsError,
    InvalidLedgerError,
    InvalidTradeError,
    LedgerNotInitializedError,
    Trade,
    TradeBatch,
    batch_net_cash_flow,
    batch_net_investment,
    cash_summary,
    derive_holdings,
    ledger_batches,
    validate_ledger,
    validate_trade_input,
)
from ppt.storage import ObjectNotFoundError, StorageConfigurationError, StorageError

HOLDINGS_PATH = "oss://test-bucket/pp_holdings.json"
BACKUP_PREFIX = "oss://test-bucket/backups/pp_holdings"


class _FakeBackend:
    """In-memory implementation of the complete mutation protocol."""

    def __init__(self):
        self.objects: dict[str, dict[str, Any]] = {}
        self.operations: list[tuple[str, str, str | None]] = []
        self.fail_exists = False
        self.fail_read = False
        self.fail_copy = False
        self.fail_write = False
        self.locked = False
        self.lock_entries = 0
        self.lock_exits = 0

    def exists(self, path: str) -> bool:
        self.operations.append(("exists", path, None))
        if self.fail_exists:
            raise StorageError("exists failed")
        return path in self.objects

    def read(self, path: str) -> dict[str, Any]:
        self.operations.append(("read", path, None))
        if self.fail_read:
            raise StorageError("read failed")
        if path not in self.objects:
            raise ObjectNotFoundError(path)
        return deepcopy(self.objects[path])

    def write(self, path: str, data: dict[str, Any]) -> None:
        self.operations.append(("write", path, None))
        if self.fail_write:
            raise StorageError("write failed")
        self.objects[path] = deepcopy(data)

    def copy(self, source: str, destination: str) -> None:
        self.operations.append(("copy", source, destination))
        if self.fail_copy:
            raise StorageError("copy failed")
        if source not in self.objects:
            raise ObjectNotFoundError(source)
        if destination in self.objects:
            raise StorageError("destination exists")
        self.objects[destination] = deepcopy(self.objects[source])

    @contextmanager
    def lock(self, path: str, *, lease_seconds: int = 300):
        del lease_seconds
        if self.locked:
            raise StorageError("already locked")
        self.operations.append(("lock", path, None))
        self.locked = True
        self.lock_entries += 1
        try:
            yield
        finally:
            self.locked = False
            self.lock_exits += 1
            self.operations.append(("unlock", path, None))


def _store(backend: _FakeBackend | None = None) -> tuple[HoldingsStore, _FakeBackend]:
    fake = backend or _FakeBackend()
    return (
        HoldingsStore(
            backend=fake,
            holdings_path=HOLDINGS_PATH,
            backup_prefix=BACKUP_PREFIX,
        ),
        fake,
    )


def _batch(
    batch_id: str,
    trades: tuple[Trade, ...],
    *,
    usdcny: float = 7.0,
) -> TradeBatch:
    return TradeBatch(
        batch_id=batch_id,
        executed_at="2026-07-22T12:00:00+00:00",
        usdcny=usdcny,
        trades=trades,
    )


def _ledger(*batches: TradeBatch) -> dict[str, Any]:
    return {
        "initialized_at": "2026-07-22T00:00:00+00:00",
        "batches": [batch.to_dict() for batch in batches],
    }


class TestSignedTradeValidation:
    def test_positive_buy_and_negative_sell_are_valid(self):
        assert validate_trade_input("SPYM", 10, 72.5) == []
        assert validate_trade_input("SPYM", -10, 72.5) == []
        assert validate_trade_input("518880.SS", -100, 5.5) == []

    @pytest.mark.parametrize("shares", [0, 1.5, True, float("nan"), float("inf")])
    def test_zero_fractional_boolean_and_nonfinite_shares_are_invalid(self, shares):
        assert validate_trade_input("SPYM", shares, 72.5)

    @pytest.mark.parametrize("shares", [1, -1, 150, -150])
    def test_a_share_lot_size_applies_to_both_directions(self, shares):
        assert validate_trade_input("518880.SS", shares, 5.5)

    @pytest.mark.parametrize("price", [0, -1, True, float("nan"), float("inf")])
    def test_price_must_be_positive_and_finite(self, price):
        assert validate_trade_input("SPYM", 1, price)

    def test_unknown_ticker_is_invalid(self):
        assert validate_trade_input("AAPL", 1, 100)

    def test_trade_cannot_be_constructed_invalid(self):
        with pytest.raises(InvalidTradeError):
            Trade("518880.SS", -1, 5.5)

    def test_batch_rejects_duplicate_ticker(self):
        with pytest.raises(InvalidTradeError, match="Duplicate"):
            _batch(
                "00000000-0000-0000-0000-000000000001",
                (Trade("SPYM", 1, 100), Trade("SPYM", -1, 101)),
            )


class TestPureLedgerReplay:
    def test_old_or_extra_fields_are_not_compatible(self):
        old = {
            "holdings": {"SPYM": 1},
            "cash_in": 700.0,
            "cash_out": 0.0,
            "transactions": [],
            "created_at": "2025-01-01",
        }
        assert validate_ledger(old) is False
        assert validate_ledger({**_ledger(), "version": 1}) is False

    def test_holdings_are_replayed_from_signed_batches(self):
        first = _batch(
            "00000000-0000-0000-0000-000000000001",
            (Trade("SPYM", 10, 100), Trade("518880.SS", 100, 5)),
        )
        second = _batch(
            "00000000-0000-0000-0000-000000000002",
            (Trade("SPYM", -3, 110),),
        )
        ledger = _ledger(first, second)

        holdings = derive_holdings(ledger)
        assert holdings["SPYM"] == 7
        assert holdings["518880.SS"] == 100
        assert ledger_batches(ledger) == (first, second)

    def test_persisted_oversell_is_invalid(self):
        oversell = _ledger(
            _batch(
                "00000000-0000-0000-0000-000000000001",
                (Trade("SPYM", -1, 100),),
            )
        )
        with pytest.raises(InvalidLedgerError, match="oversells"):
            derive_holdings(oversell)
        assert validate_ledger(oversell) is False

    def test_cash_summary_nets_each_mixed_batch_before_accumulating(self):
        contribution = _batch(
            "00000000-0000-0000-0000-000000000001",
            (Trade("SPYM", 10, 100), Trade("518880.SS", 100, 5)),
            usdcny=7.0,
        )
        # Sell ¥1,400 of SPYM and buy ¥1,000 of 518880 in one rebalance:
        # the batch is one ¥400 withdrawal, not gross input + gross output.
        rebalance = _batch(
            "00000000-0000-0000-0000-000000000002",
            (Trade("SPYM", -2, 100), Trade("518880.SS", 200, 5)),
            usdcny=7.0,
        )
        ledger = _ledger(contribution, rebalance)

        assert batch_net_cash_flow(contribution) == -7500.0
        assert batch_net_investment(contribution) == 7500.0
        assert batch_net_cash_flow(rebalance) == 400.0
        assert cash_summary(ledger) == {
            "cash_in": 7500.0,
            "cash_out": 400.0,
            "net_cash": 7100.0,
        }


class TestHoldingsStore:
    def test_environment_bucket_is_required_and_never_implicit(self, monkeypatch):
        monkeypatch.delenv("PP_OSS_BUCKET", raising=False)
        with pytest.raises(StorageConfigurationError, match="required"):
            HoldingsStore.from_environment()

        monkeypatch.setenv("PP_OSS_BUCKET", "isolated-ledger")
        store = HoldingsStore.from_environment()
        assert store.holdings_path == "oss://isolated-ledger/pp_holdings.json"

    def test_load_returns_none_only_for_missing_object(self):
        store, backend = _store()
        assert store.load() is None

        backend.fail_read = True
        with pytest.raises(StorageError, match="read failed"):
            store.load()

    def test_load_rejects_old_format(self):
        store, backend = _store()
        backend.objects[HOLDINGS_PATH] = {"holdings": {}, "transactions": []}
        with pytest.raises(InvalidLedgerError):
            store.load()

    def test_initialize_missing_object_creates_without_backup(self):
        store, backend = _store()
        backup_path = store.initialize()

        assert backup_path is None
        assert validate_ledger(backend.objects[HOLDINGS_PATH])
        assert not [operation for operation in backend.operations if operation[0] == "copy"]
        assert backend.lock_entries == backend.lock_exits == 1

    def test_initialize_backs_up_any_existing_object_before_reset(self):
        store, backend = _store()
        old_object = {"arbitrary": "old or corrupt data"}
        backend.objects[HOLDINGS_PATH] = deepcopy(old_object)

        backup_path = store.initialize()

        assert backup_path is not None
        assert backend.objects[backup_path] == old_object
        assert validate_ledger(backend.objects[HOLDINGS_PATH])
        operation_names = [operation[0] for operation in backend.operations]
        assert operation_names.index("copy") < operation_names.index("write")

    def test_initialize_backup_failure_keeps_existing_object(self):
        store, backend = _store()
        original = {"old": "must survive"}
        backend.objects[HOLDINGS_PATH] = deepcopy(original)
        backend.fail_copy = True

        with pytest.raises(StorageError, match="copy failed"):
            store.initialize()

        assert backend.objects[HOLDINGS_PATH] == original
        assert not [operation for operation in backend.operations if operation[0] == "write"]
        assert backend.locked is False

    def test_initialize_write_failure_keeps_existing_object_and_backup(self):
        store, backend = _store()
        original = {"old": "must survive"}
        backend.objects[HOLDINGS_PATH] = deepcopy(original)
        backend.fail_write = True

        with pytest.raises(StorageError, match="write failed"):
            store.initialize()

        assert backend.objects[HOLDINGS_PATH] == original
        backup_paths = [
            path for path in backend.objects if path.startswith(f"{BACKUP_PREFIX}/")
        ]
        assert len(backup_paths) == 1
        assert backend.objects[backup_paths[0]] == original
        assert backend.locked is False

    def test_record_batch_appends_signed_trades_and_derives_holdings(self):
        store, backend = _store()
        store.initialize()
        batch = store.record_batch(
            [Trade("SPYM", 10, 100), Trade("518880.SS", 100, 5)],
            usdcny=7.0,
        )
        sold = store.record_batch([Trade("SPYM", -2, 110)], usdcny=7.1)

        ledger = store.load()
        assert ledger is not None
        assert ledger_batches(ledger) == (batch, sold)
        assert derive_holdings(ledger)["SPYM"] == 8
        assert derive_holdings(ledger)["518880.SS"] == 100

        backups = [path for path in backend.objects if path.startswith(f"{BACKUP_PREFIX}/")]
        assert len(backups) == 2
        assert len(set(backups)) == 2

    def test_oversell_rejects_entire_batch_before_backup_or_write(self):
        store, backend = _store()
        store.initialize()
        store.record_batch([Trade("SPYM", 10, 100)], usdcny=7.0)
        original = deepcopy(backend.objects[HOLDINGS_PATH])
        prior_operations = len(backend.operations)

        with pytest.raises(InsufficientHoldingsError, match="SPYM"):
            store.record_batch(
                [Trade("VGIT", 1, 50), Trade("SPYM", -11, 100)],
                usdcny=7.0,
            )

        assert backend.objects[HOLDINGS_PATH] == original
        new_operations = backend.operations[prior_operations:]
        assert not [operation for operation in new_operations if operation[0] in {"copy", "write"}]
        assert backend.locked is False

    def test_invalid_batch_has_no_storage_side_effects(self):
        store, backend = _store()
        store.initialize()
        prior_operations = list(backend.operations)

        with pytest.raises(InvalidTradeError):
            store.record_batch(
                [Trade("SPYM", 1, 100), "not-a-trade"],  # type: ignore[list-item]
                usdcny=7.0,
            )

        assert backend.operations == prior_operations

    def test_record_before_initialize_is_explicit_and_releases_lock(self):
        store, backend = _store()
        with pytest.raises(LedgerNotInitializedError, match="init"):
            store.record_batch([Trade("SPYM", 1, 100)], usdcny=7.0)
        assert backend.locked is False
        assert backend.lock_entries == backend.lock_exits == 1

    def test_backup_failure_prevents_primary_write_and_releases_lock(self):
        store, backend = _store()
        store.initialize()
        original = deepcopy(backend.objects[HOLDINGS_PATH])
        backend.fail_copy = True

        with pytest.raises(StorageError, match="copy failed"):
            store.record_batch([Trade("SPYM", 1, 100)], usdcny=7.0)

        assert backend.objects[HOLDINGS_PATH] == original
        assert backend.locked is False

    def test_write_failure_keeps_primary_and_preserves_new_backup(self):
        store, backend = _store()
        store.initialize()
        original = deepcopy(backend.objects[HOLDINGS_PATH])
        existing_paths = set(backend.objects)
        backend.fail_write = True

        with pytest.raises(StorageError, match="write failed"):
            store.record_batch([Trade("SPYM", 1, 100)], usdcny=7.0)

        assert backend.objects[HOLDINGS_PATH] == original
        new_paths = set(backend.objects) - existing_paths
        assert len(new_paths) == 1
        backup_path = new_paths.pop()
        assert backend.objects[backup_path] == original
        assert backend.locked is False

    def test_init_read_probe_failure_never_writes(self):
        store, backend = _store()
        original = {"old": "must survive"}
        backend.objects[HOLDINGS_PATH] = deepcopy(original)
        backend.fail_exists = True

        with pytest.raises(StorageError, match="exists failed"):
            store.initialize()

        assert backend.objects[HOLDINGS_PATH] == original
        assert not [operation for operation in backend.operations if operation[0] == "write"]
        assert backend.locked is False

    def test_generated_timestamps_are_timezone_aware(self):
        store, _backend = _store()
        store.initialize()
        ledger = store.load()
        initialized = datetime.fromisoformat(ledger["initialized_at"])
        assert initialized.tzinfo is not None

        batch = store.record_batch([Trade("SPYM", 1, 100)], usdcny=7.0)
        assert datetime.fromisoformat(batch.executed_at).astimezone(UTC).tzinfo is not None
