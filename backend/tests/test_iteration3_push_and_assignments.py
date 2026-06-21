"""Iteration 3 backend tests:
1. POST /api/documents/{doc_id}/push — admin clones source doc to caregiver/client owners;
   recipients see their copy via GET /api/documents.
2. POST /api/assignments idempotency — same caregiver_id+client_id twice returns the
   existing assignment without creating a duplicate.
"""
import base64
import uuid
import pytest
import requests


# ---------------- helpers ----------------
def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _tiny_pdf_b64():
    # Minimal valid PDF bytes -> base64
    pdf = (
        b"%PDF-1.4\n1 0 obj<<>>endobj\nxref\n0 1\n0000000000 65535 f \n"
        b"trailer<<>>\nstartxref\n9\n%%EOF\n"
    )
    return base64.b64encode(pdf).decode()


@pytest.fixture
def admin_client(base_url, admin_token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", **_auth(admin_token)})
    s.base = base_url
    return s


@pytest.fixture
def caregiver_client(base_url, caregiver_token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", **_auth(caregiver_token)})
    s.base = base_url
    return s


# ============== Document Push ==============
class TestDocumentPush:
    def test_push_clones_to_caregiver_and_client(
        self, base_url, admin_client, caregiver_client, caregiver_user
    ):
        # 1) Create source agency doc (admin)
        title = f"TEST_push_src_{uuid.uuid4().hex[:6]}"
        src_payload = {
            "title": title,
            "category": "policy",
            "owner_type": "agency",
            "file_base64": _tiny_pdf_b64(),
            "mime_type": "application/pdf",
        }
        r = admin_client.post(f"{base_url}/api/documents", json=src_payload)
        assert r.status_code == 200, r.text
        src = r.json()
        assert "_id" not in src
        src_id = src["id"]

        # 2) Create a test client to be a push target
        cl_payload = {"name": f"TEST_pushcl_{uuid.uuid4().hex[:6]}"}
        r = admin_client.post(f"{base_url}/api/clients", json=cl_payload)
        assert r.status_code == 200, r.text
        client_obj = r.json()
        client_id = client_obj["id"]

        cg_id = caregiver_user["id"]

        created_ids: list[str] = []
        try:
            # 3) Push to caregiver + client
            r = admin_client.post(
                f"{base_url}/api/documents/{src_id}/push",
                json={
                    "targets": [
                        {"owner_id": cg_id, "owner_type": "caregiver"},
                        {"owner_id": client_id, "owner_type": "client"},
                    ]
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["created"] == 2
            assert isinstance(body.get("ids"), list) and len(body["ids"]) == 2
            assert all(i != src_id for i in body["ids"])
            created_ids = list(body["ids"])

            # 4) Recipient (caregiver) sees their copy
            r = caregiver_client.get(f"{base_url}/api/documents")
            assert r.status_code == 200
            cg_docs = r.json()
            cg_titles = [d["title"] for d in cg_docs if d.get("owner_id") == cg_id]
            assert title in cg_titles, (
                f"Pushed doc not found in caregiver's list. Got titles={cg_titles}"
            )

            # exactly one copy owned by caregiver from this push
            cg_match = [d for d in cg_docs if d["title"] == title and d.get("owner_id") == cg_id]
            assert len(cg_match) == 1
            assert cg_match[0]["id"] in created_ids
            assert cg_match[0]["owner_type"] == "caregiver"
            assert cg_match[0]["is_template"] is False
            assert cg_match[0]["id"] != src_id

            # 5) Admin sees client copy
            r = admin_client.get(f"{base_url}/api/documents", params={"owner_id": client_id})
            assert r.status_code == 200
            cl_docs = r.json()
            cl_match = [d for d in cl_docs if d["title"] == title]
            assert len(cl_match) == 1
            assert cl_match[0]["owner_type"] == "client"
            assert cl_match[0]["owner_id"] == client_id
            assert cl_match[0]["id"] in created_ids

            # 6) Source remains intact (agency)
            r = admin_client.get(f"{base_url}/api/documents/{src_id}")
            assert r.status_code == 200
            assert r.json()["owner_type"] == "agency"
        finally:
            # cleanup
            for did in created_ids + [src_id]:
                admin_client.delete(f"{base_url}/api/documents/{did}")
            admin_client.delete(f"{base_url}/api/clients/{client_id}")

    def test_push_404_for_missing_source(self, base_url, admin_client):
        r = admin_client.post(
            f"{base_url}/api/documents/does-not-exist/push",
            json={"targets": [{"owner_id": "x", "owner_type": "caregiver"}]},
        )
        assert r.status_code == 404

    def test_push_requires_admin(self, base_url, admin_client, caregiver_client):
        # admin creates a real source
        r = admin_client.post(
            f"{base_url}/api/documents",
            json={
                "title": f"TEST_pushperm_{uuid.uuid4().hex[:6]}",
                "category": "policy",
                "owner_type": "agency",
                "file_base64": _tiny_pdf_b64(),
                "mime_type": "application/pdf",
            },
        )
        assert r.status_code == 200
        src_id = r.json()["id"]
        try:
            r = caregiver_client.post(
                f"{base_url}/api/documents/{src_id}/push",
                json={"targets": [{"owner_id": "x", "owner_type": "caregiver"}]},
            )
            assert r.status_code == 403
        finally:
            admin_client.delete(f"{base_url}/api/documents/{src_id}")

    def test_push_ignores_invalid_targets(
        self, base_url, admin_client, caregiver_user
    ):
        r = admin_client.post(
            f"{base_url}/api/documents",
            json={
                "title": f"TEST_pushinv_{uuid.uuid4().hex[:6]}",
                "category": "policy",
                "owner_type": "agency",
                "file_base64": _tiny_pdf_b64(),
                "mime_type": "application/pdf",
            },
        )
        assert r.status_code == 200
        src_id = r.json()["id"]
        created = []
        try:
            r = admin_client.post(
                f"{base_url}/api/documents/{src_id}/push",
                json={
                    "targets": [
                        {"owner_id": "", "owner_type": "caregiver"},   # empty id -> skip
                        {"owner_id": "abc", "owner_type": "vendor"},   # bad type -> skip
                        {"owner_id": caregiver_user["id"], "owner_type": "caregiver"},  # valid
                    ]
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["created"] == 1
            created = list(body.get("ids") or [])
        finally:
            for did in created + [src_id]:
                admin_client.delete(f"{base_url}/api/documents/{did}")


# ============== Assignments Idempotency ==============
class TestAssignmentIdempotency:
    def test_same_pair_twice_returns_existing(
        self, base_url, admin_client, caregiver_user
    ):
        # need a client
        r = admin_client.post(
            f"{base_url}/api/clients",
            json={"name": f"TEST_assigncl_{uuid.uuid4().hex[:6]}"},
        )
        assert r.status_code == 200
        client_id = r.json()["id"]
        cg_id = caregiver_user["id"]

        a_ids = []
        try:
            r1 = admin_client.post(
                f"{base_url}/api/assignments",
                json={"caregiver_id": cg_id, "client_id": client_id, "schedule": "Mon"},
            )
            assert r1.status_code == 200, r1.text
            a1 = r1.json()
            assert a1["caregiver_id"] == cg_id
            assert a1["client_id"] == client_id
            assert "_id" not in a1
            a_ids.append(a1["id"])

            # 2nd call with same pair must return the same assignment (idempotent)
            r2 = admin_client.post(
                f"{base_url}/api/assignments",
                json={
                    "caregiver_id": cg_id,
                    "client_id": client_id,
                    "schedule": "different",  # should be ignored
                },
            )
            assert r2.status_code == 200, r2.text
            a2 = r2.json()
            assert a2["id"] == a1["id"], "Duplicate assignment created (not idempotent)"

            # Verify exactly one assignment exists for this pair via GET
            r3 = admin_client.get(f"{base_url}/api/assignments")
            assert r3.status_code == 200
            all_a = r3.json()
            pair_match = [
                a for a in all_a
                if a["caregiver_id"] == cg_id and a["client_id"] == client_id
            ]
            assert len(pair_match) == 1, (
                f"Expected exactly 1 assignment for pair, got {len(pair_match)}"
            )
        finally:
            for aid in a_ids:
                admin_client.delete(f"{base_url}/api/assignments/{aid}")
            admin_client.delete(f"{base_url}/api/clients/{client_id}")
