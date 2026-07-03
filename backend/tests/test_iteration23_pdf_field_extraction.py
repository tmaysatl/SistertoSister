"""Iteration 23 — Phase 1 backend: PDF AcroForm field extraction.

Covers:
  * module imports (`pdf_parser`)
  * unit tests for extract_acroform_fields / extract_fields_from_text / parse_pdf
  * upload hook writing `field_schemas`
  * GET /api/documents/{id}/schema (auth, 404, lazy backfill, non-PDF empty envelope)
  * regression: /form-schema endpoint still works and is separate
  * cleanup: DELETE document endpoint
"""
from __future__ import annotations

import base64
import io
import os
import sys
import time
import uuid

import pytest
import requests
from pymongo import MongoClient

# Import parser directly from backend package
sys.path.insert(0, "/app/backend")
from pdf_parser import (  # noqa: E402
    extract_acroform_fields,
    extract_fields_from_text,
    parse_pdf,
)

BASE_URL = os.environ.get(
    "EXPO_BACKEND_URL",
    "https://audit-prep-hub.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@healthguard.com"
ADMIN_PASSWORD = "Admin@123"
SAMPLE_PDF_URL = (
    "https://customer-assets.emergentagent.com/job_audit-prep-hub/"
    "artifacts/7n429w5d_SKilleRN-Fillable.pdf"
)
SAMPLE_PDF_PATH = "/tmp/skill.pdf"
LEGACY_DOC_ID = "16745d1d-22a7-4912-adbf-acc588192a01"

# ---------- fixtures ----------
@pytest.fixture(scope="module")
def sample_pdf_path() -> str:
    if not os.path.exists(SAMPLE_PDF_PATH) or os.path.getsize(SAMPLE_PDF_PATH) < 1000:
        r = requests.get(SAMPLE_PDF_URL, timeout=60)
        r.raise_for_status()
        with open(SAMPLE_PDF_PATH, "wb") as f:
            f.write(r.content)
    return SAMPLE_PDF_PATH


@pytest.fixture(scope="module")
def admin_token() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def mongo_db():
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "healthguard_db")
    return MongoClient(mongo_url)[db_name]


@pytest.fixture(scope="module")
def created_pdf_doc(sample_pdf_path, auth_headers):
    """Upload the SKilleRN PDF as a document and yield its id.

    Cleanup at teardown: DELETE the document.
    """
    with open(sample_pdf_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "title": "TEST_Phase1 e2e SKilleRN",
        "category": "credential",
        "mime_type": "application/pdf",
        "file_base64": b64,
    }
    r = requests.post(f"{API}/documents", json=payload, headers=auth_headers, timeout=60)
    assert r.status_code == 200, f"upload failed: {r.status_code} {r.text[:400]}"
    doc = r.json()
    assert "id" in doc
    doc_id = doc["id"]
    yield doc_id
    # cleanup
    try:
        requests.delete(f"{API}/documents/{doc_id}", headers=auth_headers, timeout=15)
    except Exception:
        pass


# ---------- 1. module unit ----------
class TestModuleImports:
    def test_symbols_importable(self):
        assert callable(extract_acroform_fields)
        assert callable(extract_fields_from_text)
        assert callable(parse_pdf)


# ---------- 2. parser unit — AcroForm ----------
class TestParserAcroForm:
    def test_parse_pdf_returns_182_fields(self, sample_pdf_path):
        fields = parse_pdf(sample_pdf_path)
        assert isinstance(fields, list)
        assert len(fields) == 182

    def test_every_field_has_required_keys(self, sample_pdf_path):
        fields = parse_pdf(sample_pdf_path)
        needed = {"field_name", "field_type", "page", "position",
                  "options", "required", "value", "source"}
        for f in fields:
            missing = needed - set(f.keys())
            assert not missing, f"missing keys: {missing} in {f}"

    def test_field_type_distinct_values(self, sample_pdf_path):
        fields = parse_pdf(sample_pdf_path)
        types = {f["field_type"] for f in fields}
        assert types == {"text", "checkbox", "radio", "signature"}, types

    def test_positions_are_valid_rects(self, sample_pdf_path):
        fields = parse_pdf(sample_pdf_path)
        for f in fields:
            p = f["position"]
            assert p["x0"] < p["x1"], f"x rect invalid: {p} for {f['field_name']}"
            assert p["y0"] < p["y1"], f"y rect invalid: {p} for {f['field_name']}"

    def test_all_sources_are_acroform(self, sample_pdf_path):
        fields = parse_pdf(sample_pdf_path)
        assert all(f["source"] == "acroform" for f in fields)

    def test_last_name_field_present(self, sample_pdf_path):
        fields = parse_pdf(sample_pdf_path)
        hits = [f for f in fields
                if f["field_name"] == "Last Name" and f["field_type"] == "text"
                and f["page"] == 1]
        assert hits, "expected 'Last Name' text field on page 1"

    def test_extract_acroform_equals_parse_pdf(self, sample_pdf_path):
        a = extract_acroform_fields(sample_pdf_path)
        b = parse_pdf(sample_pdf_path)
        assert a == b, "parse_pdf must prefer acroform path when widgets exist"


