"""Backend tests for Phase 3 of MongoDB -> Supabase migration (DATA COPY).

Phase 3 is a COPY-ONLY operation:
  - Mongo still serves all runtime traffic via FastAPI.
  - Supabase Postgres + Storage now have a parallel snapshot of the same data.

This suite verifies:
1. /api/supabase/status reflects migrated row counts (profiles>=13, tables>=15,
   storage bucket 'documents' present).
2. Legacy and Supabase auth still work end-to-end (regression vs Phase 2).
3. Supabase Auth UUID == Mongo user UUID for the admin (identity preserved).
4. /api/stats with Supabase JWT returns Mongo counters: 1 client, 12 caregivers,
   50 documents, 0 trainings.
5. /api/clients (1), /api/caregivers (12), /api/documents (50) still return Mongo data.
6. /api/supabase/me returns merged profile + Mongo identity.
7. Direct Supabase Postgres row counts match migration expectations.
8. profiles.id in Supabase exactly matches users.id in MongoDB (UUID alignment).
"""
import asyncio
import os

import asyncpg
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/')
SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_ANON_KEY = os.environ['SUPABASE_ANON_KEY']
SUPABASE_DIRECT_URL = os.environ['SUPABASE_DIRECT_URL']
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']

ADMIN_EMAIL = 'admin@healthguard.com'
LEGACY_PWD = 'Admin@123'
SUPABASE_PWD = 'AdminPassword123!'
EXPECTED_ADMIN_UUID = '8f16fe69-b54e-421a-bfe5-e14900e7bacd'

# Expected row counts after Phase 3 copy (from the review request)
EXPECTED_COUNTS = {
    'profiles': 13,
    'clients': 1,
    'documents': 50,
    'assignments': 2,
    'shifts': 3,
    'chat_messages': 24,
    'chat_dms': 2,
    'client_tasks': 13,
    'onboarding': 52,
}


@pytest.fixture(scope='module')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


