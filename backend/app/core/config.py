"""Application settings loaded from environment variables.

Fail fast: required keys are validated at import time via get_settings().
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator
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
    alert_webhook_url: str = ""

    # --- infrastructure ---
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)
    database_pool_timeout: int = Field(default=10, ge=1, le=60)
    gemini_read_timeout_seconds: int = Field(default=300, ge=30, le=600)
    gemini_max_retries: int = Field(default=2, ge=0, le=5)
    scheduler_mode: str = Field(default="internal", pattern="^(internal|external)$")
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    quotas_file: Path = BASE_DIR / "app" / "core" / "quotas.yaml"

    @model_validator(mode="after")
    def validate_environment_security(self) -> "Settings":
        self.job_token = self.job_token.strip()
        self.cors_origins = ",".join(self.cors_origin_list)
        if self.environment == "production":
            missing = ["JOB_TOKEN"] if not self.job_token else []
            if missing:
                raise ValueError(
                    f"production requires non-empty {', '.join(missing)}"
                )
            if "*" in self.cors_origin_list:
                raise ValueError("production CORS_ORIGINS cannot contain '*'")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def load_quotas(self) -> dict[str, ModelQuota]:
        raw = yaml.safe_load(self.quotas_file.read_text(encoding="utf-8"))
        return {name: ModelQuota(**cfg) for name, cfg in raw["models"].items()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
