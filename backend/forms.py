"""Fillable AcroForm PDF generators for client + caregiver onboarding docs.

Each builder returns raw PDF bytes with proper AcroForm text/checkbox/radio
fields so the user can fill the form inside any PDF viewer (web browser,
Adobe Reader, Preview, Edge, mobile PDF readers, etc.).

Layout follows the same branded look as `playbook_pdf.py`.
"""
import io
from typing import Callable, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

BRAND_PRIMARY = colors.HexColor("#204231")
BRAND_TERTIARY = colors.HexColor("#E3EBE6")
TEXT_DIM = colors.HexColor("#56615C")
FIELD_BORDER = colors.HexColor("#BCC2BD")
FIELD_FILL = colors.HexColor("#FBFCFB")


# ---------- shared helpers ----------
def _draw_chrome(c: canvas.Canvas, title: str, subtitle: str = "") -> float:
    """Draw branded header + return Y cursor for body content."""
    page_w, page_h = letter
    c.setFillColor(BRAND_PRIMARY)
    c.rect(0, page_h - 70, page_w, 70, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(0.6 * inch, page_h - 32, "Sister to Sister, PHCP")
    c.setFont("Helvetica", 11)
    c.drawString(0.6 * inch, page_h - 50, title)
    if subtitle:
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawString(0.6 * inch, page_h - 62, subtitle)
    c.setFillColor(BRAND_PRIMARY)
    return page_h - 95


def _footer(c: canvas.Canvas, page: int):
    c.setFillColor(TEXT_DIM)
    c.setFont("Helvetica", 7.5)
    c.drawString(0.6 * inch, 0.4 * inch,
                 "Sister to Sister, PHCP \u2014 Confidential. "
                 "Save the filled PDF and submit to your administrator.")
    c.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch, f"Page {page}")


def _section(c: canvas.Canvas, text: str, y: float) -> float:
    c.setFillColor(BRAND_TERTIARY)
    c.rect(0.5 * inch, y - 2, letter[0] - 1.0 * inch, 20, stroke=0, fill=1)
    c.setFillColor(BRAND_PRIMARY)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(0.65 * inch, y + 4, text)
    return y - 28


def _label(c: canvas.Canvas, text: str, x: float, y: float,
           note: str = ""):
    c.setFillColor(BRAND_PRIMARY)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x, y + 22, text)
    if note:
        c.setFillColor(TEXT_DIM)
        c.setFont("Helvetica-Oblique", 7.2)
        c.drawString(x, y + 12, note)
    c.setFillColor(BRAND_PRIMARY)


def _text(c: canvas.Canvas, name: str, x: float, y: float,
          w: float = 3.0 * inch, h: float = 20, multiline: bool = False,
          value: str = ""):
    c.acroForm.textfield(
        name=name, value=value,
        x=x, y=y, width=w, height=h,
        borderColor=FIELD_BORDER, fillColor=FIELD_FILL,
        textColor=colors.HexColor("#1d2421"),
        forceBorder=True, borderWidth=0.6,
        fieldFlags="multiline" if multiline else "",
        fontSize=10, fontName="Helvetica",
    )


def _check(c: canvas.Canvas, name: str, x: float, y: float, size: float = 12):
    c.acroForm.checkbox(
        name=name, x=x, y=y, size=size,
        borderColor=FIELD_BORDER, fillColor=FIELD_FILL,
        textColor=BRAND_PRIMARY, borderWidth=0.6,
    )


def _checkrow(c: canvas.Canvas, label: str, name: str, x: float, y: float):
    _check(c, name, x, y - 2)
    c.setFillColor(BRAND_PRIMARY)
    c.setFont("Helvetica", 9)
    c.drawString(x + 18, y + 1, label)


def _radio(c: canvas.Canvas, name: str, options: List[str],
           x: float, y: float, dx: float = 1.4 * inch):
    for i, opt in enumerate(options):
        c.acroForm.radio(
            name=name, value=opt, selected=False,
            x=x + i * dx, y=y, size=12,
            borderColor=FIELD_BORDER, fillColor=FIELD_FILL,
            textColor=BRAND_PRIMARY, borderWidth=0.6,
            buttonStyle="circle",
        )
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica", 9)
        c.drawString(x + i * dx + 18, y + 2, opt)


