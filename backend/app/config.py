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
    # `environment` precedes cors_origins so the production validator on
    # cors_origins can read it from info.data (pydantic validates fields in
    # definition order and only exposes already-validated fields).
    environment: Literal["development", "staging", "production"] = "development"
    # NoDecode hands the raw env string to the validator below so a plain
    # comma-separated CORS_ORIGINS works (pydantic-settings would otherwise
    # demand JSON for list fields).
    cors_origins: Annotated[list[str], NoDecode] = ["*"]

    # Placeholders for the LLM adapter PR (not yet consumed by the pipeline).
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-20250514"
    llm_request_timeout_seconds: float = 60.0
    anthropic_api_key: str = ""

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
    # Straight-through-processing gate. A claim whose Decision agent reports
    # confidence >= stp_confidence_threshold, with low derived severity and a
    # clean eligibility verdict, auto-finalizes as auto_approved; anything
    # else is escalated to the workbench with the failing legs as the reason.
    stp_confidence_threshold: float = 0.85
    # Severity is derived deterministically (never by the LLM): "low" requires
    # the claimed amount to be at or under the threshold AND the incident type
    # to be in the low-severity set (comma-separated env list).
    stp_low_severity_amount: float = 10_000.0
    stp_low_severity_types: str = "theft,weather_damage,vandalism"

    # Pipeline worker: seconds between queue polls when the queue is empty.
    worker_poll_interval_seconds: float = 1.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept CORS_ORIGINS as a comma-separated string or a JSON list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origins_in_production(cls, value: list[str], info) -> list[str]:
        """Mirror of the jwt_secret guard: production must bind CORS to explicit
        origins, so a wildcard (set explicitly or left as the default) fails boot."""
        if info.data.get("environment") == "production" and "*" in value:
            raise ValueError("CORS_ORIGINS must list explicit origins when ENVIRONMENT=production")
        return value


settings = Settings()
