"""Legacy auth sanity checks — Supabase paused, verifying MongoDB /auth/login."""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://audit-prep-hub.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


class TestLegacyAuth:
    def test_admin_login_success(self, api):
        r = api.post(f"{BASE_URL}/api/auth/login",
                     json={"email": "admin@healthguard.com", "password": "Admin@123"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "access_token" in data and data["access_token"]
        assert data["user"]["role"] == "admin"
        assert data["user"]["email"] == "admin@healthguard.com"

    def test_caregiver_login_success(self, api):
        r = api.post(f"{BASE_URL}/api/auth/login",
                     json={"email": "caregiver@healthguard.com", "password": "Caregiver@123"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["user"]["role"] == "caregiver"

    def test_wrong_password_401(self, api):
        r = api.post(f"{BASE_URL}/api/auth/login",
                     json={"email": "admin@healthguard.com", "password": "wrongpass"})
        assert r.status_code == 401
        assert "Incorrect email or password" in r.text

    def test_authed_me_after_login(self, api):
        r = api.post(f"{BASE_URL}/api/auth/login",
                     json={"email": "admin@healthguard.com", "password": "Admin@123"})
        token = r.json()["access_token"]
        me = api.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["email"] == "admin@healthguard.com"
