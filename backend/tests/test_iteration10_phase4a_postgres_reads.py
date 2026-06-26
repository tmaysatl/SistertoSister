"""Phase 4 Slice A — first runtime cutover slice.

The following FastAPI routes now READ from Supabase Postgres (instead of MongoDB):
    GET /api/auth/me
    GET /api/caregivers
    GET /api/clients
    GET /api/clients/{id}
    GET /api/stats

All other routes (documents, assignments, shifts, chat, onboarding, packets,
policies, training, training_completions, document_views, packet_shares,
client_tasks, etc.) STILL read/write from MongoDB.

Auth dual-mode JWT verification (legacy HS256 + Supabase ES256 via JWKS) continues
to work. Profile lookups in core/security.py now query Postgres profiles first,
Mongo only as fallback.

This suite verifies:
  1. Legacy login (admin@healthguard.com / Admin@123) still works.
  2. Supabase login (admin@healthguard.com / AdminPassword123!) still works.
  3. The 5 converted endpoints serve correct data from Postgres under LEGACY JWT.
  4. The 5 converted endpoints serve correct data from Postgres under SUPABASE JWT.
  5. Non-converted endpoints (documents, assignments, onboarding, shifts) still
     work (i.e., Mongo regression).
  6. Postgres data parity via SUPABASE_DIRECT_URL: profiles=13, clients=1,
     documents=50.
"""
import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
import requests
from dotenv import load_dotenv

# Load backend/.env (Supabase creds) and frontend/.env (EXPO_PUBLIC_BACKEND_URL)
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / 'backend' / '.env')
load_dotenv(_ROOT / 'frontend' / '.env')

BASE_URL = (
    os.environ.get('EXPO_PUBLIC_BACKEND_URL')
    or os.environ.get('EXPO_BACKEND_URL')
    or 'http://localhost:8001'
).rstrip('/')
SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_ANON_KEY = os.environ['SUPABASE_ANON_KEY']
SUPABASE_DIRECT_URL = os.environ['SUPABASE_DIRECT_URL']

ADMIN_EMAIL = 'admin@healthguard.com'
LEGACY_PWD = 'Admin@123'
SUPABASE_PWD = 'AdminPassword123!'
EXPECTED_ADMIN_UUID = '8f16fe69-b54e-421a-bfe5-e14900e7bacd'
EXPECTED_CLIENT_PREFIX = 'caf304a5'

# Expected after Phase 4 Slice A
EXPECTED_TOTAL_CAREGIVERS = 12
EXPECTED_TOTAL_CLIENTS = 1
EXPECTED_TOTAL_DOCUMENTS = 50
EXPECTED_TOTAL_ASSIGNMENTS = 2  # still from Mongo
EXPECTED_PROFILES_PG = 13


@pytest.fixture(scope='module')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


# ---- Token fixtures ----
@pytest.fixture(scope='module')
def legacy_token(s):
    r = s.post(
        f'{BASE_URL}/api/auth/login',
        json={'email': ADMIN_EMAIL, 'password': LEGACY_PWD},
        timeout=20,
    )
    assert r.status_code == 200, f'legacy login failed: {r.status_code} {r.text}'
    return r.json()['access_token']


@pytest.fixture(scope='module')
def supabase_login():
    r = requests.post(
        f'{SUPABASE_URL}/auth/v1/token',
        params={'grant_type': 'password'},
        headers={'apikey': SUPABASE_ANON_KEY, 'Content-Type': 'application/json'},
        json={'email': ADMIN_EMAIL, 'password': SUPABASE_PWD},
        timeout=20,
    )
    assert r.status_code == 200, f'supabase login failed: {r.status_code} {r.text}'
    body = r.json()
    assert body.get('access_token'), body
    return body


@pytest.fixture(scope='module')
def supabase_token(supabase_login):
    return supabase_login['access_token']


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


# =====================================================================
# 1) Login regression — both modes
# =====================================================================
class TestLoginRegression:
    def test_legacy_login_returns_jwt(self, legacy_token):
        assert isinstance(legacy_token, str) and legacy_token.count('.') == 2

    def test_supabase_login_returns_es256_jwt(self, supabase_login):
        tok = supabase_login['access_token']
        assert tok.count('.') == 2
        # Header must reference ES256
        import base64
        import json
        header_b64 = tok.split('.')[0] + '=='
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        assert header.get('alg') == 'ES256', header
        # user.id == canonical UUID
        user = supabase_login.get('user') or {}
        assert user.get('id') == EXPECTED_ADMIN_UUID


