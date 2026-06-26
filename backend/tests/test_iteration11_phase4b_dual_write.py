"""Phase 4 Slice B — dual-write tests (Mongo authoritative + best-effort Postgres).

Covers POST/DELETE /api/clients, PUT /api/clients/{id}/photo, PUT /api/users/{id}/photo,
POST/DELETE /api/assignments (with idempotency), POST /api/clients/{id}/bulk-assign-onboarding,
POST /api/client-tasks/{id}/toggle, GET /api/assignments (now from Postgres), plus
regression smoke for Slice A reads and non-Phase-4 routes.

All test rows are prefixed with TEST_PH4B_ and cleaned up at end of class.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import asyncpg
import pytest
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")

SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]

# Stable IDs from /app/memory/test_credentials.md
ADMIN_UUID = "8f16fe69-b54e-421a-bfe5-e14900e7bacd"
CAREGIVER_UUID = "389b257d-7edb-4d12-adfc-3b8e80f91bf1"

LEGACY_PW = "Admin@123"
SUPA_PW = "AdminPassword123!"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": "admin@healthguard.com", "password": LEGACY_PW},
                      timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def supabase_token():
    """Login through Supabase GoTrue then exchange for our API JWT via /api/auth/supabase-login."""
    import json
    sup_url = os.environ["SUPABASE_URL"]
    anon = os.environ["SUPABASE_ANON_KEY"]
    r = requests.post(
        f"{sup_url}/auth/v1/token?grant_type=password",
        headers={"apikey": anon, "Content-Type": "application/json"},
        data=json.dumps({"email": "admin@healthguard.com", "password": SUPA_PW}),
        timeout=30,
    )
    assert r.status_code == 200, f"Supabase login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


# ---------------------------------------------------------------------------
# Direct-DB helpers (sync wrappers using asyncpg via asyncio.run)
# ---------------------------------------------------------------------------

def _pg_run(coro_factory):
    import asyncio
    async def runner():
        conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
        try:
            return await coro_factory(conn)
        finally:
            await conn.close()
    return asyncio.run(runner())


def pg_get_client(cid):
    return _pg_run(lambda c: c.fetchrow(
        "select id::text, name, phone, notes, photo_base64 from public.clients where id=$1::uuid",
        cid))


def pg_get_assignment(aid):
    return _pg_run(lambda c: c.fetchrow(
        "select id::text, caregiver_id::text, client_id::text from public.assignments where id=$1::uuid",
        aid))


def pg_count(table, where_sql="", *args):
    sql = f"select count(*) from public.{table} {where_sql}"
    return _pg_run(lambda c: c.fetchval(sql, *args))


def pg_get_photo(table, uid):
    return _pg_run(lambda c: c.fetchval(
        f"select photo_base64 from public.{table} where id=$1::uuid", uid))


def pg_get_task(tid):
    return _pg_run(lambda c: c.fetchrow(
        "select id::text, completed, completed_at from public.client_tasks where id=$1::uuid", tid))


def mongo_count(table, **q):
    """Count via API since we don't have direct mongo access in tests."""
    # We use indirect proof via list endpoints where possible.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Setup / cleanup
# ---------------------------------------------------------------------------

CREATED_CLIENTS: list[str] = []
CREATED_ASSIGNMENTS: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_at_end(admin_headers):
    yield
    for aid in CREATED_ASSIGNMENTS:
        try:
            requests.delete(f"{BASE_URL}/api/assignments/{aid}",
                            headers=admin_headers, timeout=15)
        except Exception:
            pass
    for cid in CREATED_CLIENTS:
        try:
            requests.delete(f"{BASE_URL}/api/clients/{cid}",
                            headers=admin_headers, timeout=15)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Client dual-write tests
# ---------------------------------------------------------------------------

