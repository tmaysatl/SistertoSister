"""Iteration 5 — Microsoft Graph (OneDrive + Outlook) OAuth surface.

Tests the /api/ms/* endpoints and that the audit-binder regression endpoint
still serves the same PDF after the helper refactor. Does NOT attempt the
real OAuth consent flow (that requires an interactive browser).
"""
import os
import urllib.parse as up

import pytest
import requests


# ---------- /api/ms/status ----------
class TestMsStatus:
    def test_admin_status_returns_configured_true(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/ms/status", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["configured"] is True, "MS_TENANT_ID/CLIENT_ID/CLIENT_SECRET/REDIRECT_URI must all be set"
        # connected may be true if a previous test left state — admin disconnect first
        assert "connected" in j
        assert "schedule" in j and j["schedule"]
        assert "America/New_York" in j["schedule"]
        assert "1st" in j["schedule"]

    def test_caregiver_status_forbidden(self, base_url, caregiver_headers):
        r = requests.get(f"{base_url}/api/ms/status", headers=caregiver_headers, timeout=30)
        assert r.status_code == 403, r.text


# ---------- /api/ms/auth-url ----------
class TestMsAuthUrl:
    def test_admin_auth_url_well_formed(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/ms/auth-url", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        parsed = up.urlparse(url)
        assert parsed.netloc == "login.microsoftonline.com", url
        tenant_id = os.environ.get("MS_TENANT_ID") or "f8812a89-52c8-4470-8549-e8c4be809795"
        client_id = os.environ.get("MS_CLIENT_ID") or "23be8faf-e8c6-4ada-98f4-ed09bd0d28aa"
        assert tenant_id in parsed.path, f"tenant_id missing from path: {parsed.path}"
        qs = up.parse_qs(parsed.query)
        assert qs.get("client_id", [""])[0] == client_id
        assert "redirect_uri" in qs
        assert qs["redirect_uri"][0].endswith("/api/ms/callback")
        scope = qs.get("scope", [""])[0]
        # MSAL Python automatically appends the reserved scopes including offline_access
        assert "offline_access" in scope, f"offline_access missing from scope: {scope}"
        # Should also include Files.ReadWrite (OneDrive) and Mail.Send (Outlook)
        assert "Files.ReadWrite" in scope
        assert "Mail.Send" in scope

    def test_caregiver_auth_url_forbidden(self, base_url, caregiver_headers):
        r = requests.get(f"{base_url}/api/ms/auth-url", headers=caregiver_headers, timeout=30)
        assert r.status_code == 403


# ---------- /api/ms/callback ----------
class TestMsCallback:
    def test_callback_with_error_returns_html_not_500(self, base_url):
        # No auth required — Microsoft redirects here from their side
        r = requests.get(
            f"{base_url}/api/ms/callback",
            params={"error": "access_denied", "error_description": "user cancelled"},
            timeout=30,
        )
        assert r.status_code in (200, 400), f"Should NOT be 500. Got {r.status_code}: {r.text[:200]}"
        ctype = r.headers.get("content-type", "")
        assert "text/html" in ctype, f"Expected HTML response, got {ctype}"
        body = r.text.lower()
        assert "cancel" in body or "sign-in" in body or "microsoft" in body

    def test_callback_with_no_code_no_error(self, base_url):
        r = requests.get(f"{base_url}/api/ms/callback", timeout=30)
        assert r.status_code in (200, 400)
        assert "text/html" in r.headers.get("content-type", "")


# ---------- /api/ms/disconnect & /api/ms/email-recipients ----------
class TestMsDisconnectAndEmail:
    def test_caregiver_disconnect_forbidden(self, base_url, caregiver_headers):
        r = requests.post(f"{base_url}/api/ms/disconnect", headers=caregiver_headers, timeout=30)
        assert r.status_code == 403

    def test_admin_disconnect_ok(self, base_url, admin_headers):
        r = requests.post(f"{base_url}/api/ms/disconnect", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        # confirm connected flips to false
        r2 = requests.get(f"{base_url}/api/ms/status", headers=admin_headers, timeout=30)
        assert r2.status_code == 200
        assert r2.json()["connected"] is False

    def test_caregiver_email_recipients_forbidden(self, base_url, caregiver_headers):
        r = requests.post(
            f"{base_url}/api/ms/email-recipients",
            headers=caregiver_headers,
            json={"email_to": "x@y.com"},
            timeout=30,
        )
        assert r.status_code == 403

    def test_admin_set_email_recipients_persists(self, base_url, admin_headers):
        recipients = "TEST_owner@example.com, TEST_compliance@example.com"
        r = requests.post(
            f"{base_url}/api/ms/email-recipients",
            headers=admin_headers,
            json={"email_to": recipients},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        # verify persisted via GET /status
        r2 = requests.get(f"{base_url}/api/ms/status", headers=admin_headers, timeout=30)
        assert r2.status_code == 200
        assert r2.json().get("email_to") == recipients

        # cleanup — clear it
        requests.post(
            f"{base_url}/api/ms/email-recipients",
            headers=admin_headers,
            json={"email_to": ""},
            timeout=30,
        )


# ---------- /api/ms/export-now ----------
class TestMsExportNow:
    def test_caregiver_export_forbidden(self, base_url, caregiver_headers):
        r = requests.post(f"{base_url}/api/ms/export-now", headers=caregiver_headers, timeout=30)
        assert r.status_code == 403

    def test_admin_export_without_connection_returns_400_not_connected(
        self, base_url, admin_headers
    ):
        # First ensure we are disconnected so there's no refresh_token
        requests.post(f"{base_url}/api/ms/disconnect", headers=admin_headers, timeout=30)
        r = requests.post(f"{base_url}/api/ms/export-now", headers=admin_headers, timeout=60)
        assert r.status_code == 400, r.text
        j = r.json()
        detail = j.get("detail") or j.get("reason") or ""
        assert "not_connected" in str(detail).lower(), j


# ---------- Regression: /api/reports/audit-binder still works ----------
class TestAuditBinderRegression:
    def test_admin_can_download_audit_binder_pdf(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/reports/audit-binder",
            headers=admin_headers,
            timeout=120,
        )
        assert r.status_code == 200, r.text[:200]
        assert r.headers.get("content-type", "").startswith("application/pdf")
        # First bytes of any PDF must be "%PDF-"
        assert r.content[:5] == b"%PDF-", r.content[:20]
        assert len(r.content) > 1000, "PDF suspiciously tiny"

    def test_caregiver_cannot_download_audit_binder(self, base_url, caregiver_headers):
        r = requests.get(
            f"{base_url}/api/reports/audit-binder",
            headers=caregiver_headers,
            timeout=60,
        )
        assert r.status_code == 403


# ---------- Regression: unrelated existing endpoints still work ----------
class TestUnrelatedRegression:
    def test_login_still_works(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@healthguard.com", "password": "Admin@123"},
            timeout=30,
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_documents_list_still_works(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/documents", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_shifts_list_still_works(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/shifts", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_assignments_list_still_works(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/assignments", headers=admin_headers, timeout=30)
        assert r.status_code == 200

    def test_caregivers_list_still_works(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/caregivers", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------- No-auth: every /api/ms/* must require auth ----------
class TestMsAuthRequired:
    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/ms/status"),
        ("GET", "/api/ms/auth-url"),
        ("POST", "/api/ms/disconnect"),
        ("POST", "/api/ms/email-recipients"),
        ("POST", "/api/ms/export-now"),
    ])
    def test_endpoint_requires_auth(self, base_url, method, path):
        r = requests.request(method, f"{base_url}{path}", json={}, timeout=30)
        assert r.status_code in (401, 403), f"{method} {path} returned {r.status_code}, expected 401/403"
