"""Iteration 4 — Shifts (Schedule tab) backend tests.

Covers:
 - POST /api/shifts one_off + recurring (children auto-created)
 - GET /api/shifts with date filters returns one_off children only (no parent)
 - Caregiver self-scheduling: allowed iff assignment exists, else 403
 - PUT /api/shifts/{id}: admin can update all; caregiver only notes/service_type
 - DELETE /api/shifts/{id}: admin only, recurring parent cascade-deletes children
 - POST /api/shifts/{id}/clock-in -> in_progress; /clock-out -> completed
"""

import os
import uuid
import requests
from datetime import date, timedelta
import pytest


# --- helpers ---------------------------------------------------------------

def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _date_ahead(n):
    return (date.today() + timedelta(days=n)).isoformat()


# --- fixtures --------------------------------------------------------------

@pytest.fixture(scope="module")
def mod_admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def test_client(base_url, mod_admin_headers):
    admin_headers = mod_admin_headers
    payload = {"name": f"TEST_SCHED_CLIENT_{uuid.uuid4().hex[:6]}",
               "address": "1 Test St", "phone": "555-0100"}
    r = requests.post(f"{base_url}/api/clients", json=payload, headers=admin_headers, timeout=30)
    assert r.status_code == 200, r.text
    cl = r.json()
    yield cl
    requests.delete(f"{base_url}/api/clients/{cl['id']}", headers=admin_headers, timeout=30)


@pytest.fixture(scope="module")
def caregiver_assignment(base_url, mod_admin_headers, caregiver_user, test_client):
    admin_headers = mod_admin_headers
    """Create assignment so caregiver self-scheduling tests can succeed."""
    body = {"caregiver_id": caregiver_user["id"], "client_id": test_client["id"]}
    r = requests.post(f"{base_url}/api/assignments", json=body, headers=admin_headers, timeout=30)
    assert r.status_code == 200, r.text
    a = r.json()
    yield a
    requests.delete(f"{base_url}/api/assignments/{a['id']}", headers=admin_headers, timeout=30)


@pytest.fixture(scope="module")
def unassigned_client(base_url, mod_admin_headers):
    admin_headers = mod_admin_headers
    payload = {"name": f"TEST_UNASSIGNED_{uuid.uuid4().hex[:6]}"}
    r = requests.post(f"{base_url}/api/clients", json=payload, headers=admin_headers, timeout=30)
    assert r.status_code == 200, r.text
    cl = r.json()
    yield cl
    requests.delete(f"{base_url}/api/clients/{cl['id']}", headers=admin_headers, timeout=30)


@pytest.fixture
def _cleanup_shifts(base_url, admin_headers):
    created = []
    yield created
    for sid in created:
        try:
            requests.delete(f"{base_url}/api/shifts/{sid}", headers=admin_headers, timeout=20)
        except Exception:
            pass


# --- Tests: one_off creation + listing -------------------------------------

class TestOneOffShifts:
    def test_admin_creates_one_off_and_lists(self, base_url, admin_headers,
                                              caregiver_user, test_client, _cleanup_shifts):
        d = _date_ahead(2)
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": d,
            "start_time": "09:00",
            "end_time": "13:00",
            "service_type": "Personal Care",
            "notes": "TEST one-off",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body, headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        sh = r.json()
        _cleanup_shifts.append(sh["id"])
        assert sh["kind"] == "one_off"
        assert sh["status"] == "scheduled"
        assert sh["date"] == d
        # List with date filter
        r = requests.get(f"{base_url}/api/shifts?start={d}&end={d}",
                         headers=admin_headers, timeout=30)
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert sh["id"] in ids


# --- Tests: recurring expansion --------------------------------------------

class TestRecurringShifts:
    def test_recurring_expands_into_children(self, base_url, admin_headers,
                                              caregiver_user, test_client, _cleanup_shifts):
        start = _date_ahead(1)
        end = _date_ahead(28)  # ~4 weeks
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "recurring",
            "date": start,
            "weekdays": ["MON", "WED", "FRI"],
            "recurring_until": end,
            "start_time": "10:00",
            "end_time": "12:00",
            "service_type": "Companion",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body, headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        parent = r.json()
        _cleanup_shifts.append(parent["id"])
        assert parent["kind"] == "recurring"

        # GET shifts with full range -> children only, NOT parent
        r = requests.get(
            f"{base_url}/api/shifts?start={start}&end={end}",
            headers=admin_headers, timeout=30,
        )
        assert r.status_code == 200
        listing = r.json()
        ids = [s["id"] for s in listing]
        assert parent["id"] not in ids, "Recurring parent must not appear in date-range listing"
        # Children attached to this parent
        children = [s for s in listing if s.get("parent_shift_id") == parent["id"]]
        assert len(children) > 0, "Expected child one_off shifts to be auto-created"
        for c in children:
            assert c["kind"] == "one_off"
            assert c["start_time"] == "10:00" and c["end_time"] == "12:00"
        # Roughly: 4 weeks * 3 weekdays = ~12
        assert 6 <= len(children) <= 16, f"Unexpected child count {len(children)}"

    def test_delete_recurring_cascade(self, base_url, admin_headers,
                                      caregiver_user, test_client):
        start = _date_ahead(1)
        end = _date_ahead(14)
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "recurring",
            "date": start,
            "weekdays": ["TUE", "THU"],
            "recurring_until": end,
            "start_time": "08:00",
            "end_time": "09:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body, headers=admin_headers, timeout=30)
        assert r.status_code == 200
        parent = r.json()
        # Confirm children exist
        r = requests.get(f"{base_url}/api/shifts?start={start}&end={end}",
                         headers=admin_headers, timeout=30)
        assert any(s.get("parent_shift_id") == parent["id"] for s in r.json())
        # Delete parent
        r = requests.delete(f"{base_url}/api/shifts/{parent['id']}",
                            headers=admin_headers, timeout=30)
        assert r.status_code == 200
        # All children should be gone
        r = requests.get(f"{base_url}/api/shifts?start={start}&end={end}",
                         headers=admin_headers, timeout=30)
        assert not any(s.get("parent_shift_id") == parent["id"] for s in r.json()), \
            "Children should have been cascade-deleted"


