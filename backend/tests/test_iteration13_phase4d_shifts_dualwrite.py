"""Phase 4 Slice D — shifts dual-write Mongo + Supabase Postgres.

Scope:
  - POST /api/shifts (one_off, recurring) -> Mongo + PG (recurring expands children
    with parent_shift_id FK).
  - PUT /api/shifts/{id} -> patch mirrors to PG (admin can change any field;
    caregiver limited to notes+service_type).
  - DELETE /api/shifts/{id} (one_off + recurring with ON DELETE CASCADE to children).
  - POST /api/shifts/{id}/clock-in -> status='in_progress', clock_location jsonb.
  - POST /api/shifts/{id}/clock-out -> status='completed'.
  - GET /api/shifts as admin (filters caregiver_id, client_id, date range,
    recurring parent excluded).
  - GET /api/shifts as caregiver (scoped to own).
  - Phase 4 Slice A/B/C regression under BOTH legacy HS256 + Supabase ES256 JWTs.
  - Final parity: Mongo shifts count == Postgres shifts count.
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

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]

CAREGIVER_UUID = "389b257d-7edb-4d12-adfc-3b8e80f91bf1"


# --- pg helpers (sync wrappers around asyncpg) ----------------------------
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


def pg_fetch(q, *a):
    return _run(_pg_fetch_impl(q, *a))


# --- fixtures -------------------------------------------------------------
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


@pytest.fixture
def Hcg(caregiver_legacy_token):
    return {"Authorization": f"Bearer {caregiver_legacy_token}"}


@pytest.fixture(scope="session")
def the_client_id(legacy_token):
    h = {"Authorization": f"Bearer {legacy_token}"}
    r = requests.get(f"{BASE_URL}/api/clients", headers=h, timeout=30)
    assert r.status_code == 200
    clients = r.json()
    assert len(clients) >= 1, "Need at least one client in DB"
    return clients[0]["id"]


# track shifts to cleanup at module end
_created_ids: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_at_end():
    yield
    if not _created_ids:
        return
    tok = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@healthguard.com", "password": "Admin@123"},
        timeout=30,
    ).json().get("access_token")
    if not tok:
        return
    h = {"Authorization": f"Bearer {tok}"}
    for sid in _created_ids:
        try:
            requests.delete(f"{BASE_URL}/api/shifts/{sid}", headers=h, timeout=15)
        except Exception:
            pass


# =========================================================================
# 1. POST /api/shifts (one_off) — dual-write
# =========================================================================
class TestCreateOneOffShift:
    def test_create_one_off_dualwrite(self, H, the_client_id):
        payload = {
            "caregiver_id": CAREGIVER_UUID,
            "client_id": the_client_id,
            "kind": "one_off",
            "date": "2026-07-15",
            "start_time": "09:00",
            "end_time": "17:00",
            "notes": "TEST_SliceD_oneoff_create",
            "service_type": "personal_care",
        }
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json=payload, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "one_off"
        assert body["notes"] == "TEST_SliceD_oneoff_create"
        assert body["status"] == "scheduled"
        sid = body["id"]
        _created_ids.append(sid)

        # Verify in Postgres
        row = pg_fetchrow(
            "select status, to_char(date,'YYYY-MM-DD') as d, start_time, end_time, "
            "notes, service_type, caregiver_id::text as cg, client_id::text as cl "
            "from public.shifts where id=$1::uuid",
            sid,
        )
        assert row is not None, "Shift not mirrored to Postgres"
        assert row["d"] == "2026-07-15"
        assert row["start_time"] == "09:00"
        assert row["notes"] == "TEST_SliceD_oneoff_create"
        assert row["service_type"] == "personal_care"
        assert row["cg"] == CAREGIVER_UUID
        assert row["cl"] == the_client_id


# =========================================================================
# 2. PUT /api/shifts/{id} — mirror patch
# =========================================================================
class TestUpdateShift:
    def test_admin_update_notes_mirrors_to_pg(self, H, the_client_id):
        # create
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "one_off", "date": "2026-07-16",
            "start_time": "10:00", "end_time": "14:00",
            "notes": "TEST_SliceD_initial",
        }, timeout=30)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        _created_ids.append(sid)

        # update notes
        r = requests.put(f"{BASE_URL}/api/shifts/{sid}", headers=H,
                        json={"notes": "TEST_SliceD_updated_notes"}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["notes"] == "TEST_SliceD_updated_notes"
        assert pg_fetchval(
            "select notes from public.shifts where id=$1::uuid", sid
        ) == "TEST_SliceD_updated_notes"

    def test_admin_can_update_start_time(self, H, the_client_id):
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "one_off", "date": "2026-07-17",
            "start_time": "08:00", "end_time": "12:00",
            "notes": "TEST_SliceD_time",
        }, timeout=30)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        _created_ids.append(sid)

        r = requests.put(f"{BASE_URL}/api/shifts/{sid}", headers=H,
                        json={"start_time": "07:30"}, timeout=30)
        assert r.status_code == 200
        assert pg_fetchval(
            "select start_time from public.shifts where id=$1::uuid", sid
        ) == "07:30"

    def test_caregiver_can_update_notes_only(self, H, Hcg, the_client_id):
        # Need an assignment for caregiver to be able to do anything; but PUT
        # uses ownership (caregiver_id == current.id). Create via admin.
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "one_off", "date": "2026-07-18",
            "start_time": "10:00", "end_time": "14:00",
            "notes": "TEST_SliceD_cg_initial",
        }, timeout=30)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        _created_ids.append(sid)

        # caregiver can update notes
        r = requests.put(f"{BASE_URL}/api/shifts/{sid}", headers=Hcg,
                        json={"notes": "TEST_SliceD_cg_edit",
                              "start_time": "06:00"}, timeout=30)
        assert r.status_code == 200, r.text
        # start_time should be unchanged (caregiver allowed only notes+service_type)
        row = pg_fetchrow(
            "select notes, start_time from public.shifts where id=$1::uuid", sid
        )
        assert row["notes"] == "TEST_SliceD_cg_edit"
        assert row["start_time"] == "10:00"


# =========================================================================
# 3. POST /api/shifts (recurring) — parent + children
# =========================================================================
class TestRecurringShift:
    def test_recurring_creates_parent_and_children(self, H, the_client_id):
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "recurring", "date": "2026-08-03",
            "weekdays": ["MON", "WED", "FRI"],
            "recurring_until": "2026-08-21",
            "start_time": "08:00", "end_time": "12:00",
            "notes": "TEST_SliceD_recurring",
        }, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "recurring"
        sid_parent = body["id"]
        _created_ids.append(sid_parent)

        # Parent row in PG with kind='recurring'
        parent_row = pg_fetchrow(
            "select kind, to_char(date,'YYYY-MM-DD') as d, "
            "to_char(recurring_until,'YYYY-MM-DD') as ru, weekdays "
            "from public.shifts where id=$1::uuid",
            sid_parent,
        )
        assert parent_row is not None
        assert parent_row["kind"] == "recurring"
        assert parent_row["d"] == "2026-08-03"
        assert parent_row["ru"] == "2026-08-21"
        assert set(parent_row["weekdays"]) == {"MON", "WED", "FRI"}

        # Children should number = number of MON/WED/FRI between Aug 3 - Aug 21
        # 2026-08-03 (Mon), 08-05 (Wed), 08-07 (Fri), 08-10 (Mon), 08-12 (Wed),
        # 08-14 (Fri), 08-17 (Mon), 08-19 (Wed), 08-21 (Fri) = 9
        kids = pg_fetchval(
            "select count(*) from public.shifts where parent_shift_id=$1::uuid",
            sid_parent,
        )
        assert kids == 9, f"Expected 9 child shifts, got {kids}"

        # Children have kind='one_off' and parent_shift_id set
        kind_dist = pg_fetch(
            "select kind, count(*) c from public.shifts "
            "where parent_shift_id=$1::uuid group by kind", sid_parent,
        )
        assert len(kind_dist) == 1
        assert kind_dist[0]["kind"] == "one_off"
        assert kind_dist[0]["c"] == 9


# =========================================================================
# 4. DELETE /api/shifts/{id} — cascade (recurring) + one_off
# =========================================================================
class TestDeleteShift:
    def test_delete_recurring_cascades(self, H, the_client_id):
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "recurring", "date": "2026-09-07",
            "weekdays": ["TUE", "THU"],
            "recurring_until": "2026-09-24",
            "start_time": "09:00", "end_time": "11:00",
            "notes": "TEST_SliceD_recurring_delete",
        }, timeout=30)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        # do NOT append to _created_ids — we delete here intentionally
        kids_before = pg_fetchval(
            "select count(*) from public.shifts where parent_shift_id=$1::uuid", sid
        )
        assert kids_before > 0

        r = requests.delete(f"{BASE_URL}/api/shifts/{sid}", headers=H, timeout=30)
        assert r.status_code == 200

        # both parent and all children gone
        leftover = pg_fetchval(
            "select count(*) from public.shifts "
            "where id=$1::uuid or parent_shift_id=$1::uuid", sid
        )
        assert leftover == 0, f"Cascade failed: {leftover} rows remain"

    def test_delete_one_off_clears_pg(self, H, the_client_id):
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "one_off", "date": "2026-07-19",
            "start_time": "13:00", "end_time": "15:00",
            "notes": "TEST_SliceD_oneoff_delete",
        }, timeout=30)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        r = requests.delete(f"{BASE_URL}/api/shifts/{sid}", headers=H, timeout=30)
        assert r.status_code == 200
        assert pg_fetchval(
            "select count(*) from public.shifts where id=$1::uuid", sid
        ) == 0


# =========================================================================
# 5. clock-in / clock-out
# =========================================================================
class TestClockInOut:
    def test_clock_in_sets_in_progress_and_location_jsonb(self, H, the_client_id):
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "one_off", "date": "2026-07-20",
            "start_time": "08:00", "end_time": "16:00",
            "notes": "TEST_SliceD_clockin",
        }, timeout=30)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        _created_ids.append(sid)

        r = requests.post(f"{BASE_URL}/api/shifts/{sid}/clock-in", headers=H,
                         json={"location": "37.7749,-122.4194"}, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "in_progress"
        assert body["clocked_in_at"]

        row = pg_fetchrow(
            "select status, clocked_in_at, clock_location::text as loc "
            "from public.shifts where id=$1::uuid", sid,
        )
        assert row["status"] == "in_progress"
        assert row["clocked_in_at"] is not None
        # clock_location wrapped as JSON string (a JSON-encoded string value)
        assert row["loc"] is not None
        assert "37.7749" in row["loc"]

    def test_clock_out_sets_completed(self, H, the_client_id):
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "one_off", "date": "2026-07-21",
            "start_time": "08:00", "end_time": "16:00",
            "notes": "TEST_SliceD_clockout",
        }, timeout=30)
        sid = r.json()["id"]
        _created_ids.append(sid)

        requests.post(f"{BASE_URL}/api/shifts/{sid}/clock-in", headers=H,
                     json={"location": "x,y"}, timeout=30)
        r = requests.post(f"{BASE_URL}/api/shifts/{sid}/clock-out", headers=H,
                         json={}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        assert pg_fetchval(
            "select status from public.shifts where id=$1::uuid", sid
        ) == "completed"


# =========================================================================
# 6. GET /api/shifts — filters, recurring parents excluded, scope by role
# =========================================================================
class TestListShifts:
    def test_admin_get_excludes_recurring_parents(self, H, the_client_id):
        # Create a recurring + a one_off
        r = requests.post(f"{BASE_URL}/api/shifts", headers=H, json={
            "caregiver_id": CAREGIVER_UUID, "client_id": the_client_id,
            "kind": "recurring", "date": "2026-10-05",
            "weekdays": ["MON"], "recurring_until": "2026-10-19",
            "start_time": "09:00", "end_time": "10:00",
            "notes": "TEST_SliceD_list_parent",
        }, timeout=30)
        assert r.status_code == 200, r.text
        parent_id = r.json()["id"]
        _created_ids.append(parent_id)

        r = requests.get(
            f"{BASE_URL}/api/shifts?caregiver_id={CAREGIVER_UUID}",
            headers=H, timeout=30,
        )
        assert r.status_code == 200, r.text
        lst = r.json()
        assert isinstance(lst, list)
        kinds = {x["kind"] for x in lst}
        assert "recurring" not in kinds, f"Recurring parent leaked: {kinds}"
        # The parent_id should NOT appear in the list
        ids = {x["id"] for x in lst}
        assert parent_id not in ids
        # but the children (parent_shift_id == parent_id) should
        children = [x for x in lst if x.get("parent_shift_id") == parent_id]
        assert len(children) >= 1

    def test_admin_filter_by_caregiver(self, H):
        r = requests.get(
            f"{BASE_URL}/api/shifts?caregiver_id={CAREGIVER_UUID}",
            headers=H, timeout=30,
        )
        assert r.status_code == 200
        for s in r.json():
            assert s["caregiver_id"] == CAREGIVER_UUID

    def test_admin_filter_by_date_range(self, H):
        r = requests.get(
            f"{BASE_URL}/api/shifts?start=2026-07-15&end=2026-07-21",
            headers=H, timeout=30,
        )
        assert r.status_code == 200
        for s in r.json():
            d = s.get("date")
            assert d is not None
            assert "2026-07-15" <= d <= "2026-07-21"

    def test_caregiver_only_sees_own_shifts(self, Hcg):
        r = requests.get(f"{BASE_URL}/api/shifts", headers=Hcg, timeout=30)
        assert r.status_code == 200, r.text
        for s in r.json():
            assert s["caregiver_id"] == CAREGIVER_UUID

    def test_supabase_jwt_get_shifts(self, Hsupa):
        r = requests.get(
            f"{BASE_URL}/api/shifts?caregiver_id={CAREGIVER_UUID}",
            headers=Hsupa, timeout=30,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)


# =========================================================================
# 7. Phase 4 Slice A/B/C regression under BOTH JWTs
# =========================================================================
@pytest.mark.parametrize("token_fixture", ["legacy_token", "supabase_token"])
class TestSliceABCRegression:
    def test_auth_me(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/auth/me",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert r.json()["email"] == "admin@healthguard.com"

    def test_caregivers_list(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/caregivers",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_clients_list(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/clients",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_clients_detail(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        h = {"Authorization": f"Bearer {tok}"}
        lst = requests.get(f"{base_url}/api/clients", headers=h, timeout=30).json()
        cid = lst[0]["id"]
        r = requests.get(f"{base_url}/api/clients/{cid}", headers=h, timeout=30)
        assert r.status_code == 200
        assert r.json()["id"] == cid

    def test_stats(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/stats",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        # Counts surfaced by /stats (actual keys: total_clients, total_caregivers, ...)
        assert "total_clients" in d
        assert "total_caregivers" in d
        assert d["total_clients"] >= 1
        assert d["total_caregivers"] >= 1

    def test_documents_list(self, request, token_fixture, base_url):
        tok = request.getfixturevalue(token_fixture)
        r = requests.get(f"{base_url}/api/documents",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# =========================================================================
# 8. Parity: Mongo vs Postgres shifts count
# =========================================================================
class TestParity:
    def test_pg_shifts_count_reasonable(self, H):
        # Pull list of all one_off shifts via API (admin sees all)
        r = requests.get(f"{BASE_URL}/api/shifts", headers=H, timeout=30)
        assert r.status_code == 200
        api_one_off = len(r.json())

        # Direct PG: count of one_off
        pg_one_off = pg_fetchval(
            "select count(*) from public.shifts where kind='one_off'"
        )
        assert api_one_off == pg_one_off, (
            f"API one_off ({api_one_off}) != PG one_off ({pg_one_off})"
        )

    def test_pg_recurring_parents_have_no_orphan_children(self):
        orphans = pg_fetchval(
            "select count(*) from public.shifts c "
            "where c.parent_shift_id is not null "
            "and not exists (select 1 from public.shifts p "
            "                where p.id = c.parent_shift_id)"
        )
        assert orphans == 0, f"Found {orphans} orphan child shifts"
