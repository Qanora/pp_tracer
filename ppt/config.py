"""Configuration system (§6)."""

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "rebalance": {
        "tolerance": 0.005,
    },
    "conversion": {
        "gldm_shares": 1000,
        "sgov_shares": 100,
    },
    "network": {
        "max_retry": 3,
        "retry_wait": 2,
        "cache_ttl": 300,
    },
    "advanced": {
        "weighting_mode": "equal",
        "gap_elasticity": 1.5,
        "corridor_k": 2.5,
        "trend_sensitivity": 0.5,
        "rp_weight_cap": 0.40,
        "rp_weight_floor": 0.10,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base; missing keys filled from base."""
    result = deepcopy(base)
    for key, value in override.items():
        if key not in result:
            continue
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """User configuration with defaults fallback (§6)."""

    def __init__(self, data: dict[str, Any] | None = None):
        self.data = data if data is not None else deepcopy(DEFAULT_CONFIG)

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        """Load config from JSON file, filling missing keys with defaults."""
        if not path.exists():
            return cls(data=deepcopy(DEFAULT_CONFIG))
        with open(path, encoding="utf-8") as f:
            user_data = json.load(f)
        merged = _deep_merge(DEFAULT_CONFIG, user_data)
        return cls(data=merged)

    def save(self, path: Path) -> None:
        """Write config to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    @property
    def local_dir(self) -> Path:
        """Expanded local directory path."""
        return Path(os.path.expanduser("~/.pp/"))
