"""Phase 4 Slice I — POST /api/auth/register dual-write Mongo + Supabase Auth + profiles.

Verifies:
  1. Fresh-email register creates Mongo user + Supabase Auth user (SAME UUID) +
     public.profiles row, and the legacy access_token works for /auth/me.
  2. The new user can immediately log in via Supabase password grant; the
     resulting ES256 JWT works against backend /auth/me (same id).
  3. Existing email returns 400 and does NOT create a second Supabase Auth user.
  4. role='admin' works identically (Supabase user_metadata + profiles.role).
  5. Startup-seeded admin@healthguard.com + caregiver@healthguard.com have
     matching UUIDs across Mongo, Supabase Auth and Postgres profiles.
  6. Phase 4 A-H regression: 10 endpoints x 2 JWTs (legacy + Supabase) = 20 cases.
  7. Cleanup removes user from Mongo + Supabase Auth + Postgres (cascade).
"""
from __future__ import annotations
import os
import uuid
import asyncio
from pathlib import Path
from typing import Optional

import pytest
import requests
import httpx
import asyncpg
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path("/app/backend/.env"))

# ---- Config ----------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

SEEDED_ADMIN_EMAIL = "admin@healthguard.com"
SEEDED_ADMIN_UUID = "8f16fe69-b54e-421a-bfe5-e14900e7bacd"
SEEDED_CAREGIVER_EMAIL = "caregiver@healthguard.com"
SEEDED_CAREGIVER_UUID = "389b257d-7edb-4d12-adfc-3b8e80f91bf1"

# Track every test-created uid for cleanup
_CREATED_UIDS: list[str] = []


# ---- Helpers ---------------------------------------------------------------

def _svc_headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }


def _new_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@phcp-smoke.io"


def supa_get_auth_user_by_email(email: str) -> Optional[dict]:
    r = requests.get(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=_svc_headers(), params={"email": email}, timeout=15,
    )
    if r.status_code >= 400:
        return None
    users = r.json().get("users", [])
    for u in users:
        if u.get("email", "").lower() == email.lower():
            return u
    return None


def supa_password_login(email: str, password: str) -> Optional[str]:
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


def supa_delete_user(uid: str) -> int:
    r = requests.delete(
        f"{SUPABASE_URL}/auth/v1/admin/users/{uid}",
        headers=_svc_headers(), timeout=15,
    )
    return r.status_code


async def _async_get_profile(uid: str) -> Optional[dict]:
    pg = await asyncpg.connect(SUPABASE_DIRECT_URL)
    try:
        row = await pg.fetchrow(
            "select id::text as id, email, name, role from public.profiles where id=$1::uuid",
            uid,
        )
        return dict(row) if row else None
    finally:
        await pg.close()


async def _async_count_profile(uid: str) -> int:
    pg = await asyncpg.connect(SUPABASE_DIRECT_URL)
    try:
        return int(await pg.fetchval(
            "select count(*) from public.profiles where id=$1::uuid", uid
        ))
    finally:
        await pg.close()


def get_profile_sync(uid: str) -> Optional[dict]:
    return asyncio.run(_async_get_profile(uid))


def count_profile_sync(uid: str) -> int:
    return asyncio.run(_async_count_profile(uid))


async def _async_get_mongo_user(uid: str) -> Optional[dict]:
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        return await cli[DB_NAME].users.find_one({"id": uid}, {"_id": 0})
    finally:
        cli.close()


async def _async_delete_mongo_user(uid: str) -> int:
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        r = await cli[DB_NAME].users.delete_one({"id": uid})
        return r.deleted_count
    finally:
        cli.close()


def get_mongo_user_sync(uid: str) -> Optional[dict]:
    return asyncio.run(_async_get_mongo_user(uid))


def delete_mongo_user_sync(uid: str) -> int:
    return asyncio.run(_async_delete_mongo_user(uid))


# ---- Module-scope cleanup -------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _cleanup_after_module():
    yield
    for uid in list(_CREATED_UIDS):
        try:
            supa_delete_user(uid)
        except Exception:
            pass
        try:
            delete_mongo_user_sync(uid)
        except Exception:
            pass


# ---- Reusable Supabase admin token fixture --------------------------------

@pytest.fixture(scope="module")
def supabase_admin_token():
    tok = supa_password_login(SEEDED_ADMIN_EMAIL, "AdminPassword123!")
    assert tok, "Supabase admin login failed"
    return tok


