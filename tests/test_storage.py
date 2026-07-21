"""Tests for OSS storage coordination."""

import subprocess
from unittest.mock import patch

import pytest

from ppt.storage import OssBackend


def _result(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)


def test_oss_lock_uses_create_if_absent_and_releases():
    backend = OssBackend("ossutil-test")
    with patch("ppt.storage.subprocess.run", side_effect=[_result(), _result()]) as run:
        with backend.lock("oss://bucket/holdings.json"):
            pass

    acquire = run.call_args_list[0].args[0]
    release = run.call_args_list[1].args[0]
    assert acquire[:3] == ["ossutil-test", "api", "put-object"]
    assert "--forbid-overwrite" in acquire
    assert release == ["ossutil-test", "rm", "oss://bucket/holdings.json.lock", "-f"]


def test_oss_lock_collision_does_not_remove_other_owner():
    backend = OssBackend("ossutil-test")
    with patch(
        "ppt.storage.subprocess.run",
        return_value=_result(returncode=1, stderr="already exists"),
    ) as run:
        with pytest.raises(RuntimeError, match="another process"):
            with backend.lock("oss://bucket/holdings.json"):
                pass

    assert run.call_count == 1
