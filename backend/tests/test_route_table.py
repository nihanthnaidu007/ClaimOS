"""Route-table integrity: every (method, path) pair registered exactly once.

A shadowed duplicate — two handlers for the same route, where FastAPI
dispatches to the first registration and the rest are unreachable dead code —
is invisible in behavior and easy to re-introduce. These tests walk the full
app route tree (nested include_router results included) and keep the /api
table duplicate-free (the /claims/{claim_id}/documents pair was exactly such
a shadowed duplicate).
"""

from collections import Counter

import server


def _registered_routes() -> list[tuple[str, str]]:
    """Flatten the app's route tree to (method, path) pairs.

    FastAPI may wrap include_router() results in router objects that carry the
    included router and its prefix on `include_context` — recurse into those
    and accumulate prefixes. On versions that instead copy routes flat, the
    copied routes expose path/methods directly and are collected as-is (their
    paths already carry the full prefix).
    """
    pairs = []

    def _walk(routes, prefix=""):
        for route in routes:
            ctx = getattr(route, "include_context", None)
            if ctx is not None:
                _walk(ctx.included_router.routes, prefix + (ctx.prefix or ""))
            elif getattr(route, "methods", None) and hasattr(route, "path"):
                for method in route.methods:
                    pairs.append((method, prefix + route.path))

    _walk(server.app.routes)
    return pairs


def test_no_shadowed_duplicate_routes():
    pairs = [pair for pair in _registered_routes() if pair[1].startswith("/api/")]
    counts = Counter(pairs)
    duplicates = {pair for pair, count in counts.items() if count > 1}
    assert duplicates == set(), f"shadowed duplicate routes: {sorted(duplicates)}"


def test_documents_routes_are_registered_exactly_once():
    pairs = _registered_routes()
    assert pairs.count(("GET", "/api/claims/{claim_id}/documents")) == 1
    assert pairs.count(("POST", "/api/claims/{claim_id}/documents")) == 1
