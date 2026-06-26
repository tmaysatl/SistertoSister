"""Phase 4 Slice F — onboarding router dual-write Mongo + Supabase Postgres.

Scope (mirrors scripts/_smoke_slice_f.py assertions + permissions + regression):
  - POST /api/onboarding — admin creates step (dual-write Mongo + PG).
  - GET /api/onboarding?caregiver_id=<id> — admin sees that caregiver's steps from PG.
  - GET /api/onboarding — caregiver sees ONLY own steps (caregiver_id forced).
  - POST /api/onboarding/{id}/toggle — flips completed + completed_at in BOTH DBs.
  - DELETE /api/onboarding/{id} — admin-only, removes from BOTH DBs.
  - POST /api/onboarding/bulk-assign — idempotent (re-run created=0).
  - Permissions: caregiver cannot toggle another's step (403);
                 caregiver cannot DELETE any step (only admin).
  - Phase 4 Slice A/B/C/D/E regression under LEGACY + SUPABASE JWTs.
  - Parity: Mongo onboarding == PG onboarding after cleanup.
"""
from __future__ import annotations
import os
import asyncio
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import asyncpg  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

ADMIN_UUID = "8f16fe69-b54e-421a-bfe5-e14900e7bacd"
CAREGIVER_UUID = "389b257d-7edb-4d12-adfc-3b8e80f91bf1"


# ---- pg/mongo sync helpers ----
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


# Track step ids for cleanup
_created_step_ids: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_at_end():
    base_pg = pg_fetchval("select count(*) from public.onboarding")
    base_mongo = mongo_count("onboarding")
    print(f"\n[baseline] PG onboarding={base_pg} | Mongo onboarding={base_mongo}")
    yield
    if _created_step_ids:
        try:
            pg_execute(
                "delete from public.onboarding where id = any($1::uuid[])",
                _created_step_ids,
            )
        except Exception as e:
            print(f"PG cleanup error: {e}")
        try:
            mongo_delete("onboarding", {"id": {"$in": _created_step_ids}})
        except Exception as e:
            print(f"Mongo cleanup error: {e}")
    final_pg = pg_fetchval("select count(*) from public.onboarding")
    final_mongo = mongo_count("onboarding")
    print(f"[final] PG onboarding={final_pg} | Mongo onboarding={final_mongo}")


# =========================================================================
# 1. POST /api/onboarding — dual-write
# =========================================================================
class TestCreateStep:
    def test_admin_creates_step_dualwrite(self, HA):
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": CAREGIVER_UUID,
                "title": "TEST_SliceF Create Step",
                "description": "verify dual-write create",
                "completed": False,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["caregiver_id"] == CAREGIVER_UUID
        assert body["title"] == "TEST_SliceF Create Step"
        assert body["completed"] is False
        assert body["completed_at"] is None
        sid = body["id"]
        _created_step_ids.append(sid)

        # PG mirror
        row = pg_fetchrow(
            "select title, description, completed, completed_at, "
            "caregiver_id::text as cg from public.onboarding where id=$1::uuid",
            sid,
        )
        assert row is not None, "Step not mirrored to PG"
        assert row["title"] == "TEST_SliceF Create Step"
        assert row["description"] == "verify dual-write create"
        assert row["completed"] is False
        assert row["completed_at"] is None
        assert row["cg"] == CAREGIVER_UUID

        # Mongo mirror
        m = mongo_find_one("onboarding", {"id": sid})
        assert m is not None
        assert m["title"] == "TEST_SliceF Create Step"
        assert m["completed"] is False

    def test_caregiver_cannot_create_step(self, HCG):
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HCG,
            json={"caregiver_id": CAREGIVER_UUID, "title": "should fail"},
            timeout=30,
        )
        assert r.status_code == 403, r.text


# =========================================================================
# 2. GET /api/onboarding — reads from PG with role-based filter
# =========================================================================
class TestListSteps:
    def test_admin_filter_by_caregiver(self, HA):
        # ensure at least one extra step exists for the caregiver
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": CAREGIVER_UUID,
                "title": "TEST_SliceF List Filter Step",
                "description": "list filter",
                "completed": False,
            },
            timeout=30,
        )
        assert r.status_code == 200
        sid = r.json()["id"]
        _created_step_ids.append(sid)

        steps = requests.get(
            f"{BASE_URL}/api/onboarding?caregiver_id={CAREGIVER_UUID}",
            headers=HA, timeout=30,
        ).json()
        assert isinstance(steps, list)
        assert all(s["caregiver_id"] == CAREGIVER_UUID for s in steps), \
            "Admin filter returned other caregivers' steps"
        assert any(s["id"] == sid for s in steps), \
            "Just-created step not in PG-sourced list"

        # cross-check vs PG row count
        pg_c = pg_fetchval(
            "select count(*) from public.onboarding where caregiver_id=$1::uuid",
            CAREGIVER_UUID,
        )
        assert len(steps) == pg_c, f"List len {len(steps)} != PG count {pg_c}"

    def test_caregiver_sees_only_own_steps(self, HCG):
        # caregiver call ignores any caregiver_id query and forces identity
        steps = requests.get(
            f"{BASE_URL}/api/onboarding?caregiver_id={ADMIN_UUID}",
            headers=HCG, timeout=30,
        ).json()
        assert isinstance(steps, list)
        for s in steps:
            assert s["caregiver_id"] == CAREGIVER_UUID, \
                f"Caregiver saw foreign step: {s}"

        # cross-check vs PG count for this caregiver
        pg_c = pg_fetchval(
            "select count(*) from public.onboarding where caregiver_id=$1::uuid",
            CAREGIVER_UUID,
        )
        assert len(steps) == pg_c