def _signature_row(c: canvas.Canvas, y: float, who: str = "Signature"):
    """Drawn signature line + printed name + date row near the bottom."""
    _label(c, f"{who}", 0.6 * inch, y - 26)
    _text(c, f"sig_{who.lower().replace(' ', '_')}", 0.6 * inch, y - 30,
          w=3.4 * inch, h=26)
    _label(c, "Printed name", 4.2 * inch, y - 26)
    _text(c, f"print_{who.lower().replace(' ', '_')}", 4.2 * inch, y - 30,
          w=2.0 * inch, h=26)
    _label(c, "Date", 6.4 * inch, y - 26)
    _text(c, f"date_{who.lower().replace(' ', '_')}", 6.4 * inch, y - 30,
          w=1.0 * inch, h=26)
    return y - 70


def _wrap(builder: Callable[[canvas.Canvas], None],
          title: str, subtitle: str = "") -> bytes:
    """Wrap a single-page builder with branded chrome."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)
    c.setAuthor("Sister to Sister, PHCP")
    builder(c)
    c.save()
    return buf.getvalue()


# ============================================================
# CLIENT ONBOARDING (5 forms: seq 5, 9, 10, 12, 13)
# ============================================================
def client_05_authorization() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Client's Authorization Form",
                         "Authorization for services provided by Sister to Sister, PHCP")
        y = _section(c, "Client Information", y)
        _label(c, "Full legal name", 0.6 * inch, y)
        _text(c, "client_name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Date of birth", 0.6 * inch, y)
        _text(c, "client_dob", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "Effective date", 3.2 * inch, y)
        _text(c, "effective_date", 3.2 * inch, y, w=2.4 * inch)
        _label(c, "End / review date", 5.8 * inch, y)
        _text(c, "end_date", 5.8 * inch, y, w=letter[0] - 6.4 * inch)
        y -= 50
        _label(c, "Address", 0.6 * inch, y)
        _text(c, "client_address", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=44, multiline=True)
        y -= 90
        y = _section(c, "Services Authorized (check all that apply)", y)
        services = [
            ("Personal care (bathing, grooming)", "svc_personal"),
            ("Companion services", "svc_companion"),
            ("Skilled nursing", "svc_skilled"),
            ("Homemaker / light housekeeping", "svc_home"),
            ("Respite care", "svc_respite"),
            ("Transportation assistance", "svc_transport"),
            ("Medication reminders", "svc_meds"),
            ("Meal preparation", "svc_meals"),
        ]
        for i, (lab, n) in enumerate(services):
            col = i % 2
            row = i // 2
            _checkrow(c, lab, n,
                      0.6 * inch + col * 3.6 * inch, y - row * 22)
        y -= len(services) // 2 * 22 + 10
        y = _section(c, "Authorization", y)
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica", 9)
        c.drawString(0.6 * inch, y, "I authorize Sister to Sister, PHCP to provide the services checked above.")
        y -= 16
        c.drawString(0.6 * inch, y, "I understand I may revoke this authorization in writing at any time.")
        y -= 30
        _signature_row(c, y, "Client signature")
        _footer(c, 1)
    return _wrap(build, "Client's Authorization Form")


def client_09_auto_release() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Auto Release",
                         "Authorization for caregiver-driven transportation")
        y = _section(c, "Client Information", y)
        _label(c, "Client full name", 0.6 * inch, y)
        _text(c, "client_name", 0.6 * inch, y, w=4.0 * inch)
        _label(c, "Date of birth", 4.8 * inch, y)
        _text(c, "client_dob", 4.8 * inch, y, w=letter[0] - 5.4 * inch)
        y -= 50

        y = _section(c, "Authorized Driver(s)", y)
        _label(c, "Driver name(s)", 0.6 * inch, y)
        _text(c, "driver_names", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50

        y = _section(c, "Vehicle Information", y)
        _label(c, "Make", 0.6 * inch, y)
        _text(c, "vehicle_make", 0.6 * inch, y, w=2.0 * inch)
        _label(c, "Model", 2.8 * inch, y)
        _text(c, "vehicle_model", 2.8 * inch, y, w=2.0 * inch)
        _label(c, "Year", 5.0 * inch, y)
        _text(c, "vehicle_year", 5.0 * inch, y, w=1.2 * inch)
        _label(c, "License plate", 6.4 * inch, y)
        _text(c, "vehicle_plate", 6.4 * inch, y, w=letter[0] - 7.0 * inch)
        y -= 50

        y = _section(c, "Purpose & Period", y)
        _label(c, "Purpose of transportation", 0.6 * inch, y,
               note="e.g. medical appointments, errands, social outings")
        _text(c, "purpose", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=44, multiline=True)
        y -= 90
        _label(c, "Authorization start date", 0.6 * inch, y)
        _text(c, "auth_start", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "Authorization end date", 3.2 * inch, y)
        _text(c, "auth_end", 3.2 * inch, y, w=2.4 * inch)
        y -= 60

        _signature_row(c, y, "Client signature")
        _footer(c, 1)
    return _wrap(build, "Auto Release")


def client_10_payer_info() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Third Party Payer Information",
                         "Insurance and billing details")
        y = _section(c, "Primary Insurance", y)
        _label(c, "Insurer name", 0.6 * inch, y)
        _text(c, "p_insurer", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Policy / Member ID", 0.6 * inch, y)
        _text(c, "p_policy", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Group #", 3.8 * inch, y)
        _text(c, "p_group", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 50
        _label(c, "Primary holder name", 0.6 * inch, y)
        _text(c, "p_holder", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Relationship to client", 3.8 * inch, y)
        _text(c, "p_relation", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 50
        _label(c, "Contact phone", 0.6 * inch, y)
        _text(c, "p_phone", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50

        y = _section(c, "Secondary Insurance (if any)", y)
        _label(c, "Insurer name", 0.6 * inch, y)
        _text(c, "s_insurer", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Policy / Member ID", 0.6 * inch, y)
        _text(c, "s_policy", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Group #", 3.8 * inch, y)
        _text(c, "s_group", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 50
        _label(c, "Primary holder name", 0.6 * inch, y)
        _text(c, "s_holder", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Relationship to client", 3.8 * inch, y)
        _text(c, "s_relation", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 50

        _signature_row(c, y, "Client signature")
        _footer(c, 1)
    return _wrap(build, "Third Party Payer Information")


def client_12_personal_funds() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Authorization of Use of Personal Funds",
                         "Authorize a payee to manage client personal funds")
        y = _section(c, "Client Information", y)
        _label(c, "Client name", 0.6 * inch, y)
        _text(c, "client_name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50

        y = _section(c, "Authorization", y)
        _label(c, "Authorized payee name", 0.6 * inch, y)
        _text(c, "payee_name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Amount authorized", 0.6 * inch, y,
               note="$ per occurrence or per period")
        _text(c, "amount", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "Frequency", 3.2 * inch, y,
               note="e.g. weekly, monthly, as needed")
        _text(c, "frequency", 3.2 * inch, y, w=letter[0] - 3.8 * inch)
        y -= 60
        _label(c, "Purpose / use of funds", 0.6 * inch, y)
        _text(c, "purpose", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=44, multiline=True)
        y -= 90
        _label(c, "Start date", 0.6 * inch, y)
        _text(c, "start_date", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "End date", 3.2 * inch, y)
        _text(c, "end_date", 3.2 * inch, y, w=2.4 * inch)
        y -= 60

        _signature_row(c, y, "Client signature")
        _footer(c, 1)
    return _wrap(build, "Authorization of Use of Personal Funds")


def client_13_medication_dietary() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Client-Specific Medication & Dietary",
                         "Allergies, medications, and dietary restrictions")
        y = _section(c, "Allergies", y)
        _label(c, "Drug / food / environmental allergies", 0.6 * inch, y)
        _text(c, "allergies", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=44, multiline=True)
        y -= 90

        y = _section(c, "Current Medications", y)
        _label(c, "List each medication (name, dose, frequency, prescribing physician)",
               0.6 * inch, y)
        _text(c, "medications", 0.6 * inch, y - 60,
              w=letter[0] - 1.2 * inch, h=80, multiline=True)
        y -= 120

        y = _section(c, "Dietary Restrictions & Preferences", y)
        _label(c, "Restrictions (e.g. low sodium, diabetic, no shellfish)",
               0.6 * inch, y)
        _text(c, "diet", 0.6 * inch, y - 40,
              w=letter[0] - 1.2 * inch, h=58, multiline=True)
        y -= 90

        y = _section(c, "Special Instructions", y)
        _text(c, "instructions", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=44, multiline=True)
        y -= 75

        _signature_row(c, y, "Client signature")
        _footer(c, 1)
    return _wrap(build, "Client-Specific Medication & Dietary")


# ============================================================
# CAREGIVER ONBOARDING (14 forms)
# ============================================================
def caregiver_01_employment_app() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Employment Application")
        y = _section(c, "Applicant Information", y)
        _label(c, "Full legal name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Date of birth", 0.6 * inch, y)
        _text(c, "dob", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "SSN (last 4)", 3.2 * inch, y)
        _text(c, "ssn", 3.2 * inch, y, w=1.5 * inch)
        _label(c, "Phone", 4.9 * inch, y)
        _text(c, "phone", 4.9 * inch, y, w=letter[0] - 5.5 * inch)
        y -= 50
        _label(c, "Address", 0.6 * inch, y)
        _text(c, "address", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=40, multiline=True)
        y -= 85
        _label(c, "Email", 0.6 * inch, y)
        _text(c, "email", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        y = _section(c, "Prior Employment (most recent first)", y)
        for i in range(1, 4):
            _label(c, f"Employer {i}", 0.6 * inch, y)
            _text(c, f"emp{i}_name", 0.6 * inch, y, w=2.5 * inch)
            _label(c, "Role", 3.2 * inch, y)
            _text(c, f"emp{i}_role", 3.2 * inch, y, w=2.0 * inch)
            _label(c, "Dates", 5.3 * inch, y)
            _text(c, f"emp{i}_dates", 5.3 * inch, y, w=letter[0] - 5.9 * inch)
            y -= 50
        y = _section(c, "References", y)
        for i in range(1, 4):
            _label(c, f"Reference {i}", 0.6 * inch, y)
            _text(c, f"ref{i}_name", 0.6 * inch, y, w=2.5 * inch)
            _label(c, "Phone", 3.2 * inch, y)
            _text(c, f"ref{i}_phone", 3.2 * inch, y, w=2.0 * inch)
            _label(c, "Relationship", 5.3 * inch, y)
            _text(c, f"ref{i}_rel", 5.3 * inch, y, w=letter[0] - 5.9 * inch)
            y -= 50
        _signature_row(c, y, "Applicant signature")
        _footer(c, 1)
    return _wrap(build, "Employment Application")


def caregiver_02_i9() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Form I-9 Employment Eligibility (Summary)",
                         "Internal companion to the official USCIS Form I-9")
        y = _section(c, "Employee Information", y)
        _label(c, "Full name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Date of birth", 0.6 * inch, y)
        _text(c, "dob", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "SSN", 3.2 * inch, y)
        _text(c, "ssn", 3.2 * inch, y, w=2.4 * inch)
        y -= 50
        _label(c, "Address", 0.6 * inch, y)
        _text(c, "address", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=40, multiline=True)
        y -= 90
        y = _section(c, "Citizenship Attestation", y)
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica", 9)
        c.drawString(0.6 * inch, y + 8, "I attest, under penalty of perjury, that I am:")
        _radio(c, "citizenship",
               ["US citizen", "Noncitizen national", "Lawful permanent resident", "Authorized to work"],
               0.6 * inch, y - 18, dx=1.7 * inch)
        y -= 60
        y = _section(c, "Identity / Work Authorization Document", y)
        _label(c, "Document type", 0.6 * inch, y,
               note="e.g. US Passport, Driver's License + SS Card, EAD")
        _text(c, "doc_type", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Document number", 0.6 * inch, y)
        _text(c, "doc_number", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Expiration", 3.8 * inch, y)
        _text(c, "doc_exp", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 60
        _signature_row(c, y, "Employee signature")
        _footer(c, 1)
    return _wrap(build, "Form I-9 Summary")


def caregiver_03_w4() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Form W-4 Tax Withholding (Summary)",
                         "Internal companion to the official IRS Form W-4")
        y = _section(c, "Employee Information", y)
        _label(c, "Full name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=4.0 * inch)
        _label(c, "SSN", 4.8 * inch, y)
        _text(c, "ssn", 4.8 * inch, y, w=letter[0] - 5.4 * inch)
        y -= 50
        _label(c, "Address", 0.6 * inch, y)
        _text(c, "address", 0.6 * inch, y - 30,
              w=letter[0] - 1.2 * inch, h=40, multiline=True)
        y -= 90
        y = _section(c, "Filing Status", y)
        _radio(c, "filing_status",
               ["Single / MFS", "Married filing jointly", "Head of household"],
               0.6 * inch, y, dx=2.2 * inch)
        y -= 40
        y = _section(c, "Withholding Adjustments", y)
        _label(c, "Dependents claimed (multiply $2,000 per child under 17)",
               0.6 * inch, y)
        _text(c, "dependents_amount", 0.6 * inch, y, w=2.0 * inch)
        _label(c, "Other dependents (multiply $500 each)", 2.8 * inch, y)
        _text(c, "other_dependents", 2.8 * inch, y, w=2.0 * inch)
        _label(c, "Other income (annual)", 5.0 * inch, y)
        _text(c, "other_income", 5.0 * inch, y, w=letter[0] - 5.6 * inch)
        y -= 60
        _label(c, "Extra withholding per paycheck", 0.6 * inch, y)
        _text(c, "extra_withhold", 0.6 * inch, y, w=2.4 * inch)
        y -= 50
        _signature_row(c, y, "Employee signature")
        _footer(c, 1)
    return _wrap(build, "Form W-4 Summary")


def caregiver_04_direct_deposit() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Direct Deposit Authorization")
        y = _section(c, "Employee Information", y)
        _label(c, "Full name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        y = _section(c, "Bank Information", y)
        _label(c, "Bank name", 0.6 * inch, y)
        _text(c, "bank_name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(0.6 * inch, y + 22, "Account type")
        _radio(c, "account_type", ["Checking", "Savings"],
               0.6 * inch, y, dx=1.5 * inch)
        y -= 50
        _label(c, "Routing number (9 digits)", 0.6 * inch, y)
        _text(c, "routing", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Account number", 3.8 * inch, y)
        _text(c, "account", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 50
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica", 9)
        c.drawString(0.6 * inch, y, "I authorize Sister to Sister, PHCP to deposit my net pay into the account above.")
        y -= 30
        _signature_row(c, y, "Employee signature")
        _footer(c, 1)
    return _wrap(build, "Direct Deposit Authorization")


def caregiver_05_oig() -> bytes:
    def build(c):
        y = _draw_chrome(c, "OIG / Background Check Authorization")
        y = _section(c, "Authorization", y)
        _label(c, "Full legal name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _label(c, "Date of birth", 0.6 * inch, y)
        _text(c, "dob", 0.6 * inch, y, w=2.4 * inch)
        _label(c, "SSN", 3.2 * inch, y)
        _text(c, "ssn", 3.2 * inch, y, w=letter[0] - 3.8 * inch)
        y -= 50
        _label(c, "Prior names / aliases", 0.6 * inch, y)
        _text(c, "aliases", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica", 9)
        c.drawString(0.6 * inch, y,
                     "I authorize Sister to Sister, PHCP and its background-check vendor to perform")
        y -= 14
        c.drawString(0.6 * inch, y,
                     "Federal OIG, SAM, state abuse registry, and criminal-history searches.")
        y -= 30
        _signature_row(c, y, "Applicant signature")
        _footer(c, 1)
    return _wrap(build, "OIG Background Check Authorization")


def caregiver_06_competency() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Caregiver Competency Checklist",
                         "Self-rate each skill: 1 = need training, 4 = proficient")
        skills = [
            "Personal care / ADLs (bathing, toileting, dressing)",
            "Mobility transfers & ambulation",
            "Vital signs monitoring (BP, pulse, temp)",
            "Medication reminders & charting",
            "Meal preparation & feeding assistance",
            "Infection control & hand hygiene",
            "Bloodborne pathogen safety",
            "Confidentiality (HIPAA)",
            "Documentation & progress notes",
            "Communication with family / care team",
            "Emergency response & first aid",
            "Dementia / Alzheimer care techniques",
            "Range of motion & basic exercises",
            "Skin integrity / pressure injury prevention",
            "End of life / hospice support",
        ]
        y -= 5
        _label(c, "Caregiver name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 36
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(0.6 * inch, y, "SKILL")
        c.drawString(5.2 * inch, y, "1")
        c.drawString(5.7 * inch, y, "2")
        c.drawString(6.2 * inch, y, "3")
        c.drawString(6.7 * inch, y, "4")
        y -= 6
        c.setStrokeColor(FIELD_BORDER)
        c.line(0.5 * inch, y, letter[0] - 0.5 * inch, y)
        y -= 16
        for i, skill in enumerate(skills, 1):
            c.setFillColor(BRAND_PRIMARY)
            c.setFont("Helvetica", 8.5)
            c.drawString(0.6 * inch, y, f"{i}. {skill}")
            for j, val in enumerate(["1", "2", "3", "4"]):
                c.acroForm.radio(
                    name=f"skill{i}", value=val, selected=False,
                    x=5.18 * inch + j * 0.5 * inch, y=y - 3, size=10,
                    borderColor=FIELD_BORDER, fillColor=FIELD_FILL,
                    textColor=BRAND_PRIMARY, borderWidth=0.6,
                    buttonStyle="circle",
                )
            y -= 18
        y -= 6
        _signature_row(c, y, "Caregiver signature")
        _footer(c, 1)
    return _wrap(build, "Caregiver Competency Checklist")


def _ack_form(title: str, ack_lines: List[str]) -> Callable[[], bytes]:
    def factory() -> bytes:
        def build(c):
            y = _draw_chrome(c, title)
            y = _section(c, "Caregiver Information", y)
            _label(c, "Full legal name", 0.6 * inch, y)
            _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
            y -= 50
            y = _section(c, "Acknowledgment", y)
            for i, line in enumerate(ack_lines, 1):
                _checkrow(c, line, f"ack{i}", 0.6 * inch, y)
                y -= 24
            y -= 8
            _signature_row(c, y, "Caregiver signature")
            _footer(c, 1)
        return _wrap(build, title)
    return factory


caregiver_07_hipaa = _ack_form(
    "HIPAA Confidentiality Agreement",
    [
        "I will hold client PHI in the strictest confidence.",
        "I will not access PHI outside the scope of my assigned duties.",
        "I will report any suspected privacy breach immediately.",
        "I understand violation may result in termination and legal penalties.",
    ],
)

caregiver_08_code_of_conduct = _ack_form(
    "Code of Conduct Acknowledgment",
    [
        "I will treat every client and coworker with dignity and respect.",
        "I will not accept gifts, tips, or money beyond approved wages.",
        "I will refrain from drug, alcohol, and tobacco use while on duty.",
        "I will arrive on time, in uniform, and present a professional appearance.",
        "I will report any boundary or ethical concerns to my supervisor.",
    ],
)

caregiver_09_job_description = _ack_form(
    "Job Description Acknowledgment",
    [
        "I have received and read my caregiver job description.",
        "I understand the essential duties, hours, and physical requirements.",
        "I am able to perform these duties with or without reasonable accommodation.",
        "I will follow the agency's care-plan instructions for each client.",
    ],
)


def caregiver_10_emergency_contact() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Emergency Contact Form")
        y = _section(c, "Caregiver Information", y)
        _label(c, "Caregiver full name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        for i in range(1, 3):
            y = _section(c, f"Emergency Contact {i}", y)
            _label(c, "Name", 0.6 * inch, y)
            _text(c, f"ec{i}_name", 0.6 * inch, y, w=3.0 * inch)
            _label(c, "Relationship", 3.8 * inch, y)
            _text(c, f"ec{i}_rel", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
            y -= 50
            _label(c, "Primary phone", 0.6 * inch, y)
            _text(c, f"ec{i}_phone", 0.6 * inch, y, w=3.0 * inch)
            _label(c, "Alternate phone", 3.8 * inch, y)
            _text(c, f"ec{i}_phone2", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
            y -= 50
        _signature_row(c, y, "Caregiver signature")
        _footer(c, 1)
    return _wrap(build, "Emergency Contact Form")


def caregiver_11_drug_screening() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Drug Screening Consent")
        y = _section(c, "Applicant Information", y)
        _label(c, "Full legal name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=4.0 * inch)
        _label(c, "Date of birth", 4.8 * inch, y)
        _text(c, "dob", 4.8 * inch, y, w=letter[0] - 5.4 * inch)
        y -= 50
        y = _section(c, "Consent", y)
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica", 9)
        c.drawString(0.6 * inch, y,
                     "I consent to undergo pre-employment and reasonable-suspicion drug screening")
        y -= 14
        c.drawString(0.6 * inch, y,
                     "as a condition of my employment with Sister to Sister, PHCP. I understand")
        y -= 14
        c.drawString(0.6 * inch, y,
                     "that results will be reviewed confidentially and may impact my employment.")
        y -= 24
        _checkrow(c, "I have read and consent to the drug-screening policy.",
                  "consent_ack", 0.6 * inch, y)
        y -= 36
        _signature_row(c, y, "Applicant signature")
        _footer(c, 1)
    return _wrap(build, "Drug Screening Consent")


def caregiver_12_vehicle() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Vehicle Driver Authorization")
        y = _section(c, "Caregiver Information", y)
        _label(c, "Full name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        y = _section(c, "Driver's License", y)
        _label(c, "License number", 0.6 * inch, y)
        _text(c, "license_no", 0.6 * inch, y, w=2.6 * inch)
        _label(c, "State", 3.4 * inch, y)
        _text(c, "license_state", 3.4 * inch, y, w=1.0 * inch)
        _label(c, "Expiration", 4.6 * inch, y)
        _text(c, "license_exp", 4.6 * inch, y, w=letter[0] - 5.2 * inch)
        y -= 50
        y = _section(c, "Auto Insurance", y)
        _label(c, "Insurance carrier", 0.6 * inch, y)
        _text(c, "ins_carrier", 0.6 * inch, y, w=3.4 * inch)
        _label(c, "Policy number", 4.2 * inch, y)
        _text(c, "ins_policy", 4.2 * inch, y, w=letter[0] - 4.8 * inch)
        y -= 50
        y = _section(c, "Vehicle", y)
        _label(c, "Make", 0.6 * inch, y)
        _text(c, "v_make", 0.6 * inch, y, w=2.0 * inch)
        _label(c, "Model", 2.8 * inch, y)
        _text(c, "v_model", 2.8 * inch, y, w=2.0 * inch)
        _label(c, "Year", 5.0 * inch, y)
        _text(c, "v_year", 5.0 * inch, y, w=1.0 * inch)
        _label(c, "Plate", 6.2 * inch, y)
        _text(c, "v_plate", 6.2 * inch, y, w=letter[0] - 6.8 * inch)
        y -= 60
        _signature_row(c, y, "Caregiver signature")
        _footer(c, 1)
    return _wrap(build, "Vehicle Driver Authorization")


caregiver_13_policy_handbook = _ack_form(
    "Policy Handbook Acknowledgment",
    [
        "I have received the Sister to Sister, PHCP Policy & Procedure Handbook.",
        "I have read it, understood it, and agree to abide by its policies.",
        "I understand updates may be issued and I am responsible for reviewing them.",
    ],
)


def caregiver_14_availability() -> bytes:
    def build(c):
        y = _draw_chrome(c, "Emergency Contact & Availability")
        y = _section(c, "Caregiver Information", y)
        _label(c, "Full name", 0.6 * inch, y)
        _text(c, "name", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        y = _section(c, "Emergency Contact (in case of issue while on shift)", y)
        _label(c, "Name", 0.6 * inch, y)
        _text(c, "ec_name", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Relationship", 3.8 * inch, y)
        _text(c, "ec_rel", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 50
        _label(c, "Primary phone", 0.6 * inch, y)
        _text(c, "ec_phone", 0.6 * inch, y, w=3.0 * inch)
        _label(c, "Alternate phone", 3.8 * inch, y)
        _text(c, "ec_phone2", 3.8 * inch, y, w=letter[0] - 4.4 * inch)
        y -= 60
        y = _section(c, "Weekly Availability", y)
        days = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        shifts = [("Morning", "morn"), ("Afternoon", "aft"),
                  ("Evening", "eve"), ("Overnight", "night")]
        col_w = 0.85 * inch
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica-Bold", 8)
        for i, d in enumerate(days):
            c.drawString(1.7 * inch + i * col_w + 6, y + 14, d)
        for r, (label, key) in enumerate(shifts):
            row_y = y - r * 22
            c.setFont("Helvetica-Bold", 8)
            c.drawString(0.6 * inch, row_y + 1, label)
            for i, d in enumerate(days):
                _check(c, f"avail_{key}_{d}",
                       1.7 * inch + i * col_w + 10, row_y - 2, size=10)
        y -= len(shifts) * 22 + 8
        _label(c, "Preferred geographic zones / ZIP codes", 0.6 * inch, y)
        _text(c, "zones", 0.6 * inch, y, w=letter[0] - 1.2 * inch)
        y -= 50
        _signature_row(c, y, "Caregiver signature")
        _footer(c, 1)
    return _wrap(build, "Emergency Contact & Availability")


# ============================================================
# Registry: title (must match seq prefix in DB) -> builder
# ============================================================
CLIENT_BUILDERS: Dict[str, Callable[[], bytes]] = {
    "05 - Client's Authorization Form": client_05_authorization,
    "09 - Auto Release": client_09_auto_release,
    "10 - Third Party Payer Information": client_10_payer_info,
    "12 - Authorization of Use of Personal Funds": client_12_personal_funds,
    "13 - Client-Specific Medication & Dietary": client_13_medication_dietary,
}

CAREGIVER_BUILDERS: Dict[str, Callable[[], bytes]] = {
    "01 - Employment Application": caregiver_01_employment_app,
    "02 - Form I-9 Employment Eligibility": caregiver_02_i9,
    "03 - Form W-4 Tax Withholding": caregiver_03_w4,
    "04 - Direct Deposit Authorization": caregiver_04_direct_deposit,
    "05 - OIG / Background Check Authorization": caregiver_05_oig,
    "06 - Caregiver Competency Checklist": caregiver_06_competency,
    "07 - HIPAA Confidentiality Agreement": caregiver_07_hipaa,
    "08 - Code of Conduct Acknowledgment": caregiver_08_code_of_conduct,
    "09 - Job Description Acknowledgment": caregiver_09_job_description,
    "10 - Emergency Contact Form": caregiver_10_emergency_contact,
    "11 - Drug Screening Consent": caregiver_11_drug_screening,
    "12 - Vehicle Driver Authorization": caregiver_12_vehicle,
    "13 - Policy Handbook Acknowledgment": caregiver_13_policy_handbook,
    "14 - Emergency Contact & Availability": caregiver_14_availability,
}


def all_fillable_pdfs() -> List[Tuple[str, str, bytes, int]]:
    """Return (category, title, pdf_bytes, seq) for every fillable form."""
    out: List[Tuple[str, str, bytes, int]] = []
    for title, fn in CLIENT_BUILDERS.items():
        seq = int(title[:2])
        out.append(("client_onboarding", title, fn(), seq))
    for title, fn in CAREGIVER_BUILDERS.items():
        seq = int(title[:2])
        out.append(("caregiver_onboarding", title, fn(), seq))
    return out
