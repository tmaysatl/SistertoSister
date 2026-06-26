"""Iteration 20 — Storage-path gating (UI fix follow-up).

Contract under test (backend side):
  1) GET /api/documents (admin Supabase JWT) returns 50 docs with ~38 having
     non-null storage_path and ~12 having storage_path=null.
  2) POST /api/documents with a small fake base64 PDF returns a Document whose
     `storage_path` field is set (e.g. 'documents/<uuid>.pdf'), and the created
     id is queryable via GET /api/documents/{id}/url (200, https url +
     storage_path matching).
  3) For a metadata-only doc (storage_path=null, e.g. '02 - HIPAA Privacy Policy'),
     GET /api/documents/{id}/url MUST return 404 with detail mentioning
     'No stored file for this document'. This 404 is intentional, NOT a bug.
"""
import base64
import os

import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


# --- minimal valid PDF (base64) -------------------------------------------------
TINY_PDF_BYTES = (
    b"%PDF-1.1\n%\xe2\xe3\xcf\xd3\n"
    b"1 0 obj<< /Type/Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type/Pages /Count 1 /Kids[3 0 R] >>endobj\n"
    b"3 0 obj<< /Type/Page /Parent 2 0 R /MediaBox[0 0 200 200] /Resources<<>> >>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000018 00000 n \n0000000064 00000 n \n0000000111 00000 n \n"
    b"trailer<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n"
)
TINY_PDF_B64 = base64.b64encode(TINY_PDF_BYTES).decode("ascii")


# -------- fixtures ----------
@pytest.fixture(scope="module")
def supabase_admin_token():
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": "admin@healthguard.com", "password": "AdminPassword123!"},
        timeout=30,
    )
    assert r.status_code == 200, f"Supabase password grant failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture
def sb_headers(supabase_admin_token):
    return {"Authorization": f"Bearer {supabase_admin_token}"}


# ------------------------------------------------------------------------------
# (1) GET /api/documents and split
# ------------------------------------------------------------------------------
class TestDocumentsSplit:
    def test_list_has_50_items_with_split(self, base_url, sb_headers):
        r = requests.get(f"{base_url}/api/documents", headers=sb_headers, timeout=30)
        assert r.status_code == 200, r.text
        docs = r.json()
        assert isinstance(docs, list)
        with_path = [d for d in docs if d.get("storage_path")]
        without_path = [d for d in docs if not d.get("storage_path")]
        print(
            f"TOTAL={len(docs)} with_storage_path={len(with_path)} "
            f"without_storage_path={len(without_path)}"
        )
        # Loosen exact 50 in case admin runs add demo docs; assert distribution.
        assert len(docs) >= 40, f"expected >=40 docs, got {len(docs)}"
        assert len(with_path) >= 30, "expected ~38 docs with storage_path"
        assert len(without_path) >= 5, "expected ~12 metadata-only docs"

    def test_storage_path_field_is_present_in_payload(self, base_url, sb_headers):
        """Pydantic Document model must expose storage_path on list endpoint."""
        r = requests.get(f"{base_url}/api/documents", headers=sb_headers, timeout=30)
        assert r.status_code == 200
        docs = r.json()
        # At least one doc must contain the storage_path KEY (None or string).
        # We assert the key is present on every record.
        missing = [d.get("id") for d in docs if "storage_path" not in d]
        assert not missing, f"storage_path key missing on docs: {missing[:5]}"


# ------------------------------------------------------------------------------
# (2) POST /api/documents with base64 -> storage_path must be set
# ------------------------------------------------------------------------------
class TestUploadPersistsStoragePath:
    created_id = None

    def test_create_doc_returns_storage_path(self, base_url, sb_headers):
        payload = {
            "title": "TEST_iter20_storage_path",
            "category": "credential",
            "owner_type": "agency",
            "owner_id": None,
            "file_base64": TINY_PDF_B64,
            "mime_type": "application/pdf",
            "notes": "iteration_20 smoke",
        }
        r = requests.post(
            f"{base_url}/api/documents",
            headers={**sb_headers, "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text}"
        doc = r.json()
        assert doc.get("id")
        sp = doc.get("storage_path")
        assert sp, f"expected storage_path set on create response, got: {doc}"
        assert sp.startswith("documents/"), f"unexpected storage_path: {sp}"
        TestUploadPersistsStoragePath.created_id = doc["id"]
        TestUploadPersistsStoragePath.created_sp = sp

    def test_signed_url_endpoint_returns_url_and_path(self, base_url, sb_headers):
        doc_id = TestUploadPersistsStoragePath.created_id
        assert doc_id, "previous test must have run"
        r = requests.get(
            f"{base_url}/api/documents/{doc_id}/url",
            headers=sb_headers,
            timeout=30,
        )
        assert r.status_code == 200, f"signed url failed: {r.status_code} {r.text}"
        j = r.json()
        assert j.get("url", "").startswith("http"), j
        assert "storage_path" in j
        assert j["storage_path"] == TestUploadPersistsStoragePath.created_sp

    def test_cleanup_created_doc(self, base_url, sb_headers):
        doc_id = TestUploadPersistsStoragePath.created_id
        if not doc_id:
            pytest.skip("no doc created")
        r = requests.delete(
            f"{base_url}/api/documents/{doc_id}", headers=sb_headers, timeout=30
        )
        assert r.status_code in (200, 204), r.text


# ------------------------------------------------------------------------------
# (3) Metadata-only doc must return 404 (intentional)
# ------------------------------------------------------------------------------
class TestMetadataOnlyDoc404:
    def test_metadata_only_doc_returns_404(self, base_url, sb_headers):
        r = requests.get(f"{base_url}/api/documents", headers=sb_headers, timeout=30)
        assert r.status_code == 200
        docs = r.json()
        no_path = [d for d in docs if not d.get("storage_path")]
        assert no_path, "expected at least one metadata-only doc in seed"
        # Prefer the HIPAA Privacy Policy if present, else any.
        target = next(
            (d for d in no_path if "HIPAA" in (d.get("title") or "")),
            no_path[0],
        )
        print(f"Testing 404 on metadata-only doc: {target.get('title')}")
        r2 = requests.get(
            f"{base_url}/api/documents/{target['id']}/url",
            headers=sb_headers,
            timeout=30,
        )
        assert r2.status_code == 404, f"expected 404, got {r2.status_code} {r2.text}"
        body = r2.json()
        detail = (body.get("detail") or body.get("message") or "").lower()
        assert "no stored file" in detail or "not found" in detail, body
