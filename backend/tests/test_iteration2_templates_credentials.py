"""Iteration 2 tests:
- Admin rebrand to 'Sister to Sister, PHCP'
- POST /api/documents/seed-templates (admin, idempotent, 403 caregiver)
- Seeded client_onboarding (10), caregiver_onboarding (12), policy (10) numbered + sorted by seq
- POST /api/documents w/ category='credential' as caregiver (with expires_at)
- GET /api/documents as caregiver scoping (sees credentials they own + agency/templates)
- GET /api/credentials/templates (admin + caregiver)
- GET /api/credentials/expiring?days=60 (caregiver only sees own)
- /api/stats still returns same shape
- No _id leakage on Document responses
"""
import json
from datetime import datetime, timedelta, timezone

import requests


# ---------- REBRAND ----------
class TestAdminRebrand:
    def test_admin_name_is_sister_to_sister(self, base_url):
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@healthguard.com", "password": "Admin@123"},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["name"] == "Sister to Sister, PHCP", (
            f"Expected admin name to be rebranded, got: {body['user']['name']}"
        )
        assert body["user"]["role"] == "admin"


# ---------- SEED TEMPLATES ----------
class TestSeedTemplates:
    def test_seed_forbidden_for_caregiver(self, base_url, caregiver_headers):
        r = requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=caregiver_headers,
            timeout=30,
        )
        assert r.status_code == 403

    def test_seed_unauthorized(self, base_url):
        r = requests.post(f"{base_url}/api/documents/seed-templates", timeout=15)
        assert r.status_code == 401

    def test_seed_creates_then_idempotent(self, base_url, admin_headers):
        # First run -- may create some (0..32 depending on prior state)
        r1 = requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=admin_headers,
            timeout=60,
        )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert "created" in body1
        assert isinstance(body1["created"], int)
        assert body1["created"] >= 0

        # Second run -- must be idempotent → created == 0
        r2 = requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=admin_headers,
            timeout=60,
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["created"] == 0, (
            f"Seeding should be idempotent, got created={body2['created']} on 2nd run"
        )

    def _fetch(self, base_url, headers, category):
        r = requests.get(
            f"{base_url}/api/documents?category={category}",
            headers=headers,
            timeout=20,
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_client_onboarding_seeded_10_sorted(self, base_url, admin_headers):
        # Ensure seeded
        requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=admin_headers,
            timeout=60,
        )
        docs = self._fetch(base_url, admin_headers, "client_onboarding")
        # Only template-seeded ones (filter is_template just to be robust)
        templates = [d for d in docs if d.get("is_template")]
        assert len(templates) == 10, f"Expected 10 client_onboarding templates, got {len(templates)}"

        # Numbered & sorted by seq ASC
        seqs = [t.get("seq") for t in templates]
        assert seqs == sorted(seqs), f"client_onboarding not sorted by seq ASC: {seqs}"
        # The list endpoint sorts by ('seq',1) so the first 10 entries are in order
        for i, t in enumerate(templates, start=1):
            prefix = f"{i:02d} - "
            assert t["title"].startswith(prefix), (
                f"client_onboarding[{i}] title doesn't start with '{prefix}': {t['title']}"
            )
            assert t.get("seq") == i
            assert "_id" not in t

    def test_caregiver_onboarding_seeded_12_sorted(self, base_url, admin_headers):
        requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=admin_headers,
            timeout=60,
        )
        docs = self._fetch(base_url, admin_headers, "caregiver_onboarding")
        templates = [d for d in docs if d.get("is_template")]
        assert len(templates) == 12, (
            f"Expected 12 caregiver_onboarding templates, got {len(templates)}"
        )
        seqs = [t.get("seq") for t in templates]
        assert seqs == sorted(seqs), f"caregiver_onboarding not sorted by seq: {seqs}"
        for i, t in enumerate(templates, start=1):
            prefix = f"{i:02d} - "
            assert t["title"].startswith(prefix), (
                f"caregiver_onboarding[{i}] title doesn't start with '{prefix}': {t['title']}"
            )
            assert t.get("seq") == i

    def test_policy_seeded_10(self, base_url, admin_headers):
        requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=admin_headers,
            timeout=60,
        )
        docs = self._fetch(base_url, admin_headers, "policy")
        # Policies are seeded as is_template=False; identify by seq presence
        policy_stubs = [d for d in docs if d.get("seq") is not None]
        assert len(policy_stubs) == 10, f"Expected 10 policy stubs, got {len(policy_stubs)}"
        seqs = [d.get("seq") for d in policy_stubs]
        assert seqs == sorted(seqs), f"policy not sorted by seq ASC: {seqs}"
        for i, d in enumerate(policy_stubs, start=1):
            assert d["title"].startswith(f"{i:02d} - ")


