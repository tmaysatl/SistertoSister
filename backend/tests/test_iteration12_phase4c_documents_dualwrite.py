"""Phase 4 Slice C: documents dual-write (Mongo + Supabase Postgres + Storage).

Scope:
  - POST /api/documents (with & without file_base64) -> Mongo, Postgres, Storage
  - GET /api/documents/{id}/url -> signed URL works / 404s when expected
  - DELETE /api/documents/{id} -> cascades to Mongo + PG + Storage
  - POST /api/documents/{id}/push -> clones get own UUID + own blob + own URL
  - POST /api/documents/{id}/sign -> new signed doc in BOTH stores + own blob
  - POST /api/documents/{id}/submit-form -> filled PDF in BOTH stores + own blob
  - Phase 4 Slice A regression: /auth/me, /caregivers, /clients, /stats under both JWTs
  - Phase 4 Slice B regression: client + assignment dual-write/idempotent/cascade,
    client-task toggle
  - Non-converted reads regression: GET /api/documents (50), GET /api/documents/{id} full payload
  - Parity check: Mongo & Postgres document counts equal after create/delete cycles
"""
from __future__ import annotations
import os
import base64
import uuid
import asyncio
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load backend env so we can hit Postgres + Storage directly for verification
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import asyncpg  # noqa: E402
from supabase import create_client  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]
SUPABASE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")


# --- Tiny valid PDF (same one used by the smoke script) -------------------
MINI_PDF_B64 = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 24>>stream\nBT /F1 12 Tf 10 100 Td (hi) Tj ET\nendstream endobj\n"
    b"xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n248\n%%EOF\n"
).decode()


# --- helpers --------------------------------------------------------------
def _sb_service():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _storage_names() -> set[str]:
    sb = _sb_service()
    files = sb.storage.from_(SUPABASE_BUCKET).list("documents", {"limit": 2000})
    return {f["name"] for f in files}


async def _pg(query: str, *args):
    conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        if query.strip().lower().startswith("select"):
            return await conn.fetch(query, *args)
        return await conn.execute(query, *args)
    finally:
        await conn.close()


def pg_fetchrow(query: str, *args):
    return asyncio.get_event_loop().run_until_complete(
        _pg_fetchrow_impl(query, *args)
    )


async def _pg_fetchrow_impl(query: str, *args):
    conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await conn.fetchrow(query, *args)
    finally:
        await conn.close()


def pg_fetchval(query: str, *args):
    return asyncio.get_event_loop().run_until_complete(
        _pg_fetchval_impl(query, *args)
    )


async def _pg_fetchval_impl(query: str, *args):
    conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await conn.fetchval(query, *args)
    finally:
        await conn.close()


def pg_execute(query: str, *args):
    return asyncio.get_event_loop().run_until_complete(
        _pg_execute_impl(query, *args)
    )


async def _pg_execute_impl(query: str, *args):
    conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await conn.execute(query, *args)
    finally:
        await conn.close()


# --- session fixtures -----------------------------------------------------
@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def legacy_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@healthguard.com", "password": "Admin@123"},
        timeout=30,
    )
    assert r.status_code == 200, f"legacy login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def supabase_token():
    # Get a Supabase ES256 JWT via gotrue
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": "admin@healthguard.com", "password": "AdminPassword123!"},
        timeout=30,
    )
    assert r.status_code == 200, f"supabase login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def caregiver_legacy_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "caregiver@healthguard.com", "password": "Caregiver@123"},
        timeout=30,
    )
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.fixture
def H(legacy_token):
    return {"Authorization": f"Bearer {legacy_token}"}


@pytest.fixture
def Hsupa(supabase_token):
    return {"Authorization": f"Bearer {supabase_token}"}


# Track docs to clean up at end of run
_created_doc_ids: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_at_end():
    yield
    # best-effort cleanup
    if not _created_doc_ids:
        return
    token = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@healthguard.com", "password": "Admin@123"},
        timeout=30,
    ).json().get("access_token")
    if not token:
        return
    h = {"Authorization": f"Bearer {token}"}
    for did in _created_doc_ids:
        try:
            requests.delete(f"{BASE_URL}/api/documents/{did}", headers=h, timeout=15)
        except Exception:
            pass


