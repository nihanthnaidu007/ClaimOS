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

    # ---- Auth & security (auth backend PR) ----
    # HS256 signing secret for access tokens. MUST be set in any deployed
    # environment; the validator below fails boot in production when empty.
    jwt_secret: str = ""
    # Short-lived access token held in browser memory (never persisted client-side).
    access_token_ttl_minutes: int = 15
    # Long-lived refresh token: opaque random value, stored SHA-256-hashed
    # server-side, delivered as an httpOnly cookie.
    refresh_token_ttl_days: int = 7

    # Registration is invite-only: REGISTER requires an exact INVITE_CODE match.
    # An empty code disables registration entirely (production posture).
    invite_code: str = ""

    # Demo accounts seeded at startup for local development. Passwords are
    # hashed at rest; an empty password skips seeding that role.
    demo_adjuster_email: str = ""
    demo_adjuster_password: str = ""
    demo_customer_email: str = ""
    demo_customer_password: str = ""

    # Refresh/CSRF cookie flags. Secure is forced on outside development
    # (TestClient runs over http, so tests rely on the development default).
    cookie_secure: bool | None = None

    # Rate limits (slowapi syntax). Applied per client IP.
    login_rate_limit: str = "5/minute"
    fnol_rate_limit: str = "10/minute"

    @property
    def refresh_cookie_secure(self) -> bool:
        """Secure cookies outside development unless explicitly overridden."""
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment != "development"

    @field_validator("jwt_secret")
    @classmethod
    def _reject_empty_secret_in_production(cls, value: str, info) -> str:
        if info.data.get("environment") == "production" and not value:
            raise ValueError("JWT_SECRET must be set when ENVIRONMENT=production")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept CORS_ORIGINS as a comma-separated string or a JSON list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
