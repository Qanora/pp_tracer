"""Immutable signed-trade ledger persisted as one atomic OSS object."""

from __future__ import annotations

import math
import os
import re
import uuid
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ppt.constants import TICKER_CURRENCY, TICKER_LOT_SIZE, TICKER_WHITELIST
from ppt.storage import (
    IStorageBackend,
    ObjectNotFoundError,
    OssBackend,
    StorageConfigurationError,
)


class LedgerError(ValueError):
    """Base class for invalid ledger operations or persisted data."""


class InvalidTradeError(LedgerError):
    """A trade does not satisfy the fixed ticker and lot-size contract."""


class InvalidLedgerError(LedgerError):
    """The stored object is not the current ledger format."""


class LedgerNotInitializedError(LedgerError):
    """A mutation was attempted before ``initialize``."""


class InsufficientHoldingsError(LedgerError):
    """A signed batch would make at least one position negative."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp.tzinfo is not None


def validate_trade_input(ticker: object, shares: object, price: object) -> list[str]:
    """Return every error in one signed trade input.

    Positive shares buy and negative shares sell. Zero, fractional quantities,
    non-finite prices, unknown tickers, and illegal lot sizes are rejected.
    """

    errors: list[str] = []
    if not isinstance(ticker, str) or ticker not in TICKER_WHITELIST:
        return [f"Unknown ticker: {ticker}"]

    if isinstance(shares, bool) or not isinstance(shares, int):
        errors.append(f"Shares must be a signed integer: {shares}")
    elif shares == 0:
        errors.append("Shares must not be zero")
    elif abs(shares) % TICKER_LOT_SIZE[ticker] != 0:
        lot = TICKER_LOT_SIZE[ticker]
        errors.append(f"Shares for {ticker} must be a multiple of {lot}: {shares}")

    if (
        isinstance(price, bool)
        or not isinstance(price, (int, float))
        or not math.isfinite(price)
        or price <= 0
    ):
        errors.append(f"Price must be positive and finite: {price}")
    return errors


@dataclass(frozen=True, slots=True)
class Trade:
    """One trade leg; the sign of ``shares`` is its direction."""

    ticker: str
    shares: int
    price: float

    def __post_init__(self) -> None:
        errors = validate_trade_input(self.ticker, self.shares, self.price)
        if errors:
            raise InvalidTradeError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        return {"ticker": self.ticker, "shares": self.shares, "price": self.price}


@dataclass(frozen=True, slots=True)
class TradeBatch:
    """One atomic user-recorded batch with the historical FX rate."""

    batch_id: str
    executed_at: str
    usdcny: float
    trades: tuple[Trade, ...]

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.batch_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidLedgerError(f"Invalid batch id: {self.batch_id}") from exc
        if not _valid_timestamp(self.executed_at):
            raise InvalidLedgerError(f"Invalid batch timestamp: {self.executed_at}")
        if (
            isinstance(self.usdcny, bool)
            or not isinstance(self.usdcny, (int, float))
            or not math.isfinite(self.usdcny)
            or self.usdcny <= 0
        ):
            raise InvalidLedgerError(f"Invalid USD/CNY rate: {self.usdcny}")
        _validate_trade_sequence(self.trades)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.batch_id,
            "executed_at": self.executed_at,
            "usdcny": self.usdcny,
            "trades": [trade.to_dict() for trade in self.trades],
        }


def _validate_trade_sequence(trades: Sequence[Trade]) -> None:
    if not trades:
        raise InvalidTradeError("A batch must contain at least one trade")
    seen: set[str] = set()
    for trade in trades:
        if not isinstance(trade, Trade):
            raise InvalidTradeError("Every batch item must be a Trade")
        if trade.ticker in seen:
            raise InvalidTradeError(f"Duplicate ticker in one batch: {trade.ticker}")
        seen.add(trade.ticker)


def _trade_from_dict(data: object) -> Trade:
    if not isinstance(data, dict) or set(data) != {"ticker", "shares", "price"}:
        raise InvalidLedgerError("Invalid trade object")
    try:
        return Trade(ticker=data["ticker"], shares=data["shares"], price=data["price"])
    except InvalidTradeError as exc:
        raise InvalidLedgerError(str(exc)) from exc


def _batch_from_dict(data: object) -> TradeBatch:
    if not isinstance(data, dict) or set(data) != {"id", "executed_at", "usdcny", "trades"}:
        raise InvalidLedgerError("Invalid batch object")
    raw_trades = data["trades"]
    if not isinstance(raw_trades, list):
        raise InvalidLedgerError("Batch trades must be a list")
    trades = tuple(_trade_from_dict(trade) for trade in raw_trades)
    return TradeBatch(
        batch_id=data["id"],
        executed_at=data["executed_at"],
        usdcny=data["usdcny"],
        trades=trades,
    )


def _validated_batches(ledger: object) -> tuple[TradeBatch, ...]:
    if not isinstance(ledger, dict) or set(ledger) != {"initialized_at", "batches"}:
        raise InvalidLedgerError("Ledger must contain only initialized_at and batches")
    if not _valid_timestamp(ledger["initialized_at"]):
        raise InvalidLedgerError("Ledger initialized_at must be a timezone-aware timestamp")
    raw_batches = ledger["batches"]
    if not isinstance(raw_batches, list):
        raise InvalidLedgerError("Ledger batches must be a list")

    batches = tuple(_batch_from_dict(batch) for batch in raw_batches)
    ids = [batch.batch_id for batch in batches]
    if len(ids) != len(set(ids)):
        raise InvalidLedgerError("Ledger contains duplicate batch ids")
    return batches


def ledger_batches(ledger: object) -> tuple[TradeBatch, ...]:
    """Return immutable, fully validated batches in recorded order."""

    return _validated_batches(ledger)


def derive_holdings(ledger: object) -> dict[str, int]:
    """Purely replay signed batches into current whole-share positions."""

    holdings = {ticker: 0 for ticker in sorted(TICKER_WHITELIST)}
    for batch in _validated_batches(ledger):
        for trade in batch.trades:
            updated = holdings[trade.ticker] + trade.shares
            if updated < 0:
                raise InvalidLedgerError(
                    f"Batch {batch.batch_id} oversells {trade.ticker}: "
                    f"{holdings[trade.ticker]} + {trade.shares}"
                )
            holdings[trade.ticker] = updated
    return holdings


def _trade_value_cny(trade: Trade, usdcny: float) -> float:
    multiplier = usdcny if TICKER_CURRENCY[trade.ticker] == "USD" else 1.0
    try:
        value = abs(trade.shares) * trade.price * multiplier
    except OverflowError as exc:
        raise InvalidLedgerError(f"Trade value overflows for {trade.ticker}") from exc
    if not math.isfinite(value):
        raise InvalidLedgerError(f"Trade value is not finite for {trade.ticker}")
    return value


def batch_net_cash_flow(batch: TradeBatch) -> float:
    """Return sale proceeds minus purchase costs in CNY for one batch."""

    cash_flow = sum(
        _trade_value_cny(trade, batch.usdcny) * (-1.0 if trade.shares > 0 else 1.0)
        for trade in batch.trades
    )
    if not math.isfinite(cash_flow):
        raise InvalidLedgerError(f"Batch cash flow is not finite: {batch.batch_id}")
    return cash_flow


def batch_net_investment(batch: TradeBatch) -> float:
    """Return purchase costs minus sale proceeds in CNY for one batch."""

    return -batch_net_cash_flow(batch)


def cash_summary(ledger: object) -> dict[str, float]:
    """Derive cumulative net contributions and withdrawals by atomic batch.

    A mixed rebalance batch contributes only its net external cash movement;
    its internal sale and purchase legs do not inflate both cumulative totals.
    """

    cash_in = 0.0
    cash_out = 0.0
    for batch in _validated_batches(ledger):
        net_investment = batch_net_investment(batch)
        if net_investment > 0:
            cash_in += net_investment
        elif net_investment < 0:
            cash_out -= net_investment
        if not math.isfinite(cash_in) or not math.isfinite(cash_out):
            raise InvalidLedgerError("Cumulative ledger cash flow is not finite")
    return {
        "cash_in": cash_in,
        "cash_out": cash_out,
        "net_cash": cash_in - cash_out,
    }


def validate_ledger(ledger: object) -> bool:
    """Return whether an object is exactly the current replayable format."""

    try:
        derive_holdings(ledger)
        cash_summary(ledger)
    except LedgerError:
        return False
    return True


class HoldingsStore:
    """Serialize all ledger mutations through one OSS lock."""

    def __init__(
        self,
        *,
        backend: IStorageBackend,
        holdings_path: str,
        backup_prefix: str,
    ):
        self.backend = backend
        self.holdings_path = holdings_path
        self.backup_prefix = backup_prefix.rstrip("/")

    @classmethod
    def from_environment(cls) -> HoldingsStore:
        """Build the production store; PP_OSS_BUCKET is intentionally required."""

        bucket = os.environ.get("PP_OSS_BUCKET", "").strip()
        if not bucket:
            raise StorageConfigurationError(
                "PP_OSS_BUCKET is required; refusing to use an implicit holdings bucket"
            )
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket) is None:
            raise StorageConfigurationError(f"Invalid PP_OSS_BUCKET: {bucket}")
        return cls(
            backend=OssBackend(),
            holdings_path=f"oss://{bucket}/pp_holdings.json",
            backup_prefix=f"oss://{bucket}/backups/pp_holdings",
        )

    def _backup_path(self, purpose: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{self.backup_prefix}/{timestamp}-{purpose}-{uuid.uuid4()}.json"

    def load(self) -> dict[str, Any] | None:
        """Load a valid current ledger; return None only when definitely absent."""

        try:
            ledger = self.backend.read(self.holdings_path)
        except ObjectNotFoundError:
            return None
        # Parsing the batches also proves that old or internally inconsistent
        # formats cannot silently enter calculations.
        derive_holdings(ledger)
        cash_summary(ledger)
        return deepcopy(ledger)

    def initialize(self) -> str | None:
        """Back up any object, then atomically replace it with an empty ledger."""

        ledger = {"initialized_at": _now_iso(), "batches": []}
        backup_path: str | None = None
        with self.backend.lock(self.holdings_path):
            if self.backend.exists(self.holdings_path):
                backup_path = self._backup_path("init")
                self.backend.copy(self.holdings_path, backup_path)
            self.backend.write(self.holdings_path, ledger)
        return backup_path

    def record_batch(
        self,
        trades: Sequence[Trade],
        usdcny: float,
    ) -> TradeBatch:
        """Validate and append one batch with backup-before-write semantics."""

        normalized_trades = tuple(trades)
        _validate_trade_sequence(normalized_trades)
        batch = TradeBatch(
            batch_id=str(uuid.uuid4()),
            executed_at=_now_iso(),
            usdcny=usdcny,
            trades=normalized_trades,
        )

        with self.backend.lock(self.holdings_path):
            try:
                ledger = self.backend.read(self.holdings_path)
            except ObjectNotFoundError as exc:
                raise LedgerNotInitializedError("Ledger not initialized; run ppt init") from exc

            holdings = derive_holdings(ledger)
            for trade in batch.trades:
                available = holdings[trade.ticker]
                updated = available + trade.shares
                if updated < 0:
                    raise InsufficientHoldingsError(
                        f"Insufficient holdings for {trade.ticker}: "
                        f"have {available}, change {trade.shares}"
                    )
                holdings[trade.ticker] = updated

            updated_ledger = deepcopy(ledger)
            updated_ledger["batches"].append(batch.to_dict())
            # Validate the exact persisted representation before any backup or
            # write, so invalid input has zero storage side effects.
            derive_holdings(updated_ledger)
            cash_summary(updated_ledger)

            backup_path = self._backup_path("write")
            self.backend.copy(self.holdings_path, backup_path)
            self.backend.write(self.holdings_path, updated_ledger)
        return batch
