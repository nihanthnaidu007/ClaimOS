"""Central application settings.

Single source of truth for environment configuration. Replaces the scattered
os.environ/os.getenv reads (and the import-time KeyError on missing MONGO_URL)
called out in the production-readiness audit.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core infrastructure. Defaults keep the module importable (tests, CI);
    # /api/ready surfaces actual database connectivity at runtime.
    mongo_url: str = "mongodb://127.0.0.1:27017"
    db_name: str = "claimos"
    # NoDecode hands the raw env string to the validator below so a plain
    # comma-separated CORS_ORIGINS works (pydantic-settings would otherwise
    # demand JSON for list fields).
    cors_origins: Annotated[list[str], NoDecode] = ["*"]
    environment: Literal["development", "staging", "production"] = "development"

    # Placeholders for the LLM adapter PR (not yet consumed by the pipeline).
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-20250514"
    llm_request_timeout_seconds: float = 60.0
    anthropic_api_key: str = ""
    # Legacy Emergent platform credential. The adapter PR retires this variable;
    # it stays readable here so the current pipeline keeps working unchanged.
    emergent_llm_key: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept CORS_ORIGINS as a comma-separated string or a JSON list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
