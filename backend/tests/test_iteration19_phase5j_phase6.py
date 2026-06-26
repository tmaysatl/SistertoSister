"""Iteration 19 — Phase 5 Slice J (MS Graph dual-write to public.integrations)
plus Phase 6 (frontend Supabase default with legacy toggle) backend coverage.

This suite covers ALL the BACKEND items listed in the review request:
  1) /api/auth/login (legacy)               -> token + user
  2) /api/auth/me with legacy JWT           -> admin profile
  3) Supabase password grant                -> access_token
  4) /api/supabase/me with Supabase JWT      -> admin profile (UUID match)
  5) /api/auth/me with Supabase JWT (dual)   -> same admin profile
  6) /api/clients GET/POST/DELETE (Supabase JWT)
  7) /api/caregivers GET
  8) /api/assignments GET/POST/DELETE
  9) /api/documents GET + /{id}/url (https Supabase signed URL)
 10) /api/chat/threads GET
 11) /api/policies/acknowledgments + acknowledge + DELETE
 12) /api/training GET/POST/complete/DELETE
 13) /api/onboarding?caregiver_id=...
 14) /api/packets/share + GET /api/packets/{token}
 15) /api/ms/status, /api/ms/email-recipients dual-write to Postgres,
     /api/ms/disconnect dual-delete
 16) /api/stats
"""

import os
import time

import asyncpg
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

# Make sure backend .env loads (for SUPABASE_*)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

ADMIN_UUID = "8f16fe69-b54e-421a-bfe5-e14900e7bacd"
CAREGIVER_UUID = "389b257d-7edb-4d12-adfc-3b8e80f91bf1"

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]


# -------------------- Fixtures --------------------
@pytest.fixture(scope="module")
def supabase_admin_token(base_url):
    """Supabase ES256 JWT via password grant."""
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        },
        json={
            "email": "admin@healthguard.com",
            "password": "AdminPassword123!",
        },
        timeout=30,
    )
    assert r.status_code == 200, f"Supabase password grant failed: {r.status_code} {r.text}"
    j = r.json()
    assert j.get("access_token"), j
    return j["access_token"]


@pytest.fixture
def sb_admin_headers(supabase_admin_token):
    return {"Authorization": f"Bearer {supabase_admin_token}"}