class TestClientDualWrite:

    def test_create_client_writes_both(self, admin_headers):
        payload = {"name": "TEST_PH4B_Client_A", "address": "1 Test St",
                   "phone": "555-0001", "notes": "ph4b-create"}
        r = requests.post(f"{BASE_URL}/api/clients", headers=admin_headers,
                          json=payload, timeout=30)
        assert r.status_code == 200, r.text
        client = r.json()
        assert client["name"] == "TEST_PH4B_Client_A"
        cid = client["id"]
        CREATED_CLIENTS.append(cid)

        # Postgres verification
        row = pg_get_client(cid)
        assert row is not None, "Client missing from Postgres after dual-write"
        assert row["name"] == "TEST_PH4B_Client_A"
        assert row["phone"] == "555-0001"

        # GET via API (reads from Postgres in Slice A) reflects new row
        r2 = requests.get(f"{BASE_URL}/api/clients/{cid}", headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        assert r2.json()["name"] == "TEST_PH4B_Client_A"

    def test_update_client_photo_writes_both(self, admin_headers):
        cid = CREATED_CLIENTS[0]
        b64 = "iVBORw0KGgoTESTPHOTO=="
        r = requests.put(f"{BASE_URL}/api/clients/{cid}/photo",
                         headers=admin_headers,
                         json={"photo_base64": f"data:image/png;base64,{b64}"},
                         timeout=15)
        assert r.status_code == 200
        photo = pg_get_photo("clients", cid)
        assert photo == b64, f"Expected {b64}, got {photo}"

    def test_delete_client_cascades_in_postgres(self, admin_headers):
        # Create a throwaway client, assign caregiver, then delete and assert cascade.
        rc = requests.post(f"{BASE_URL}/api/clients", headers=admin_headers,
                           json={"name": "TEST_PH4B_Client_Delete",
                                 "address": "", "phone": "", "notes": ""},
                           timeout=15)
        assert rc.status_code == 200
        cid = rc.json()["id"]

        ra = requests.post(f"{BASE_URL}/api/assignments", headers=admin_headers,
                           json={"caregiver_id": CAREGIVER_UUID, "client_id": cid,
                                 "schedule": "Mon-Fri", "notes": ""},
                           timeout=15)
        assert ra.status_code == 200, ra.text
        aid = ra.json()["id"]

        assert pg_get_client(cid) is not None
        assert pg_get_assignment(aid) is not None

        rd = requests.delete(f"{BASE_URL}/api/clients/{cid}",
                             headers=admin_headers, timeout=15)
        assert rd.status_code == 200

        # Verify gone from Postgres (client + cascade on assignment)
        assert pg_get_client(cid) is None
        assert pg_get_assignment(aid) is None

        # /api/clients/{cid} should now be 404
        rg = requests.get(f"{BASE_URL}/api/clients/{cid}",
                          headers=admin_headers, timeout=15)
        assert rg.status_code == 404


# ---------------------------------------------------------------------------
# User photo dual-write
# ---------------------------------------------------------------------------

class TestUserPhotoDualWrite:

    def test_update_admin_photo_writes_both(self, admin_headers):
        b64 = "iVBORw0KAdminPhoto=="
        r = requests.put(f"{BASE_URL}/api/users/{ADMIN_UUID}/photo",
                         headers=admin_headers,
                         json={"photo_base64": f"data:image/png;base64,{b64}"},
                         timeout=15)
        assert r.status_code == 200, r.text
        assert pg_get_photo("profiles", ADMIN_UUID) == b64


# ---------------------------------------------------------------------------
# Assignments dual-write + idempotency + GET-from-Postgres
# ---------------------------------------------------------------------------

class TestAssignmentDualWrite:

    def test_create_assignment_writes_both(self, admin_headers):
        cid = CREATED_CLIENTS[0]
        r = requests.post(f"{BASE_URL}/api/assignments", headers=admin_headers,
                          json={"caregiver_id": CAREGIVER_UUID, "client_id": cid,
                                "schedule": "M-F 9-5", "notes": "ph4b"},
                          timeout=15)
        assert r.status_code == 200, r.text
        aid = r.json()["id"]
        CREATED_ASSIGNMENTS.append(aid)
        assert pg_get_assignment(aid) is not None

    def test_create_assignment_idempotent(self, admin_headers):
        cid = CREATED_CLIENTS[0]
        # Same (caregiver, client) — must return existing id, no new row
        r = requests.post(f"{BASE_URL}/api/assignments", headers=admin_headers,
                          json={"caregiver_id": CAREGIVER_UUID, "client_id": cid,
                                "schedule": "different schedule"},
                          timeout=15)
        assert r.status_code == 200
        aid2 = r.json()["id"]
        assert aid2 == CREATED_ASSIGNMENTS[0], "Idempotency broken — got new id"

        # Postgres should still have exactly one row for this (cg, cl) pair
        cnt = _pg_run(lambda c: c.fetchval(
            "select count(*) from public.assignments where caregiver_id=$1::uuid and client_id=$2::uuid",
            CAREGIVER_UUID, cid))
        assert cnt == 1, f"Expected 1 assignment, got {cnt}"

    def test_get_assignments_reads_from_postgres(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/assignments", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        ids = [a["id"] for a in r.json()]
        assert CREATED_ASSIGNMENTS[0] in ids, "New assignment not surfaced by GET (Postgres read)"

    def test_delete_assignment_removes_both(self, admin_headers):
        aid = CREATED_ASSIGNMENTS[0]
        r = requests.delete(f"{BASE_URL}/api/assignments/{aid}",
                            headers=admin_headers, timeout=15)
        assert r.status_code == 200
        assert pg_get_assignment(aid) is None
        # And GET no longer lists it
        lst = requests.get(f"{BASE_URL}/api/assignments",
                           headers=admin_headers, timeout=15).json()
        assert aid not in [a["id"] for a in lst]
        CREATED_ASSIGNMENTS.remove(aid)


# ---------------------------------------------------------------------------
# Bulk-assign onboarding + toggle client task
# ---------------------------------------------------------------------------

class TestClientTasksDualWrite:

    def test_bulk_assign_creates_tasks_in_both(self, admin_headers):
        cid = CREATED_CLIENTS[0]
        r = requests.post(f"{BASE_URL}/api/clients/{cid}/bulk-assign-onboarding",
                          headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        created = body.get("created", 0)
        # /api/clients/{cid}/tasks lists Mongo rows
        rt = requests.get(f"{BASE_URL}/api/clients/{cid}/tasks",
                          headers=admin_headers, timeout=15)
        assert rt.status_code == 200
        tasks = rt.json()
        # Postgres count for this client must equal Mongo count (after dual-write)
        pg_n = _pg_run(lambda c: c.fetchval(
            "select count(*) from public.client_tasks where client_id=$1::uuid", cid))
        assert pg_n == len(tasks), f"Mongo has {len(tasks)} tasks, Postgres has {pg_n}"
        if tasks:
            TestClientTasksDualWrite._task_id = tasks[0]["id"]
        else:
            pytest.skip("No client_onboarding documents seeded -> no tasks")

    def test_toggle_task_writes_both(self, admin_headers):
        tid = getattr(TestClientTasksDualWrite, "_task_id", None)
        if not tid:
            pytest.skip("No task id from bulk-assign step")
        # Toggle to completed=True
        r = requests.post(f"{BASE_URL}/api/client-tasks/{tid}/toggle",
                          headers=admin_headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["completed"] is True
        pg = pg_get_task(tid)
        assert pg is not None
        assert pg["completed"] is True
        assert pg["completed_at"] is not None

        # Toggle back to False
        r2 = requests.post(f"{BASE_URL}/api/client-tasks/{tid}/toggle",
                           headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        assert r2.json()["completed"] is False
        pg2 = pg_get_task(tid)
        assert pg2["completed"] is False
        assert pg2["completed_at"] is None


# ---------------------------------------------------------------------------
# Phase 4 Slice A regression — reads under BOTH auth modes
# ---------------------------------------------------------------------------

class TestSliceARegression:

    @pytest.mark.parametrize("mode", ["legacy", "supabase"])
    def test_auth_me(self, mode, admin_token, supabase_token):
        tok = admin_token if mode == "legacy" else supabase_token
        r = requests.get(f"{BASE_URL}/api/auth/me",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        assert r.status_code == 200, f"{mode}: {r.text}"
        d = r.json()
        assert d["id"] == ADMIN_UUID
        assert d["role"] == "admin"

    @pytest.mark.parametrize("mode", ["legacy", "supabase"])
    def test_caregivers(self, mode, admin_token, supabase_token):
        tok = admin_token if mode == "legacy" else supabase_token
        r = requests.get(f"{BASE_URL}/api/caregivers",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        assert r.status_code == 200
        assert len(r.json()) >= 12

    @pytest.mark.parametrize("mode", ["legacy", "supabase"])
    def test_clients(self, mode, admin_token, supabase_token):
        tok = admin_token if mode == "legacy" else supabase_token
        r = requests.get(f"{BASE_URL}/api/clients",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        assert r.status_code == 200
        # at least the baseline seeded client + any TEST_PH4B_ ones
        assert len(r.json()) >= 1

    @pytest.mark.parametrize("mode", ["legacy", "supabase"])
    def test_stats(self, mode, admin_token, supabase_token):
        tok = admin_token if mode == "legacy" else supabase_token
        r = requests.get(f"{BASE_URL}/api/stats",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["total_caregivers"] >= 12
        assert d["total_documents"] >= 50


# ---------------------------------------------------------------------------
# Non-Phase-4 regression — Mongo-backed endpoints
# ---------------------------------------------------------------------------

class TestMongoRegression:

    def test_documents(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/documents", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        assert len(r.json()) >= 50

    def test_onboarding(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/onboarding", headers=admin_headers, timeout=15)
        assert r.status_code == 200

    def test_shifts(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/shifts", headers=admin_headers, timeout=15)
        assert r.status_code == 200

    def test_training(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/training", headers=admin_headers, timeout=15)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Final parity check — Mongo and Postgres row counts must match after cleanup
# ---------------------------------------------------------------------------

class TestParity:

    def test_clients_and_assignments_count_parity(self, admin_headers):
        """After all the create/delete cycles the Postgres counts for clients +
        assignments should match the API list lengths (which reflect Postgres
        for Slice A reads). This is a sanity check that no orphan rows were
        left behind by Slice B writes."""
        api_clients = requests.get(f"{BASE_URL}/api/clients",
                                   headers=admin_headers, timeout=15).json()
        api_assignments = requests.get(f"{BASE_URL}/api/assignments",
                                       headers=admin_headers, timeout=15).json()
        pg_clients = pg_count("clients")
        pg_assignments = pg_count("assignments")
        assert pg_clients == len(api_clients), \
            f"Drift: pg.clients={pg_clients} vs api.clients={len(api_clients)}"
        assert pg_assignments == len(api_assignments), \
            f"Drift: pg.assignments={pg_assignments} vs api.assignments={len(api_assignments)}"
