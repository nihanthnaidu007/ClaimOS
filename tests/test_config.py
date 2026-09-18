"""Settings production guards — verification remediation F-5.

Settings is constructed with explicit kwargs throughout: init arguments
outrank environment variables and backend/.env in pydantic-settings, so the
assertions hold regardless of the surrounding shell, the smoke suite's env
defaults, or a developer's local backend/.env.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings


def test_production_boot_fails_with_wildcard_cors():
    """ENVIRONMENT=production with CORS_ORIGINS resolving to ['*'] refuses to boot."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment="production", jwt_secret="test-secret", cors_origins=["*"])
    assert "CORS_ORIGINS must list explicit origins" in str(excinfo.value)


def test_production_boot_succeeds_with_explicit_origins():
    """An explicit origin list boots cleanly in production."""
    # email_disabled=True satisfies the F1 email-plan guard so this test stays
    # focused on the CORS invariant it exists to prove.
    settings = Settings(
        environment="production",
        jwt_secret="test-secret",
        cors_origins=["https://claims.example.com"],
        email_disabled=True,
    )
    assert settings.cors_origins == ["https://claims.example.com"]


def test_development_still_allows_wildcard_cors():
    """The wildcard stays legal outside production (dev defaults are unchanged)."""
    settings = Settings(environment="development", cors_origins=["*"])
    assert settings.cors_origins == ["*"]


# ---- Email plan guard (F1, AC-1.4) ----


def test_production_requires_smtp_config_or_explicit_disable():
    """Production with no email plan refuses to boot with a clear error."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment="production", jwt_secret="test-secret", cors_origins=["https://claims.example.com"])
    assert "EMAIL_PROVIDER=smtp" in str(excinfo.value)
    assert "EMAIL_DISABLED" in str(excinfo.value)


def test_production_boots_with_smtp_configured():
    """A production config with SMTP host and sender set is a valid email plan."""
    settings = Settings(
        environment="production",
        jwt_secret="test-secret",
        cors_origins=["https://claims.example.com"],
        email_provider="smtp",
        smtp_host="smtp.example.com",
        smtp_from="claims@example.com",
    )
    assert settings.email_provider == "smtp"


def test_production_boots_with_explicit_email_disabled():
    """EMAIL_DISABLED=true is an accepted plan: silence is explicit, not default."""
    settings = Settings(
        environment="production",
        jwt_secret="test-secret",
        cors_origins=["https://claims.example.com"],
        email_disabled=True,
    )
    assert settings.email_disabled is True


def test_development_boots_without_email_plan():
    """Dev/CI defaults (console driver) never trigger the production guard."""
    settings = Settings()  # environment defaults to development
    assert settings.email_provider == "console"
    assert settings.email_disabled is False
