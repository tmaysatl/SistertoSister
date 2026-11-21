"""Intake forms that mirror the agency website (sistertosisterhomecare.com).

Three forms, same questions as the public site:
  * client_enrollment  - "Request Care / New Client Enrollment"
  * referral           - "Case Manager Referral"
  * job_application    - "We Are Hiring" (RN or Caregiver/CNA/HHA)

Admin-only (the data is personal information). Submissions are stored in the
Mongo collection `intake_submissions`; nothing here touches existing forms,
documents or auth. The website remains the public entry point; staff use this
to enter / review the same information in the app.

Mounted under `/api` by server.py.
"""
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.db import db
from core.security import require_admin
from models import UserPublic, now_iso

router = APIRouter(tags=["intake"])
logger = logging.getLogger(__name__)

STATUSES = ["new", "contacted", "scheduled", "closed"]
MAX_TEXT = 2000

# --------------------------------------------------------------------------
# Field helpers (keep the schema table below readable)
# --------------------------------------------------------------------------
def T(key, label, required=False, **kw):
    return {"key": key, "label": label, "type": "text", "required": required, **kw}


def LT(key, label, required=False, **kw):
    return {"key": key, "label": label, "type": "longtext", "required": required, **kw}


def D(key, label, required=False):
    return {"key": key, "label": label, "type": "date", "required": required}


def SEL(key, label, options, required=False):
    return {"key": key, "label": label, "type": "select", "options": options, "required": required}


def RAD(key, label, options, required=False):
    return {"key": key, "label": label, "type": "radio", "options": options, "required": required}


def CHK(key, label, options, required=False):
    return {"key": key, "label": label, "type": "checkbox", "options": options, "required": required}


def CONSENT(key, label, required=True):
    return {"key": key, "label": label, "type": "consent", "required": required}