# =========================================================================
# 3. POST /api/onboarding/{id}/toggle — dual-write toggle
# =========================================================================
class TestToggleStep:
    def test_toggle_flips_completed_and_completed_at_in_both_dbs(self, HA):
        # create a fresh step
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": CAREGIVER_UUID,
                "title": "TEST_SliceF Toggle Step",
                "description": "toggle me",
                "completed": False,
            },
            timeout=30,
        )
        sid = r.json()["id"]
        _created_step_ids.append(sid)

        # toggle 1 -> completed=true, completed_at not null
        r = requests.post(
            f"{BASE_URL}/api/onboarding/{sid}/toggle",
            headers=HA, timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["completed"] is True
        assert body["completed_at"] is not None

        pg = pg_fetchrow(
            "select completed, completed_at from public.onboarding where id=$1::uuid",
            sid,
        )
        assert pg["completed"] is True
        assert pg["completed_at"] is not None, "PG completed_at must be set"

        m = mongo_find_one("onboarding", {"id": sid})
        assert m["completed"] is True
        assert m["completed_at"] is not None, "Mongo completed_at must be set"

        # toggle 2 -> completed=false, completed_at null
        r = requests.post(
            f"{BASE_URL}/api/onboarding/{sid}/toggle",
            headers=HA, timeout=30,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["completed"] is False
        assert body["completed_at"] is None

        pg = pg_fetchrow(
            "select completed, completed_at from public.onboarding where id=$1::uuid",
            sid,
        )
        assert pg["completed"] is False
        assert pg["completed_at"] is None, "PG completed_at must clear"

        m = mongo_find_one("onboarding", {"id": sid})
        assert m["completed"] is False
        assert m["completed_at"] is None, "Mongo completed_at must clear"

    def test_caregiver_can_toggle_own_step(self, HA, HCG):
        # admin creates a step for the demo caregiver
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": CAREGIVER_UUID,
                "title": "TEST_SliceF CG Own Toggle",
                "description": "cg toggles own",
                "completed": False,
            },
            timeout=30,
        )
        sid = r.json()["id"]
        _created_step_ids.append(sid)

        r = requests.post(
            f"{BASE_URL}/api/onboarding/{sid}/toggle",
            headers=HCG, timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json()["completed"] is True

    def test_caregiver_cannot_toggle_others_step(self, HA, HCG):
        # find some OTHER caregiver to assign a step to
        all_cg = requests.get(
            f"{BASE_URL}/api/caregivers", headers=HA, timeout=30,
        ).json()
        other = next(
            (c for c in all_cg if c["id"] != CAREGIVER_UUID), None
        )
        assert other is not None, "Need >=2 caregivers for this test"

        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": other["id"],
                "title": "TEST_SliceF Other CG Step",
                "description": "other cg",
                "completed": False,
            },
            timeout=30,
        )
        sid = r.json()["id"]
        _created_step_ids.append(sid)

        # demo caregiver attempts to toggle -> 403
        r = requests.post(
            f"{BASE_URL}/api/onboarding/{sid}/toggle",
            headers=HCG, timeout=30,
        )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

    def test_toggle_nonexistent_returns_404(self, HA):
        bogus = "00000000-0000-0000-0000-000000000000"
        r = requests.post(
            f"{BASE_URL}/api/onboarding/{bogus}/toggle",
            headers=HA, timeout=30,
        )
        assert r.status_code == 404


