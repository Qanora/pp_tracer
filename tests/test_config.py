"""Tests for config system (§6)."""

import json
import tempfile
from pathlib import Path

from ppt.config import DEFAULT_CONFIG, Config


class TestConfigDefaults:
    """Default config values match README §6."""

    def test_tolerance(self):
        assert DEFAULT_CONFIG["rebalance"]["tolerance"] == 0.005

    def test_weighting_mode(self):
        assert DEFAULT_CONFIG["advanced"]["weighting_mode"] == "equal"

    def test_gap_elasticity(self):
        assert DEFAULT_CONFIG["advanced"]["gap_elasticity"] == 1.5

    def test_corridor_k(self):
        assert DEFAULT_CONFIG["advanced"]["corridor_k"] == 2.5

    def test_trend_sensitivity(self):
        assert DEFAULT_CONFIG["advanced"]["trend_sensitivity"] == 0.5

    def test_conversion_gldm(self):
        assert DEFAULT_CONFIG["conversion"]["gldm_shares"] == 1000

    def test_conversion_sgov(self):
        assert DEFAULT_CONFIG["conversion"]["sgov_shares"] == 100


class TestConfig:
    """Config file I/O."""

    def test_load_with_defaults(self):
        """Loading nonexistent file returns defaults."""
        cfg = Config.from_file(Path("/nonexistent/pp_config.json"))
        assert cfg.data["rebalance"]["tolerance"] == 0.005

    def test_save_and_load(self):
        """Round-trip save → load preserves data."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pp_config.json"
            cfg = Config(data=DEFAULT_CONFIG.copy())
            cfg.save(path)
            loaded = Config.from_file(path)
            assert loaded.data == cfg.data

    def test_missing_keys_filled(self):
        """Missing keys are filled with defaults on load."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pp_config.json"
            path.write_text(json.dumps({"rebalance": {"tolerance": 0.01}}))
            loaded = Config.from_file(path)
            # Provided key preserved
            assert loaded.data["rebalance"]["tolerance"] == 0.01
            # Missing keys filled from defaults
            assert loaded.data["advanced"]["weighting_mode"] == "equal"

    def test_removed_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pp_config.json"
            path.write_text(json.dumps({"rebalance": {"target": 0.30}}))

            loaded = Config.from_file(path)

            assert "target" not in loaded.data["rebalance"]
