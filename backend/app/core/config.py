"""Application settings loaded from environment variables.

Fail fast: required keys are validated at import time via get_settings().
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ModelQuota(BaseSettings):
    """Per-model free-tier quota. Numbers live in quotas.yaml, never in code."""

    rpm: int
    rpd: int
    tpm: int


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- required secrets ---
    gemini_api_key: str = Field(min_length=1)
    finmind_token: str = Field(min_length=1)

    # --- optional ---
    openrouter_api_key: str = ""
    job_token: str = ""
    api_token: str = ""
    alert_webhook_url: str = ""

    # --- infrastructure ---
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    scheduler_mode: str = Field(default="internal", pattern="^(internal|external)$")
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    quotas_file: Path = BASE_DIR / "app" / "core" / "quotas.yaml"

    def load_quotas(self) -> dict[str, ModelQuota]:
        raw = yaml.safe_load(self.quotas_file.read_text(encoding="utf-8"))
        return {name: ModelQuota(**cfg) for name, cfg in raw["models"].items()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
