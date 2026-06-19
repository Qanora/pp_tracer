"""Tests for configuration system."""

import os
import tempfile
from pathlib import Path

from pptracer.config import Settings


class TestSettings:
    """Tests for Pydantic Settings."""

    def test_default_values(self):
        """Settings should have sensible defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PPTTRACER_DATA_DIR"] = tmp
            try:
                settings = Settings()
                assert settings.log_level == "INFO"
                assert settings.data_dir == Path(tmp)
                assert settings.database_url.endswith("pptracer.db")
            finally:
                del os.environ["PPTTRACER_DATA_DIR"]

    def test_env_override(self):
        """Environment variables should override defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PPTTRACER_LOG_LEVEL"] = "DEBUG"
            os.environ["PPTTRACER_DATA_DIR"] = tmp
            try:
                settings = Settings()
                assert settings.log_level == "DEBUG"
            finally:
                del os.environ["PPTTRACER_LOG_LEVEL"]
                del os.environ["PPTTRACER_DATA_DIR"]

    def test_data_dir_creation(self):
        """Data directory should be created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "new_data_dir"
            os.environ["PPTTRACER_DATA_DIR"] = str(data_dir)
            try:
                _ = Settings()
                assert data_dir.exists()
                assert data_dir.is_dir()
            finally:
                del os.environ["PPTTRACER_DATA_DIR"]
