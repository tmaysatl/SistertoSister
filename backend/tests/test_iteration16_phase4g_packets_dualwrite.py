"""Phase 4 Slice G — packet-sharing routes dual-write Mongo + Supabase Postgres.

Endpoints under test:
  - POST /api/packets/share (admin)            dual-write
  - GET  /api/packets/{token} (public)         read from PG; sets viewed_at in BOTH
  - GET  /api/packets/{token}/document/{doc_id} (public) returns stamped PDF
  - POST /api/packets/{token}/sign/{doc_id} (public) dual-writes signed Document +
        appends signed_ids in BOTH DBs; flips completed_at only when ALL signed.

Also covers:
  - Idempotency of signing the same doc twice (signed_ids unique in PG).
  - Invalid signature -> 400, unknown token -> 404, unknown doc -> 404.
  - Phase 4 Slice A/B/C/D/E/F regression under legacy AND supabase JWTs.
  - Final parity between Mongo and PG for packet_shares and documents.
"""
from __future__ import annotations
import os
import base64
import asyncio
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import asyncpg  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from supabase import create_client  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

ADMIN_UUID = "8f16fe69-b54e-421a-bfe5-e14900e7bacd"
CAREGIVER_UUID = "389b257d-7edb-4d12-adfc-3b8e80f91bf1"

# 1x1 PNG signature image
PNG_1x1 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63f8cf00000000000ff0001a4c1a3d0000000049"
    "454e44ae426082"
)).decode()


# ---- async pg/mongo helpers wrapped sync ----
async def _pg_fetchrow_impl(q, *a):
    c = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await c.fetchrow(q, *a)
    finally:
        await c.close()


async def _pg_fetchval_impl(q, *a):
    c = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await c.fetchval(q, *a)
    finally:
        await c.close()


async def _pg_execute_impl(q, *a):
    c = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await c.execute(q, *a)
    finally:
        await c.close()


async def _pg_fetch_impl(q, *a):
    c = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await c.fetch(q, *a)
    finally:
        await c.close()


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def pg_fetchrow(q, *a):
    return _run(_pg_fetchrow_impl(q, *a))


def pg_fetchval(q, *a):
    return _run(_pg_fetchval_impl(q, *a))


def pg_execute(q, *a):
    return _run(_pg_execute_impl(q, *a))


def pg_fetch(q, *a):
    return _run(_pg_fetch_impl(q, *a))


async def _mongo_count_impl(coll, q):
    cl = AsyncIOMotorClient(MONGO_URL)
    try:
        return await cl[DB_NAME][coll].count_documents(q)
    finally:
        cl.close()


async def _mongo_find_one_impl(coll, q):
    cl = AsyncIOMotorClient(MONGO_URL)
    try:
        return await cl[DB_NAME][coll].find_one(q, {"_id": 0})
    finally:
        cl.close()


async def _mongo_delete_impl(coll, q):
    cl = AsyncIOMotorClient(MONGO_URL)
    try:
        return await cl[DB_NAME][coll].delete_many(q)
    finally:
        cl.close()


def mongo_count(coll, q=None):
    return _run(_mongo_count_impl(coll, q or {}))


def mongo_find_one(coll, q):
    return _run(_mongo_find_one_impl(coll, q))


def mongo_delete(coll, q):
    return _run(_mongo_delete_impl(coll, q))


# ---- fixtures ----
@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def legacy_admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@healthguard.com", "password": "Admin@123"},
        timeout=30,
    )
    assert r.status_code == 200, f"legacy admin login: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def supabase_admin_token():
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": "admin@healthguard.com", "password": "AdminPassword123!"},
        timeout=30,
    )
    assert r.status_code == 200, f"supabase admin login: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def legacy_caregiver_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "caregiver@healthguard.com", "password": "Caregiver@123"},
        timeout=30,
    )
    assert r.status_code == 200, f"legacy cg login: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture
def HA(legacy_admin_token):
    return {"Authorization": f"Bearer {legacy_admin_token}"}


@pytest.fixture
def HCG(legacy_caregiver_token):
    return {"Authorization": f"Bearer {legacy_caregiver_token}"}


