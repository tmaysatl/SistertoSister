"""Form schemas + filled-PDF generator for native in-app form rendering.

Each schema declares fields the React Native UI renders as text inputs,
date pickers, dropdowns, checkboxes, etc. On submit, the backend overlays
the typed values + signature image onto a clean copy of the AcroForm PDF
template (using reportlab) and saves the result as a NEW document owned by
the submitting user.
"""
import base64
import io
from typing import Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter

# Field kinds the frontend understands
# text | longtext | date | number | money | select | radio | checkbox |
# checkboxgrid (rows x cols) | signature
SCHEMAS: Dict[str, dict] = {
    # ----- CLIENT ONBOARDING -----
    "05 - Client's Authorization Form": {
        "sections": [
            {"title": "Client Information", "fields": [
                {"key": "client_name", "label": "Full legal name", "type": "text", "required": True},
                {"key": "client_dob", "label": "Date of birth", "type": "date"},
                {"key": "effective_date", "label": "Effective date", "type": "date"},
                {"key": "end_date", "label": "End / review date", "type": "date"},
                {"key": "client_address", "label": "Address", "type": "longtext"},
            ]},
            {"title": "Services Authorized", "fields": [
                {"key": "services", "label": "Check all that apply", "type": "checkbox",
                 "options": ["Personal care (bathing, grooming)", "Companion services",
                             "Skilled nursing", "Homemaker / light housekeeping",
                             "Respite care", "Transportation assistance",
                             "Medication reminders", "Meal preparation"]},
            ]},
            {"title": "Authorization", "fields": [
                {"key": "signature", "label": "Client signature", "type": "signature", "required": True},
            ]},
        ],
    },
    "09 - Auto Release": {
        "sections": [
            {"title": "Client", "fields": [
                {"key": "client_name", "label": "Client full name", "type": "text", "required": True},
                {"key": "client_dob", "label": "Date of birth", "type": "date"},
            ]},
            {"title": "Authorized Driver(s)", "fields": [
                {"key": "driver_names", "label": "Driver name(s)", "type": "text"},
            ]},
            {"title": "Vehicle", "fields": [
                {"key": "vehicle_make", "label": "Make", "type": "text"},
                {"key": "vehicle_model", "label": "Model", "type": "text"},
                {"key": "vehicle_year", "label": "Year", "type": "text"},
                {"key": "vehicle_plate", "label": "License plate", "type": "text"},
            ]},
            {"title": "Purpose & Period", "fields": [
                {"key": "purpose", "label": "Purpose of transportation", "type": "longtext"},
                {"key": "auth_start", "label": "Authorization start date", "type": "date"},
                {"key": "auth_end", "label": "Authorization end date", "type": "date"},
                {"key": "signature", "label": "Client signature", "type": "signature", "required": True},
            ]},
        ],
    },
    "01 - Employment Application": {
        "sections": [
            {"title": "Applicant", "fields": [
                {"key": "name", "label": "Full legal name", "type": "text", "required": True},
                {"key": "dob", "label": "Date of birth", "type": "date"},
                {"key": "ssn", "label": "SSN (last 4)", "type": "text"},
                {"key": "phone", "label": "Phone", "type": "text"},
                {"key": "email", "label": "Email", "type": "text"},
                {"key": "address", "label": "Address", "type": "longtext"},
            ]},
            {"title": "Prior Employment (most recent first)", "fields": [
                {"key": "emp1_name", "label": "Employer 1 name", "type": "text"},
                {"key": "emp1_role", "label": "Role", "type": "text"},
                {"key": "emp1_dates", "label": "Dates", "type": "text"},
                {"key": "emp2_name", "label": "Employer 2 name", "type": "text"},
                {"key": "emp2_role", "label": "Role", "type": "text"},
                {"key": "emp2_dates", "label": "Dates", "type": "text"},
            ]},
            {"title": "References", "fields": [
                {"key": "ref1_name", "label": "Reference 1 name", "type": "text"},
                {"key": "ref1_phone", "label": "Phone", "type": "text"},
                {"key": "ref1_rel", "label": "Relationship", "type": "text"},
                {"key": "ref2_name", "label": "Reference 2 name", "type": "text"},
                {"key": "ref2_phone", "label": "Phone", "type": "text"},
                {"key": "ref2_rel", "label": "Relationship", "type": "text"},
            ]},
            {"title": "Sign", "fields": [
                {"key": "signature", "label": "Applicant signature", "type": "signature", "required": True},
            ]},
        ],
    },
    "04 - Direct Deposit Authorization": {
        "sections": [
            {"title": "Employee", "fields": [
                {"key": "name", "label": "Full name", "type": "text", "required": True},
            ]},
            {"title": "Bank Information", "fields": [
                {"key": "bank_name", "label": "Bank name", "type": "text", "required": True},
                {"key": "account_type", "label": "Account type", "type": "radio",
                 "options": ["Checking", "Savings"]},
                {"key": "routing", "label": "Routing number (9 digits)", "type": "text", "required": True},
                {"key": "account", "label": "Account number", "type": "text", "required": True},
            ]},
            {"title": "Authorization", "fields": [
                {"key": "consent", "label": "I authorize Sister to Sister, PHCP to deposit my net pay into the account above.",
                 "type": "checkbox", "options": ["I authorize"]},
                {"key": "signature", "label": "Employee signature", "type": "signature", "required": True},
            ]},
        ],
    },
    "10 - Emergency Contact Form": {
        "sections": [
            {"title": "Caregiver", "fields": [
                {"key": "name", "label": "Caregiver full name", "type": "text", "required": True},
            ]},
            {"title": "Emergency Contact 1", "fields": [
                {"key": "ec1_name", "label": "Name", "type": "text", "required": True},
                {"key": "ec1_rel", "label": "Relationship", "type": "text"},
                {"key": "ec1_phone", "label": "Primary phone", "type": "text", "required": True},
                {"key": "ec1_phone2", "label": "Alternate phone", "type": "text"},
            ]},
            {"title": "Emergency Contact 2", "fields": [
                {"key": "ec2_name", "label": "Name", "type": "text"},
                {"key": "ec2_rel", "label": "Relationship", "type": "text"},
                {"key": "ec2_phone", "label": "Primary phone", "type": "text"},
                {"key": "ec2_phone2", "label": "Alternate phone", "type": "text"},
            ]},
            {"title": "Sign", "fields": [
                {"key": "signature", "label": "Caregiver signature", "type": "signature", "required": True},
            ]},
        ],
    },
}