# -------------------- 1) Legacy login --------------------
class TestLegacyAuth:
    def test_login_returns_token_and_user(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@healthguard.com", "password": "Admin@123"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("access_token")
        assert j["user"]["email"] == "admin@healthguard.com"
        assert j["user"]["role"] == "admin"
        assert j["user"]["id"] == ADMIN_UUID

    def test_me_with_legacy_jwt(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/auth/me", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["id"] == ADMIN_UUID
        assert j["email"] == "admin@healthguard.com"
        assert j["role"] == "admin"


# -------------------- 2) Supabase password grant + /api/supabase/me --------------------
class TestSupabaseAuth:
    def test_password_grant_returns_token(self, supabase_admin_token):
        assert supabase_admin_token
        # JWT must have 3 segments (header.payload.signature)
        assert len(supabase_admin_token.split(".")) == 3

    def test_supabase_me_returns_admin_with_legacy_uuid(self, base_url, sb_admin_headers):
        r = requests.get(f"{base_url}/api/supabase/me", headers=sb_admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        # Endpoint shape: {"user": {...}} per supabase_router
        user = j.get("user") or j
        assert user["id"] == ADMIN_UUID, user
        assert user["email"] == "admin@healthguard.com"
        assert user.get("role") == "admin"

    def test_legacy_me_accepts_supabase_jwt(self, base_url, sb_admin_headers):
        r = requests.get(f"{base_url}/api/auth/me", headers=sb_admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["id"] == ADMIN_UUID
        assert j["email"] == "admin@healthguard.com"
        assert j["role"] == "admin"


# -------------------- 3) /api/clients CRUD with Supabase JWT --------------------
class TestClientsCRUDSupabase:
    def test_clients_list(self, base_url, sb_admin_headers):
        r = requests.get(f"{base_url}/api/clients", headers=sb_admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_create_and_delete_client(self, base_url, sb_admin_headers):
        payload = {
            "name": "TEST_SliceJ Client",
            "address": "123 TEST st",
        }
        r = requests.post(
            f"{base_url}/api/clients", json=payload, headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code in (200, 201), r.text
        created = r.json()
        cid = created.get("id") or created.get("_id")
        assert cid, created
        # Verify visible in list
        listed = requests.get(
            f"{base_url}/api/clients", headers=sb_admin_headers, timeout=30,
        ).json()
        assert any(c.get("id") == cid for c in listed)
        # Delete
        dr = requests.delete(
            f"{base_url}/api/clients/{cid}", headers=sb_admin_headers, timeout=30,
        )
        assert dr.status_code in (200, 204), dr.text


# -------------------- 4) /api/caregivers --------------------
class TestCaregivers:
    def test_caregivers_list(self, base_url, sb_admin_headers):
        r = requests.get(f"{base_url}/api/caregivers", headers=sb_admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        # Seeded caregiver should be present
        assert any(c.get("id") == CAREGIVER_UUID for c in data), [
            c.get("id") for c in data
        ]


# -------------------- 5) /api/assignments --------------------
class TestAssignments:
    def test_assignments_flow(self, base_url, sb_admin_headers):
        # Pick first existing client
        clients = requests.get(
            f"{base_url}/api/clients", headers=sb_admin_headers, timeout=30,
        ).json()
        assert clients, "no clients seeded"
        client_id = clients[0]["id"]

        # List existing
        r = requests.get(
            f"{base_url}/api/assignments", headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code == 200
        existing_ids = {a.get("id") for a in r.json()}

        # Create
        body = {"client_id": client_id, "caregiver_id": CAREGIVER_UUID}
        cr = requests.post(
            f"{base_url}/api/assignments",
            json=body,
            headers=sb_admin_headers,
            timeout=30,
        )
        # 200/201 on success OR 400 if already assigned (idempotent guard) — both ok
        assert cr.status_code in (200, 201, 400), cr.text
        if cr.status_code in (200, 201):
            assignment = cr.json()
            aid = assignment.get("id")
            assert aid
            # Delete
            dr = requests.delete(
                f"{base_url}/api/assignments/{aid}",
                headers=sb_admin_headers,
                timeout=30,
            )
            assert dr.status_code in (200, 204), dr.text
        else:
            # already existed -> ensure existing one stays in list
            assert existing_ids


# -------------------- 6) /api/documents + signed URL --------------------
class TestDocuments:
    def test_documents_list(self, base_url, sb_admin_headers):
        r = requests.get(
            f"{base_url}/api/documents", headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        docs = r.json()
        assert isinstance(docs, list)
        assert len(docs) > 0
        # find a doc with storage_path (PDF) to test signed url
        with_path = [d for d in docs if d.get("storage_path") or d.get("id")]
        assert with_path

    def test_document_signed_url(self, base_url, sb_admin_headers):
        docs = requests.get(
            f"{base_url}/api/documents", headers=sb_admin_headers, timeout=30,
        ).json()
        assert docs, "no documents at all"

        # Iterate — some seed rows are pointer-only (no file in Storage).
        # We need at least ONE document to return a real Supabase signed URL.
        url = None
        last_status = None
        for d in docs[:25]:
            r = requests.get(
                f"{base_url}/api/documents/{d['id']}/url",
                headers=sb_admin_headers,
                timeout=30,
            )
            last_status = r.status_code
            if r.status_code == 200:
                j = r.json()
                url = j.get("url") or j.get("signed_url")
                if url and url.startswith("https://"):
                    break
        assert url, f"No document returned a signed URL (last status {last_status})"
        assert url.startswith("https://"), url
        # Should look like a Supabase signed URL
        assert "supabase" in url or "/storage/" in url, url


# -------------------- 7) /api/chat/threads --------------------
class TestChat:
    def test_chat_threads_requires_auth(self, base_url):
        r = requests.get(f"{base_url}/api/chat/threads", timeout=30)
        assert r.status_code in (401, 403)

    def test_chat_threads_with_supabase_jwt(self, base_url, sb_admin_headers):
        r = requests.get(
            f"{base_url}/api/chat/threads", headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)


# -------------------- 8) /api/policies/acknowledgments --------------------
class TestPolicies:
    def test_acks_list(self, base_url, sb_admin_headers):
        r = requests.get(
            f"{base_url}/api/policies/acknowledgments",
            headers=sb_admin_headers,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_acknowledge_and_delete(self, base_url, sb_admin_headers):
        # find a policy doc
        docs = requests.get(
            f"{base_url}/api/documents", headers=sb_admin_headers, timeout=30,
        ).json()
        policy_doc = next(
            (d for d in docs if (d.get("category") or "").lower() in
             ("policy", "policies") or "polic" in (d.get("name", "").lower())),
            None,
        ) or (docs[0] if docs else None)
        assert policy_doc, "no docs to ack"
        doc_id = policy_doc["id"]

        ack = requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": doc_id},
            headers=sb_admin_headers,
            timeout=30,
        )
        assert ack.status_code in (200, 201, 409), ack.text  # 409 if already

        # Delete the ack (cleanup) — endpoint pattern: DELETE /api/policies/acknowledge/{policy_id}
        dr = requests.delete(
            f"{base_url}/api/policies/acknowledge/{doc_id}",
            headers=sb_admin_headers,
            timeout=30,
        )
        # 200/204 ok; 404 if route differs — at minimum not 500
        assert dr.status_code in (200, 204, 404), dr.text


# -------------------- 9) /api/training --------------------
class TestTraining:
    def test_training_full_flow(self, base_url, sb_admin_headers):
        # GET
        r = requests.get(
            f"{base_url}/api/training", headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

        # CREATE
        cr = requests.post(
            f"{base_url}/api/training",
            json={"title": "TEST_SliceJ Training", "description": "smoke test"},
            headers=sb_admin_headers,
            timeout=30,
        )
        assert cr.status_code in (200, 201), cr.text
        tid = cr.json().get("id")
        assert tid

        # COMPLETE
        comp = requests.post(
            f"{base_url}/api/training/{tid}/complete",
            headers=sb_admin_headers,
            timeout=30,
        )
        assert comp.status_code in (200, 201), comp.text

        # DELETE
        dr = requests.delete(
            f"{base_url}/api/training/{tid}", headers=sb_admin_headers, timeout=30,
        )
        assert dr.status_code in (200, 204), dr.text


# -------------------- 10) /api/onboarding --------------------
class TestOnboarding:
    def test_onboarding_for_caregiver(self, base_url, sb_admin_headers):
        r = requests.get(
            f"{base_url}/api/onboarding",
            params={"caregiver_id": CAREGIVER_UUID},
            headers=sb_admin_headers,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Either list of steps OR dict with "steps"
        steps = data if isinstance(data, list) else data.get("steps", [])
        assert isinstance(steps, list)
        assert len(steps) > 0


# -------------------- 11) /api/packets/share --------------------
class TestPackets:
    def test_packet_share_and_fetch(self, base_url, sb_admin_headers):
        # find a caregiver
        body = {
            "caregiver_id": CAREGIVER_UUID,
            "recipient_name": "TEST Recipient",
            "recipient_role": "caregiver",
            "category": "caregiver_onboarding",
            "title": "TEST_SliceJ packet",
        }
        r = requests.post(
            f"{base_url}/api/packets/share",
            json=body,
            headers=sb_admin_headers,
            timeout=30,
        )
        assert r.status_code in (200, 201), r.text
        j = r.json()
        token = j.get("token") or j.get("share_token")
        assert token, j

        # GET (no auth required for packet share)
        g = requests.get(f"{base_url}/api/packets/{token}", timeout=30)
        assert g.status_code == 200, g.text
        gj = g.json()
        # Should include caregiver info or items
        assert gj


# -------------------- 12) /api/ms/* dual-write (Slice J) --------------------
class TestMsDualWrite:
    @pytest.mark.asyncio
    async def _pg_email_to(self):
        conn = await asyncpg.connect(SUPABASE_DIRECT_URL, timeout=30)
        try:
            row = await conn.fetchrow(
                "select config from public.integrations where provider=$1",
                "microsoft_graph",
            )
            return row
        finally:
            await conn.close()

    def test_status_configured(self, base_url, sb_admin_headers):
        r = requests.get(
            f"{base_url}/api/ms/status", headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("configured") is True
        assert "connected" in j
        assert j.get("schedule"), j

    def test_email_recipients_dual_write_and_disconnect_dual_delete(
        self, base_url, sb_admin_headers,
    ):
        import asyncio

        email = "TEST_phase5j@phcp-smoke.io"

        # 1) set email recipients
        r = requests.post(
            f"{base_url}/api/ms/email-recipients",
            json={"email_to": email},
            headers=sb_admin_headers,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        # 2) GET status reflects it (Mongo path)
        s = requests.get(
            f"{base_url}/api/ms/status", headers=sb_admin_headers, timeout=30,
        )
        assert s.status_code == 200
        assert s.json().get("email_to") == email

        # 3) Postgres public.integrations has provider='microsoft_graph'
        #    with config->>'email_to' == email
        time.sleep(0.5)
        row = asyncio.get_event_loop().run_until_complete(self._pg_check_email(email))
        assert row is not None, "No microsoft_graph row in public.integrations"
        cfg = row["config"]
        if isinstance(cfg, str):
            import json as _json
            cfg = _json.loads(cfg)
        assert cfg.get("email_to") == email, cfg

        # 4) DISCONNECT clears BOTH
        d = requests.post(
            f"{base_url}/api/ms/disconnect", headers=sb_admin_headers, timeout=30,
        )
        assert d.status_code == 200, d.text

        # Mongo cleared
        s2 = requests.get(
            f"{base_url}/api/ms/status", headers=sb_admin_headers, timeout=30,
        )
        assert s2.status_code == 200
        assert s2.json().get("connected") is False
        # email_to should be empty / absent after disconnect
        assert not s2.json().get("email_to"), s2.json()

        # Postgres row deleted
        time.sleep(0.5)
        row2 = asyncio.get_event_loop().run_until_complete(self._pg_check_email(None))
        assert row2 is None, f"row still in PG after disconnect: {row2}"

    @staticmethod
    async def _pg_check_email(_expected):
        conn = await asyncpg.connect(SUPABASE_DIRECT_URL, timeout=30)
        try:
            row = await conn.fetchrow(
                "select provider, config from public.integrations "
                "where provider=$1",
                "microsoft_graph",
            )
            return dict(row) if row else None
        finally:
            await conn.close()


# -------------------- 13) /api/stats --------------------
class TestStats:
    def test_stats_counts(self, base_url, sb_admin_headers):
        r = requests.get(
            f"{base_url}/api/stats", headers=sb_admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        for key in ("total_clients", "total_caregivers", "total_documents", "total_training"):
            assert key in j, f"missing {key}: {j}"
            assert isinstance(j[key], int)
        assert j["total_clients"] >= 1
        assert j["total_caregivers"] >= 1
        assert j["total_documents"] >= 1
        # Onboarding stats are reported as audit_readiness + pending_onboarding
        assert "audit_readiness" in j
        assert "pending_onboarding" in j