# ==========================================================================
# TestRegisterFreshUser — happy path (caregiver)
# ==========================================================================
class TestRegisterFreshUser:
    @pytest.fixture(scope="class")
    def fresh_user(self, base_url):
        email = _new_email("slicei-careg")
        password = "TestPass123!"
        r = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": password,
                  "name": "Slice I Caregiver", "role": "caregiver"},
            timeout=20,
        )
        assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
        data = r.json()
        uid = data["user"]["id"]
        _CREATED_UIDS.append(uid)
        return {
            "email": email, "password": password, "uid": uid,
            "access_token": data["access_token"], "user": data["user"],
        }

    def test_register_returns_200_with_token(self, fresh_user):
        assert "access_token" in fresh_user and len(fresh_user["access_token"]) > 20
        assert fresh_user["user"]["email"] == fresh_user["email"]
        assert fresh_user["user"]["role"] == "caregiver"
        assert fresh_user["user"]["name"] == "Slice I Caregiver"
        # id is a UUID
        uuid.UUID(fresh_user["uid"])

    def test_mongo_row_exists(self, fresh_user):
        m = get_mongo_user_sync(fresh_user["uid"])
        assert m is not None, "Mongo user row missing"
        assert m["id"] == fresh_user["uid"]
        assert m["email"] == fresh_user["email"]
        assert m["role"] == "caregiver"
        assert "hashed_password" in m

    def test_supabase_auth_user_has_same_uuid(self, fresh_user):
        u = supa_get_auth_user_by_email(fresh_user["email"])
        assert u is not None, "Supabase Auth user missing"
        assert u["id"] == fresh_user["uid"], \
            f"UUID mismatch: mongo={fresh_user['uid']} supabase={u['id']}"
        # Slice I sets role + name in user_metadata
        meta = u.get("user_metadata") or {}
        assert meta.get("role") == "caregiver"
        assert meta.get("name") == "Slice I Caregiver"

    def test_public_profiles_row_matches(self, fresh_user):
        prof = get_profile_sync(fresh_user["uid"])
        assert prof is not None, "public.profiles row missing"
        assert prof["id"] == fresh_user["uid"]
        assert prof["email"] == fresh_user["email"]
        assert prof["role"] == "caregiver"
        assert prof["name"] == "Slice I Caregiver"

    def test_legacy_token_authenticates_me(self, base_url, fresh_user):
        r = requests.get(
            f"{base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {fresh_user['access_token']}"},
            timeout=15,
        )
        assert r.status_code == 200
        me = r.json()
        assert me["id"] == fresh_user["uid"]
        assert me["email"] == fresh_user["email"]

    def test_supabase_password_grant_works(self, fresh_user):
        tok = supa_password_login(fresh_user["email"], fresh_user["password"])
        assert tok and len(tok) > 100, "Supabase login failed for new user"

    def test_supabase_token_authenticates_backend_me(self, base_url, fresh_user):
        tok = supa_password_login(fresh_user["email"], fresh_user["password"])
        assert tok
        r = requests.get(
            f"{base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {tok}"}, timeout=15,
        )
        assert r.status_code == 200
        me = r.json()
        # SAME id as Mongo's register response
        assert me["id"] == fresh_user["uid"]
        assert me["email"] == fresh_user["email"]


# ==========================================================================
# TestRegisterDuplicateEmail
# ==========================================================================
class TestRegisterDuplicateEmail:
    def test_existing_admin_email_returns_400(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": SEEDED_ADMIN_EMAIL, "password": "Whatever123!",
                  "name": "Dup", "role": "caregiver"},
            timeout=15,
        )
        assert r.status_code == 400
        assert "already registered" in r.text.lower()

    def test_no_extra_supabase_user_created_on_dupe(self, base_url):
        # Snapshot Supabase user count before
        before = supa_get_auth_user_by_email(SEEDED_ADMIN_EMAIL)
        assert before is not None
        before_id = before["id"]
        r = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": SEEDED_ADMIN_EMAIL, "password": "Whatever123!",
                  "name": "Dup", "role": "caregiver"},
            timeout=15,
        )
        assert r.status_code == 400
        after = supa_get_auth_user_by_email(SEEDED_ADMIN_EMAIL)
        assert after is not None
        assert after["id"] == before_id, "Supabase admin uid changed on dupe attempt"


# ==========================================================================
# TestRegisterAdminRole
# ==========================================================================
class TestRegisterAdminRole:
    @pytest.fixture(scope="class")
    def fresh_admin(self, base_url):
        email = _new_email("slicei-admin")
        password = "TestAdminPass123!"
        r = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": password,
                  "name": "Slice I Admin", "role": "admin"},
            timeout=20,
        )
        assert r.status_code == 200, f"register admin failed: {r.text}"
        data = r.json()
        uid = data["user"]["id"]
        _CREATED_UIDS.append(uid)
        return {"email": email, "password": password, "uid": uid, "user": data["user"]}

    def test_admin_register_role_in_response(self, fresh_admin):
        assert fresh_admin["user"]["role"] == "admin"

    def test_admin_supabase_metadata_role(self, fresh_admin):
        u = supa_get_auth_user_by_email(fresh_admin["email"])
        assert u and u["id"] == fresh_admin["uid"]
        assert (u.get("user_metadata") or {}).get("role") == "admin"

    def test_admin_profiles_role(self, fresh_admin):
        prof = get_profile_sync(fresh_admin["uid"])
        assert prof and prof["role"] == "admin"

    def test_admin_can_supabase_login(self, fresh_admin):
        tok = supa_password_login(fresh_admin["email"], fresh_admin["password"])
        assert tok


