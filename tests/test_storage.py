"""Tests for strict OSS outcomes and owner-aware leased locks."""

import json
import subprocess
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from ppt.storage import (
    LockReleaseError,
    LockUnavailableError,
    ObjectNotFoundError,
    OssBackend,
    StorageError,
    StoredDataError,
)


def _result(
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class _LeaseRunner:
    def __init__(self):
        self.objects: dict[str, dict[str, str]] = {}
        self.deleted: list[str] = []
        self.fail_delete = False

    def __call__(self, arguments, **_kwargs):
        if arguments[1:3] == ["api", "put-object"]:
            key = arguments[arguments.index("--key") + 1]
            payload = json.loads(arguments[arguments.index("--body") + 1])
            assert arguments[arguments.index("--forbid-overwrite") + 1] == "true"
            if key in self.objects:
                return _result(1, stderr="FileAlreadyExists")
            self.objects[key] = payload
            return _result()
        if arguments[1:3] == ["api", "list-objects-v2"]:
            prefix = arguments[arguments.index("--prefix") + 1]
            contents = [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]
            return _result(stdout=json.dumps({"Contents": contents, "IsTruncated": False}))
        if arguments[1] == "cat":
            key = arguments[2].removeprefix("oss://bucket/")
            if key not in self.objects:
                return _result(1, stderr="NoSuchKey status code: 404")
            return _result(stdout=json.dumps(self.objects[key]))
        if arguments[1:3] == ["api", "delete-object"]:
            if self.fail_delete:
                return _result(1, stderr="AccessDenied")
            key = arguments[arguments.index("--key") + 1]
            self.objects.pop(key, None)
            self.deleted.append(key)
            return _result()
        raise AssertionError(arguments)


def test_read_distinguishes_missing_from_transport_failure():
    backend = OssBackend("ossutil-test")
    with patch(
        "ppt.storage.subprocess.run",
        return_value=_result(1, stderr="Status Code: 404 NoSuchKey"),
    ):
        with pytest.raises(ObjectNotFoundError):
            backend.read("oss://bucket/ledger.json")

    with patch(
        "ppt.storage.subprocess.run",
        return_value=_result(1, stderr="AccessDenied"),
    ):
        with pytest.raises(StorageError, match="Failed to read"):
            backend.read("oss://bucket/ledger.json")


def test_read_rejects_invalid_or_non_object_json():
    backend = OssBackend("ossutil-test")
    with patch("ppt.storage.subprocess.run", return_value=_result(stdout="not-json")):
        with pytest.raises(StoredDataError):
            backend.read("oss://bucket/ledger.json")

    with patch("ppt.storage.subprocess.run", return_value=_result(stdout="[]")):
        with pytest.raises(StoredDataError):
            backend.read("oss://bucket/ledger.json")


def test_exists_returns_false_only_for_confirmed_missing():
    backend = OssBackend("ossutil-test")
    with patch(
        "ppt.storage.subprocess.run",
        return_value=_result(1, stderr="NoSuchKey status code: 404"),
    ):
        assert backend.exists("oss://bucket/ledger.json") is False

    with patch(
        "ppt.storage.subprocess.run",
        return_value=_result(1, stderr="network unavailable"),
    ):
        with pytest.raises(StorageError, match="determine"):
            backend.exists("oss://bucket/ledger.json")


def test_copy_is_create_only():
    backend = OssBackend("ossutil-test")
    with patch("ppt.storage.subprocess.run", return_value=_result()) as run:
        backend.copy("oss://bucket/ledger.json", "oss://bucket/backups/one.json")

    assert run.call_args.args[0] == [
        "ossutil-test",
        "api",
        "copy-object",
        "--bucket",
        "bucket",
        "--key",
        "backups/one.json",
        "--copy-source",
        "/bucket/ledger.json",
        "--forbid-overwrite",
        "true",
    ]


def test_lock_uses_unique_owner_lease_and_removes_own_claim():
    backend = OssBackend("ossutil-test")
    runner = _LeaseRunner()

    with patch("ppt.storage.subprocess.run", side_effect=runner):
        with backend.lock("oss://bucket/ledger.json", lease_seconds=60):
            assert len(runner.objects) == 1
            payload = next(iter(runner.objects.values()))
            assert payload["owner"]
            assert datetime.fromisoformat(payload["expires_at"]).tzinfo is not None

    assert runner.objects == {}
    assert len(runner.deleted) == 1
    assert runner.deleted[0].startswith("ledger.json.locks/")


def test_live_lock_collision_never_deletes_other_owner():
    backend = OssBackend("ossutil-test")
    runner = _LeaseRunner()
    other_key = "ledger.json.locks/other-owner.json"
    runner.objects[other_key] = {
        "owner": "other-owner",
        "expires_at": datetime(2099, 1, 1, tzinfo=UTC).isoformat(),
    }

    with patch("ppt.storage.subprocess.run", side_effect=runner):
        with pytest.raises(LockUnavailableError, match="another process"):
            with backend.lock("oss://bucket/ledger.json"):
                pass

    assert set(runner.objects) == {other_key}
    assert other_key not in runner.deleted


def test_expired_owner_claim_is_removed_without_touching_new_owner():
    backend = OssBackend("ossutil-test")
    runner = _LeaseRunner()
    stale_key = "ledger.json.locks/dead-owner.json"
    runner.objects[stale_key] = {
        "owner": "dead-owner",
        "expires_at": datetime(2000, 1, 1, tzinfo=UTC).isoformat(),
    }

    with patch("ppt.storage.subprocess.run", side_effect=runner):
        with backend.lock("oss://bucket/ledger.json"):
            assert stale_key not in runner.objects
            assert len(runner.objects) == 1

    assert runner.objects == {}
    assert runner.deleted[0] == stale_key
    assert len(runner.deleted) == 2


def test_lock_is_released_when_mutation_body_raises():
    backend = OssBackend("ossutil-test")
    runner = _LeaseRunner()

    with patch("ppt.storage.subprocess.run", side_effect=runner):
        with pytest.raises(ValueError, match="boom"):
            with backend.lock("oss://bucket/ledger.json"):
                raise ValueError("boom")

    assert runner.objects == {}
    assert len(runner.deleted) == 1


def test_release_failure_is_explicit_and_lease_can_expire():
    backend = OssBackend("ossutil-test")
    runner = _LeaseRunner()
    runner.fail_delete = True

    with patch("ppt.storage.subprocess.run", side_effect=runner):
        with pytest.raises(LockReleaseError, match="release"):
            with backend.lock("oss://bucket/ledger.json"):
                pass

    assert len(runner.objects) == 1
    payload = next(iter(runner.objects.values()))
    assert datetime.fromisoformat(payload["expires_at"]).tzinfo is not None
