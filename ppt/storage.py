"""Strict OSS object storage with typed failures and leased mutation locks."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote


class StorageError(RuntimeError):
    """Base class for storage failures whose outcome must not be ignored."""


class StorageConfigurationError(StorageError):
    """The OSS backend cannot be constructed safely."""


class ObjectNotFoundError(StorageError):
    """The requested OSS object is known not to exist."""


class StoredDataError(StorageError):
    """An OSS object exists but is not a valid JSON object."""


class LockUnavailableError(StorageError):
    """Another live owner holds the mutation lock."""


class LockReleaseError(StorageError):
    """A lock owned by this process could not be released safely."""


@runtime_checkable
class IStorageBackend(Protocol):
    """Complete storage contract required by ledger mutations.

    Implementations must make ``write`` an atomic object replacement and must
    never collapse transport/permission errors into a missing-object result.
    """

    def exists(self, path: str) -> bool:
        """Return whether ``path`` exists, or raise on an uncertain result."""
        ...

    def read(self, path: str) -> dict[str, Any]:
        """Read one JSON object; raise ObjectNotFoundError when absent."""
        ...

    def write(self, path: str, data: dict[str, Any]) -> None:
        """Atomically replace one JSON object, raising on failure."""
        ...

    def copy(self, source: str, destination: str) -> None:
        """Copy to a new destination without overwriting it."""
        ...

    def lock(self, path: str, *, lease_seconds: int = 300) -> AbstractContextManager[None]:
        """Serialize mutations of ``path`` with a self-recovering lease."""
        ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


class OssBackend:
    """OSS backend implemented through ``ossutil`` without a shell.

    Every contender creates a UUID-unique lease claim, then lists the claim
    prefix. A live peer blocks entry; an expired peer can be deleted safely
    because owner paths are never reused. Release deletes only the caller's own
    claim, including when the protected operation raises.
    """

    _NOT_FOUND_MARKERS = (
        "status code: 404",
        "statuscode=404",
        "nosuchkey",
        "not found",
        "object does not exist",
        "object not exist",
    )
    _ALREADY_EXISTS_MARKERS = (
        "status code: 409",
        "statuscode=409",
        "already exists",
        "filealreadyexists",
        "objectalreadyexists",
        "forbid overwrite",
    )

    def __init__(self, ossutil_path: str | None = None):
        executable = ossutil_path or os.environ.get("OSSUTIL_PATH", "ossutil")
        if not executable.strip():
            raise StorageConfigurationError("OSSUTIL_PATH must not be empty")
        self.ossutil = executable

    @staticmethod
    def _split_path(oss_path: str) -> tuple[str, str]:
        if not oss_path.startswith("oss://"):
            raise StorageConfigurationError(f"Invalid OSS path: {oss_path}")
        bucket, separator, key = oss_path[6:].partition("/")
        if not bucket or not separator or not key:
            raise StorageConfigurationError(f"OSS object path required: {oss_path}")
        return bucket, key

    def _invoke(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise StorageError(f"OSS command timed out: {arguments[1]}") from exc
        except (FileNotFoundError, OSError) as exc:
            raise StorageError(f"Cannot execute OSS command: {arguments[0]}") from exc

    @staticmethod
    def _command_detail(result: subprocess.CompletedProcess[str]) -> str:
        return (result.stderr or result.stdout or "unknown OSS error").strip()

    @classmethod
    def _matches(cls, result: subprocess.CompletedProcess[str], markers: tuple[str, ...]) -> bool:
        detail = f"{result.stderr}\n{result.stdout}".lower()
        return any(marker in detail for marker in markers)

    def exists(self, oss_path: str) -> bool:
        self._split_path(oss_path)
        result = self._invoke([self.ossutil, "stat", oss_path])
        if result.returncode == 0:
            return True
        if self._matches(result, self._NOT_FOUND_MARKERS):
            return False
        detail = self._command_detail(result)
        raise StorageError(f"Failed to determine whether {oss_path} exists: {detail}")

    def read(self, oss_path: str) -> dict[str, Any]:
        self._split_path(oss_path)
        result = self._invoke([self.ossutil, "cat", oss_path])
        if result.returncode != 0:
            if self._matches(result, self._NOT_FOUND_MARKERS):
                raise ObjectNotFoundError(f"OSS object not found: {oss_path}")
            raise StorageError(f"Failed to read {oss_path}: {self._command_detail(result)}")
        try:
            data = json.loads(result.stdout, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise StoredDataError(f"Invalid JSON object at {oss_path}") from exc
        if not isinstance(data, dict):
            raise StoredDataError(f"Expected JSON object at {oss_path}")
        return data

    def write(self, oss_path: str, data: dict[str, Any]) -> None:
        self._split_path(oss_path)
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="ppt-ledger-")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    data,
                    stream,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            bucket, key = self._split_path(oss_path)
            result = self._invoke(
                [
                    self.ossutil,
                    "api",
                    "put-object",
                    "--bucket",
                    bucket,
                    "--key",
                    key,
                    "--body",
                    f"file://{temp_path}",
                ]
            )
            if result.returncode != 0:
                raise StorageError(f"Failed to write {oss_path}: {self._command_detail(result)}")
        except (TypeError, ValueError, OSError) as exc:
            raise StorageError(f"Failed to serialize ledger for {oss_path}") from exc
        finally:
            if temp_path is not None:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def copy(self, source: str, destination: str) -> None:
        source_bucket, source_key = self._split_path(source)
        destination_bucket, destination_key = self._split_path(destination)
        result = self._invoke(
            [
                self.ossutil,
                "api",
                "copy-object",
                "--bucket",
                destination_bucket,
                "--key",
                destination_key,
                "--copy-source",
                f"/{source_bucket}/{quote(source_key, safe='')}",
                "--forbid-overwrite",
                "true",
            ]
        )
        if result.returncode != 0:
            if self._matches(result, self._NOT_FOUND_MARKERS):
                raise ObjectNotFoundError(f"OSS object not found: {source}")
            raise StorageError(
                f"Failed to copy {source} to {destination}: {self._command_detail(result)}"
            )

    def _create_claim(self, claim_path: str, payload: dict[str, str]) -> bool:
        bucket, key = self._split_path(claim_path)
        result = self._invoke(
            [
                self.ossutil,
                "api",
                "put-object",
                "--bucket",
                bucket,
                "--key",
                key,
                "--body",
                json.dumps(payload, separators=(",", ":")),
                "--forbid-overwrite",
                "true",
            ]
        )
        if result.returncode == 0:
            return True
        if self._matches(result, self._ALREADY_EXISTS_MARKERS):
            return False
        detail = self._command_detail(result)
        raise StorageError(f"Failed to create OSS lease claim {claim_path}: {detail}")

    def _delete_claim(self, claim_path: str) -> None:
        bucket, key = self._split_path(claim_path)
        result = self._invoke(
            [
                self.ossutil,
                "api",
                "delete-object",
                "--bucket",
                bucket,
                "--key",
                key,
            ]
        )
        if result.returncode == 0:
            return
        if self._matches(result, self._NOT_FOUND_MARKERS):
            return
        detail = self._command_detail(result)
        raise StorageError(f"Failed to delete OSS lease claim {claim_path}: {detail}")

    @staticmethod
    def _list_payload(data: object) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise StoredDataError("Invalid list-objects-v2 JSON output")
        for wrapper in ("output", "Output", "data", "Data"):
            nested = data.get(wrapper)
            if isinstance(nested, dict):
                data = nested
                break
        return data

    def _list_claim_keys(self, bucket: str, prefix: str) -> list[str]:
        keys: list[str] = []
        continuation_token: str | None = None
        while True:
            arguments = [
                self.ossutil,
                "api",
                "list-objects-v2",
                "--bucket",
                bucket,
                "--prefix",
                prefix,
                "--max-keys",
                "1000",
                "--output-format",
                "json",
            ]
            if continuation_token is not None:
                arguments.extend(["--continuation-token", continuation_token])
            result = self._invoke(arguments)
            if result.returncode != 0:
                detail = self._command_detail(result)
                raise StorageError(f"Failed to list OSS lease claims: {detail}")
            try:
                page = self._list_payload(
                    json.loads(result.stdout, parse_constant=_reject_json_constant)
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise StoredDataError("Invalid list-objects-v2 JSON output") from exc

            contents = page.get("Contents", page.get("contents", []))
            if contents is None:
                contents = []
            if isinstance(contents, dict):
                contents = [contents]
            if not isinstance(contents, list):
                raise StoredDataError("Invalid Contents in list-objects-v2 output")
            for item in contents:
                if not isinstance(item, dict):
                    raise StoredDataError("Invalid object entry in lease listing")
                key = item.get("Key", item.get("key"))
                if not isinstance(key, str) or not key.startswith(prefix):
                    raise StoredDataError("Invalid key in lease listing")
                keys.append(key)

            truncated = page.get("IsTruncated", page.get("isTruncated", False))
            if truncated not in (True, "true", "True"):
                return keys
            token = page.get(
                "NextContinuationToken",
                page.get("nextContinuationToken"),
            )
            if not isinstance(token, str) or not token:
                raise StoredDataError("Truncated lease listing has no continuation token")
            continuation_token = token

    @staticmethod
    def _lease_expiry(payload: dict[str, Any]) -> datetime | None:
        raw = payload.get("expires_at")
        if not isinstance(raw, str):
            return None
        try:
            expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if expires_at.tzinfo is None:
            return None
        return expires_at.astimezone(UTC)

    def _release_claim(self, claim_path: str) -> None:
        try:
            self._delete_claim(claim_path)
        except StorageError as exc:
            raise LockReleaseError(f"Failed to release OSS lease {claim_path}") from exc

    @contextmanager
    def lock(self, oss_path: str, *, lease_seconds: int = 300):
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be a positive integer")
        bucket, key = self._split_path(oss_path)
        owner = str(uuid.uuid4())
        claim_prefix = f"{key}.locks/"
        claim_key = f"{claim_prefix}{owner}.json"
        claim_path = f"oss://{bucket}/{claim_key}"
        now = _utcnow()
        payload = {
            "owner": owner,
            "expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
        }

        if not self._create_claim(claim_path, payload):
            raise LockUnavailableError(f"Duplicate OSS lease owner: {claim_path}")

        try:
            claim_keys = self._list_claim_keys(bucket, claim_prefix)
            if claim_key not in claim_keys:
                raise StorageError(f"New OSS lease is not visible in listing: {claim_path}")
            for other_key in claim_keys:
                if other_key == claim_key:
                    continue
                other_path = f"oss://{bucket}/{other_key}"
                try:
                    other_payload = self.read(other_path)
                except ObjectNotFoundError:
                    continue
                expires_at = self._lease_expiry(other_payload)
                if expires_at is not None and expires_at <= now:
                    # Owner paths are UUID-unique and never reused, so deleting
                    # this exact expired claim cannot affect a newer lease.
                    self._delete_claim(other_path)
                    continue
                raise LockUnavailableError(
                    f"Ledger is being updated by another process: {other_path}"
                )
        except BaseException:
            self._release_claim(claim_path)
            raise

        try:
            yield
        finally:
            self._release_claim(claim_path)
