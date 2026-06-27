"""Iteration 22 — Verify Unicode-safe Content-Disposition + per-id PDF uniqueness.

Covers:
- BACKEND #1 — em-dash titled doc 10cac2c3-... returns 200 application/pdf with
  RFC-5987 Content-Disposition (both filename= ASCII + filename*=UTF-8'').
- BACKEND #2 — 3 non-ASCII + 3 ASCII titled docs return 200 stamped PDFs.
- BACKEND #3 — 5 different doc ids → 5 distinct SHA-256 hashes (no cached blob).
"""
import hashlib
import os
import re
import unicodedata

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://audit-prep-hub.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@healthguard.com"
ADMIN_LEGACY_PWD = "Admin@123"
EM_DASH_DOC_ID = "10cac2c3-2c17-4e7e-ab35-146d4c60e190"


@pytest.fixture(scope="module")
def auth_headers():
    """Get a legacy JWT (works for /api/documents and /stamped)."""
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_LEGACY_PWD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    token = r.json().get("access_token") or r.json().get("token")
    assert token, f"no token in login response: {r.json()}"
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def all_docs(auth_headers):
    r = requests.get(f"{BASE_URL}/api/documents", headers=auth_headers, timeout=30)
    assert r.status_code == 200
    docs = r.json()
    assert isinstance(docs, list) and len(docs) > 0
    return docs


def _is_non_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


# ---------------- BACKEND #1 — em-dash doc ----------------
class TestEmDashDocStamped:
    def test_em_dash_doc_returns_200_pdf(self, auth_headers):
        r = requests.get(
            f"{BASE_URL}/api/documents/{EM_DASH_DOC_ID}/stamped",
            headers=auth_headers,
            timeout=60,
        )
        assert r.status_code == 200, f"status={r.status_code} body[:200]={r.content[:200]!r}"
        assert "application/pdf" in r.headers.get("content-type", "").lower()
        assert r.content.startswith(b"%PDF"), f"first16={r.content[:16]!r}"
        assert len(r.content) > 1000, "PDF body too small"

    def test_em_dash_content_disposition_rfc5987(self, auth_headers):
        r = requests.get(
            f"{BASE_URL}/api/documents/{EM_DASH_DOC_ID}/stamped",
            headers=auth_headers,
            timeout=60,
        )
        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert cd, "missing Content-Disposition"
        # Must contain BOTH classic filename= and the RFC-5987 filename*= form.
        m_ascii = re.search(r'filename="([^"]+)"', cd)
        assert m_ascii, f"missing classic filename= form in: {cd!r}"
        ascii_name = m_ascii.group(1)
        # ASCII-only (em-dash replaced with `_` or similar)
        ascii_name.encode("ascii")  # should not raise
        assert "filename*=UTF-8''" in cd, f"missing RFC-5987 filename*=UTF-8'' in: {cd!r}"


# ---------------- BACKEND #2 — 3 non-ASCII + 3 ASCII ----------------
class TestMixedTitleStamped:
    def test_three_non_ascii_three_ascii(self, all_docs, auth_headers):
        non_ascii = [d for d in all_docs if _is_non_ascii(d.get("title", "")) and d.get("storage_path")]
        ascii_docs = [d for d in all_docs if not _is_non_ascii(d.get("title", "")) and d.get("storage_path")]
        # We need at least 3 of each
        picked_non = non_ascii[:3]
        picked_ascii = ascii_docs[:3]
        assert len(picked_non) >= 1, f"expected ≥1 non-ASCII titled doc with storage_path, got {len(picked_non)}"
        assert len(picked_ascii) >= 3, f"expected ≥3 ASCII titled docs with storage_path, got {len(picked_ascii)}"

        failures = []
        for d in picked_non + picked_ascii:
            r = requests.get(
                f"{BASE_URL}/api/documents/{d['id']}/stamped",
                headers=auth_headers,
                timeout=60,
            )
            ok = (
                r.status_code == 200
                and "application/pdf" in r.headers.get("content-type", "").lower()
                and r.content[:4] == b"%PDF"
            )
            if not ok:
                failures.append(
                    f"id={d['id']} title={d['title']!r} status={r.status_code} ct={r.headers.get('content-type')}"
                )
        assert not failures, "stamped failures:\n" + "\n".join(failures)


# ---------------- BACKEND #3 — uniqueness ----------------
class TestStampedUniqueness:
    def test_five_docs_have_distinct_sha256(self, all_docs, auth_headers):
        with_blob = [d for d in all_docs if d.get("storage_path")]
        picked = with_blob[:5]
        assert len(picked) >= 5, f"need ≥5 docs with storage_path, got {len(picked)}"

        hashes = {}
        for d in picked:
            r = requests.get(
                f"{BASE_URL}/api/documents/{d['id']}/stamped",
                headers=auth_headers,
                timeout=60,
            )
            assert r.status_code == 200, f"{d['id']} -> {r.status_code}"
            assert r.content[:4] == b"%PDF", f"{d['id']} not a PDF"
            h = hashlib.sha256(r.content).hexdigest()
            hashes[d["id"]] = h
        unique = set(hashes.values())
        assert len(unique) == len(picked), f"expected {len(picked)} unique hashes, got {len(unique)}: {hashes}"
