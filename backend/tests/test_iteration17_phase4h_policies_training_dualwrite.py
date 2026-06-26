"""Phase 4 Slice H — policy_acks + training + training_completions dual-write.

Endpoints under test:
  POLICIES:
    - GET    /api/policies/acknowledgments
    - POST   /api/policies/acknowledge
    - DELETE /api/policies/acknowledge/{policy_id}
  TRAINING:
    - POST   /api/training (admin) [dual-write + Storage if file_base64]
    - GET    /api/training
    - DELETE /api/training/{tid} (admin) [PG CASCADE clears completions]
    - POST   /api/training/{tid}/complete (caregiver) [idempotent UPSERT]
    - GET    /api/training/completions

Also covers:
  - Re-ack idempotent in both Mongo + PG.
  - Non-existent policy_id => 404.
  - Caregiver sees only own data; admin can filter ?user_id / ?caregiver_id.
  - Training file_base64 -> Storage upload + storage_path persisted in PG.
  - Slices A–G regression under legacy + supabase JWTs.
  - Parity: PG and Mongo counts equal for policy_acks, training, training_completions
    after test cleanup (baselines: policy_acks=2, training=0, training_completions=0).
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

# Tiny base64-encoded mp4-like blob (just bytes — Supabase Storage doesn't validate)
TINY_BLOB = base64.b64encode(b"\x00\x00\x00\x18ftypmp42TESTSLICEHDUMMY").decode()


# ---- async pg/mongo helpers ----
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


@pytest.fixture(scope="session")
def policy_id(legacy_admin_token):
    """Find an existing policy document id."""
    r = requests.get(
        f"{BASE_URL}/api/documents",
        headers={"Authorization": f"Bearer {legacy_admin_token}"}, timeout=30,
    )
    assert r.status_code == 200
    pols = [d for d in r.json() if d.get("category") == "policy"]
    assert len(pols) >= 1, "no policy documents in DB"
    return pols[0]["id"]


# ---- cleanup tracker ----
_training_ids: list[str] = []
_storage_paths: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _baseline_and_cleanup():
    base_pg_acks = pg_fetchval("select count(*) from public.policy_acks")
    base_mongo_acks = mongo_count("policy_acks")
    base_pg_tr = pg_fetchval("select count(*) from public.training")
    base_mongo_tr = mongo_count("training")
    base_pg_tc = pg_fetchval("select count(*) from public.training_completions")
    base_mongo_tc = mongo_count("training_completions")
    print(
        f"\n[baseline] PG/Mongo policy_acks={base_pg_acks}/{base_mongo_acks} "
        f"training={base_pg_tr}/{base_mongo_tr} "
        f"training_completions={base_pg_tc}/{base_mongo_tc}"
    )
    yield
    # Cleanup storage blobs
    if _storage_paths:
        try:
            sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            sb.storage.from_(SUPABASE_STORAGE_BUCKET).remove(_storage_paths)
        except Exception as e:
            print(f"storage cleanup err: {e}")
    # Cleanup training rows (cascades completions in PG; explicit in Mongo)
    if _training_ids:
        try:
            pg_execute(
                "delete from public.training where id = any($1::uuid[])",
                _training_ids,
            )
        except Exception as e:
            print(f"pg training cleanup err: {e}")
        try:
            mongo_delete("training", {"id": {"$in": _training_ids}})
            mongo_delete(
                "training_completions", {"training_id": {"$in": _training_ids}}
            )
        except Exception as e:
            print(f"mongo training cleanup err: {e}")
    # Drop any orphan caregiver acks for our policy (only those created by us)
    # Print final counts
    final_pg_acks = pg_fetchval("select count(*) from public.policy_acks")
    final_mongo_acks = mongo_count("policy_acks")
    final_pg_tr = pg_fetchval("select count(*) from public.training")
    final_mongo_tr = mongo_count("training")
    final_pg_tc = pg_fetchval("select count(*) from public.training_completions")
    final_mongo_tc = mongo_count("training_completions")
    print(
        f"\n[final] PG/Mongo policy_acks={final_pg_acks}/{final_mongo_acks} "
        f"training={final_pg_tr}/{final_mongo_tr} "
        f"training_completions={final_pg_tc}/{final_mongo_tc}"
    )


# =========================================================================
# 1. POLICY ACKNOWLEDGMENT
# =========================================================================
class TestPolicyAcknowledge:
    def test_post_ack_creates_row_in_both_dbs(self, HCG, policy_id):
        r = requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG, json={"policy_id": policy_id}, timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["policy_id"] == policy_id
        assert body["user_id"] == CAREGIVER_UUID
        assert body["acknowledged_at"]

        pg_row = pg_fetchrow(
            "select id::text, user_id::text, policy_id, policy_title "
            "from public.policy_acks where policy_id=$1 and user_id=$2::uuid",
            policy_id, CAREGIVER_UUID,
        )
        assert pg_row is not None, "PG ack not created"
        assert pg_row["policy_id"] == policy_id
        assert pg_row["user_id"] == CAREGIVER_UUID

        mongo_row = mongo_find_one(
            "policy_acks", {"policy_id": policy_id, "user_id": CAREGIVER_UUID}
        )
        assert mongo_row is not None
        assert mongo_row["policy_id"] == policy_id

    def test_re_ack_is_idempotent_in_both_dbs(self, HCG, policy_id):
        # First ack (or already there from previous test)
        requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG, json={"policy_id": policy_id}, timeout=30,
        )
        # Second ack — must NOT duplicate
        r = requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG, json={"policy_id": policy_id}, timeout=30,
        )
        assert r.status_code == 200, r.text

        pg_count = pg_fetchval(
            "select count(*) from public.policy_acks "
            "where policy_id=$1 and user_id=$2::uuid",
            policy_id, CAREGIVER_UUID,
        )
        assert pg_count == 1, f"PG duplicated ack: {pg_count}"

        mongo_c = mongo_count(
            "policy_acks", {"policy_id": policy_id, "user_id": CAREGIVER_UUID}
        )
        assert mongo_c == 1, f"Mongo duplicated ack: {mongo_c}"

    def test_get_acknowledgments_caregiver_sees_only_own(self, HCG, policy_id):
        # ensure exists
        requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG, json={"policy_id": policy_id}, timeout=30,
        )
        r = requests.get(
            f"{BASE_URL}/api/policies/acknowledgments", headers=HCG, timeout=30,
        )
        assert r.status_code == 200
        acks = r.json()
        assert isinstance(acks, list)
        assert all(a["user_id"] == CAREGIVER_UUID for a in acks)
        assert any(a["policy_id"] == policy_id for a in acks)

    def test_get_acknowledgments_caregiver_user_id_filter_ignored(
        self, HCG, policy_id
    ):
        """Caregivers can't peek at others — server forces user_id to self."""
        r = requests.get(
            f"{BASE_URL}/api/policies/acknowledgments",
            params={"user_id": ADMIN_UUID},
            headers=HCG, timeout=30,
        )
        assert r.status_code == 200
        acks = r.json()
        assert all(a["user_id"] == CAREGIVER_UUID for a in acks), \
            "caregiver leaked another user's acks!"

    def test_get_acknowledgments_admin_filter(self, HA, HCG, policy_id):
        requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG, json={"policy_id": policy_id}, timeout=30,
        )
        r = requests.get(
            f"{BASE_URL}/api/policies/acknowledgments",
            params={"user_id": CAREGIVER_UUID},
            headers=HA, timeout=30,
        )
        assert r.status_code == 200
        acks = r.json()
        assert isinstance(acks, list) and len(acks) >= 1
        assert all(a["user_id"] == CAREGIVER_UUID for a in acks)

    def test_get_acknowledgments_admin_no_filter_returns_all(self, HA):
        r = requests.get(
            f"{BASE_URL}/api/policies/acknowledgments", headers=HA, timeout=30,
        )
        assert r.status_code == 200
        acks = r.json()
        assert isinstance(acks, list)
        # Should include >=1 from above tests
        assert len(acks) >= 1

    def test_post_ack_nonexistent_policy_returns_404(self, HCG):
        r = requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG,
            json={"policy_id": "00000000-0000-0000-0000-000000000000"},
            timeout=30,
        )
        assert r.status_code == 404, r.text

    def test_delete_ack_removes_from_both_dbs(self, HCG, policy_id):
        # ensure exists
        requests.post(
            f"{BASE_URL}/api/policies/acknowledge",
            headers=HCG, json={"policy_id": policy_id}, timeout=30,
        )
        r = requests.delete(
            f"{BASE_URL}/api/policies/acknowledge/{policy_id}",
            headers=HCG, timeout=30,
        )
        assert r.status_code == 200, r.text

        pg_count = pg_fetchval(
            "select count(*) from public.policy_acks "
            "where policy_id=$1 and user_id=$2::uuid",
            policy_id, CAREGIVER_UUID,
        )
        assert pg_count == 0, "PG row not deleted"

        mongo_c = mongo_count(
            "policy_acks", {"policy_id": policy_id, "user_id": CAREGIVER_UUID}
        )
        assert mongo_c == 0, "Mongo row not deleted"