def has_schema(title: str) -> bool:
    return title in SCHEMAS


def get_schema(title: str) -> Optional[dict]:
    return SCHEMAS.get(title)


def render_filled_pdf(title: str, values: dict, signature_b64: Optional[str],
                      submitter_name: str) -> bytes:
    """Generate a completed PDF: branded header + every field as
    "Label: Value" + the signature image at the bottom."""
    schema = SCHEMAS[title]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    page_w, page_h = letter

    # White page
    c.setFillColor(colors.white)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)

    # Header
    c.setFillColor(colors.HexColor("#204231"))
    c.rect(0, page_h - 60, page_w, 60, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(0.6 * 72, page_h - 30, "Sister to Sister, PHCP")
    c.setFont("Helvetica", 11)
    c.drawString(0.6 * 72, page_h - 48, title)

    y = page_h - 80
    for section in schema["sections"]:
        # Section title
        c.setFillColor(colors.HexColor("#E3EBE6"))
        c.rect(0.5 * 72, y - 2, page_w - 72, 20, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#204231"))
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(0.65 * 72, y + 4, section["title"])
        y -= 28

        for f in section["fields"]:
            if f["type"] == "signature":
                # render signature at the end
                continue
            val = values.get(f["key"], "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            c.setFillColor(colors.HexColor("#0E1A12"))
            c.setFont("Helvetica-Bold", 9)
            c.drawString(0.6 * 72, y, f["label"] + ":")
            c.setFont("Helvetica", 10)
            # wrap long values
            text = str(val) if val else "—"
            from reportlab.lib.utils import simpleSplit
            for line in simpleSplit(text, "Helvetica", 10, page_w - 1.3 * 72):
                c.drawString(2.4 * 72, y, line)
                y -= 14
            y -= 4
            if y < 1.5 * 72:
                c.showPage()
                c.setFillColor(colors.white)
                c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
                y = page_h - 0.6 * 72

    # Signature image
    if signature_b64:
        try:
            raw = signature_b64.split(",", 1)[-1]
            sig_bytes = base64.b64decode(raw)
            img = ImageReader(io.BytesIO(sig_bytes))
            c.setFillColor(colors.HexColor("#0E1A12"))
            c.setFont("Helvetica-Bold", 9)
            c.drawString(0.6 * 72, y - 4, "Signature:")
            c.drawImage(img, 0.6 * 72, y - 70, width=3.0 * 72, height=60,
                        preserveAspectRatio=True, mask='auto')
            y -= 80
        except Exception:
            pass

    c.setFillColor(colors.HexColor("#56615C"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.6 * 72, 0.4 * 72,
                 f"Submitted by {submitter_name} via Sister to Sister, PHCP compliance app.")
    c.save()
    return buf.getvalue()