# ---- Token fixtures (module scope) ----
@pytest.fixture(scope='module')
def legacy_token(s):
    r = s.post(
        f'{BASE_URL}/api/auth/login',
        json={'email': ADMIN_EMAIL, 'password': LEGACY_PWD},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    return r.json()['access_token']


@pytest.fixture(scope='module')
def supabase_login():
    """Return the full Supabase /auth/v1/token response so tests can assert on
    user.id (UUID alignment) as well as the access_token."""
    r = requests.post(
        f'{SUPABASE_URL}/auth/v1/token',
        params={'grant_type': 'password'},
        headers={'apikey': SUPABASE_ANON_KEY, 'Content-Type': 'application/json'},
        json={'email': ADMIN_EMAIL, 'password': SUPABASE_PWD},
        timeout=20,
    )
    assert r.status_code == 200, f'Supabase login failed: {r.status_code} {r.text}'
    body = r.json()
    assert body.get('access_token'), body
    return body


@pytest.fixture(scope='module')
def supabase_token(supabase_login):
    return supabase_login['access_token']


# =====================================================================
# 1) /api/supabase/status
# =====================================================================
class TestSupabaseStatus:
    def test_status_reflects_migration(self, s):
        r = s.get(f'{BASE_URL}/api/supabase/status', timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get('enabled') is True, data
        assert data.get('db') == 'ok', data
        assert isinstance(data.get('tables'), int) and data['tables'] >= 15, data
        # After Phase 3, profiles should equal 13 (all Mongo users migrated)
        assert data.get('profiles') == EXPECTED_COUNTS['profiles'], (
            f"expected profiles={EXPECTED_COUNTS['profiles']}, got {data.get('profiles')}"
        )
        assert 'documents' in (data.get('storage_buckets') or []), data


# =====================================================================
# 2) Regression: legacy auth still works against Mongo
# =====================================================================
class TestLegacyAuthRegression:
    def test_legacy_login_succeeds(self, legacy_token):
        assert isinstance(legacy_token, str) and len(legacy_token) > 20

    def test_legacy_token_hits_stats(self, s, legacy_token):
        r = s.get(
            f'{BASE_URL}/api/stats',
            headers={'Authorization': f'Bearer {legacy_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['total_clients'] == 1, body
        assert body['total_caregivers'] == 12, body
        assert body['total_documents'] == 50, body
        assert body['total_training'] == 0, body


# =====================================================================
# 3) Supabase auth still works + UUID alignment
# =====================================================================
class TestSupabaseAuthAndUUIDAlignment:
    def test_supabase_login_returns_expected_uuid(self, supabase_login):
        user = supabase_login.get('user') or {}
        assert user.get('id') == EXPECTED_ADMIN_UUID, (
            f"Supabase Auth admin UUID should equal Mongo UUID "
            f"{EXPECTED_ADMIN_UUID}, got {user.get('id')}"
        )
        assert (user.get('email') or '').lower() == ADMIN_EMAIL

    def test_supabase_token_is_jwt(self, supabase_token):
        assert supabase_token.count('.') == 2

    @pytest.mark.parametrize('endpoint', [
        '/api/stats', '/api/clients', '/api/caregivers', '/api/documents',
    ])
    def test_supabase_token_authorises_protected_routes(self, s, supabase_token, endpoint):
        r = s.get(
            f'{BASE_URL}{endpoint}',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, f'{endpoint}: {r.status_code} {r.text}'


# =====================================================================
# 4) /api/stats with Supabase JWT returns Mongo dashboard counters
# =====================================================================
class TestStatsCounters:
    def test_stats_counters_match_mongo(self, s, supabase_token):
        r = s.get(
            f'{BASE_URL}/api/stats',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['total_clients'] == 1, body
        assert body['total_caregivers'] == 12, body
        assert body['total_documents'] == 50, body
        assert body['total_training'] == 0, body


# =====================================================================
# 5) /api/clients, /api/caregivers, /api/documents return Mongo data
# =====================================================================
class TestMongoCollectionsViaApi:
    def test_clients_list(self, s, supabase_token):
        r = s.get(
            f'{BASE_URL}/api/clients',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1, f'expected 1 client, got {len(data)}'

    def test_caregivers_list(self, s, supabase_token):
        r = s.get(
            f'{BASE_URL}/api/caregivers',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 12, f'expected 12 caregivers, got {len(data)}'
        # each must have id + email + role==caregiver
        for u in data:
            assert u.get('role') == 'caregiver', u
            assert u.get('id')

    def test_documents_list(self, s, supabase_token):
        r = s.get(
            f'{BASE_URL}/api/documents',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 50, f'expected 50 documents, got {len(data)}'


# =====================================================================
# 6) /api/supabase/me — merged profile
# =====================================================================
class TestSupabaseMe:
    def test_me_returns_merged_profile(self, s, supabase_token):
        r = s.get(
            f'{BASE_URL}/api/supabase/me',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        user = body.get('user') or {}
        assert (user.get('email') or '').lower() == ADMIN_EMAIL
        assert user.get('role') == 'admin'
        sp = body.get('supabase_profile')
        assert sp is not None, body
        assert (sp.get('email') or '').lower() == ADMIN_EMAIL
        assert sp.get('role') == 'admin'
        # Profile UUID should equal Mongo UUID
        assert sp.get('id') == EXPECTED_ADMIN_UUID, sp


# =====================================================================
# 7) Direct Supabase Postgres row counts
# =====================================================================
@pytest.fixture(scope='module')
def pg_counts():
    async def _run():
        conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
        try:
            out = {}
            for tbl in EXPECTED_COUNTS:
                out[tbl] = await conn.fetchval(f'select count(*) from public.{tbl}')
            return out
        finally:
            await conn.close()
    return asyncio.get_event_loop().run_until_complete(_run())


class TestSupabasePgCounts:
    @pytest.mark.parametrize('table,expected', list(EXPECTED_COUNTS.items()))
    def test_table_row_count(self, pg_counts, table, expected):
        actual = pg_counts.get(table)
        assert actual == expected, (
            f'public.{table} expected={expected}, actual={actual}'
        )


# =====================================================================
# 8) UUID alignment: profiles.id == users.id from MongoDB
# =====================================================================
@pytest.fixture(scope='module')
def uuid_alignment():
    async def _run():
        mongo = AsyncIOMotorClient(MONGO_URL)
        try:
            mongo_users = await mongo[DB_NAME].users.find(
                {}, {'_id': 0, 'id': 1, 'email': 1}
            ).to_list(500)
            mongo_ids = {u['id'] for u in mongo_users if u.get('id')}
            mongo_id_to_email = {
                u['id']: (u.get('email') or '').lower()
                for u in mongo_users if u.get('id')
            }
        finally:
            mongo.close()

        conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
        try:
            rows = await conn.fetch(
                'select id::text as id, email from public.profiles'
            )
        finally:
            await conn.close()
        pg_ids = {r['id'] for r in rows}
        pg_id_to_email = {r['id']: (r['email'] or '').lower() for r in rows}
        return mongo_ids, pg_ids, mongo_id_to_email, pg_id_to_email
    return asyncio.get_event_loop().run_until_complete(_run())


class TestUuidAlignment:
    def test_mongo_user_count(self, uuid_alignment):
        mongo_ids, *_ = uuid_alignment
        assert len(mongo_ids) == 13, f'expected 13 Mongo users, got {len(mongo_ids)}'

    def test_pg_profile_count(self, uuid_alignment):
        _, pg_ids, *_ = uuid_alignment
        assert len(pg_ids) == 13, f'expected 13 PG profiles, got {len(pg_ids)}'

    def test_uuids_match_exactly(self, uuid_alignment):
        mongo_ids, pg_ids, *_ = uuid_alignment
        missing_in_pg = mongo_ids - pg_ids
        extra_in_pg = pg_ids - mongo_ids
        assert not missing_in_pg, f'Mongo UUIDs missing from Supabase: {missing_in_pg}'
        assert not extra_in_pg, f'Supabase profile UUIDs not in Mongo: {extra_in_pg}'

    def test_admin_uuid_is_preserved_mongo_uuid(self, uuid_alignment):
        mongo_ids, pg_ids, _, pg_id_to_email = uuid_alignment
        assert EXPECTED_ADMIN_UUID in mongo_ids, (
            f'Expected admin UUID {EXPECTED_ADMIN_UUID} not found in Mongo'
        )
        assert EXPECTED_ADMIN_UUID in pg_ids, (
            f'Expected admin UUID {EXPECTED_ADMIN_UUID} not found in PG profiles'
        )
        assert pg_id_to_email.get(EXPECTED_ADMIN_UUID) == ADMIN_EMAIL

    def test_emails_align_for_same_uuid(self, uuid_alignment):
        _, _, mongo_id_to_email, pg_id_to_email = uuid_alignment
        mismatches = []
        for uid, email in mongo_id_to_email.items():
            pg_email = pg_id_to_email.get(uid)
            if pg_email and email and pg_email != email:
                mismatches.append((uid, email, pg_email))
        assert not mismatches, f'Email mismatches per UUID: {mismatches[:5]}'