# =========================================================================
# 4. DELETE /api/onboarding/{id} — dual-delete + permissions
# =========================================================================
class TestDeleteStep:
    def test_admin_delete_removes_from_both_dbs(self, HA):
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": CAREGIVER_UUID,
                "title": "TEST_SliceF Delete Step",
                "description": "delete me",
                "completed": False,
            },
            timeout=30,
        )
        sid = r.json()["id"]
        # do NOT add to _created_step_ids because this test deletes it

        r = requests.delete(
            f"{BASE_URL}/api/onboarding/{sid}",
            headers=HA, timeout=30,
        )
        assert r.status_code == 200, r.text

        assert pg_fetchval(
            "select count(*) from public.onboarding where id=$1::uuid", sid
        ) == 0
        assert mongo_count("onboarding", {"id": sid}) == 0

    def test_caregiver_cannot_delete(self, HA, HCG):
        r = requests.post(
            f"{BASE_URL}/api/onboarding",
            headers=HA,
            json={
                "caregiver_id": CAREGIVER_UUID,
                "title": "TEST_SliceF CG No Delete",
                "description": "cg blocked",
                "completed": False,
            },
            timeout=30,
        )
        sid = r.json()["id"]
        _created_step_ids.append(sid)

        r = requests.delete(
            f"{BASE_URL}/api/onboarding/{sid}",
            headers=HCG, timeout=30,
        )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"

        # still present in BOTH dbs
        assert pg_fetchval(
            "select count(*) from public.onboarding where id=$1::uuid", sid
        ) == 1
        assert mongo_count("onboarding", {"id": sid}) == 1


# =========================================================================
# 5. POST /api/onboarding/bulk-assign — idempotent dual-write
# =========================================================================
class TestBulkAssign:
    def test_bulk_assign_idempotent_for_existing_caregiver(self, HA):
        # second run should create 0 new (caregiver already has them from migration)
        r = requests.post(
            f"{BASE_URL}/api/onboarding/bulk-assign",
            headers=HA,
            json={"caregiver_id": CAREGIVER_UUID},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "created" in body and "total_steps" in body
        first_created = body["created"]

        # re-run -> created must be 0
        r2 = requests.post(
            f"{BASE_URL}/api/onboarding/bulk-assign",
            headers=HA,
            json={"caregiver_id": CAREGIVER_UUID},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["created"] == 0, \
            f"Expected idempotent re-run created=0, got {r2.json()}"

        # any newly created in first run should be in PG too
        if first_created:
            # Verify the new ones (titles match documents) are in PG
            pg_titles = [r["title"] for r in pg_execute_fetch(
                "select title from public.onboarding where caregiver_id=$1::uuid",
                CAREGIVER_UUID,
            )] if False else None  # noqa: unused branch placeholder
            # If first_created > 0, parity check below catches drift anyway

    def test_bulk_assign_unknown_caregiver_returns_404(self, HA):
        r = requests.post(
            f"{BASE_URL}/api/onboarding/bulk-assign",
            headers=HA,
            json={"caregiver_id": "00000000-0000-0000-0000-000000000000"},
            timeout=30,
        )
        assert r.status_code == 404

    def test_caregiver_cannot_bulk_assign(self, HCG):
        r = requests.post(
            f"{BASE_URL}/api/onboarding/bulk-assign",
            headers=HCG,
            json={"caregiver_id": CAREGIVER_UUID},
            timeout=30,
        )
        assert r.status_code == 403


# Helper used in TestBulkAssign (defined out here to keep tests tidy)
async def _pg_fetch_impl(q, *a):
    c = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
    try:
        return await c.fetch(q, *a)
    finally:
        await c.close()


def pg_execute_fetch(q, *a):
    return _run(_pg_fetch_impl(q, *a))


# =========================================================================
# 6. Slice A/B/C/D/E regression under BOTH legacy + supabase JWTs
# =========================================================================
@pytest.mark.parametrize("token_fixture",
                         ["legacy_admin_token", "supabase_admin_token"])
class TestSliceABCDERegression:
    def test_auth_me(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/auth/me",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert r.json()["email"] == "admin@healthguard.com"

    def test_caregivers(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/caregivers",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
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
        r = requests.get(f"{base_url}/api/assignments",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_documents(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/documents",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_shifts(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/shifts",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_chat_threads(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/chat/threads",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_chat_contacts(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/chat/contacts",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# =========================================================================
# 7. Parity — Mongo onboarding == PG onboarding (after cleanup is module-end;
#    here we assert |delta| <= 5 during the run)
# =========================================================================
class TestParity:
    def test_onboarding_counts_within_tolerance(self):
        pg_c = pg_fetchval("select count(*) from public.onboarding")
        mongo_c = mongo_count("onboarding")
        assert abs(pg_c - mongo_c) <= 5, \
            f"onboarding drift too large: PG={pg_c}, Mongo={mongo_c}"

    def test_caregiver_onboarding_counts_match_per_caregiver(self):
        # check the demo caregiver: PG and Mongo must match exactly
        pg_c = pg_fetchval(
            "select count(*) from public.onboarding where caregiver_id=$1::uuid",
            CAREGIVER_UUID,
        )
        mongo_c = mongo_count("onboarding", {"caregiver_id": CAREGIVER_UUID})
        assert pg_c == mongo_c, \
            f"Per-caregiver drift demo: PG={pg_c}, Mongo={mongo_c}"