# ---------- 3. parser unit — flat PDF text-heuristic fallback ----------
class TestParserTextHeuristic:
    @pytest.fixture(scope="class")
    def flat_pdf_path(self):
        # Build a flat (no-widget) PDF using pymupdf.
        import pymupdf
        path = "/tmp/flat.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        text = (
            "Full Name: ______________\n"
            "Date of Birth ___/___/___\n"
            "hh YES hh NO Are you 18 or older?\n"
        )
        page.insert_text((72, 100), text, fontsize=12)
        doc.save(path)
        doc.close()
        return path

    def test_flat_pdf_has_no_widgets(self, flat_pdf_path):
        assert extract_acroform_fields(flat_pdf_path) == []

    def test_text_heuristic_returns_text_fields(self, flat_pdf_path):
        fields = extract_fields_from_text(flat_pdf_path)
        text_names = [f["field_name"] for f in fields if f["field_type"] == "text"]
        # At least 'Full Name' and 'Date of Birth' should be detected
        assert any("Full Name" in n for n in text_names), text_names
        assert any("Date of Birth" in n for n in text_names), text_names
        # source flag
        for f in fields:
            assert f["source"] == "text-heuristic"
        assert len([f for f in fields if f["field_type"] == "text"]) >= 2

    def test_parse_pdf_falls_back_to_text(self, flat_pdf_path):
        fields = parse_pdf(flat_pdf_path)
        assert fields, "parse_pdf should fall back to text heuristic for flat PDF"
        assert all(f["source"] == "text-heuristic" for f in fields)


# ---------- 4. upload hook writes field_schemas ----------
class TestUploadHook:
    def test_upload_stores_schema_row(self, created_pdf_doc, mongo_db):
        # give the fire-and-forget hook a beat (it's awaited but still)
        doc_id = created_pdf_doc
        row = mongo_db.field_schemas.find_one({"document_id": doc_id})
        assert row is not None, "field_schemas row should exist after upload"
        assert row["field_count"] == 182, row["field_count"]
        assert row["source"] == "acroform"


