"""Iteration 7 tests:
1. Real fillable AcroForm PDFs for client/caregiver onboarding docs
2. Policy acknowledgment endpoints (idempotent upsert / RBAC / delete)
3. Regression smoke for existing endpoints (login, shifts, ms/status, audit-binder, replication PDFs, doc push, assignment, chat)
"""
import base64
import os

import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")


# ----------------------- Fillable docs -------------------------
class TestRebuildFillable:
    """POST /api/documents/rebuild-fillable + idempotency."""

    def test_rebuild_fillable_first_call(self, base_url, admin_headers):
        r = requests.post(
            f"{base_url}/api/documents/rebuild-fillable",
            headers=admin_headers, timeout=120,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 5 client + 14 caregiver = 19 expected updates each call
        assert body.get("updated") == 19, body
        assert "deleted_duplicates" in body

    def test_rebuild_fillable_idempotent(self, base_url, admin_headers):
        """Second call should not produce duplicates (deleted_duplicates == 0)."""
        r1 = requests.post(
            f"{base_url}/api/documents/rebuild-fillable",
            headers=admin_headers, timeout=120,
        )
        assert r1.status_code == 200
        r2 = requests.post(
            f"{base_url}/api/documents/rebuild-fillable",
            headers=admin_headers, timeout=120,
        )
        assert r2.status_code == 200, r2.text
        b2 = r2.json()
        assert b2.get("updated") == 19
        assert b2.get("deleted_duplicates", 0) == 0, (
            f"Second rebuild should not delete duplicates, got {b2}"
        )

    def test_rebuild_fillable_forbidden_for_caregiver(self, base_url, caregiver_headers):
        r = requests.post(
            f"{base_url}/api/documents/rebuild-fillable",
            headers=caregiver_headers, timeout=30,
        )
        assert r.status_code in (401, 403), r.status_code


class TestOnboardingLists:
    """GET /api/documents?category=... contracts."""

    def test_client_onboarding_returns_13_unique_docs(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/documents?category=client_onboarding",
            headers=admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        docs = r.json()
        assert len(docs) == 13, (
            f"Expected 13 client_onboarding docs, got {len(docs)}: "
            f"{[d['title'] for d in docs]}"
        )
        seqs = sorted([d.get("seq") for d in docs])
        assert seqs == list(range(1, 14)), f"Got seqs {seqs}"
        titles = [d["title"] for d in docs]
        assert len(set(titles)) == len(titles), f"Duplicate titles: {titles}"
        # All must have file_base64 populated
        for d in docs:
            assert d.get("file_base64"), f"{d['title']} missing file_base64"

    def test_caregiver_onboarding_returns_14_unique_docs(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/documents?category=caregiver_onboarding",
            headers=admin_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        docs = r.json()
        assert len(docs) == 14, (
            f"Expected 14 caregiver_onboarding docs, got {len(docs)}: "
            f"{[d['title'] for d in docs]}"
        )
        seqs = sorted([d.get("seq") for d in docs])
        assert seqs == list(range(1, 15)), f"Got seqs {seqs}"
        titles = [d["title"] for d in docs]
        assert len(set(titles)) == len(titles), f"Duplicate titles: {titles}"

    def test_caregiver_onboarding_all_fillable_pdfs(self, base_url, admin_headers):
        """All 14 caregiver onboarding docs must have file_base64 populated,
        each >5KB, and contain real AcroForm markers."""
        r = requests.get(
            f"{base_url}/api/documents?category=caregiver_onboarding",
            headers=admin_headers, timeout=30,
        )
        assert r.status_code == 200
        docs = r.json()
        assert len(docs) == 14
        for d in docs:
            b64 = d.get("file_base64")
            assert b64, f"{d['title']} missing file_base64"
            raw = base64.b64decode(b64)
            assert len(raw) > 5 * 1024, (
                f"{d['title']} PDF too small ({len(raw)} bytes)"
            )
            # AcroForm markers
            assert b"/AcroForm" in raw, f"{d['title']} missing /AcroForm dict"
            assert b"/T (" in raw or b"/T(" in raw, (
                f"{d['title']} missing field name marker /T ("
            )
            assert d.get("mime_type") == "application/pdf"

    def test_client_onboarding_fillable_subset(self, base_url, admin_headers):
        """Client docs with builders (seq 5, 9, 10, 12, 13) must be real fillable PDFs."""
        fillable_seqs = {5, 9, 10, 12, 13}
        r = requests.get(
            f"{base_url}/api/documents?category=client_onboarding",
            headers=admin_headers, timeout=30,
        )
        assert r.status_code == 200
        docs = [d for d in r.json() if d.get("is_template")]
        seq_to_doc = {d["seq"]: d for d in docs}
        for s in fillable_seqs:
            d = seq_to_doc.get(s)
            assert d, f"Missing client_onboarding seq {s}"
            b64 = d.get("file_base64")
            assert b64, f"seq {s} ({d['title']}) missing file_base64"
            raw = base64.b64decode(b64)
            assert len(raw) > 5 * 1024, (
                f"seq {s} ({d['title']}) PDF too small ({len(raw)} bytes)"
            )
            assert b"/AcroForm" in raw, f"seq {s} ({d['title']}) missing /AcroForm"


# ----------------------- Policy acknowledgments -------------------------
@pytest.fixture
def policy_id(admin_headers):
    """Pick the first policy doc, seeding one if missing."""
    r = requests.get(
        f"{BASE_URL}/api/documents?category=policy",
        headers=admin_headers, timeout=30,
    )
    assert r.status_code == 200
    policies = r.json()
    if not policies:
        # Trigger the seed endpoint
        s = requests.post(
            f"{BASE_URL}/api/documents/seed-templates",
            headers=admin_headers, timeout=30,
        )
        assert s.status_code == 200
        r = requests.get(
            f"{BASE_URL}/api/documents?category=policy",
            headers=admin_headers, timeout=30,
        )
        policies = r.json()
    assert policies, "no policy templates available"
    return policies[0]["id"]


class TestPolicyAcknowledgments:

    def test_caregiver_can_acknowledge_policy(self, base_url, caregiver_headers, policy_id):
        r = requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=caregiver_headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        ack = r.json()
        assert ack.get("policy_id") == policy_id
        assert "user_id" in ack and "acknowledged_at" in ack

    def test_acknowledge_is_idempotent(self, base_url, caregiver_headers, policy_id):
        # Call twice — second call should upsert, not create a duplicate
        requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=caregiver_headers, timeout=30,
        )
        r2 = requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=caregiver_headers, timeout=30,
        )
        assert r2.status_code == 200

        # GET as caregiver — should contain exactly one entry for this policy
        g = requests.get(
            f"{base_url}/api/policies/acknowledgments",
            headers=caregiver_headers, timeout=30,
        )
        assert g.status_code == 200
        acks = [a for a in g.json() if a.get("policy_id") == policy_id]
        assert len(acks) == 1, f"Expected 1 ack, got {len(acks)}: {acks}"

    def test_admin_can_acknowledge(self, base_url, admin_headers, policy_id):
        r = requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=admin_headers, timeout=30,
        )
        assert r.status_code == 200

    def test_acknowledge_unknown_policy_404(self, base_url, caregiver_headers):
        r = requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": "nonexistent-policy-id"},
            headers=caregiver_headers, timeout=30,
        )
        assert r.status_code == 404

    def test_caregiver_sees_only_own_acks(self, base_url, caregiver_headers, caregiver_user, policy_id):
        # Ensure ack exists
        requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=caregiver_headers, timeout=30,
        )
        g = requests.get(
            f"{base_url}/api/policies/acknowledgments",
            headers=caregiver_headers, timeout=30,
        )
        assert g.status_code == 200
        acks = g.json()
        assert len(acks) >= 1
        for a in acks:
            assert a["user_id"] == caregiver_user["id"], (
                f"Caregiver leaked another user's ack: {a}"
            )

    def test_caregiver_user_id_query_is_ignored(self, base_url, caregiver_headers, caregiver_user):
        """Even if a caregiver passes ?user_id=otherId, only their own acks return."""
        g = requests.get(
            f"{base_url}/api/policies/acknowledgments?user_id=some-other-user-id",
            headers=caregiver_headers, timeout=30,
        )
        assert g.status_code == 200
        for a in g.json():
            assert a["user_id"] == caregiver_user["id"]

    def test_admin_can_query_user_id(self, base_url, admin_headers, caregiver_headers, caregiver_user, policy_id):
        # Ensure caregiver has an ack
        requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=caregiver_headers, timeout=30,
        )
        g = requests.get(
            f"{base_url}/api/policies/acknowledgments",
            params={"user_id": caregiver_user["id"]},
            headers=admin_headers, timeout=30,
        )
        assert g.status_code == 200
        acks = g.json()
        assert any(
            a["user_id"] == caregiver_user["id"] and a["policy_id"] == policy_id
            for a in acks
        ), f"Admin user_id filter missed caregiver's ack: {acks}"

    def test_delete_removes_ack(self, base_url, caregiver_headers, policy_id):
        # Ensure exists
        requests.post(
            f"{base_url}/api/policies/acknowledge",
            json={"policy_id": policy_id},
            headers=caregiver_headers, timeout=30,
        )
        d = requests.delete(
            f"{base_url}/api/policies/acknowledge/{policy_id}",
            headers=caregiver_headers, timeout=30,
        )
        assert d.status_code == 200
        assert d.json().get("ok") is True

        g = requests.get(
            f"{base_url}/api/policies/acknowledgments",
            headers=caregiver_headers, timeout=30,
        )
        assert g.status_code == 200
        assert not any(a["policy_id"] == policy_id for a in g.json())


