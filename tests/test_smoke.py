"""CI smoke tests — substrate task (todo_QNAl5K7v).

The production-readiness audit found tests/ empty; the deep suites (auth,
pipeline, determinism) land with their own tasks. These tests assert the app
boots with real dependencies installed — including the vendored
emergentintegrations stub wheel — and that the API surface is mounted.
"""

import os
import sys
from pathlib import Path

# Env must exist before backend modules import (database.py reads MONGO_URL;
# Motor clients connect lazily, so no live MongoDB is needed for these tests).
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/claimos-test")
os.environ.setdefault("DB_NAME", "claimos-test")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def test_app_boots_and_serves_root():
    """The FastAPI app starts with all deps importable and /api/ serves."""
    client = TestClient(server.app)
    response = client.get("/api/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_core_api_surface_is_mounted():
    """Claims, policies, dashboard, and SSE stream routes are registered."""
    # Derive the route table from the OpenAPI schema rather than iterating
    # app.routes: newer FastAPI wraps include_router() output in a lazy
    # _IncludedRouter object with no .path attribute, so flat enumeration
    # raises AttributeError. openapi() is the public, version-stable surface.
    paths = set(server.app.openapi()["paths"])
    expected = {
        "/api/claims",
        "/api/claims/stream/{claim_id}",
        "/api/policies",
        "/api/policies/lookup",
        "/api/dashboard/stats",
    }
    missing = expected - paths
    assert not missing, f"missing routes: {missing}"


def test_health_probe_target_responds():
    """The endpoint the Dockerfile HEALTHCHECK probes returns 200."""
    client = TestClient(server.app)
    assert client.get("/api/").status_code == 200
