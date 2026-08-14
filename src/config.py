"""Application configuration loaded from environment / .env."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_db_path() -> str:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'healthcare.db'}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (SpaceXAI / xAI)
    xai_api_key: str = Field(default="", alias="XAI_API_KEY")
    llm_model: str = Field(default="grok-4.5", alias="LLM_MODEL")
    llm_base_url: str = Field(default="https://api.x.ai/v1", alias="LLM_BASE_URL")

    # Twilio (optional for PoC)
    twilio_account_sid: str = Field(default="", alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(default="", alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: str = Field(default="", alias="TWILIO_PHONE_NUMBER")

    # Agent behavior
    response_timeout_sec: int = Field(default=120, alias="RESPONSE_TIMEOUT_SEC")
    retry_attempt: int = Field(default=1, alias="RETRY_ATTEMPT")
    max_search_time_sec: int = Field(default=60, alias="MAX_SEARCH_TIME_SEC")
    confidence_threshold: float = Field(default=0.95, alias="CONFIDENCE_THRESHOLD")

    # Logging
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    # Database
    database_url: str = Field(default_factory=_default_db_path, alias="DATABASE_URL")

    # Data paths
    json_data_dir: str = Field(default="data/json", alias="JSON_DATA_DIR")
    json_watch_dir: str = Field(default="data/json/incoming", alias="JSON_WATCH_DIR")
    sample_data_path: str = Field(
        default="data/json/sample_doctors.json", alias="SAMPLE_DATA_PATH"
    )

    @property
    def sqlite_path(self) -> str:
        """Resolve filesystem path from sqlite URL."""
        url = self.database_url
        if url.startswith("sqlite:////"):
            # Absolute path: sqlite:////app/data/db.sqlite → /app/data/db.sqlite
            return "/" + url[len("sqlite:////") :]
        if url.startswith("sqlite:///"):
            # Relative path: sqlite:///data/db.sqlite → data/db.sqlite
            return url[len("sqlite:///") :]
        if url.startswith("sqlite://"):
            return url[len("sqlite://") :]
        return url

    @property
    def has_llm(self) -> bool:
        return bool(self.xai_api_key and self.xai_api_key != "your_xai_api_key_here")


@lru_cache
def get_settings() -> Settings:
    # Prefer project root .env when running outside Docker
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if env_path.exists():
        os.environ.setdefault("DOTENV_PATH", str(env_path))
    return Settings()