# --- Tests: caregiver self-scheduling ACL ----------------------------------

class TestCaregiverSelfScheduling:
    def test_caregiver_cannot_schedule_for_unassigned_client(
        self, base_url, caregiver_headers, caregiver_user, unassigned_client
    ):
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": unassigned_client["id"],
            "kind": "one_off",
            "date": _date_ahead(3),
            "start_time": "09:00",
            "end_time": "12:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body,
                          headers=caregiver_headers, timeout=30)
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

    def test_caregiver_can_schedule_for_assigned_client(
        self, base_url, caregiver_headers, admin_headers,
        caregiver_user, test_client, caregiver_assignment, _cleanup_shifts
    ):
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": _date_ahead(4),
            "start_time": "09:00",
            "end_time": "12:00",
            "service_type": "Homemaker",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body,
                          headers=caregiver_headers, timeout=30)
        assert r.status_code == 200, r.text
        sh = r.json()
        _cleanup_shifts.append(sh["id"])
        assert sh["caregiver_id"] == caregiver_user["id"]

    def test_caregiver_cannot_schedule_for_other_caregiver(
        self, base_url, caregiver_headers, admin_user, test_client
    ):
        body = {
            "caregiver_id": admin_user["id"],  # different person
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": _date_ahead(5),
            "start_time": "09:00",
            "end_time": "12:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body,
                          headers=caregiver_headers, timeout=30)
        assert r.status_code == 403


# --- Tests: PUT edit ACL ---------------------------------------------------

class TestShiftEdit:
    def test_admin_can_edit_all_fields(self, base_url, admin_headers,
                                        caregiver_user, test_client, _cleanup_shifts):
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": _date_ahead(6),
            "start_time": "09:00",
            "end_time": "13:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body, headers=admin_headers, timeout=30)
        assert r.status_code == 200
        sh = r.json()
        _cleanup_shifts.append(sh["id"])
        patch = {"start_time": "10:00", "end_time": "14:00", "notes": "edited"}
        r = requests.put(f"{base_url}/api/shifts/{sh['id']}", json=patch,
                         headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["start_time"] == "10:00"
        assert out["end_time"] == "14:00"
        assert out["notes"] == "edited"

    def test_caregiver_can_only_edit_notes_and_service_type(
        self, base_url, admin_headers, caregiver_headers,
        caregiver_user, test_client, caregiver_assignment, _cleanup_shifts
    ):
        # Admin creates
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": _date_ahead(7),
            "start_time": "09:00",
            "end_time": "13:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body, headers=admin_headers, timeout=30)
        sh = r.json()
        _cleanup_shifts.append(sh["id"])
        # Caregiver tries to change time AND notes/service_type
        patch = {"start_time": "06:00", "notes": "CG note", "service_type": "Respite"}
        r = requests.put(f"{base_url}/api/shifts/{sh['id']}", json=patch,
                         headers=caregiver_headers, timeout=30)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["notes"] == "CG note"
        assert out["service_type"] == "Respite"
        # start_time MUST NOT have changed (caregiver isn't allowed)
        assert out["start_time"] == "09:00", "Caregiver should not be able to change start_time"


# --- Tests: DELETE ACL -----------------------------------------------------

class TestShiftDelete:
    def test_caregiver_cannot_delete(self, base_url, admin_headers, caregiver_headers,
                                      caregiver_user, test_client, _cleanup_shifts):
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": _date_ahead(8),
            "start_time": "09:00",
            "end_time": "13:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body, headers=admin_headers, timeout=30)
        sh = r.json()
        _cleanup_shifts.append(sh["id"])
        r = requests.delete(f"{base_url}/api/shifts/{sh['id']}",
                            headers=caregiver_headers, timeout=30)
        assert r.status_code == 403


# --- Tests: clock-in / clock-out -------------------------------------------

class TestClock:
    def test_clock_in_then_out(self, base_url, admin_headers, caregiver_headers,
                                caregiver_user, test_client, caregiver_assignment,
                                _cleanup_shifts):
        body = {
            "caregiver_id": caregiver_user["id"],
            "client_id": test_client["id"],
            "kind": "one_off",
            "date": _date_ahead(9),
            "start_time": "09:00",
            "end_time": "13:00",
        }
        r = requests.post(f"{base_url}/api/shifts", json=body,
                          headers=admin_headers, timeout=30)
        sh = r.json()
        _cleanup_shifts.append(sh["id"])
        r = requests.post(f"{base_url}/api/shifts/{sh['id']}/clock-in",
                          json={"location": "test-loc"},
                          headers=caregiver_headers, timeout=30)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "in_progress"
        assert out["clocked_in_at"]

        r = requests.post(f"{base_url}/api/shifts/{sh['id']}/clock-out",
                          json={},
                          headers=caregiver_headers, timeout=30)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "completed"
        assert out["clocked_out_at"]