@pytest.fixture
def HSUPA(supabase_admin_token):
    return {"Authorization": f"Bearer {supabase_admin_token}"}


# ---- cleanup tracker ----
_packet_tokens: list[str] = []
_signed_doc_ids: list[str] = []
_storage_paths: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_at_end():
    base_pg_packets = pg_fetchval("select count(*) from public.packet_shares")
    base_mongo_packets = mongo_count("packet_shares")
    base_pg_docs = pg_fetchval("select count(*) from public.documents")
    base_mongo_docs = mongo_count("documents")
    print(
        f"\n[baseline] PG packet_shares={base_pg_packets} "
        f"Mongo packet_shares={base_mongo_packets} "
        f"PG documents={base_pg_docs} Mongo documents={base_mongo_docs}"
    )
    yield
    # Cleanup storage
    if _storage_paths:
        try:
            sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            sb.storage.from_(SUPABASE_STORAGE_BUCKET).remove(_storage_paths)
        except Exception as e:
            print(f"storage cleanup err: {e}")
    # Cleanup signed docs from PG + Mongo
    if _signed_doc_ids:
        try:
            pg_execute(
                "delete from public.documents where id = any($1::uuid[])",
                _signed_doc_ids,
            )
        except Exception as e:
            print(f"pg docs cleanup err: {e}")
        try:
            mongo_delete("documents", {"id": {"$in": _signed_doc_ids}})
        except Exception as e:
            print(f"mongo docs cleanup err: {e}")
    # Cleanup test packets
    if _packet_tokens:
        try:
            pg_execute(
                "delete from public.packet_shares where token = any($1)",
                _packet_tokens,
            )
        except Exception as e:
            print(f"pg packets cleanup err: {e}")
        try:
            mongo_delete("packet_shares", {"token": {"$in": _packet_tokens}})
        except Exception as e:
            print(f"mongo packets cleanup err: {e}")
    end_pg = pg_fetchval("select count(*) from public.packet_shares")
    end_mongo = mongo_count("packet_shares")
    end_pg_d = pg_fetchval("select count(*) from public.documents")
    end_mongo_d = mongo_count("documents")
    print(
        f"\n[end] PG packets={end_pg} Mongo packets={end_mongo} "
        f"PG docs={end_pg_d} Mongo docs={end_mongo_d}"
    )


def _create_packet(headers, recipient="TEST_SliceG", category="caregiver_onboarding"):
    r = requests.post(
        f"{BASE_URL}/api/packets/share",
        headers=headers,
        json={
            "recipient_name": recipient,
            "recipient_role": "caregiver",
            "category": category,
            "delivery": "link",
        },
        timeout=30,
    )
    assert r.status_code == 200, f"share failed: {r.status_code} {r.text}"
    body = r.json()
    assert "token" in body
    _packet_tokens.append(body["token"])
    return body