# ---------- CREDENTIALS ----------
class TestCredentials:
    def test_credentials_templates_admin(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/credentials/templates", headers=admin_headers, timeout=15
        )
        assert r.status_code == 200
        body = r.json()
        assert "titles" in body and isinstance(body["titles"], list)
        assert len(body["titles"]) >= 3
        # spot-check
        assert any("OIG" in t for t in body["titles"])
        assert any("Training Certificate" in t for t in body["titles"])

    def test_credentials_templates_caregiver(self, base_url, caregiver_headers):
        r = requests.get(
            f"{base_url}/api/credentials/templates", headers=caregiver_headers, timeout=15
        )
        assert r.status_code == 200
        assert isinstance(r.json()["titles"], list)

    def test_credentials_templates_unauthorized(self, base_url):
        r = requests.get(f"{base_url}/api/credentials/templates", timeout=15)
        assert r.status_code == 401

    def test_caregiver_create_credential_and_persist(
        self, base_url, caregiver_headers, caregiver_user, admin_headers
    ):
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        payload = {
            "title": "TEST_OIG_Background_Check",
            "category": "credential",
            "owner_id": caregiver_user["id"],
            "owner_type": "caregiver",
            "expires_at": expires,
            "notes": "TEST credential",
        }
        r = requests.post(
            f"{base_url}/api/documents",
            headers=caregiver_headers,
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["category"] == "credential"
        assert doc["owner_id"] == caregiver_user["id"]
        assert doc["owner_type"] == "caregiver"
        assert doc["expires_at"] == expires
        did = doc["id"]

        try:
            # Persistence: caregiver should see it via list
            lst = requests.get(
                f"{base_url}/api/documents", headers=caregiver_headers, timeout=15
            )
            assert lst.status_code == 200
            ids = [d["id"] for d in lst.json()]
            assert did in ids, "Caregiver-created credential not visible in list"

            # And no _id leakage anywhere (check each dict key, not substring)
            for d in lst.json():
                assert "_id" not in d
        finally:
            requests.delete(
                f"{base_url}/api/documents/{did}", headers=admin_headers, timeout=15
            )

    def test_caregiver_document_scoping_with_templates(
        self, base_url, admin_headers, caregiver_headers, caregiver_user
    ):
        # Ensure templates exist
        requests.post(
            f"{base_url}/api/documents/seed-templates",
            headers=admin_headers,
            timeout=60,
        )

        # Caregiver-owned credential
        expires = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        own = requests.post(
            f"{base_url}/api/documents",
            headers=caregiver_headers,
            json={
                "title": "TEST_own_credential_scope",
                "category": "credential",
                "owner_id": caregiver_user["id"],
                "owner_type": "caregiver",
                "expires_at": expires,
            },
            timeout=15,
        )
        assert own.status_code == 200, own.text
        own_id = own.json()["id"]

        # Other caregiver credential (admin creates with fake owner)
        other = requests.post(
            f"{base_url}/api/documents",
            headers=admin_headers,
            json={
                "title": "TEST_other_credential_scope",
                "category": "credential",
                "owner_id": "fake-other-cg-id",
                "owner_type": "caregiver",
                "expires_at": expires,
            },
            timeout=15,
        )
        assert other.status_code == 200
        other_id = other.json()["id"]

        try:
            r = requests.get(
                f"{base_url}/api/documents", headers=caregiver_headers, timeout=20
            )
            assert r.status_code == 200
            docs = r.json()
            titles = {d["title"] for d in docs}
            cats = {d["category"] for d in docs}

            # Sees own credential
            assert "TEST_own_credential_scope" in titles
            # Does NOT see other caregiver's credential
            assert "TEST_other_credential_scope" not in titles

            # Sees template/onboarding/policy categories
            assert "client_onboarding" in cats, f"caregiver should see client_onboarding templates; cats={cats}"
            assert "caregiver_onboarding" in cats, f"caregiver should see caregiver_onboarding templates; cats={cats}"
            assert "policy" in cats, f"caregiver should see policy docs; cats={cats}"
        finally:
            requests.delete(f"{base_url}/api/documents/{own_id}", headers=admin_headers, timeout=15)
            requests.delete(f"{base_url}/api/documents/{other_id}", headers=admin_headers, timeout=15)

    def test_credentials_expiring_scoping(
        self, base_url, admin_headers, caregiver_headers, caregiver_user
    ):
        soon = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        far = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

        # Caregiver's own: one already expired, one expiring soon, one far away
        created_ids = []
        for title, exp in [
            ("TEST_exp_self_expired", expired),
            ("TEST_exp_self_soon", soon),
            ("TEST_exp_self_far", far),
        ]:
            r = requests.post(
                f"{base_url}/api/documents",
                headers=caregiver_headers,
                json={
                    "title": title,
                    "category": "credential",
                    "owner_id": caregiver_user["id"],
                    "owner_type": "caregiver",
                    "expires_at": exp,
                },
                timeout=15,
            )
            assert r.status_code == 200, r.text
            created_ids.append(r.json()["id"])

        # Other caregiver's expiring soon - admin creates
        other = requests.post(
            f"{base_url}/api/documents",
            headers=admin_headers,
            json={
                "title": "TEST_exp_other_soon",
                "category": "credential",
                "owner_id": "fake-other-cg-id",
                "owner_type": "caregiver",
                "expires_at": soon,
            },
            timeout=15,
        )
        assert other.status_code == 200
        created_ids.append(other.json()["id"])

        try:
            # Caregiver view: only their own, only expired or soon (within 60 days)
            r = requests.get(
                f"{base_url}/api/credentials/expiring?days=60",
                headers=caregiver_headers,
                timeout=15,
            )
            assert r.status_code == 200, r.text
            results = r.json()
            titles = {d["title"] for d in results}

            assert "TEST_exp_self_expired" in titles
            assert "TEST_exp_self_soon" in titles
            assert "TEST_exp_self_far" not in titles, "far-future credential should not be returned"
            assert "TEST_exp_other_soon" not in titles, "caregiver must not see other caregiver's expiring credential"

            # Every result must be category=credential and owned by caregiver
            for d in results:
                assert d["category"] == "credential"
                assert d["owner_id"] == caregiver_user["id"]

            # Sorted ASC by expires_at
            exps = [d["expires_at"] for d in results]
            assert exps == sorted(exps), f"expiring list should be sorted by expires_at ASC: {exps}"

            # Admin view: sees all (own + other) within window
            ar = requests.get(
                f"{base_url}/api/credentials/expiring?days=60",
                headers=admin_headers,
                timeout=15,
            )
            assert ar.status_code == 200
            admin_titles = {d["title"] for d in ar.json()}
            assert "TEST_exp_self_soon" in admin_titles
            assert "TEST_exp_other_soon" in admin_titles
            assert "TEST_exp_self_far" not in admin_titles
        finally:
            for did in created_ids:
                requests.delete(
                    f"{base_url}/api/documents/{did}", headers=admin_headers, timeout=15
                )

    def test_credentials_expiring_unauthorized(self, base_url):
        r = requests.get(f"{base_url}/api/credentials/expiring", timeout=15)
        assert r.status_code == 401


# ---------- STATS REGRESSION ----------
class TestStatsRegression:
    def test_stats_shape_unchanged(self, base_url, admin_headers):
        r = requests.get(f"{base_url}/api/stats", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        for k in [
            "total_clients", "total_caregivers", "total_documents",
            "total_assignments", "total_training", "audit_readiness",
            "pending_onboarding", "pending_training",
            "onboarding_pct", "training_pct",
        ]:
            assert k in body, f"missing {k} in /api/stats"
        assert "_id" not in json.dumps(body)
