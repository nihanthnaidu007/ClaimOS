"""Document upload tests (spec Tier 3): allowlists, caps, sanitization, storage seam."""

import pytest

from app.config import settings
from app.storage import LocalFsProvider, new_storage_key, sanitize_filename

pytestmark = pytest.mark.asyncio

PDF = b"%PDF-1.4 fake pdf bytes"


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    """Point the process-wide provider at a temp root for the route tests."""
    import app.storage.provider as provider_module

    root = tmp_path / "uploads"
    monkeypatch.setattr(provider_module, "_provider", LocalFsProvider(root=root))
    return root


# ============ filename sanitization ============


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("report.pdf", "report.pdf"),
            ("../../etc/passwd", "passwd"),
            ("..\\..\\evil.exe", "evil.exe"),
            (".htaccess", "htaccess"),
            ("  ..hidden.pdf", "hidden.pdf"),
            ("r\u00e9sum\u00e9.pdf", "r_sum_.pdf"),
            ("weird name (2)~final.PDF", "weird_name__2__final.PDF"),
            ("", "upload"),
            (None, "upload"),
            ("....", "upload"),
        ],
    )
    def test_exact_sanitized_values(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_long_name_keeps_extension_under_cap(self):
        raw = "x" * 200 + ".pdf"
        result = sanitize_filename(raw)
        assert len(result) == 120
        assert result.endswith(".pdf")

    def test_never_returns_separators(self):
        assert "/" not in sanitize_filename("a/b/c.pdf")
        assert "\\" not in sanitize_filename("a\\b\\c.pdf")


# ============ storage provider ============


class TestLocalFsProvider:
    async def test_round_trip_save_and_open(self, tmp_path):
        provider = LocalFsProvider(root=tmp_path)
        stored = await provider.save(
            key="CLM-1/abc-report.pdf", content=PDF, content_type="application/pdf"
        )
        assert stored.storage_key == "CLM-1/abc-report.pdf"
        assert stored.size_bytes == len(PDF)
        assert await provider.open("CLM-1/abc-report.pdf") == PDF

    @pytest.mark.parametrize("key", ["../escape.txt", "/absolute.txt", "a/../../b.txt", ""])
    async def test_rejects_key_escapes(self, tmp_path, key):
        provider = LocalFsProvider(root=tmp_path)
        with pytest.raises(ValueError):
            await provider.save(key=key, content=b"x", content_type="application/pdf")

    def test_new_storage_key_is_unique_and_claim_scoped(self):
        first = new_storage_key("CLM-1", "report.pdf")
        second = new_storage_key("CLM-1", "report.pdf")
        assert first != second
        assert first.startswith("CLM-1/")
        assert first.endswith("report.pdf")


# ============ API surface ============


class TestUploadRoute:
    async def test_upload_stores_blob_metadata_and_event(
        self, patched_mongo, local_storage, make_authenticated_user
    ):
        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.claims.insert_one({"id": "CLM-TEST-1", "status": "pending"})

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.post(
                "/api/claims/CLM-TEST-1/documents",
                headers=headers,
                files={"file": ("adjuster report.pdf", PDF, "application/pdf")},
            )
            listing = client.get("/api/claims/CLM-TEST-1/documents", headers=headers)

        assert response.status_code == 201
        body = response.json()
        assert body["claim_id"] == "CLM-TEST-1"
        assert body["file_name"] == "adjuster_report.pdf"
        assert body["content_type"] == "application/pdf"
        assert body["size_bytes"] == len(PDF)
        assert body["uploaded_by"]

        # Blob landed inside the temp root under the claim's folder.
        stored = [p for p in local_storage.rglob("*") if p.is_file()]
        assert len(stored) == 1

        # Metadata row exists with an integrity digest.
        meta = await patched_mongo.claim_documents.find_one({"claim_id": "CLM-TEST-1"})
        assert meta["document_type"] == "upload"
        assert meta["sha256"]
        listing_body = listing.json()
        assert len(listing_body) == 1
        assert listing_body[0]["file_name"] == "adjuster_report.pdf"

    async def test_rejects_non_allowlisted_content_type(
        self, patched_mongo, local_storage, make_authenticated_user
    ):
        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.claims.insert_one({"id": "CLM-TEST-2", "status": "pending"})

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.post(
                "/api/claims/CLM-TEST-2/documents",
                headers=headers,
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
        assert response.status_code == 415
        assert "application/pdf" in response.json()["detail"]

    async def test_rejects_oversized_file_with_413(
        self, patched_mongo, local_storage, monkeypatch, make_authenticated_user
    ):
        from fastapi.testclient import TestClient
        import server as server_mod

        monkeypatch.setattr(settings, "upload_max_bytes", 100)
        await patched_mongo.claims.insert_one({"id": "CLM-TEST-3", "status": "pending"})

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.post(
                "/api/claims/CLM-TEST-3/documents",
                headers=headers,
                files={"file": ("big.pdf", b"x" * 101, "application/pdf")},
            )
        assert response.status_code == 413

    async def test_rejects_empty_file_with_422(
        self, patched_mongo, local_storage, make_authenticated_user
    ):
        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.claims.insert_one({"id": "CLM-TEST-4", "status": "pending"})

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.post(
                "/api/claims/CLM-TEST-4/documents",
                headers=headers,
                files={"file": ("empty.pdf", b"", "application/pdf")},
            )
        assert response.status_code == 422

    async def test_unknown_claim_404(self, patched_mongo, local_storage, make_authenticated_user):
        from fastapi.testclient import TestClient
        import server as server_mod

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.post(
                "/api/claims/CLM-MISSING/documents",
                headers=headers,
                files={"file": ("a.pdf", PDF, "application/pdf")},
            )
        assert response.status_code == 404

    async def test_requires_authentication(self, patched_mongo, local_storage):
        from fastapi.testclient import TestClient
        import server as server_mod

        with TestClient(server_mod.app):
            response = TestClient(server_mod.app).post(
                "/api/claims/CLM-1/documents",
                files={"file": ("a.pdf", PDF, "application/pdf")},
            )
        assert response.status_code == 401

    async def test_rejects_customer_role(
        self, patched_mongo, local_storage, make_authenticated_user
    ):
        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.claims.insert_one({"id": "CLM-TEST-5", "status": "pending"})

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client, role="customer")
            response = client.post(
                "/api/claims/CLM-TEST-5/documents",
                headers=headers,
                files={"file": ("a.pdf", PDF, "application/pdf")},
            )
        assert response.status_code == 403

    async def test_png_jpeg_allowed(self, patched_mongo, local_storage, make_authenticated_user):
        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.claims.insert_one({"id": "CLM-TEST-6", "status": "pending"})

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            for name, ctype in [("scan.png", "image/png"), ("photo.jpg", "image/jpeg")]:
                response = client.post(
                    "/api/claims/CLM-TEST-6/documents",
                    headers=headers,
                    files={"file": (name, b"bytes", ctype)},
                )
                assert response.status_code == 201, name