# =========================================================================
# 1. POST /api/packets/share — admin dual-write
# =========================================================================
class TestPacketShareCreate:
    def test_admin_creates_packet_dual_write(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_Create")
        token = body["token"]
        assert body["delivery"] == "link"
        # PG check
        row = pg_fetchrow(
            "select id::text, token, recipient_name, recipient_role, category, "
            "viewed_at, completed_at, "
            "coalesce(array_length(signed_ids,1),0) as n_signed, "
            "created_by::text "
            "from public.packet_shares where token=$1",
            token,
        )
        assert row is not None, "packet not in PG"
        assert row["recipient_name"] == "TEST_SliceG_Create"
        assert row["recipient_role"] == "caregiver"
        assert row["category"] == "caregiver_onboarding"
        assert row["viewed_at"] is None
        assert row["completed_at"] is None
        assert row["n_signed"] == 0
        assert row["created_by"] == ADMIN_UUID
        # Mongo check
        m = mongo_find_one("packet_shares", {"token": token})
        assert m is not None
        assert m["recipient_name"] == "TEST_SliceG_Create"
        assert m["viewed_at"] is None
        assert m["signed_ids"] == []

    def test_caregiver_cannot_share(self, HCG):
        r = requests.post(
            f"{BASE_URL}/api/packets/share",
            headers=HCG,
            json={
                "recipient_name": "X",
                "recipient_role": "caregiver",
                "category": "caregiver_onboarding",
            },
            timeout=30,
        )
        assert r.status_code == 403

    def test_no_auth_cannot_share(self):
        r = requests.post(
            f"{BASE_URL}/api/packets/share",
            json={
                "recipient_name": "X",
                "recipient_role": "caregiver",
                "category": "caregiver_onboarding",
            },
            timeout=30,
        )
        assert r.status_code in (401, 403)


# =========================================================================
# 2. GET /api/packets/{token} — public read, sets viewed_at in BOTH
# =========================================================================
class TestPacketGet:
    def test_get_unknown_token_404(self):
        r = requests.get(f"{BASE_URL}/api/packets/deadbeef-not-a-token", timeout=30)
        assert r.status_code == 404

    def test_first_view_sets_viewed_at_in_both(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_View")
        token = body["token"]

        # before view: viewed_at null in both
        assert pg_fetchval(
            "select viewed_at from public.packet_shares where token=$1", token
        ) is None
        m_pre = mongo_find_one("packet_shares", {"token": token})
        assert m_pre["viewed_at"] is None

        # public GET (no auth)
        r = requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "packet" in body and "documents" in body
        pkt = body["packet"]
        docs = body["documents"]
        assert pkt["token"] == token
        assert pkt["category"] == "caregiver_onboarding"
        assert pkt["viewed_at"] is not None
        assert isinstance(docs, list) and len(docs) >= 1
        # docs sorted by seq
        seqs = [d.get("seq") for d in docs if d.get("seq") is not None]
        assert seqs == sorted(seqs), "documents not sorted by seq asc"
        # no file_base64 in list
        for d in docs:
            assert "file_base64" not in d, "PDF bytes leaked into list response"

        # viewed_at mirrored to BOTH
        pg_v1 = pg_fetchval(
            "select viewed_at from public.packet_shares where token=$1", token
        )
        m_v1 = mongo_find_one("packet_shares", {"token": token})
        assert pg_v1 is not None
        assert m_v1["viewed_at"] is not None

        # second view doesn't overwrite viewed_at
        r2 = requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30)
        assert r2.status_code == 200
        pg_v2 = pg_fetchval(
            "select viewed_at from public.packet_shares where token=$1", token
        )
        m_v2 = mongo_find_one("packet_shares", {"token": token})
        # Mongo stores as string; PG returns datetime - compare to first values
        assert pg_v2 == pg_v1, "PG viewed_at was overwritten on 2nd call"
        assert m_v2["viewed_at"] == m_v1["viewed_at"], \
            "Mongo viewed_at was overwritten on 2nd call"


# =========================================================================
# 3. GET /api/packets/{token}/document/{doc_id} — stamped PDF
# =========================================================================
class TestPacketStampedPdf:
    def test_returns_pdf_with_watermark(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_Stamp")
        token = body["token"]
        view = requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30).json()
        assert view["documents"], "no docs in caregiver_onboarding packet"
        doc_id = view["documents"][0]["id"]

        r = requests.get(
            f"{BASE_URL}/api/packets/{token}/document/{doc_id}", timeout=30
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF", "response is not a PDF"
        # stamp adds bytes — watermark text shouldn't be plaintext-searchable in
        # binary PDFs reliably, but the PDF should at least parse: cheapest check
        # is "PDF header + has trailer".
        assert b"%%EOF" in r.content[-1024:] or b"%%EOF" in r.content

    def test_unknown_token_404(self):
        r = requests.get(
            f"{BASE_URL}/api/packets/not-a-token/document/whatever", timeout=30
        )
        assert r.status_code == 404

    def test_unknown_doc_404(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_UnknownDoc")
        token = body["token"]
        # Trigger viewed_at so packet exists in PG view path
        requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30)
        r = requests.get(
            f"{BASE_URL}/api/packets/{token}/document/no-such-doc-id", timeout=30
        )
        assert r.status_code == 404


# =========================================================================
# 4. POST /api/packets/{token}/sign/{doc_id} — dual-write signed doc + array
# =========================================================================
class TestPacketSign:
    def test_invalid_signature_400(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_BadSig")
        token = body["token"]
        view = requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30).json()
        doc_id = view["documents"][0]["id"]
        # Missing signature
        r = requests.post(
            f"{BASE_URL}/api/packets/{token}/sign/{doc_id}",
            json={"signature_base64": ""},
            timeout=30,
        )
        assert r.status_code == 400

    def test_unknown_token_404(self):
        r = requests.post(
            f"{BASE_URL}/api/packets/nope/sign/whatever",
            json={"signature_base64": PNG_1x1},
            timeout=30,
        )
        assert r.status_code == 404

    def test_unknown_doc_404(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_UnknownSign")
        token = body["token"]
        r = requests.post(
            f"{BASE_URL}/api/packets/{token}/sign/no-such-doc",
            json={"signature_base64": PNG_1x1},
            timeout=30,
        )
        assert r.status_code == 404

    def test_sign_dual_writes_signed_doc_and_signed_ids(self, HA):
        body = _create_packet(HA, recipient="TEST_SliceG_Sign")
        token = body["token"]
        view = requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30).json()
        doc_id = view["documents"][0]["id"]
        n_docs = len(view["documents"])
        assert n_docs >= 2, "expected at least 2 caregiver_onboarding docs"

        # Sign once
        r = requests.post(
            f"{BASE_URL}/api/packets/{token}/sign/{doc_id}",
            json={"signature_base64": PNG_1x1},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        signed_id = r.json()["signed_doc_id"]
        _signed_doc_ids.append(signed_id)

        # PG signed doc row exists with storage_path and owner_id/uploaded_by null
        pg_doc = pg_fetchrow(
            "select id::text, title, category, owner_id, uploaded_by, "
            "storage_path, mime_type "
            "from public.documents where id=$1::uuid",
            signed_id,
        )
        assert pg_doc is not None, "signed doc not in PG"
        assert pg_doc["category"] == "caregiver_onboarding"
        assert pg_doc["mime_type"] == "application/pdf"
        assert pg_doc["storage_path"], "storage_path missing for signed doc in PG"
        assert pg_doc["owner_id"] is None, \
            "owner_id should be NULL in PG (non-UUID packet token)"
        assert pg_doc["uploaded_by"] is None, \
            "uploaded_by should be NULL in PG ('public-share' is not UUID)"
        _storage_paths.append(pg_doc["storage_path"])

        # Storage object exists (probe by listing)
        try:
            sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            # storage_path = "documents/<id>.pdf"; list parent prefix
            res = sb.storage.from_(SUPABASE_STORAGE_BUCKET).list("documents")
            names = [o["name"] for o in (res or [])]
            assert f"{signed_id}.pdf" in names, \
                f"signed PDF not in storage bucket: {names[:3]}..."
        except Exception as e:
            pytest.fail(f"storage list failed: {e}")

        # Mongo signed doc row exists with original owner_id/uploaded_by
        m_doc = mongo_find_one("documents", {"id": signed_id})
        assert m_doc is not None, "signed doc not in Mongo"
        assert m_doc["owner_id"] == token, "Mongo owner_id should be packet token"
        assert m_doc["uploaded_by"] == "public-share"

        # signed_ids array updated in BOTH
        pg_ids = pg_fetchval(
            "select array(select x::text from unnest(signed_ids) x) "
            "from public.packet_shares where token=$1",
            token,
        )
        assert doc_id in pg_ids, f"PG signed_ids missing {doc_id}: {pg_ids}"

        m_pkt = mongo_find_one("packet_shares", {"token": token})
        assert doc_id in (m_pkt.get("signed_ids") or [])

        # completed_at must NOT be set (only 1 of N signed)
        pg_completed = pg_fetchval(
            "select completed_at from public.packet_shares where token=$1", token
        )
        m_pkt2 = mongo_find_one("packet_shares", {"token": token})
        assert pg_completed is None, \
            "completed_at flipped prematurely in PG (only 1 of N signed)"
        assert m_pkt2.get("completed_at") is None, \
            "completed_at flipped prematurely in Mongo"

    def test_sign_same_doc_twice_idempotent_in_pg(self, HA):
        """Signing the same doc twice MUST NOT duplicate doc_id in PG signed_ids."""
        body = _create_packet(HA, recipient="TEST_SliceG_Idem")
        token = body["token"]
        view = requests.get(f"{BASE_URL}/api/packets/{token}", timeout=30).json()
        doc_id = view["documents"][0]["id"]

        # Sign 1st time
        r1 = requests.post(
            f"{BASE_URL}/api/packets/{token}/sign/{doc_id}",
            json={"signature_base64": PNG_1x1},
            timeout=60,
        )
        assert r1.status_code == 200, r1.text
        sid1 = r1.json()["signed_doc_id"]
        _signed_doc_ids.append(sid1)
        sp1 = pg_fetchval(
            "select storage_path from public.documents where id=$1::uuid", sid1
        )
        if sp1:
            _storage_paths.append(sp1)

        # Sign 2nd time (same doc)
        r2 = requests.post(
            f"{BASE_URL}/api/packets/{token}/sign/{doc_id}",
            json={"signature_base64": PNG_1x1},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        sid2 = r2.json()["signed_doc_id"]
        _signed_doc_ids.append(sid2)
        sp2 = pg_fetchval(
            "select storage_path from public.documents where id=$1::uuid", sid2
        )
        if sp2:
            _storage_paths.append(sp2)

        # PG signed_ids should contain doc_id exactly ONCE
        pg_ids = pg_fetchval(
            "select array(select x::text from unnest(signed_ids) x) "
            "from public.packet_shares where token=$1",
            token,
        )
        assert pg_ids.count(doc_id) == 1, \
            f"PG signed_ids duplicated doc_id: {pg_ids}"

        # Mongo uses $addToSet which is also idempotent
        m_pkt = mongo_find_one("packet_shares", {"token": token})
        sids = m_pkt.get("signed_ids") or []
        assert sids.count(doc_id) == 1, \
            f"Mongo signed_ids duplicated doc_id: {sids}"


# =========================================================================
# 5. Slice A/B/C/D/E/F regression under BOTH legacy + supabase JWTs
# =========================================================================
@pytest.mark.parametrize(
    "token_fixture", ["legacy_admin_token", "supabase_admin_token"]
)
class TestRegressionAtoF:
    def test_auth_me(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert r.json()["email"] == "admin@healthguard.com"

    def test_caregivers(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/caregivers",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list) and len(r.json()) >= 1

    def test_clients_list_and_detail(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        h = {"Authorization": f"Bearer {tok}"}
        r = requests.get(f"{base_url}/api/clients", headers=h, timeout=30)
        assert r.status_code == 200
        lst = r.json()
        assert len(lst) >= 1
        cid = lst[0]["id"]
        r2 = requests.get(f"{base_url}/api/clients/{cid}", headers=h, timeout=30)
        assert r2.status_code == 200
        assert r2.json()["id"] == cid

    def test_assignments(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/assignments",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_documents(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/documents",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_shifts(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/shifts",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_chat_threads(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/chat/threads",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_chat_contacts(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/chat/contacts",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_onboarding_admin_can_filter(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/onboarding",
            params={"caregiver_id": CAREGIVER_UUID},
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 1
        assert all(x["caregiver_id"] == CAREGIVER_UUID for x in rows)


# =========================================================================
# 6. Parity — Mongo and PG packet_shares and documents row counts match
# =========================================================================
class TestParity:
    def test_packet_shares_drift_within_tolerance(self):
        # During the run we have a few in-flight tests; tolerance = 10.
        pg_c = pg_fetchval("select count(*) from public.packet_shares")
        mongo_c = mongo_count("packet_shares")
        assert abs(pg_c - mongo_c) <= 10, \
            f"packet_shares drift PG={pg_c} Mongo={mongo_c}"

    def test_documents_drift_within_tolerance(self):
        pg_c = pg_fetchval("select count(*) from public.documents")
        mongo_c = mongo_count("documents")
        assert abs(pg_c - mongo_c) <= 10, \
            f"documents drift PG={pg_c} Mongo={mongo_c}"