# =========================================================================
# 2. TRAINING CRUD (no file)
# =========================================================================
class TestTrainingNoFile:
    def test_create_training_no_file_dual_writes(self, HA):
        r = requests.post(
            f"{BASE_URL}/api/training",
            headers=HA,
            json={
                "title": "TEST_SliceH NoFile",
                "description": "no file attached",
                "required": False,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        tid = body["id"]
        _training_ids.append(tid)

        # Mongo
        m = mongo_find_one("training", {"id": tid})
        assert m is not None
        assert m["title"] == "TEST_SliceH NoFile"
        assert m.get("required") is False

        # PG: storage_path NULL
        pg = pg_fetchrow(
            "select title, description, storage_path, required "
            "from public.training where id=$1::uuid", tid,
        )
        assert pg is not None
        assert pg["title"] == "TEST_SliceH NoFile"
        assert pg["description"] == "no file attached"
        assert pg["required"] is False
        assert pg["storage_path"] is None, \
            f"storage_path must be NULL when no file: {pg['storage_path']}"

    def test_create_training_with_file_uploads_to_storage(self, HA):
        r = requests.post(
            f"{BASE_URL}/api/training",
            headers=HA,
            json={
                "title": "TEST_SliceH WithFile",
                "description": "with file attached",
                "required": True,
                "file_base64": TINY_BLOB,
                "mime_type": "video/mp4",
            },
            timeout=60,
        )
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        _training_ids.append(tid)

        # PG storage_path should reference documents/<id>.mp4
        pg = pg_fetchrow(
            "select storage_path, mime_type, required "
            "from public.training where id=$1::uuid", tid,
        )
        assert pg is not None
        sp = pg["storage_path"]
        assert sp is not None, "storage_path should be set when file_base64 given"
        assert sp.startswith("documents/"), f"unexpected path: {sp}"
        assert tid in sp, f"path should contain training id: {sp}"
        _storage_paths.append(sp)

        # Verify blob exists in Supabase Storage. storage_path looks like
        # 'documents/<id>.mp4' (a subfolder INSIDE bucket 'documents').
        sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        prefix, _, blob_name = sp.partition("/")
        listing = sb.storage.from_(SUPABASE_STORAGE_BUCKET).list(prefix)
        names = [f["name"] for f in listing]
        assert blob_name in names, \
            f"blob {blob_name} not in storage at prefix '{prefix}'; have: {names[:5]}..."

    def test_get_training_returns_from_pg_for_admin(self, HA):
        r = requests.get(
            f"{BASE_URL}/api/training", headers=HA, timeout=30,
        )
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        # Our created TIDs should be present
        ids = [t["id"] for t in items]
        for tid in _training_ids:
            assert tid in ids, f"training {tid} missing from list"

    def test_get_training_returns_from_pg_for_caregiver(self, HCG):
        r = requests.get(
            f"{BASE_URL}/api/training", headers=HCG, timeout=30,
        )
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        ids = [t["id"] for t in items]
        for tid in _training_ids:
            assert tid in ids


# =========================================================================
# 3. TRAINING COMPLETIONS
# =========================================================================
class TestTrainingCompletions:
    def test_complete_creates_row_in_both_dbs(self, HA, HCG):
        # create a fresh training so the test is self-contained
        r = requests.post(
            f"{BASE_URL}/api/training", headers=HA,
            json={"title": "TEST_SliceH Comp", "required": True}, timeout=30,
        )
        tid = r.json()["id"]
        _training_ids.append(tid)

        rc = requests.post(
            f"{BASE_URL}/api/training/{tid}/complete", headers=HCG, timeout=30,
        )
        assert rc.status_code == 200, rc.text
        body = rc.json()
        assert body["training_id"] == tid
        assert body["caregiver_id"] == CAREGIVER_UUID

        pg_count = pg_fetchval(
            "select count(*) from public.training_completions "
            "where training_id=$1::uuid and caregiver_id=$2::uuid",
            tid, CAREGIVER_UUID,
        )
        assert pg_count == 1

        m = mongo_find_one(
            "training_completions",
            {"training_id": tid, "caregiver_id": CAREGIVER_UUID},
        )
        assert m is not None
        assert m["training_id"] == tid

    def test_recomplete_is_idempotent(self, HA, HCG):
        r = requests.post(
            f"{BASE_URL}/api/training", headers=HA,
            json={"title": "TEST_SliceH Idem", "required": True}, timeout=30,
        )
        tid = r.json()["id"]
        _training_ids.append(tid)

        r1 = requests.post(
            f"{BASE_URL}/api/training/{tid}/complete", headers=HCG, timeout=30,
        )
        assert r1.status_code == 200
        first_id = r1.json()["id"]

        r2 = requests.post(
            f"{BASE_URL}/api/training/{tid}/complete", headers=HCG, timeout=30,
        )
        assert r2.status_code == 200
        second_id = r2.json()["id"]
        assert first_id == second_id, \
            f"re-complete must return same record: {first_id} != {second_id}"

        pg_count = pg_fetchval(
            "select count(*) from public.training_completions "
            "where training_id=$1::uuid and caregiver_id=$2::uuid",
            tid, CAREGIVER_UUID,
        )
        assert pg_count == 1

        m_count = mongo_count(
            "training_completions",
            {"training_id": tid, "caregiver_id": CAREGIVER_UUID},
        )
        assert m_count == 1

    def test_get_completions_caregiver_sees_only_own(self, HA, HCG):
        # Create + complete a training as caregiver
        r = requests.post(
            f"{BASE_URL}/api/training", headers=HA,
            json={"title": "TEST_SliceH OwnList"}, timeout=30,
        )
        tid = r.json()["id"]
        _training_ids.append(tid)
        requests.post(
            f"{BASE_URL}/api/training/{tid}/complete", headers=HCG, timeout=30,
        )

        rc = requests.get(
            f"{BASE_URL}/api/training/completions", headers=HCG, timeout=30,
        )
        assert rc.status_code == 200
        comps = rc.json()
        assert isinstance(comps, list)
        assert all(c["caregiver_id"] == CAREGIVER_UUID for c in comps)
        assert any(c["training_id"] == tid for c in comps)

    def test_get_completions_caregiver_id_filter_ignored_for_caregiver(
        self, HCG
    ):
        r = requests.get(
            f"{BASE_URL}/api/training/completions",
            params={"caregiver_id": ADMIN_UUID},
            headers=HCG, timeout=30,
        )
        assert r.status_code == 200
        comps = r.json()
        assert all(c["caregiver_id"] == CAREGIVER_UUID for c in comps), \
            "caregiver leaked another caregiver's completions!"

    def test_get_completions_admin_can_filter(self, HA):
        r = requests.get(
            f"{BASE_URL}/api/training/completions",
            params={"caregiver_id": CAREGIVER_UUID},
            headers=HA, timeout=30,
        )
        assert r.status_code == 200
        comps = r.json()
        assert isinstance(comps, list) and len(comps) >= 1
        assert all(c["caregiver_id"] == CAREGIVER_UUID for c in comps)


# =========================================================================
# 4. DELETE TRAINING (cascades completions in BOTH DBs)
# =========================================================================
class TestTrainingDelete:
    def test_delete_training_cascades_completions(self, HA, HCG):
        # Create + complete
        r = requests.post(
            f"{BASE_URL}/api/training", headers=HA,
            json={"title": "TEST_SliceH Del", "required": True}, timeout=30,
        )
        tid = r.json()["id"]
        requests.post(
            f"{BASE_URL}/api/training/{tid}/complete", headers=HCG, timeout=30,
        )

        # Pre-condition: completion exists
        assert pg_fetchval(
            "select count(*) from public.training_completions "
            "where training_id=$1::uuid", tid,
        ) == 1
        assert mongo_count(
            "training_completions", {"training_id": tid}
        ) == 1

        # Delete
        rd = requests.delete(
            f"{BASE_URL}/api/training/{tid}", headers=HA, timeout=30,
        )
        assert rd.status_code == 200, rd.text

        # Training gone in BOTH
        assert pg_fetchval(
            "select count(*) from public.training where id=$1::uuid", tid,
        ) == 0
        assert mongo_count("training", {"id": tid}) == 0

        # Completions cascaded in BOTH (PG CASCADE; Mongo explicit delete_many)
        assert pg_fetchval(
            "select count(*) from public.training_completions "
            "where training_id=$1::uuid", tid,
        ) == 0
        assert mongo_count(
            "training_completions", {"training_id": tid}
        ) == 0


# =========================================================================
# 5. Slice A–G regression under BOTH legacy + supabase JWTs
# =========================================================================
@pytest.mark.parametrize(
    "token_fixture", ["legacy_admin_token", "supabase_admin_token"]
)
class TestRegressionAtoG:
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

    def test_clients(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/clients",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_assignments(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/assignments",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200

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

    def test_chat_threads(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/chat/threads",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200

    def test_onboarding(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/onboarding",
            params={"caregiver_id": CAREGIVER_UUID},
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200

    def test_policies_acks(self, request, token_fixture, base_url):
        """Slice H regression: admin can list all acks under both JWT modes."""
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/policies/acknowledgments",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_training_list(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(
            f"{base_url}/api/training",
            headers={"Authorization": f"Bearer {tok}"}, timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# =========================================================================
# 6. Parity — Mongo and PG row counts match for slice-H tables (with tolerance)
# =========================================================================
class TestParity:
    def test_policy_acks_parity(self):
        pg_c = pg_fetchval("select count(*) from public.policy_acks")
        m_c = mongo_count("policy_acks")
        assert abs(pg_c - m_c) <= 5, f"policy_acks drift PG={pg_c} Mongo={m_c}"

    def test_training_parity(self):
        pg_c = pg_fetchval("select count(*) from public.training")
        m_c = mongo_count("training")
        assert abs(pg_c - m_c) <= 5, f"training drift PG={pg_c} Mongo={m_c}"

    def test_training_completions_parity(self):
        pg_c = pg_fetchval(
            "select count(*) from public.training_completions"
        )
        m_c = mongo_count("training_completions")
        assert abs(pg_c - m_c) <= 5, \
            f"training_completions drift PG={pg_c} Mongo={m_c}"