# ----------------------- Regression smoke -------------------------
class TestRegressionSmoke:

    def test_admin_login(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@healthguard.com", "password": "Admin@123"},
            timeout=30,
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_caregiver_login(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "caregiver@healthguard.com", "password": "Caregiver@123"},
            timeout=30,
        )
        assert r.status_code == 200

    def test_shifts_list(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/shifts", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_ms_status(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/ms/status", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert "connected" in body

    def test_audit_binder(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/reports/audit-binder",
            headers=admin_headers, timeout=60,
        )
        assert r.status_code == 200
        # Either JSON or PDF — just confirm non-empty body
        assert len(r.content) > 100

    def test_replication_playbook_pdf(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/reports/replication-playbook.pdf",
            headers=admin_headers, timeout=60,
        )
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF", "Response is not a PDF"

    def test_replication_intake_form_pdf(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/reports/replication-intake-form.pdf",
            headers=admin_headers, timeout=60,
        )
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

    def test_chat_threads(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/chat/threads", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_assignments_list(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/assignments", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_document_push_endpoint_reachable(self, base_url, admin_headers):
        """Sanity: push endpoint rejects missing payload (proves it's wired)."""
        # Fetch any document
        rd = requests.get(
            f"{base_url}/api/documents?category=caregiver_onboarding",
            headers=admin_headers, timeout=30,
        )
        assert rd.status_code == 200
        docs = rd.json()
        if docs:
            doc_id = docs[0]["id"]
            # Empty target list should still respond cleanly (200 or 400)
            r = requests.post(
                f"{base_url}/api/documents/{doc_id}/push",
                json={"caregiver_ids": []},
                headers=admin_headers, timeout=30,
            )
            assert r.status_code in (200, 400, 422), r.status_code
