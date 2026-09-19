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
    # 180s, not 60s: a full structured-output generation at the eligibility
    # agent's 3000-token budget takes ~90-120s end-to-end under API load
    # (llm_usage latencies); at the old 60s default every attempt died
    # mid-generation and burned the retry cap. Wired into the adapter
    # singleton's client timeout.
    llm_request_timeout_seconds: float = 180.0
    anthropic_api_key: str = ""

    # ---- Auth & security (auth backend PR) ----
    # HS256 signing secret for access tokens. MUST be set in any deployed
    # environment; the validator below fails boot in production when empty.
    # Development falls back to an obviously-non-production secret: pyjwt
    # >= 2.13 raises InvalidKeyError on an empty HMAC key, which turned every
    # login in a JWT_SECRET-less environment into a 500.
    jwt_secret: str = "dev-only-insecure-jwt-secret-change-me"
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
    # Public status portal lookups. Headroom above the status page's own
    # polling (one lookup every few seconds per open tab) while still capping
    # code-guessing per IP.
    status_lookup_rate_limit: str = "60/minute"
    # Access-code recovery (F1): stricter than status lookup because a match
    # triggers a real email (address-harvesting / mail-bomb vector).
    access_code_recovery_rate_limit: str = "3/minute"
    # F4 customer portal uploads per client IP — uploads are heavier than
    # lookups, so this matches the FNOL budget rather than the lookup one.
    portal_upload_rate_limit: str = "10/minute"

    # F5 claim messaging: portal send/list rate limit (own constant, per
    # client IP) and the plain-text body cap enforced in app.claim_messages.
    portal_message_rate_limit: str = "10/minute"
    claim_message_max_chars: int = 2000

    # ---- Email delivery (F1) ----
    # Customer-email channel: "console" logs structured events (the dev/CI
    # default — byte-identical to the pre-email behavior); "smtp" delivers
    # real email over SMTP.
    email_provider: Literal["console", "smtp"] = "console"
    # SMTP connection details (required when email_provider="smtp").
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # STARTTLS on the plain connection (port 587) by default; smtp_ssl
    # switches to implicit TLS (port 465).
    smtp_use_tls: bool = True
    smtp_ssl: bool = False
    # Explicit kill switch — the validator below accepts either a working
    # email configuration or this flag in production, never silence.
    email_disabled: bool = False

    @field_validator("email_disabled")
    @classmethod
    def _production_requires_email_plan(cls, value: bool, info) -> bool:
        """Production must either deliver email (SMTP host and sender
        configured) or explicitly disable it — a default config that silently
        sends nothing must not boot (AC-1.4)."""
        if info.data.get("environment") != "production" or value:
            return value
        configured = (
            info.data.get("email_provider") == "smtp"
            and bool(info.data.get("smtp_host"))
            and bool(info.data.get("smtp_from"))
        )
        if configured:
            return value
        raise ValueError(
            "ENVIRONMENT=production requires EMAIL_PROVIDER=smtp with SMTP_HOST and SMTP_FROM "
            "set, or an explicit EMAIL_DISABLED=true"
        )

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

    # Intake flag threshold (high_amount flag). The flag never invalidates a
    # submission; it adds risk points and routes to human review.
    intake_flag_amount_threshold: float = 500_000.0

    # Ops analytics SLA targets (Tier 3). Hours allowed from submission to
    # decision per derived severity; breaches surface in the ops dashboard.
    sla_low_hours: float = 48.0
    sla_elevated_hours: float = 24.0

    # Document uploads (Tier 3). Files land under upload_dir (a mounted
    # volume in deployments); allowlist is a comma-separated content-type list.
    upload_dir: str = "/data/uploads"
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_allowed_content_types: str = "application/pdf,image/png,image/jpeg"

    # Pipeline worker: seconds between queue polls when the queue is empty.
    worker_poll_interval_seconds: float = 1.0

    # ---- Adjuster workbench ----
    # SLA hours per derived severity ("severity:hours" pairs, comma-separated).
    # Severity vocabulary is the STP gate's binary one (app.stp): low | elevated.
    sla_hours_per_severity: str = "low:72,elevated:24"
    # Fallback SLA for a severity missing from the mapping above.
    sla_default_hours: float = 48.0
    # Fraction of the SLA target past which a queue row shows at-risk (amber);
    # past 100% it is breached (red).
    sla_at_risk_fraction: float = 0.75
    # SLA escalation threshold (spec F9): a multiplier on the severity's SLA
    # window. 1.0 (the default) escalates exactly at-breach; 1.5 escalates
    # only when the claim is 50% past its window. The validator below rejects
    # non-positive values so a typo'd env var cannot escalate everything at
    # birth (or nothing ever).
    sla_escalation_factor: float = 1.0
    # Claim assignment (spec F10). "round_robin" assigns every new claim to the
    # active adjuster with the oldest last-assignment time; "none" leaves claims
    # unassigned. Typed Literal, so a typo'd env value fails boot loudly instead
    # of silently disabling assignment.
    auto_assign: Literal["round_robin", "none"] = "round_robin"
    # Workbench SSE stream: seconds between queue re-reads on an open stream.
    workbench_stream_interval_seconds: float = 2.0

    @field_validator("sla_escalation_factor")
    @classmethod
    def _require_positive_escalation_factor(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("SLA_ESCALATION_FACTOR must be a positive number (multiplier on the SLA window)")
        return value

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
