"""HealthGuard backend API tests - covers auth, RBAC, CRUD, scoping, AI streaming."""
import json
import uuid
import requests


# ---------- AUTH ----------
class TestAuth:
    def test_health(self, base_url):
        r = requests.get(f"{base_url}/api/", timeout=15)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_login_admin_seeded(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@healthguard.com", "password": "Admin@123"},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["role"] == "admin"
        assert "access_token" in body
        assert "_id" not in json.dumps(body)

    def test_login_caregiver_seeded(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "caregiver@healthguard.com", "password": "Caregiver@123"},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "caregiver"

    def test_login_invalid(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@healthguard.com", "password": "wrong"},
            timeout=15,
        )
        assert r.status_code == 401

    def test_register_and_duplicate(self, base_url):
        email = f"TEST_user_{uuid.uuid4().hex[:8]}@example.com"
        r = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": "Pass@123", "name": "TEST User", "role": "caregiver"},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["email"] == email
        assert body["user"]["role"] == "caregiver"
        # duplicate
        r2 = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": "Pass@123", "name": "Dup", "role": "caregiver"},
            timeout=15,
        )
        assert r2.status_code == 400

    def test_me_unauthorized(self, base_url):
        r = requests.get(f"{base_url}/api/auth/me", timeout=15)
        assert r.status_code == 401

    def test_me_authorized(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/auth/me", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        assert r.json()["email"] == "admin@healthguard.com"


# ---------- RBAC ----------
class TestRBAC:
    def test_caregiver_cannot_create_client(self, base_url, caregiver_headers):
        r = requests.post(
            f"{base_url}/api/clients",
            headers=caregiver_headers,
            json={"name": "TEST_blocked"},
            timeout=15,
        )
        assert r.status_code == 403

    def test_caregiver_cannot_create_training(self, base_url, caregiver_headers):
        r = requests.post(
            f"{base_url}/api/training",
            headers=caregiver_headers,
            json={"title": "TEST_blocked"},
            timeout=15,
        )
        assert r.status_code == 403

    def test_caregiver_cannot_create_onboarding(self, base_url, caregiver_headers, caregiver_user):
        r = requests.post(
            f"{base_url}/api/onboarding",
            headers=caregiver_headers,
            json={"caregiver_id": caregiver_user["id"], "title": "TEST_blocked"},
            timeout=15,
        )
        assert r.status_code == 403


# ---------- CLIENTS ----------
class TestClients:
    def test_client_crud(self, base_url, admin_headers):
        payload = {"name": "TEST_Client_" + uuid.uuid4().hex[:6], "phone": "555"}
        r = requests.post(f"{base_url}/api/clients", headers=admin_headers, json=payload, timeout=15)
        assert r.status_code == 200
        cid = r.json()["id"]
        assert r.json()["name"] == payload["name"]

        # GET single
        g = requests.get(f"{base_url}/api/clients/{cid}", headers=admin_headers, timeout=15)
        assert g.status_code == 200
        assert g.json()["id"] == cid

        # list
        lst = requests.get(f"{base_url}/api/clients", headers=admin_headers, timeout=15)
        assert lst.status_code == 200
        assert any(c["id"] == cid for c in lst.json())

        # delete
        d = requests.delete(f"{base_url}/api/clients/{cid}", headers=admin_headers, timeout=15)
        assert d.status_code == 200

        # verify gone
        g2 = requests.get(f"{base_url}/api/clients/{cid}", headers=admin_headers, timeout=15)
        assert g2.status_code == 404


# ---------- CAREGIVERS ----------
class TestCaregivers:
    def test_list_caregivers(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/caregivers", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        emails = [u["email"] for u in r.json()]
        assert "caregiver@healthguard.com" in emails


# ---------- DOCUMENTS ----------
class TestDocuments:
    def test_doc_create_admin(self, base_url, admin_headers):
        payload = {
            "title": "TEST_AgencyPolicy",
            "category": "policy",
            "owner_type": "agency",
            "file_base64": "data:application/pdf;base64,JVBERi0xLjQK",
        }
        r = requests.post(f"{base_url}/api/documents", headers=admin_headers, json=payload, timeout=15)
        assert r.status_code == 200
        doc = r.json()
        assert doc["title"] == payload["title"]
        assert doc["category"] == "policy"

        # filter by category
        lst = requests.get(
            f"{base_url}/api/documents?category=policy", headers=admin_headers, timeout=15
        )
        assert lst.status_code == 200
        assert any(d["id"] == doc["id"] for d in lst.json())

        # delete
        d = requests.delete(
            f"{base_url}/api/documents/{doc['id']}", headers=admin_headers, timeout=15
        )
        assert d.status_code == 200

    def test_caregiver_cannot_delete(self, base_url, admin_headers, caregiver_headers):
        # admin creates a doc
        r = requests.post(
            f"{base_url}/api/documents",
            headers=admin_headers,
            json={"title": "TEST_del", "category": "policy", "owner_type": "agency"},
            timeout=15,
        )
        assert r.status_code == 200
        did = r.json()["id"]
        # caregiver delete should 403
        d = requests.delete(f"{base_url}/api/documents/{did}", headers=caregiver_headers, timeout=15)
        assert d.status_code == 403
        # admin cleanup
        requests.delete(f"{base_url}/api/documents/{did}", headers=admin_headers, timeout=15)

    def test_caregiver_scoping(self, base_url, admin_headers, caregiver_headers, caregiver_user):
        # create various docs
        created = []
        defs = [
            {"title": "TEST_agency_wide", "category": "policy", "owner_type": "agency"},
            {"title": "TEST_training_doc", "category": "training", "owner_type": "agency"},
            {
                "title": "TEST_caregiver_own",
                "category": "caregiver",
                "owner_type": "caregiver",
                "owner_id": caregiver_user["id"],
            },
            {
                "title": "TEST_caregiver_other",
                "category": "caregiver",
                "owner_type": "caregiver",
                "owner_id": "other-fake-cgid",
            },
            {
                "title": "TEST_onboard_own",
                "category": "caregiver_onboarding",
                "owner_type": "caregiver",
                "owner_id": caregiver_user["id"],
            },
        ]
        for body in defs:
            r = requests.post(f"{base_url}/api/documents", headers=admin_headers, json=body, timeout=15)
            assert r.status_code == 200, r.text
            created.append(r.json()["id"])

        try:
            r = requests.get(f"{base_url}/api/documents", headers=caregiver_headers, timeout=15)
            assert r.status_code == 200
            titles = {d["title"] for d in r.json()}
            assert "TEST_agency_wide" in titles
            assert "TEST_training_doc" in titles
            assert "TEST_caregiver_own" in titles
            assert "TEST_onboard_own" in titles
            assert "TEST_caregiver_other" not in titles
        finally:
            for did in created:
                requests.delete(f"{base_url}/api/documents/{did}", headers=admin_headers, timeout=15)


# ---------- ASSIGNMENTS ----------
class TestAssignments:
    def test_assignment_crud_and_scope(self, base_url, admin_headers, caregiver_headers, caregiver_user):
        # create client first
        c = requests.post(
            f"{base_url}/api/clients", headers=admin_headers, json={"name": "TEST_AC"}, timeout=15
        )
        cid = c.json()["id"]
        try:
            a = requests.post(
                f"{base_url}/api/assignments",
                headers=admin_headers,
                json={
                    "caregiver_id": caregiver_user["id"],
                    "client_id": cid,
                    "schedule": "Mon 9-12",
                },
                timeout=15,
            )
            assert a.status_code == 200
            aid = a.json()["id"]

            # caregiver creating should 403
            f403 = requests.post(
                f"{base_url}/api/assignments",
                headers=caregiver_headers,
                json={"caregiver_id": caregiver_user["id"], "client_id": cid},
                timeout=15,
            )
            assert f403.status_code == 403

            # caregiver only sees their own assignment
            la = requests.get(f"{base_url}/api/assignments", headers=caregiver_headers, timeout=15)
            assert la.status_code == 200
            for asg in la.json():
                assert asg["caregiver_id"] == caregiver_user["id"]
            assert any(a["id"] == aid for a in la.json())

            # cleanup
            d = requests.delete(f"{base_url}/api/assignments/{aid}", headers=admin_headers, timeout=15)
            assert d.status_code == 200
        finally:
            requests.delete(f"{base_url}/api/clients/{cid}", headers=admin_headers, timeout=15)


# ---------- TRAINING ----------
class TestTraining:
    def test_training_lifecycle_and_idempotent_complete(
        self, base_url, admin_headers, caregiver_headers, caregiver_user
    ):
        t = requests.post(
            f"{base_url}/api/training",
            headers=admin_headers,
            json={"title": "TEST_Tr_" + uuid.uuid4().hex[:6], "required": True},
            timeout=15,
        )
        assert t.status_code == 200
        tid = t.json()["id"]

        try:
            # complete twice → same record
            c1 = requests.post(
                f"{base_url}/api/training/{tid}/complete", headers=caregiver_headers, timeout=15
            )
            assert c1.status_code == 200
            c2 = requests.post(
                f"{base_url}/api/training/{tid}/complete", headers=caregiver_headers, timeout=15
            )
            assert c2.status_code == 200
            assert c1.json()["id"] == c2.json()["id"], "completion should be idempotent"

            # completions filter
            lc = requests.get(
                f"{base_url}/api/training/completions", headers=caregiver_headers, timeout=15
            )
            assert lc.status_code == 200
            assert all(comp["caregiver_id"] == caregiver_user["id"] for comp in lc.json())

            # admin filter by caregiver
            la = requests.get(
                f"{base_url}/api/training/completions?caregiver_id={caregiver_user['id']}",
                headers=admin_headers,
                timeout=15,
            )
            assert la.status_code == 200
            assert any(comp["training_id"] == tid for comp in la.json())
        finally:
            requests.delete(f"{base_url}/api/training/{tid}", headers=admin_headers, timeout=15)


# ---------- ONBOARDING ----------
class TestOnboarding:
    def test_onboarding_toggle(self, base_url, admin_headers, caregiver_headers, caregiver_user):
        s = requests.post(
            f"{base_url}/api/onboarding",
            headers=admin_headers,
            json={"caregiver_id": caregiver_user["id"], "title": "TEST_step"},
            timeout=15,
        )
        assert s.status_code == 200
        sid = s.json()["id"]
        assert s.json()["completed"] is False

        try:
            t1 = requests.post(
                f"{base_url}/api/onboarding/{sid}/toggle", headers=caregiver_headers, timeout=15
            )
            assert t1.status_code == 200
            assert t1.json()["completed"] is True
            assert t1.json()["completed_at"]

            t2 = requests.post(
                f"{base_url}/api/onboarding/{sid}/toggle", headers=caregiver_headers, timeout=15
            )
            assert t2.status_code == 200
            assert t2.json()["completed"] is False

            # caregiver scoping
            lst = requests.get(f"{base_url}/api/onboarding", headers=caregiver_headers, timeout=15)
            assert lst.status_code == 200
            for step in lst.json():
                assert step["caregiver_id"] == caregiver_user["id"]
        finally:
            requests.delete(f"{base_url}/api/onboarding/{sid}", headers=admin_headers, timeout=15)


# ---------- STATS ----------
class TestStats:
    def test_stats(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/stats", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        for k in [
            "total_clients", "total_caregivers", "total_documents",
            "total_assignments", "total_training", "audit_readiness",
            "pending_onboarding", "pending_training",
        ]:
            assert k in body, f"missing {k}"
        assert isinstance(body["audit_readiness"], int)


# ---------- AI ASSISTANT (Claude streaming) ----------
class TestAssistant:
    def test_chat_stream_and_history(self, base_url, admin_token):
        session_id = "TEST_sess_" + uuid.uuid4().hex[:8]
        url = f"{base_url}/api/assistant/chat"
        with requests.post(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"session_id": session_id, "message": "In one sentence, what is HIPAA?"},
            stream=True,
            timeout=120,
        ) as r:
            assert r.status_code == 200, r.text
            assert "text/event-stream" in r.headers.get("content-type", "")
            chunks = []
            done_seen = False
            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("data:"):
                    payload = raw[len("data:"):].lstrip()
                    if payload == "[DONE]":
                        done_seen = True
                        break
                    chunks.append(payload)
            assert done_seen, "SSE stream did not terminate with [DONE]"
            assert len(chunks) > 0, "No data chunks received from Claude"
            joined = "".join(chunks)
            assert not joined.startswith("[Error"), f"LLM error chunk: {joined[:200]}"

        # history persists
        h = requests.get(
            f"{base_url}/api/assistant/history/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=15,
        )
        assert h.status_code == 200
        msgs = h.json()
        assert len(msgs) >= 2
        roles = [m["role"] for m in msgs]
        assert "user" in roles and "assistant" in roles
        # serialization check - no raw mongo _id key in any message
        for m in msgs:
            assert "_id" not in m
