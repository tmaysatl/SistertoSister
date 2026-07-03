"""
Iteration 24 — Phase 2 backend tests for POST /api/documents/{id}/submissions.

Covers:
  * 201 with populated field_count on a real seeded doc
  * 201 with field_count=0 when values={}
  * 404 for unknown document id
  * 401/403 without bearer token
  * Optional signature_b64 accepted
  * MongoDB persistence — the returned id exists in the `submissions`
    collection with the required shape.
  * Regression: GET /schema still returns the schema envelope for a
    Phase-1 seeded doc.
"""
import os
import uuid
import base64
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

SEEDED_DOC_ID = "16745d1d-22a7-4912-adbf-acc588192a01"


# ----- Utility ---------------------------------------------------------------
def _post_submission(base_url, headers, doc_id, body):
    return requests.post(
        f"{base_url}/api/documents/{doc_id}/submissions",
        headers=headers,
        json=body,
        timeout=30,
    )


# ----- 1. Happy path ---------------------------------------------------------
class TestSubmissionsHappyPath:
    def test_populated_submission_returns_201_and_field_count(
        self, base_url, admin_headers
    ):
        body = {
            "values": {
                "Last Name": "Smith",
                "Date": "2026-07-03",
                "I am 18 or older": True,
            }
        }
        r = _post_submission(base_url, admin_headers, SEEDED_DOC_ID, body)
        assert r.status_code == 201, r.text
        data = r.json()
        assert "id" in data and isinstance(data["id"], str)
        assert data["document_id"] == SEEDED_DOC_ID
        assert "submitted_at" in data
        assert data["field_count"] == 3

    def test_empty_values_returns_field_count_zero(self, base_url, admin_headers):
        r = _post_submission(base_url, admin_headers, SEEDED_DOC_ID, {"values": {}})
        assert r.status_code == 201, r.text
        assert r.json()["field_count"] == 0

    def test_signature_b64_optional_field_accepted(self, base_url, admin_headers):
        sig = base64.b64encode(b"fake-signature-bytes").decode()
        body = {"values": {"Signature Field": "OK"}, "signature_b64": sig}
        r = _post_submission(base_url, admin_headers, SEEDED_DOC_ID, body)
        assert r.status_code == 201, r.text
        assert r.json()["field_count"] == 1


# ----- 2. Error handling -----------------------------------------------------
class TestSubmissionsErrors:
    def test_unknown_document_returns_404(self, base_url, admin_headers):
        r = _post_submission(
            base_url, admin_headers, str(uuid.uuid4()), {"values": {"a": "b"}}
        )
        assert r.status_code == 404, r.text
        assert "not found" in r.json().get("detail", "").lower()

    def test_missing_auth_returns_401_or_403(self, base_url):
        r = requests.post(
            f"{base_url}/api/documents/{SEEDED_DOC_ID}/submissions",
            json={"values": {"a": "b"}},
            timeout=30,
        )
        assert r.status_code in (401, 403), f"got {r.status_code}: {r.text}"


# ----- 3. MongoDB persistence ------------------------------------------------
@pytest.mark.asyncio
async def test_submission_row_persisted_in_mongo(base_url, admin_headers, admin_user):
    values = {"Persist Check": "value-" + uuid.uuid4().hex[:8]}
    sig = base64.b64encode(b"sig-bytes").decode()
    r = _post_submission(
        base_url, admin_headers, SEEDED_DOC_ID,
        {"values": values, "signature_b64": sig},
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]

    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        row = await db.submissions.find_one({"id": sub_id})
        assert row is not None, f"submission {sub_id} not found in mongo"
        assert row["document_id"] == SEEDED_DOC_ID
        assert row["submitter_email"] == admin_user["email"]
        assert row["submitter_role"] == admin_user["role"]
        assert row["values"] == values
        assert row["signature_b64"] == sig
        assert "submitted_at" in row and row["submitted_at"]
        assert row.get("document_title")  # non-empty title snapshot
    finally:
        client.close()


@pytest.mark.asyncio
async def test_submission_row_signature_null_when_omitted(
    base_url, admin_headers
):
    r = _post_submission(
        base_url, admin_headers, SEEDED_DOC_ID, {"values": {"k": "v"}}
    )
    assert r.status_code == 201
    sub_id = r.json()["id"]
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        row = await client[DB_NAME].submissions.find_one({"id": sub_id})
        assert row is not None
        assert row.get("signature_b64") is None
    finally:
        client.close()


# ----- 4. Regression — Phase 1 schema endpoint unchanged --------------------
class TestPhase1SchemaRegression:
    def test_schema_endpoint_still_returns_envelope(self, base_url, admin_headers):
        r = requests.get(
            f"{base_url}/api/documents/{SEEDED_DOC_ID}/schema",
            headers=admin_headers,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        env = r.json()
        assert env["document_id"] == SEEDED_DOC_ID
        assert "field_count" in env and isinstance(env["field_count"], int)
        assert "source" in env
        assert "fields" in env and isinstance(env["fields"], list)
        assert env.get("parser_version") == "1.0"
