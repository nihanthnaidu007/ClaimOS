"""Pytest environment defaults.

Tests run without MongoDB or an Anthropic API key: the Motor client is built
lazily by Motor itself (no connection until a call is awaited), and every LLM
test injects a mocked client instead of constructing a real AsyncAnthropic.
"""

import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "claimos_test")