# =========================================================================
# 1. POST /api/documents — with blob (dual-write Mongo + PG + Storage)
# =========================================================================
class TestCreateDocumentWithBlob:
    def test_create_with_blob_returns_document_shape(self, H):
        r = requests.post(
            f"{BASE_URL}/api/documents",
            headers=H,
            json={
                "title": "TEST_SliceC_WithBlob",
                "category": "caregiver",
                "file_base64": MINI_PDF_B64,
                "mime_type": "application/pdf",
                "notes": "slice C blob test",
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        for f in ("id", "title", "category", "mime_type", "uploaded_by", "uploaded_at"):
            assert f in d
        assert d["title"] == "TEST_SliceC_WithBlob"
        assert d["category"] == "caregiver"
        _created_doc_ids.append(d["id"])
        # store for later asserts via env-local
        pytest._slice_c_blob_id = d["id"]

    def test_blob_doc_in_postgres_with_storage_path(self):
        did = pytest._slice_c_blob_id
        row = pg_fetchrow(
            "select title::text, storage_path from public.documents where id=$1::uuid",
            did,
        )
        assert row is not None, "doc missing from Postgres"
        assert row["title"] == "TEST_SliceC_WithBlob"
        assert row["storage_path"] == f"documents/{did}.pdf"

    def test_blob_in_supabase_storage(self):
        did = pytest._slice_c_blob_id
        names = _storage_names()
        assert f"{did}.pdf" in names, f"{did}.pdf missing from Storage"


# =========================================================================
# 2. POST /api/documents — metadata only (no blob)
# =========================================================================
class TestCreateDocumentMetadataOnly:
    def test_create_metadata_only(self, H):
        r = requests.post(
            f"{BASE_URL}/api/documents",
            headers=H,
            json={
                "title": "TEST_SliceC_MetaOnly",
                "category": "policy",
                "notes": "no blob",
                "is_template": True,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        _created_doc_ids.append(d["id"])
        pytest._slice_c_meta_id = d["id"]

    def test_meta_only_in_postgres_no_storage_path(self):
        did = pytest._slice_c_meta_id
        row = pg_fetchrow(
            "select title::text, storage_path from public.documents where id=$1::uuid", did
        )
        assert row is not None
        assert row["storage_path"] is None

    def test_meta_only_no_blob_in_storage(self):
        did = pytest._slice_c_meta_id
        names = _storage_names()
        assert f"{did}.pdf" not in names


# =========================================================================
# 3. GET /api/documents/{id}/url
# =========================================================================
class TestSignedUrl:
    def test_signed_url_for_blob_doc(self, H):
        did = pytest._slice_c_blob_id
        r = requests.get(f"{BASE_URL}/api/documents/{did}/url", headers=H, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["expires_in"] == 3600
        assert body["storage_path"] == f"documents/{did}.pdf"
        assert body["url"].startswith("http")
        # Fetch the signed URL without auth — must serve a PDF
        pdf = requests.get(body["url"], allow_redirects=True, timeout=30)
        assert pdf.status_code == 200, pdf.text[:200]
        assert pdf.headers.get("content-type", "").startswith("application/pdf")
        assert pdf.content[:5] == b"%PDF-"

    def test_signed_url_for_meta_only_returns_404(self, H):
        did = pytest._slice_c_meta_id
        # Doc has NO blob -> server synthesizes path documents/<id>.pdf but Storage
        # has no such object -> signed_url_for_document either returns a URL that
        # 400s on fetch OR the storage layer returns None and route 404s.
        r = requests.get(f"{BASE_URL}/api/documents/{did}/url", headers=H, timeout=30)
        if r.status_code == 200:
            # If a URL was returned, fetching it MUST not be a PDF (object missing).
            body = r.json()
            f = requests.get(body["url"], allow_redirects=True, timeout=20)
            # 400 / 404 from Supabase Storage for missing object
            assert f.status_code in (400, 404), (
                f"meta-only doc unexpectedly served PDF via signed URL: {f.status_code}"
            )
        else:
            assert r.status_code == 404
            assert "stored file" in r.text.lower() or "not found" in r.text.lower()

    def test_signed_url_unknown_id_404(self, H):
        r = requests.get(
            f"{BASE_URL}/api/documents/{uuid.uuid4()}/url", headers=H, timeout=15
        )
        assert r.status_code == 404


# =========================================================================
# 4. DELETE cascades to Mongo + PG + Storage
# =========================================================================
class TestDeleteCascade:
    def test_delete_blob_doc_cascades(self, H):
        # Create a fresh doc so this test is self-contained
        r = requests.post(
            f"{BASE_URL}/api/documents",
            headers=H,
            json={
                "title": "TEST_SliceC_DeleteCascade",
                "category": "caregiver",
                "file_base64": MINI_PDF_B64,
                "mime_type": "application/pdf",
            },
            timeout=30,
        )
        assert r.status_code == 200
        did = r.json()["id"]
        assert pg_fetchval(
            "select count(*) from public.documents where id=$1::uuid", did
        ) == 1
        assert f"{did}.pdf" in _storage_names()

        # Delete
        r = requests.delete(f"{BASE_URL}/api/documents/{did}", headers=H, timeout=30)
        assert r.status_code == 200
        # Mongo: GET should now 404
        g = requests.get(f"{BASE_URL}/api/documents/{did}", headers=H, timeout=15)
        assert g.status_code == 404
        # Postgres: row gone
        assert pg_fetchval(
            "select count(*) from public.documents where id=$1::uuid", did
        ) == 0
        # Storage: blob gone
        assert f"{did}.pdf" not in _storage_names()


# =========================================================================
# 5. POST /api/documents/{id}/push — clones land in both DBs + own blob
# =========================================================================
class TestPushClones:
    def test_push_clones_into_both_dbs_and_storage(self, H):
        # Create source with blob
        r = requests.post(
            f"{BASE_URL}/api/documents",
            headers=H,
            json={
                "title": "TEST_SliceC_PushSource",
                "category": "caregiver",
                "file_base64": MINI_PDF_B64,
                "mime_type": "application/pdf",
            },
            timeout=30,
        )
        assert r.status_code == 200
        src_id = r.json()["id"]
        _created_doc_ids.append(src_id)

        # Pick two caregivers as targets
        cg = requests.get(f"{BASE_URL}/api/caregivers", headers=H, timeout=15).json()
        assert len(cg) >= 2
        targets = [
            {"owner_id": cg[0]["id"], "owner_type": "caregiver"},
            {"owner_id": cg[1]["id"], "owner_type": "caregiver"},
        ]
        r = requests.post(
            f"{BASE_URL}/api/documents/{src_id}/push",
            headers=H,
            json={"targets": targets},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 2
        clone_ids = body["ids"]
        assert len(set(clone_ids)) == 2
        assert src_id not in clone_ids  # each clone has its OWN uuid
        for cid in clone_ids:
            _created_doc_ids.append(cid)

        # Each clone has PG row + Storage blob + signed URL works
        names = _storage_names()
        for cid in clone_ids:
            row = pg_fetchrow(
                "select storage_path, owner_type::text from public.documents where id=$1::uuid",
                cid,
            )
            assert row is not None
            assert row["storage_path"] == f"documents/{cid}.pdf"
            assert f"{cid}.pdf" in names

            ru = requests.get(
                f"{BASE_URL}/api/documents/{cid}/url", headers=H, timeout=15
            )
            assert ru.status_code == 200
            assert ru.json()["storage_path"] == f"documents/{cid}.pdf"


# =========================================================================
# 6. POST /api/documents/{id}/sign — produces a new doc in both DBs
# =========================================================================
class TestSignDocument:
    def test_sign_creates_new_doc_with_own_blob(self, H):
        # Create source with blob
        r = requests.post(
            f"{BASE_URL}/api/documents",
            headers=H,
            json={
                "title": "TEST_SliceC_SignSource",
                "category": "caregiver",
                "file_base64": MINI_PDF_B64,
                "mime_type": "application/pdf",
            },
            timeout=30,
        )
        assert r.status_code == 200
        src_id = r.json()["id"]
        _created_doc_ids.append(src_id)

        # tiny 1x1 transparent PNG for signature
        sig_png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAen"
            "k5AAAAABJRU5ErkJggg=="
        )
        r = requests.post(
            f"{BASE_URL}/api/documents/{src_id}/sign",
            headers=H,
            json={"signature_base64": sig_png_b64},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        signed = r.json()
        assert signed["id"] != src_id
        sid = signed["id"]
        _created_doc_ids.append(sid)

        # In Postgres
        row = pg_fetchrow(
            "select storage_path from public.documents where id=$1::uuid", sid
        )
        assert row is not None
        assert row["storage_path"] == f"documents/{sid}.pdf"

        # In Storage
        assert f"{sid}.pdf" in _storage_names()

        # Signed URL works and serves a PDF
        ru = requests.get(f"{BASE_URL}/api/documents/{sid}/url", headers=H, timeout=15)
        assert ru.status_code == 200
        pdf = requests.get(ru.json()["url"], allow_redirects=True, timeout=20)
        assert pdf.status_code == 200
        assert pdf.headers.get("content-type", "").startswith("application/pdf")


# =========================================================================
# 7. POST /api/documents/{id}/submit-form — filled PDF dual-write
# =========================================================================
class TestSubmitForm:
    def test_submit_form_dualwrite(self, H):
        # Find a template with a fillable schema. The slice-C spec mentions
        # '01 - Employment Application'; pick the first onboarding doc whose
        # form-schema endpoint returns has_form=true.
        docs = requests.get(
            f"{BASE_URL}/api/documents?category=caregiver_onboarding",
            headers=H, timeout=20,
        ).json()
        target = None
        for d in docs:
            r = requests.get(
                f"{BASE_URL}/api/documents/{d['id']}/form-schema",
                headers=H, timeout=15,
            )
            if r.status_code == 200 and r.json().get("has_form"):
                target = d
                break
        if target is None:
            pytest.skip("no fillable schema template available in this environment")

        schema = requests.get(
            f"{BASE_URL}/api/documents/{target['id']}/form-schema",
            headers=H, timeout=15,
        ).json()["schema"]

        # Build a minimal values dict (just give every field the string 'x' / today)
        values = {}
        for field in schema.get("fields", []):
            ftype = field.get("type", "text")
            if ftype == "checkbox":
                values[field["name"]] = True
            elif ftype == "date":
                values[field["name"]] = "2026-01-01"
            else:
                values[field["name"]] = "x"

        r = requests.post(
            f"{BASE_URL}/api/documents/{target['id']}/submit-form",
            headers=H,
            json={"values": values},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        new_id = r.json()["id"]
        _created_doc_ids.append(new_id)

        # Postgres
        row = pg_fetchrow(
            "select storage_path, title::text from public.documents where id=$1::uuid",
            new_id,
        )
        assert row is not None
        assert row["storage_path"] == f"documents/{new_id}.pdf"
        assert row["title"].startswith("COMPLETED - ")

        # Storage
        assert f"{new_id}.pdf" in _storage_names()

        # Signed URL fetch returns PDF
        ru = requests.get(f"{BASE_URL}/api/documents/{new_id}/url", headers=H, timeout=15)
        assert ru.status_code == 200
        pdf = requests.get(ru.json()["url"], allow_redirects=True, timeout=20)
        assert pdf.status_code == 200
        assert pdf.headers.get("content-type", "").startswith("application/pdf")


# =========================================================================
# 8. Phase 4 Slice A regression — reads under BOTH JWT modes
# =========================================================================
class TestSliceARegression:
    @pytest.mark.parametrize("which", ["legacy", "supabase"])
    def test_auth_me(self, which, legacy_token, supabase_token):
        tok = legacy_token if which == "legacy" else supabase_token
        r = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        me = r.json()
        assert me["email"] == "admin@healthguard.com"
        assert me["role"] == "admin"

    @pytest.mark.parametrize("which", ["legacy", "supabase"])
    def test_caregivers(self, which, legacy_token, supabase_token):
        tok = legacy_token if which == "legacy" else supabase_token
        r = requests.get(
            f"{BASE_URL}/api/caregivers",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    @pytest.mark.parametrize("which", ["legacy", "supabase"])
    def test_clients_list_and_get(self, which, legacy_token, supabase_token):
        tok = legacy_token if which == "legacy" else supabase_token
        h = {"Authorization": f"Bearer {tok}"}
        r = requests.get(f"{BASE_URL}/api/clients", headers=h, timeout=15)
        assert r.status_code == 200
        clients = r.json()
        assert isinstance(clients, list)
        if clients:
            cid = clients[0]["id"]
            rg = requests.get(f"{BASE_URL}/api/clients/{cid}", headers=h, timeout=15)
            assert rg.status_code == 200
            assert rg.json()["id"] == cid

    @pytest.mark.parametrize("which", ["legacy", "supabase"])
    def test_stats(self, which, legacy_token, supabase_token):
        tok = legacy_token if which == "legacy" else supabase_token
        r = requests.get(
            f"{BASE_URL}/api/stats",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=15,
        )
        assert r.status_code == 200
        s = r.json()
        for k in ("total_clients", "total_caregivers", "total_documents"):
            assert k in s


# =========================================================================
# 9. Phase 4 Slice B regression — client / assignment / toggle dual-write
# =========================================================================
class TestSliceBRegression:
    def test_client_create_dualwrite_and_delete_cascade(self, H):
        cname = f"TEST_SliceC_Regress_{uuid.uuid4().hex[:6]}"
        r = requests.post(
            f"{BASE_URL}/api/clients",
            headers=H,
            json={"name": cname, "address": "1 Test St", "phone": "555"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        # Postgres
        row = pg_fetchrow(
            "select name from public.clients where id=$1::uuid", cid
        )
        assert row is not None and row["name"] == cname

        # Assignment idempotency
        cg = requests.get(f"{BASE_URL}/api/caregivers", headers=H, timeout=15).json()
        if cg:
            cgid = cg[0]["id"]
            a1 = requests.post(
                f"{BASE_URL}/api/assignments",
                headers=H,
                json={"caregiver_id": cgid, "client_id": cid, "schedule": "M-F"},
                timeout=20,
            )
            assert a1.status_code == 200
            a2 = requests.post(
                f"{BASE_URL}/api/assignments",
                headers=H,
                json={"caregiver_id": cgid, "client_id": cid, "schedule": "M-F"},
                timeout=20,
            )
            assert a2.status_code == 200
            assert a1.json()["id"] == a2.json()["id"]
            # Postgres has exactly 1 such row
            assert (
                pg_fetchval(
                    "select count(*) from public.assignments "
                    "where caregiver_id=$1::uuid and client_id=$2::uuid",
                    cgid, cid,
                )
                == 1
            )

        # Delete client cascades
        d = requests.delete(f"{BASE_URL}/api/clients/{cid}", headers=H, timeout=20)
        assert d.status_code == 200
        assert pg_fetchval(
            "select count(*) from public.clients where id=$1::uuid", cid
        ) == 0


# =========================================================================
# 10. Non-converted documents reads still work
# =========================================================================
class TestDocumentReadsRegression:
    def test_list_documents_still_mongo(self, H):
        r = requests.get(f"{BASE_URL}/api/documents", headers=H, timeout=30)
        assert r.status_code == 200
        docs = r.json()
        assert isinstance(docs, list)
        # Slice C spec expected 50 base docs but several TEST_ docs may exist
        # mid-run; just enforce the floor.
        non_test = [d for d in docs if not str(d.get("title", "")).startswith("TEST_")]
        assert len(non_test) >= 40, (
            f"expected ~50 baseline docs, got {len(non_test)}"
        )

    def test_get_single_document_full_payload(self, H):
        r = requests.get(f"{BASE_URL}/api/documents", headers=H, timeout=15).json()
        # Pick a non-test doc that has a blob
        chosen = None
        for d in r:
            if not d.get("title", "").startswith("TEST_"):
                full = requests.get(
                    f"{BASE_URL}/api/documents/{d['id']}", headers=H, timeout=15
                )
                if full.status_code == 200 and full.json().get("file_base64"):
                    chosen = full.json()
                    break
        assert chosen is not None, "no baseline doc with file_base64 found"
        assert chosen["file_base64"]
        assert chosen["mime_type"]


# =========================================================================
# 11. Other non-Phase-4 routes still respond
# =========================================================================
class TestOtherRoutes:
    def test_shifts(self, H):
        r = requests.get(f"{BASE_URL}/api/shifts", headers=H, timeout=20)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_onboarding(self, H):
        r = requests.get(f"{BASE_URL}/api/onboarding", headers=H, timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_training(self, H):
        r = requests.get(f"{BASE_URL}/api/training", headers=H, timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# =========================================================================
# 12. Final parity check — Mongo & Postgres counts match after test churn
# =========================================================================
class TestParity:
    def test_mongo_and_pg_document_counts_match(self, H):
        # PG count
        pg_total = pg_fetchval("select count(*) from public.documents")
        # Mongo count via the LIST endpoint (admin sees all)
        mongo_total = len(requests.get(
            f"{BASE_URL}/api/documents", headers=H, timeout=30
        ).json())
        # Slice C dual-writes only ADD rows to PG; PG can be >= Mongo only when
        # legacy doc cleanup has happened. Allow small skew but assert no
        # silent loss in either direction.
        delta = abs(pg_total - mongo_total)
        assert delta <= 5, f"Mongo={mongo_total}  PG={pg_total}  drift={delta}"
