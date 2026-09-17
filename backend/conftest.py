"""Pytest environment defaults.

Tests run without MongoDB or an Anthropic API key: the Motor client is built
lazily by Motor itself (no connection until a call is awaited), and every LLM
test injects a mocked client instead of constructing a real AsyncAnthropic.
"""

import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "claimos_test")

import pytest
from mongomock_motor import AsyncMongoMockClient


@pytest.fixture
def patched_mongo(monkeypatch):
    """Swap every Mongo binding for a mongomock instance.

    server.py and agents.py bind collection names at import time, so patching
    the `database` module alone is not enough — every static importer gets the
    same mock handles.
    """
    import agents
    import database
    import server

    client = AsyncMongoMockClient()
    db = client["claimos_test"]
    # Attribute names carry a _col suffix that the real collection names drop.
    for attr in (
        "policies_col",
        "claims_col",
        "claim_documents_col",
        "counters_col",
        "events_col",
        "seed_state_col",
    ):
        monkeypatch.setattr(database, attr, db[attr.removesuffix("_col")])
    monkeypatch.setattr(database, "db", db)
    monkeypatch.setattr(database, "client", client)
    monkeypatch.setattr(agents, "policies_col", db.policies)
    monkeypatch.setattr(agents, "claims_col", db.claims)
    monkeypatch.setattr(server, "policies_col", db.policies)
    monkeypatch.setattr(server, "claims_col", db.claims)
    monkeypatch.setattr(server, "db", db)
    return db
