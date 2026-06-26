"""Backend tests for Phase 2 of MongoDB->Supabase migration (dual-mode auth).

Validates:
- /api/supabase/status returns enabled=true and Postgres + Storage are reachable
- Legacy login flow still works and Mongo HS256 JWT authenticates protected routes
- Supabase login (via Supabase Auth REST) returns an ES256 JWT that the backend
  accepts on the same protected routes (looked up by email)
- /api/supabase/me returns merged Mongo UserPublic + Postgres profile row
- Garbage / expired tokens are rejected with 401
"""
import os
import pytest
import requests

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/')
SUPABASE_URL = 'https://wzshjedcbkygohcpytgf.supabase.co'
SUPABASE_ANON_KEY = 'sb_publishable_7b__UveZEujmPcy5yW71AQ_u6q93cvh'

ADMIN_EMAIL = 'admin@healthguard.com'
LEGACY_PWD = 'Admin@123'
SUPABASE_PWD = 'AdminPassword123!'

PROTECTED_ENDPOINTS = [
    '/api/stats',
    '/api/documents',
    '/api/clients',
    '/api/caregivers',
]


@pytest.fixture(scope='module')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


# --- /api/supabase/status ---
class TestSupabaseStatus:
    def test_status_enabled_and_db_ok(self, s):
        r = s.get(f'{BASE_URL}/api/supabase/status', timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get('enabled') is True, data
        assert data.get('db') == 'ok', data
        assert isinstance(data.get('tables'), int) and data['tables'] >= 15, data
        assert isinstance(data.get('profiles'), int) and data['profiles'] >= 2, data
        assert 'documents' in (data.get('storage_buckets') or []), data


# --- Legacy login flow ---
class TestLegacyAuth:
    @pytest.fixture(scope='class')
    def legacy_token(self, s):
        r = s.post(
            f'{BASE_URL}/api/auth/login',
            json={'email': ADMIN_EMAIL, 'password': LEGACY_PWD},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert 'access_token' in body
        assert body['user']['email'].lower() == ADMIN_EMAIL
        assert body['user']['role'] == 'admin'
        return body['access_token']

    def test_legacy_login_token_present(self, legacy_token):
        assert isinstance(legacy_token, str) and len(legacy_token) > 20

    @pytest.mark.parametrize('endpoint', PROTECTED_ENDPOINTS)
    def test_legacy_token_authenticates_protected_routes(self, s, legacy_token, endpoint):
        r = s.get(
            f'{BASE_URL}{endpoint}',
            headers={'Authorization': f'Bearer {legacy_token}'},
            timeout=20,
        )
        assert r.status_code == 200, f'{endpoint}: {r.status_code} {r.text}'
        # data structure sanity
        if endpoint == '/api/stats':
            j = r.json()
            assert isinstance(j, dict)
        else:
            assert isinstance(r.json(), list)


# --- Supabase login flow ---
@pytest.fixture(scope='module')
def supabase_token():
    r = requests.post(
        f'{SUPABASE_URL}/auth/v1/token',
        params={'grant_type': 'password'},
        headers={
            'apikey': SUPABASE_ANON_KEY,
            'Content-Type': 'application/json',
        },
        json={'email': ADMIN_EMAIL, 'password': SUPABASE_PWD},
        timeout=20,
    )
    assert r.status_code == 200, f'Supabase login failed: {r.status_code} {r.text}'
    body = r.json()
    token = body.get('access_token')
    assert token, body
    return token


class TestSupabaseAuth:
    def test_supabase_token_obtained(self, supabase_token):
        assert isinstance(supabase_token, str)
        # ES256 tokens are 3-part JWTs
        assert supabase_token.count('.') == 2

    @pytest.mark.parametrize('endpoint', PROTECTED_ENDPOINTS)
    def test_supabase_token_authenticates_protected_routes(self, s, supabase_token, endpoint):
        r = s.get(
            f'{BASE_URL}{endpoint}',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, f'{endpoint}: {r.status_code} {r.text}'

    def test_supabase_me_returns_merged_profile(self, s, supabase_token):
        r = s.get(
            f'{BASE_URL}/api/supabase/me',
            headers={'Authorization': f'Bearer {supabase_token}'},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get('user', {}).get('email', '').lower() == ADMIN_EMAIL
        assert body.get('user', {}).get('role') == 'admin'
        sp = body.get('supabase_profile')
        assert sp is not None, body
        assert sp.get('email', '').lower() == ADMIN_EMAIL


# --- 401 rejection ---
class TestInvalidTokens:
    def test_garbage_token_rejected(self, s):
        r = s.get(
            f'{BASE_URL}/api/stats',
            headers={'Authorization': 'Bearer not-a-real-jwt'},
            timeout=20,
        )
        assert r.status_code == 401, r.text

    def test_random_jwt_rejected(self, s):
        # Well-formed JWT structure but bogus signature/payload
        garbage = (
            'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWNrZXIifQ.invalid_signature_xxx'
        )
        r = s.get(
            f'{BASE_URL}/api/stats',
            headers={'Authorization': f'Bearer {garbage}'},
            timeout=20,
        )
        assert r.status_code == 401, r.text

    def test_missing_auth_rejected(self, s):
        r = s.get(f'{BASE_URL}/api/stats', timeout=20)
        assert r.status_code == 401, r.text
