"""Storage backend abstraction — IStorageBackend protocol + OssBackend default."""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class IStorageBackend(Protocol):
    """Protocol for remote/cloud storage of holdings JSON.

    Implementations must provide read() and write() for JSON objects,
    and read_list() for JSON arrays.
    """

    def read(self, path: str) -> Optional[Dict[str, Any]]:
        """Read a JSON object from `path`.  Return None on failure."""
        ...

    def read_list(self, path: str) -> List[Any]:
        """Read a JSON array from `path`.  Return [] on failure."""
        ...

    def write(self, path: str, data: Any) -> bool:
        """Write `data` as JSON to `path`.  Return True on success."""
        ...


class OssBackend:
    """Default backend: `ossutil cat` / `ossutil cp`.

    ossutil_path can be overridden via OSSUTIL_PATH env var.
    """

    def __init__(self, ossutil_path: Optional[str] = None):
        self.ossutil = ossutil_path or os.environ.get("OSSUTIL_PATH", "ossutil")

    def read(self, oss_path: str) -> Optional[Dict[str, Any]]:
        try:
            result = subprocess.run(
                [self.ossutil, "cat", oss_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning("ossutil cat %s failed: %s", oss_path, result.stderr.strip())
                return None
            raw = result.stdout.strip()
            last_brace = raw.rfind("}")
            if last_brace >= 0:
                raw = raw[: last_brace + 1]
            return json.loads(raw)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("OSS read error (%s): %s", oss_path, e)
            return None

    def read_list(self, oss_path: str) -> List[Any]:
        try:
            result = subprocess.run(
                [self.ossutil, "cat", oss_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.debug("ossutil cat %s failed: %s", oss_path, result.stderr.strip())
                return []
            raw = result.stdout.strip()
            last_bracket = raw.rfind("]")
            if last_bracket >= 0:
                raw = raw[: last_bracket + 1]
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.debug("OSS read error (%s): %s", oss_path, e)
            return []

    def write(self, oss_path: str, data: Any) -> bool:
        tmp_path_str = None
        try:
            fd, tmp_path_str = tempfile.mkstemp(suffix=".json", prefix="ppt_")
            os.close(fd)
            Path(tmp_path_str).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                [self.ossutil, "cp", tmp_path_str, oss_path, "-f"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error("ossutil cp %s failed: %s", oss_path, result.stderr.strip())
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.error("OSS write error (%s): %s", oss_path, e)
            return False
        finally:
            if tmp_path_str and os.path.exists(tmp_path_str):
                os.unlink(tmp_path_str)
