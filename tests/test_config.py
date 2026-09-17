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
    settings = Settings(
        environment="production", jwt_secret="test-secret", cors_origins=["https://claims.example.com"]
    )
    assert settings.cors_origins == ["https://claims.example.com"]


def test_development_still_allows_wildcard_cors():
    """The wildcard stays legal outside production (dev defaults are unchanged)."""
    settings = Settings(environment="development", cors_origins=["*"])
    assert settings.cors_origins == ["*"]