# --------------------------------------------------------------------------
# The three forms
# --------------------------------------------------------------------------
FORMS: Dict[str, dict] = {
    "client_enrollment": {
        "title": "Client Enrollment (Request Care)",
        "description": (
            "Starts the process of requesting care. This is not a service agreement and does "
            "not confirm a start date. Do not enter payment card numbers, passwords, or home "
            "entry codes."
        ),
        "contact_any": ["phone", "email"],
        "summary_key": "client_name",
        "sections": [
            {"title": "Client Information", "fields": [
                T("client_name", "Client full name", True),
                D("dob", "Date of birth"),
                T("address", "Home address"),
                T("city", "City"),
                T("state", "State"),
                T("zip", "ZIP"),
                T("county", "County"),
                T("phone", "Primary phone"),
                T("email", "Email"),
            ]},
            {"title": "Authorized Representative (if applicable)", "fields": [
                T("rep_name", "Representative name"),
                T("rep_relationship", "Relationship to client"),
                T("rep_phone", "Representative phone"),
                T("rep_email", "Representative email"),
            ]},
            {"title": "Referral & Requested Services", "fields": [
                T("heard_about", "How did you hear about us?"),
                CHK("services", "Services requested (a request, not an approval)", [
                    "Personal care (bathing, dressing, hygiene)",
                    "Companionship / supervision",
                    "Light housekeeping",
                    "Meal preparation",
                    "Medication reminders",
                    "Transportation (non-medical errands)",
                    "Respite care",
                    "Other (describe below)",
                ]),
                LT("services_other", "If other, please describe"),
            ]},
            {"title": "Requested Schedule & Start Date", "fields": [
                D("start_date", "Requested start date"),
                T("hours_per_week", "Approx. hours/week requested"),
                T("preferred_times", "Preferred days/times"),
            ]},
            {"title": "Payer Information", "fields": [
                RAD("payer", "Expected payment approach (not a coverage guarantee)", [
                    "Private pay",
                    "Medicaid Waiver (GA)",
                    "Medicaid CMO / managed care plan",
                    "Georgia Pediatric Program (GAPP)",
                    "Other third-party payer",
                ]),
                T("plan_name", "If Medicaid/CMO, plan or waiver name"),
                T("member_id", "Member/case ID (if known)"),
            ]},
            {"title": "Confirmation", "fields": [
                T("completed_by", "Name of person completing this form", True),
                T("completed_by_relationship", "Relationship to the client"),
                CONSENT("consent_contact",
                        "I agree Sister to Sister may contact me at the phone number and/or email above about this inquiry."),
                CONSENT("consent_accurate",
                        "I confirm the information above is accurate to the best of my knowledge."),
            ]},
        ],
    },
    "referral": {
        "title": "Case Manager Referral",
        "description": (
            "For hospitals, case managers and care coordinators. Do NOT enter diagnoses or "
            "Medicaid / insurance ID numbers here. For an urgent discharge, call 404-282-2447."
        ),
        "contact_any": [],
        "summary_key": "client_ref",
        "sections": [
            {"title": "Referral Source", "fields": [
                T("referrer_name", "Your name", True),
                T("organization", "Organization (hospital, agency, clinic, school)", True),
                SEL("role", "Your role", [
                    "Case manager / social worker",
                    "Discharge planner",
                    "Nurse / physician office",
                    "Support coordinator",
                    "Care coordinator (health plan)",
                    "Other",
                ], True),
                T("phone", "Best phone number", True),
                T("email", "Email", True),
            ]},
            {"title": "Client", "fields": [
                T("client_ref", "Client's first name and last initial", True),
                SEL("age_group", "Client's age group", [
                    "Child (under 21)", "Adult (21-64)", "Senior (65+)",
                ], True),
                T("county", "County where care will be provided"),
            ]},
            {"title": "Care Needed", "fields": [
                SEL("service", "Service requested", [
                    "GAPP pediatric nursing / personal care",
                    "Senior home care",
                    "Private-pay home care",
                    "Autism support",
                    "Dementia / Alzheimer's care",
                    "Respite / homemaker",
                    "Not sure - please advise",
                ], True),
                SEL("payment", "How will care be paid for?", [
                    "GAPP (Georgia Pediatric Program)",
                    "Humana Medicare Advantage",
                    "CareSource",
                    "Kaiser Permanente",
                    "Other Georgia Medicaid plan",
                    "Private pay",
                    "Not sure",
                ], True),
                SEL("urgency", "How soon is care needed?", [
                    "As soon as possible (discharge in 72 hours or less)",
                    "Within 1-2 weeks",
                    "Within the month",
                    "Just exploring options",
                ], True),
                LT("notes", "Anything else we should know? (no diagnoses, Medicaid or insurance ID numbers)"),
            ]},
            {"title": "Consent", "fields": [
                CONSENT("consent_contact",
                        "I am a professional referral source, and I agree Sister to Sister may contact me about this referral."),
            ]},
        ],
    },
    "job_application": {
        "title": "Job Application (RN / Caregiver)",
        "description": (
            "Same application as the 'We Are Hiring' page. A request to be considered for "
            "employment; it does not guarantee an interview or employment."
        ),
        "contact_any": ["phone", "email"],
        "summary_key": "full_name",
        "sections": [
            {"title": "Applicant Information", "fields": [
                SEL("position", "Position applying for", [
                    "Registered Nurse (RN)",
                    "Caregiver / CNA / HHA / Companion Care",
                ], True),
                T("full_name", "Full legal name", True),
                T("phone", "Phone"),
                T("email", "Email"),
                T("address", "Address"),
                T("city", "City"),
                T("state", "State"),
                T("zip", "ZIP"),
            ]},
            {"title": "Licensure & Credentials (RN)",
             "show_if": {"key": "position", "equals": "Registered Nurse (RN)"},
             "fields": [
                T("rn_license", "RN license number"),
                T("rn_license_state", "Licensing state"),
                D("rn_license_exp", "License expiration date"),
                CHK("rn_flags", "Check all that apply", [
                    "Current BLS/CPR certification",
                    "Compact (multistate) RN license",
                    "Home health / home care experience",
                    "Supervisory / case management experience",
                ]),
                T("rn_years", "Years of nursing experience"),
                T("rn_credential", "Nursing school / highest nursing credential"),
            ]},
            {"title": "Certifications & Experience",
             "show_if": {"key": "position", "equals": "Caregiver / CNA / HHA / Companion Care"},
             "fields": [
                CHK("cg_certs", "Certifications", [
                    "Certified Nursing Assistant (CNA)",
                    "Home Health Aide (HHA) certification",
                    "Current CPR/First Aid certification",
                ]),
                CHK("cg_experience", "Experience", [
                    "Experience with personal care (bathing, dressing, hygiene)",
                    "Experience with transfers/mobility assistance",
                    "Experience with dementia/Alzheimer's care",
                ]),
                T("cg_cert_number", "If certified, certification number"),
                D("cg_cert_exp", "Certification expiration date"),
                T("cg_years", "Years of caregiving experience"),
            ]},
            {"title": "Other Training", "fields": [
                LT("other_training", "Other relevant training or experience"),
            ]},
            {"title": "Availability", "fields": [
                CHK("days", "Days generally available",
                    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
                CHK("shifts", "Shifts generally available",
                    ["Mornings", "Afternoons", "Evenings", "Overnights"]),
                SEL("employment_type", "Desired employment type",
                    ["Full-time", "Part-time", "PRN / as-needed"]),
                D("earliest_start", "Earliest available start date"),
            ]},
            {"title": "Employment History", "fields": [
                T("emp1_employer", "Employer"),
                T("emp1_position", "Position"),
                T("emp1_dates", "Dates employed (e.g., 2021-2024)"),
                T("emp1_reason", "Reason for leaving"),
                T("emp2_employer", "Employer 2 (optional)"),
                T("emp2_position", "Position"),
                T("emp2_dates", "Dates employed (e.g., 2018-2021)"),
                T("emp2_reason", "Reason for leaving"),
            ]},
            {"title": "Professional References (at least one, not a relative)", "fields": [
                T("ref1_name", "Reference name"),
                T("ref1_relationship", "Relationship"),
                T("ref1_phone", "Phone"),
                T("ref2_name", "Reference 2 name (optional)"),
                T("ref2_relationship", "Relationship"),
                T("ref2_phone", "Phone"),
            ]},
            {"title": "Eligibility & Background", "fields": [
                CHK("eligibility", "Check all that apply", [
                    "I am at least 18 years of age.",
                    "I am legally eligible to work in the United States.",
                    "I hold a valid driver's license.",
                    "I have reliable transportation.",
                    "I am willing to complete a background check and any state-required registry check.",
                    "I am willing to complete a drug screening if required.",
                    "I have been convicted of a crime relevant to this position (explain below if checked).",
                ]),
                LT("conviction_explain",
                   "If checked above, please explain (a conviction does not automatically disqualify you)"),
            ]},
            {"title": "Certification", "fields": [
                CONSENT("certify",
                        "I certify that the information in this application is true and complete to the best of my knowledge."),
            ]},
        ],
    },
}

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
# 13-19 digits (card-like) or SSN-like; the website forbids these in free text.
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")


def _section_visible(section: dict, values: Dict[str, Any]) -> bool:
    cond = section.get("show_if")
    return True if not cond else values.get(cond["key"]) == cond["equals"]


def _clean_text(v: Any, label: str) -> str:
    s = str(v).strip() if v is not None else ""
    if len(s) > MAX_TEXT:
        raise HTTPException(400, f"'{label}' is too long (max {MAX_TEXT} characters)")
    if s and (_CARD_RE.search(s) or _SSN_RE.search(s)):
        raise HTTPException(
            400, f"'{label}' looks like a card or ID number. Please remove it - this form does not collect those.")
    return s


def validate_values(form_key: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a cleaned values dict (allow-listed keys, coerced types).
    Raises HTTPException(400) with a human-readable message on problems."""
    form = FORMS[form_key]
    raw = raw or {}
    out: Dict[str, Any] = {}
    # Section visibility depends on (possibly select) values, so evaluate on raw.
    for section in form["sections"]:
        if not _section_visible(section, raw):
            continue
        for f in section["fields"]:
            key, label, ftype = f["key"], f["label"], f["type"]
            v = raw.get(key)
            if ftype == "consent":
                ok = v is True or str(v).lower() == "true"
                if f.get("required") and not ok:
                    raise HTTPException(400, f"Please confirm: {label}")
                out[key] = bool(ok)
            elif ftype == "checkbox":
                arr = v if isinstance(v, list) else []
                bad = [x for x in arr if x not in f["options"]]
                if bad:
                    raise HTTPException(400, f"Invalid choice for '{label}'")
                if f.get("required") and not arr:
                    raise HTTPException(400, f"Please fill: {label}")
                out[key] = [x for x in f["options"] if x in arr]
            elif ftype in ("select", "radio"):
                s = _clean_text(v, label)
                if s and s not in f["options"]:
                    raise HTTPException(400, f"Invalid choice for '{label}'")
                if f.get("required") and not s:
                    raise HTTPException(400, f"Please fill: {label}")
                out[key] = s
            else:  # text | longtext | date
                s = _clean_text(v, label)
                if ftype == "date" and s and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                    raise HTTPException(400, f"'{label}' must be a date (YYYY-MM-DD)")
                if f.get("required") and not s:
                    raise HTTPException(400, f"Please fill: {label}")
                out[key] = s
    contact = form.get("contact_any") or []
    if contact and not any(out.get(k) for k in contact):
        raise HTTPException(400, "Please provide at least a phone number or an email.")
    return out


# --------------------------------------------------------------------------
# Endpoints (admin only - these hold personal information)
# --------------------------------------------------------------------------
class SubmitReq(BaseModel):
    form_key: str
    values: Dict[str, Any]


class UpdateReq(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None


@router.get("/intake/forms")
async def list_intake_forms(current: UserPublic = Depends(require_admin)):
    return [
        {"key": k, "title": f["title"], "description": f["description"], "schema": {"sections": f["sections"]}}
        for k, f in FORMS.items()
    ]


@router.post("/intake/submissions")
async def create_submission(req: SubmitReq, current: UserPublic = Depends(require_admin)):
    if req.form_key not in FORMS:
        raise HTTPException(404, "Unknown form")
    values = validate_values(req.form_key, req.values)
    form = FORMS[req.form_key]
    doc = {
        "id": str(uuid.uuid4()),
        "form_key": req.form_key,
        "form_title": form["title"],
        "summary": values.get(form["summary_key"], "") or "(no name)",
        "values": values,
        "status": "new",
        "admin_notes": "",
        "submitted_by": current.id,
        "submitted_by_name": current.name,
        "submitted_at": now_iso(),
    }
    await db.intake_submissions.insert_one(dict(doc))
    logger.info("intake: %s submitted by %s", req.form_key, current.id)
    return {"id": doc["id"], "status": "new"}


@router.get("/intake/submissions")
async def list_submissions(
    form_key: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200),
    current: UserPublic = Depends(require_admin),
):
    q: Dict[str, Any] = {}
    if form_key:
        q["form_key"] = form_key
    if status:
        q["status"] = status
    cursor = db.intake_submissions.find(
        q, {"_id": 0, "values": 0}).sort("submitted_at", -1).limit(limit)
    return await cursor.to_list(limit)


@router.get("/intake/submissions/{sub_id}")
async def get_submission(sub_id: str, current: UserPublic = Depends(require_admin)):
    row = await db.intake_submissions.find_one({"id": sub_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Submission not found")
    row["schema"] = {"sections": FORMS.get(row["form_key"], {}).get("sections", [])}
    return row


@router.patch("/intake/submissions/{sub_id}")
async def update_submission(sub_id: str, req: UpdateReq, current: UserPublic = Depends(require_admin)):
    patch: Dict[str, Any] = {}
    if req.status is not None:
        if req.status not in STATUSES:
            raise HTTPException(400, "Invalid status")
        patch["status"] = req.status
    if req.admin_notes is not None:
        patch["admin_notes"] = _clean_text(req.admin_notes, "Notes")
    if not patch:
        raise HTTPException(400, "Nothing to update")
    patch["updated_at"] = now_iso()
    res = await db.intake_submissions.update_one({"id": sub_id}, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(404, "Submission not found")
    return {"id": sub_id, **patch}