# =====================================================================
# 2) Converted endpoints under LEGACY JWT
# =====================================================================
class TestConvertedEndpointsLegacy:
    def test_auth_me_legacy(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/auth/me', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get('id') == EXPECTED_ADMIN_UUID, body
        assert body.get('role') == 'admin', body
        assert (body.get('email') or '').lower() == ADMIN_EMAIL

    def test_caregivers_legacy(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/caregivers', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= EXPECTED_TOTAL_CAREGIVERS, (
            f'expected >={EXPECTED_TOTAL_CAREGIVERS}, got {len(data)}'
        )
        for u in data:
            assert u.get('role') == 'caregiver', u
            assert u.get('id')
            assert u.get('email')

    def test_clients_legacy(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/clients', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == EXPECTED_TOTAL_CLIENTS, f'expected 1 client, got {len(data)}'
        c = data[0]
        assert c.get('id', '').startswith(EXPECTED_CLIENT_PREFIX), c
        assert 'Test Client' in (c.get('name') or '') or 'Jones' in (c.get('name') or ''), c

    def test_client_by_id_legacy(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/clients', headers=_auth(legacy_token), timeout=20)
        client_id = r.json()[0]['id']
        r2 = s.get(
            f'{BASE_URL}/api/clients/{client_id}',
            headers=_auth(legacy_token), timeout=20,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json().get('id') == client_id

    def test_stats_legacy(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/stats', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['total_clients'] == EXPECTED_TOTAL_CLIENTS, body
        assert body['total_caregivers'] == EXPECTED_TOTAL_CAREGIVERS, body
        assert body['total_documents'] == EXPECTED_TOTAL_DOCUMENTS, body
        assert body['total_assignments'] == EXPECTED_TOTAL_ASSIGNMENTS, body
        assert isinstance(body['audit_readiness'], (int, float)), body


# =====================================================================
# 3) Converted endpoints under SUPABASE JWT — same data shape
# =====================================================================
class TestConvertedEndpointsSupabase:
    def test_auth_me_supabase(self, s, supabase_token):
        r = s.get(f'{BASE_URL}/api/auth/me', headers=_auth(supabase_token), timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get('id') == EXPECTED_ADMIN_UUID, body
        assert body.get('role') == 'admin', body

    def test_caregivers_supabase(self, s, supabase_token):
        r = s.get(f'{BASE_URL}/api/caregivers', headers=_auth(supabase_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= EXPECTED_TOTAL_CAREGIVERS

    def test_clients_supabase(self, s, supabase_token):
        r = s.get(f'{BASE_URL}/api/clients', headers=_auth(supabase_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data) == EXPECTED_TOTAL_CLIENTS

    def test_client_by_id_supabase(self, s, supabase_token):
        r = s.get(f'{BASE_URL}/api/clients', headers=_auth(supabase_token), timeout=20)
        client_id = r.json()[0]['id']
        r2 = s.get(
            f'{BASE_URL}/api/clients/{client_id}',
            headers=_auth(supabase_token), timeout=20,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()['id'] == client_id

    def test_stats_supabase(self, s, supabase_token):
        r = s.get(f'{BASE_URL}/api/stats', headers=_auth(supabase_token), timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['total_clients'] == EXPECTED_TOTAL_CLIENTS, body
        assert body['total_caregivers'] == EXPECTED_TOTAL_CAREGIVERS, body
        assert body['total_documents'] == EXPECTED_TOTAL_DOCUMENTS, body
        assert body['total_assignments'] == EXPECTED_TOTAL_ASSIGNMENTS, body

    def test_legacy_and_supabase_return_same_shape(self, s, legacy_token, supabase_token):
        # Compare /api/stats payloads — should be identical numeric data
        a = s.get(f'{BASE_URL}/api/stats', headers=_auth(legacy_token), timeout=20).json()
        b = s.get(f'{BASE_URL}/api/stats', headers=_auth(supabase_token), timeout=20).json()
        for k in ('total_clients', 'total_caregivers', 'total_documents',
                  'total_assignments', 'total_training'):
            assert a[k] == b[k], f'{k}: legacy={a[k]} vs supabase={b[k]}'


# =====================================================================
# 4) Non-converted endpoints still work (Mongo regression)
# =====================================================================
class TestMongoRegression:
    def test_documents_still_mongo(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/documents', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= EXPECTED_TOTAL_DOCUMENTS, len(data)

    def test_assignments_still_mongo(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/assignments', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        # assignments expected: 2 in Mongo
        assert len(data) == EXPECTED_TOTAL_ASSIGNMENTS

    def test_onboarding_still_mongo(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/onboarding', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)

    def test_shifts_still_mongo(self, s, legacy_token):
        r = s.get(f'{BASE_URL}/api/shifts', headers=_auth(legacy_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)


# =====================================================================
# 5) Postgres data parity via SUPABASE_DIRECT_URL
# =====================================================================
@pytest.fixture(scope='module')
def pg_counts():
    async def _run():
        conn = await asyncpg.connect(SUPABASE_DIRECT_URL, statement_cache_size=0)
        try:
            return {
                'profiles': await conn.fetchval('select count(*) from public.profiles'),
                'clients': await conn.fetchval('select count(*) from public.clients'),
                'documents': await conn.fetchval('select count(*) from public.documents'),
                'caregivers': await conn.fetchval(
                    "select count(*) from public.profiles where role='caregiver'"
                ),
            }
        finally:
            await conn.close()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError('closed')
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_run())


class TestPostgresParity:
    def test_profiles_count(self, pg_counts):
        assert pg_counts['profiles'] == EXPECTED_PROFILES_PG, pg_counts

    def test_clients_count(self, pg_counts):
        assert pg_counts['clients'] == EXPECTED_TOTAL_CLIENTS, pg_counts

    def test_documents_count(self, pg_counts):
        assert pg_counts['documents'] == EXPECTED_TOTAL_DOCUMENTS, pg_counts

    def test_caregivers_count(self, pg_counts):
        assert pg_counts['caregivers'] == EXPECTED_TOTAL_CAREGIVERS, pg_counts
