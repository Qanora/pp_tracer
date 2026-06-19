"""Application configuration via Pydantic Settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """PP Tracer configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_prefix="PPTTRACER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Paths ───────────────────────────────────────────────────────────
    data_dir: Path = Path.home() / ".pptracer" / "data"
    log_dir: Path = Path.home() / ".pptracer" / "logs"

    # ── Database ────────────────────────────────────────────────────────
    database_url: str = ""

    # ── Logging ─────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── API ─────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-derive database_url if not explicitly set
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir}/pptracer.db"
        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