# ==========================================================================
# TestSeededUserParity — admin + caregiver seed dual-write verification
# ==========================================================================
class TestSeededUserParity:
    def test_admin_uuid_matches_across_stores(self):
        m = get_mongo_user_sync(SEEDED_ADMIN_UUID)
        assert m is not None and m["email"] == SEEDED_ADMIN_EMAIL
        assert m["id"] == SEEDED_ADMIN_UUID

        u = supa_get_auth_user_by_email(SEEDED_ADMIN_EMAIL)
        assert u is not None
        assert u["id"] == SEEDED_ADMIN_UUID, \
            f"Admin UUID mismatch: mongo={SEEDED_ADMIN_UUID} supabase={u['id']}"

        prof = get_profile_sync(SEEDED_ADMIN_UUID)
        assert prof is not None
        assert prof["email"] == SEEDED_ADMIN_EMAIL
        assert prof["role"] == "admin"

    def test_caregiver_uuid_matches_across_stores(self):
        m = get_mongo_user_sync(SEEDED_CAREGIVER_UUID)
        assert m is not None and m["email"] == SEEDED_CAREGIVER_EMAIL

        u = supa_get_auth_user_by_email(SEEDED_CAREGIVER_EMAIL)
        assert u is not None
        assert u["id"] == SEEDED_CAREGIVER_UUID

        prof = get_profile_sync(SEEDED_CAREGIVER_UUID)
        assert prof is not None
        assert prof["role"] == "caregiver"

    def test_admin_can_login_both_modes(self, base_url):
        # Legacy
        r1 = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": SEEDED_ADMIN_EMAIL, "password": "Admin@123"},
            timeout=15,
        )
        assert r1.status_code == 200
        assert r1.json()["user"]["id"] == SEEDED_ADMIN_UUID
        # Supabase
        tok = supa_password_login(SEEDED_ADMIN_EMAIL, "AdminPassword123!")
        assert tok

    def test_caregiver_can_login_both_modes(self, base_url):
        r1 = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": SEEDED_CAREGIVER_EMAIL, "password": "Caregiver@123"},
            timeout=15,
        )
        assert r1.status_code == 200
        assert r1.json()["user"]["id"] == SEEDED_CAREGIVER_UUID
        tok = supa_password_login(SEEDED_CAREGIVER_EMAIL, "Caregiver123!")
        assert tok


# ==========================================================================
# TestCleanupParity — register then delete -> all stores clean
# ==========================================================================
class TestCleanupParity:
    def test_cleanup_removes_from_all_stores(self, base_url):
        email = _new_email("slicei-clean")
        password = "TestPass123!"
        r = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": password,
                  "name": "Clean Me", "role": "caregiver"},
            timeout=20,
        )
        assert r.status_code == 200
        uid = r.json()["user"]["id"]

        # Pre-condition: present in all 3
        assert get_mongo_user_sync(uid) is not None
        assert supa_get_auth_user_by_email(email) is not None
        assert count_profile_sync(uid) == 1

        # Cleanup
        rc = supa_delete_user(uid)
        assert rc in (200, 204), f"Supabase delete returned {rc}"
        deleted = delete_mongo_user_sync(uid)
        assert deleted == 1

        # Post-condition: gone from all 3 (profile via CASCADE)
        assert get_mongo_user_sync(uid) is None
        assert supa_get_auth_user_by_email(email) is None
        assert count_profile_sync(uid) == 0


# ==========================================================================
# TestRegressionAtoH — 10 endpoints x 2 JWTs (legacy + Supabase)
# ==========================================================================
REGRESSION_ENDPOINTS = [
    ("/api/auth/me", None),
    ("/api/caregivers", None),
    ("/api/clients", None),
    ("/api/assignments", None),
    ("/api/documents", None),
    ("/api/shifts", None),
    ("/api/chat/threads", None),
    ("/api/onboarding", {"caregiver_id": SEEDED_CAREGIVER_UUID}),
    ("/api/policies/acknowledgments", None),
    ("/api/training", None),
]


class TestRegressionAtoH:
    @pytest.mark.parametrize("endpoint,params", REGRESSION_ENDPOINTS)
    def test_with_legacy_admin_token(self, base_url, admin_token, endpoint, params):
        r = requests.get(
            f"{base_url}{endpoint}",
            headers={"Authorization": f"Bearer {admin_token}"},
            params=params or {}, timeout=20,
        )
        assert r.status_code == 200, f"{endpoint} legacy 200 expected, got {r.status_code} {r.text[:200]}"
        # Validate JSON shape: list or dict
        body = r.json()
        assert isinstance(body, (list, dict))

    @pytest.mark.parametrize("endpoint,params", REGRESSION_ENDPOINTS)
    def test_with_supabase_admin_token(self, base_url, supabase_admin_token, endpoint, params):
        r = requests.get(
            f"{base_url}{endpoint}",
            headers={"Authorization": f"Bearer {supabase_admin_token}"},
            params=params or {}, timeout=20,
        )
        assert r.status_code == 200, f"{endpoint} supabase 200 expected, got {r.status_code} {r.text[:200]}"
        body = r.json()
        assert isinstance(body, (list, dict))