# ---------- 5. GET /schema endpoint ----------
class TestSchemaEndpoint:
    def test_get_schema_success(self, created_pdf_doc, auth_headers):
        doc_id = created_pdf_doc
        r = requests.get(f"{API}/documents/{doc_id}/schema",
                         headers=auth_headers, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["document_id"] == doc_id
        assert body["field_count"] == 182
        assert body["source"] == "acroform"
        assert body["parser_version"] == "1.0"
        assert "extracted_at" in body
        assert isinstance(body["fields"], list)
        assert len(body["fields"]) == 182
        needed = {"field_name", "field_type", "page", "position",
                  "options", "required", "value", "source"}
        for f in body["fields"]:
            assert needed <= set(f.keys())
            p = f["position"]
            assert {"x0", "y0", "x1", "y1"} <= set(p.keys())
            assert isinstance(f["options"], list)
            assert isinstance(f["required"], bool)

    def test_get_schema_missing_doc_404(self, auth_headers):
        r = requests.get(f"{API}/documents/does-not-exist-123/schema",
                         headers=auth_headers, timeout=15)
        assert r.status_code == 404, r.status_code
        body = r.json()
        assert body.get("detail") == "Document not found"

    def test_get_schema_requires_auth(self, created_pdf_doc):
        r = requests.get(f"{API}/documents/{created_pdf_doc}/schema", timeout=15)
        assert r.status_code in (401, 403), r.status_code

    def test_lazy_backfill_after_delete(self, created_pdf_doc, auth_headers, mongo_db):
        doc_id = created_pdf_doc
        # Remove the cached row
        mongo_db.field_schemas.delete_one({"document_id": doc_id})
        assert mongo_db.field_schemas.find_one({"document_id": doc_id}) is None
        # Re-GET must re-extract from stored file_base64
        r = requests.get(f"{API}/documents/{doc_id}/schema",
                         headers=auth_headers, timeout=30)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["field_count"] == 182, body["field_count"]
        assert body["source"] == "acroform"


# ---------- 6. non-PDF doc returns empty envelope ----------
class TestNonPdfDoc:
    def test_non_pdf_returns_empty_schema(self, auth_headers, mongo_db):
        # 1x1 png (tiny valid)
        png = base64.b64encode(bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A"
            "49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
        )).decode("ascii")
        payload = {
            "title": f"TEST_nonpdf_{uuid.uuid4().hex[:8]}",
            "category": "credential",
            "mime_type": "image/png",
            "file_base64": png,
        }
        r = requests.post(f"{API}/documents", json=payload,
                          headers=auth_headers, timeout=30)
        assert r.status_code == 200, r.text[:300]
        doc_id = r.json()["id"]
        try:
            r2 = requests.get(f"{API}/documents/{doc_id}/schema",
                              headers=auth_headers, timeout=15)
            assert r2.status_code == 200, r2.text[:300]
            body = r2.json()
            assert body["field_count"] == 0
            assert body["source"] == "empty"
        finally:
            requests.delete(f"{API}/documents/{doc_id}",
                            headers=auth_headers, timeout=15)


# ---------- 7. regression: /form-schema still works ----------
class TestFormSchemaRegression:
    def test_legacy_form_schema_endpoint(self, auth_headers):
        r = requests.get(f"{API}/documents/{LEGACY_DOC_ID}/form-schema",
                         headers=auth_headers, timeout=15)
        # Endpoint must still 200 (or 404 if seed doc absent, but per spec should exist).
        # Per problem: "must still return 200 for the seeded '01 - Employment Application' doc"
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("has_form") is True
        assert "schema" in body

    def test_new_schema_endpoint_separate_from_form_schema(self, auth_headers):
        # Both should be accessible independently for the same doc if it's a PDF.
        r_new = requests.get(f"{API}/documents/{LEGACY_DOC_ID}/schema",
                             headers=auth_headers, timeout=30)
        assert r_new.status_code == 200
        # The new endpoint returns the extraction envelope with field_count
        assert "field_count" in r_new.json()
        assert "parser_version" in r_new.json()


# ---------- 8. metadata-only upload should not crash ----------
class TestMetadataOnlyUpload:
    def test_no_file_base64_upload_ok(self, auth_headers, mongo_db):
        payload = {
            "title": f"TEST_meta_{uuid.uuid4().hex[:8]}",
            "category": "credential",
            "mime_type": "application/pdf",
            # no file_base64
        }
        r = requests.post(f"{API}/documents", json=payload,
                          headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]
        doc_id = r.json()["id"]
        try:
            row = mongo_db.field_schemas.find_one({"document_id": doc_id})
            # row is absent OR field_count == 0 — both are acceptable per spec
            if row is not None:
                assert row.get("field_count", 0) == 0
        finally:
            requests.delete(f"{API}/documents/{doc_id}",
                            headers=auth_headers, timeout=15)


# ---------- 9. cleanup: DELETE works ----------
class TestCleanup:
    def test_delete_document(self, auth_headers, sample_pdf_path):
        # Upload a throwaway then delete it explicitly here.
        with open(sample_pdf_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        r = requests.post(f"{API}/documents",
                         json={"title": f"TEST_delete_{uuid.uuid4().hex[:6]}",
                               "category": "credential",
                               "mime_type": "application/pdf",
                               "file_base64": b64},
                         headers=auth_headers, timeout=60)
        assert r.status_code == 200
        doc_id = r.json()["id"]
        d = requests.delete(f"{API}/documents/{doc_id}",
                            headers=auth_headers, timeout=15)
        assert d.status_code == 200, f"{d.status_code} {d.text[:300]}"
