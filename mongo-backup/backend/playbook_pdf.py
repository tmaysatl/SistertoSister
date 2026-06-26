"""Generate two PDFs:

1. `build_playbook_pdf()` — formatted reference playbook (the markdown content
   rendered to a polished, branded PDF).
2. `build_intake_form_pdf()` — fillable AcroForm PDF that a new agency can
   complete in Adobe Reader / Preview and send back to start a replication.
"""
import io
from typing import Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table,
    TableStyle, PageBreak, ListFlowable, ListItem, HRFlowable,
)

BRAND_PRIMARY = colors.HexColor("#204231")
BRAND_TERTIARY = colors.HexColor("#E3EBE6")
TEXT_DIM = colors.HexColor("#56615C")
ACCENT = colors.HexColor("#C28A47")


# ---------- Shared header/footer ----------
def _draw_header_footer(canv: canvas.Canvas, doc):
    canv.saveState()
    # Header band
    canv.setFillColor(BRAND_PRIMARY)
    canv.rect(0, letter[1] - 36, letter[0], 36, stroke=0, fill=1)
    canv.setFillColor(colors.white)
    canv.setFont("Helvetica-Bold", 11)
    canv.drawString(0.6 * inch, letter[1] - 23,
                    "Sister to Sister, PHCP \u2014 Agency App Replication Playbook")
    canv.setFont("Helvetica", 8)
    canv.drawRightString(letter[0] - 0.6 * inch, letter[1] - 23,
                         f"Page {doc.page}")
    # Footer
    canv.setFillColor(TEXT_DIM)
    canv.setFont("Helvetica", 7)
    canv.drawString(0.6 * inch, 0.4 * inch,
                    "Generated June 2026  \u2022  Sister to Sister, PHCP")
    canv.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch,
                         "Confidential")
    canv.restoreState()


# =========================================================
# 1) Reference Playbook PDF
# =========================================================
def build_playbook_pdf() -> bytes:
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.85 * inch, bottomMargin=0.7 * inch,
        title="Agency App Replication Playbook",
        author="Sister to Sister, PHCP",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="normal")
    doc.addPageTemplates([
        PageTemplate(id="all", frames=frame, onPage=_draw_header_footer),
    ])

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"],
                        textColor=BRAND_PRIMARY, fontSize=20,
                        spaceAfter=10, leading=24)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"],
                        textColor=BRAND_PRIMARY, fontSize=14,
                        spaceBefore=14, spaceAfter=6, leading=18)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"],
                        textColor=BRAND_PRIMARY, fontSize=11,
                        spaceBefore=8, spaceAfter=4, leading=14)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=10,
                          leading=13, textColor=colors.HexColor("#1d2421"))
    small = ParagraphStyle("small", parent=body, fontSize=8.5,
                           textColor=TEXT_DIM)

    story = []
    story.append(Paragraph("Home Health Agency App", h1))
    story.append(Paragraph("Replication Playbook", ParagraphStyle(
        "subtitle", parent=ss["Heading2"], textColor=ACCENT, fontSize=13,
        spaceAfter=14)))
    story.append(Paragraph(
        "Master checklist to spin up this compliance-management app for any "
        "new home health agency. Source-of-truth app: <b>Sister to Sister, "
        "PHCP</b> (June 2026 build).", body))
    story.append(HRFlowable(width="100%", thickness=0.7,
                            color=BRAND_TERTIARY, spaceBefore=10,
                            spaceAfter=14))

    # 1. Accounts & Credentials
    story.append(Paragraph("1. Accounts &amp; Credentials Required", h2))
    story.append(Paragraph("A. Mandatory \u2014 cannot launch without these", h3))
    table_a = Table([
        ["#", "Account / Service", "Purpose", "Cost", "Who"],
        ["1", "Apple Developer", "iOS App Store publishing", "$99 / year", "Agency owner"],
        ["2", "Google Play Console", "Android Play Store publishing", "$25 one-time", "Agency owner"],
        ["3", "Firebase project (Google Cloud)", "Push notifications (FCM)", "Free tier", "Agency owner"],
        ["4", "Domain + privacy-policy hosting", "Required by both stores", "~$12 / yr", "Agency owner"],
        ["5", "Emergent account", "Builds, hosts backend + DB", "Subscription", "In place"],
        ["6", "Microsoft 365 admin email", "Audit-binder \u2192 Outlook / OneDrive", "$0-15 / user / mo", "Agency owner"],
    ], colWidths=[0.3 * inch, 1.7 * inch, 2.3 * inch, 1.1 * inch, 1.3 * inch])
    table_a.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, BRAND_TERTIARY]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCD7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table_a)

    story.append(Paragraph("B. Recommended (better UX, not strictly required)", h3))
    table_b = Table([
        ["#", "Account / Service", "Purpose", "Cost"],
        ["7", "Microsoft 365 Business Basic", "Adds OneDrive storage", "$7.20 / user / mo"],
        ["8", "Custom domain email", "Trust signal in shareable packets", "Included with M365"],
        ["9", "Logo design (vector PNG/SVG)", "Branding inside PDFs + watermarks", "Variable"],
    ], colWidths=[0.3 * inch, 2.0 * inch, 2.8 * inch, 1.6 * inch])
    table_b.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, BRAND_TERTIARY]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCD7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table_b)

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<i>For per-agency credential capture, use the Intake Form PDF "
        "(separate file).</i>", small))

    # 2. Systems & Integrations
    story.append(Paragraph("2. Systems &amp; Integrations Used", h2))
    sys_rows = [
        ["Layer", "Tech", "Purpose"],
        ["Frontend", "React Native (Expo Router, SDK 53)", "Cross-platform iOS + Android + Web"],
        ["Backend", "FastAPI (Python 3.11)", "Async APIs, auto-OpenAPI docs"],
        ["Database", "MongoDB (Motor async)", "Flexible document store"],
        ["Auth", "JWT (jose) + bcrypt", "Admin & caregiver roles"],
        ["AI assistant", "Claude Sonnet 4.5 / Emergent LLM Key", "In-app compliance Q&A"],
        ["PDF stamping", "reportlab + pypdf", "Watermarks, audit trail"],
        ["E-signature", "react-native-signature-canvas", "Signature canvas embedded into PDF"],
        ["Push notifications", "Emergent Push (FCM + APNs)", "Chat + shift change alerts"],
        ["Microsoft Graph", "MSAL + httpx + APScheduler", "Monthly Audit Binder export"],
    ]
    sys_t = Table(sys_rows, colWidths=[1.2 * inch, 2.6 * inch, 2.9 * inch])
    sys_t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, BRAND_TERTIARY]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCD7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(sys_t)

    story.append(PageBreak())

    # 3. Per-Agency tweak checklist
    story.append(Paragraph("3. Per-Agency Tweak Checklist (~2-3 hours)", h2))
    story.append(Paragraph("Frontend", h3))
    story.append(ListFlowable([
        ListItem(Paragraph("<font face='Courier'>/app/frontend/src/theme.ts</font> \u2014 BRAND_NAME, color tokens", body)),
        ListItem(Paragraph("<font face='Courier'>/app/frontend/app.json</font> \u2014 name, slug, iOS bundle ID, Android package", body)),
        ListItem(Paragraph("<font face='Courier'>/app/frontend/assets/</font> \u2014 logo, app icon, splash screen", body)),
        ListItem(Paragraph("<font face='Courier'>/app/frontend/google-services.json</font> \u2014 new Firebase project file", body)),
        ListItem(Paragraph("Login screen demo creds line (remove or update)", body)),
    ], bulletType="bullet"))
    story.append(Paragraph("Backend", h3))
    story.append(ListFlowable([
        ListItem(Paragraph("<font face='Courier'>/app/backend/.env</font> \u2014 every MS_*, JWT secret, Mongo DB name, support email", body)),
        ListItem(Paragraph("<font face='Courier'>/app/backend/core/pdf_utils.py</font> \u2014 LOGO_URL + watermark text", body)),
        ListItem(Paragraph("<font face='Courier'>/app/backend/server.py</font> \u2014 seed-admin name/email", body)),
        ListItem(Paragraph("<font face='Courier'>/app/backend/models.py</font> \u2014 onboarding-doc templates if list differs", body)),
    ], bulletType="bullet"))
    story.append(Paragraph("Microsoft Azure", h3))
    story.append(ListFlowable([
        ListItem(Paragraph("New app registration per tenant", body)),
        ListItem(Paragraph("Redirect URI: <i>https://&lt;prod-domain&gt;/api/ms/callback</i>", body)),
        ListItem(Paragraph("Permissions: Files.ReadWrite, Mail.ReadWrite, Mail.Send, User.Read, offline_access \u2014 grant admin consent", body)),
    ], bulletType="bullet"))
    story.append(Paragraph("App Store / Play Store", h3))
    story.append(ListFlowable([
        ListItem(Paragraph("Icons 1024\u00b2 (iOS) and 512\u00b2 (Android adaptive)", body)),
        ListItem(Paragraph("5\u20138 screenshots per device class", body)),
        ListItem(Paragraph("Description, keywords, category = Medical or Business", body)),
        ListItem(Paragraph("Privacy policy URL (must mention SSNs, health data, caregiver credentials)", body)),
        ListItem(Paragraph("Content rating: Everyone / Medical", body)),
    ], bulletType="bullet"))

    # 4. Build steps narrative
    story.append(Paragraph("4. How This Build Was Assembled (in order)", h2))
    steps = [
        "Auth + base CRUD (users, clients, caregivers, MongoDB schema)",
        "Custom branding (theme, logo, audit-binder watermark)",
        "13 client + 14 caregiver onboarding PDFs seeded as templates",
        "PDF watermark + in-modal e-signature canvas",
        "Public packet share link (/packet/[token])",
        "One-tap 28 MB Audit Binder export",
        "In-app chat (admin \u2194 caregiver)",
        "Push notifications via Emergent Push",
        "Caregiver/client drill-down detail pages",
        "Mutual assignment UI (caregiver \u2194 client chips, idempotent)",
        "Document push to specific users",
        "Schedule tab (one-off + recurring, clock-in/out, edit/cancel notifications)",
        "Microsoft 365 monthly export with auto-fallback (OneDrive \u2192 Outlook attachment)",
        "Refactor: server.py split into core/ + models.py + routers/",
    ]
    story.append(ListFlowable(
        [ListItem(Paragraph(s, body)) for s in steps],
        bulletType="1", start="1",
    ))

    # 5. Pre-launch checklist
    story.append(PageBreak())
    story.append(Paragraph("5. Pre-Launch Checklist", h2))
    cl = [
        "Brand assets dropped into /app/frontend/assets/",
        "theme.ts updated (name + colors)",
        "app.json updated (name, slug, bundle IDs)",
        "google-services.json placed for new Firebase project",
        "Backend .env updated (Mongo DB name, MS_* values, JWT secret)",
        "Audit-binder watermark text updated in pdf_utils.py",
        "Seed-admin email/name updated in server.py",
        "Apple Developer account active, certs generated",
        "Google Play Console active, keystore generated/stored",
        "Azure app registered, consented, redirect URI added",
        "Privacy policy URL live and linked",
        "App icons + screenshots ready",
        "End-to-end test admin + caregiver flows",
        "Test audit binder export \u2192 OneDrive or Outlook",
        "Test push notification on a real device build",
        "Click Emergent \u201cPublish\u201d button",
        "Wait for review: 1\u20133 days iOS, 1\u20133 days Android",
        "Approved \u2192 live on stores \U0001F389",
    ]
    box = []
    for item in cl:
        box.append([Paragraph("\u2610", ParagraphStyle("cb", fontSize=14)),
                    Paragraph(item, body)])
    box_t = Table(box, colWidths=[0.3 * inch, 6.4 * inch])
    box_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(box_t)

    # 6. Cost summary
    story.append(Paragraph("6. Cost Per Agency (typical first year)", h2))
    cost_t = Table([
        ["Item", "One-time", "Recurring"],
        ["Apple Developer", "\u2014", "$99 / yr"],
        ["Google Play Console", "$25", "\u2014"],
        ["Domain (privacy policy)", "\u2014", "$12 / yr"],
        ["Microsoft 365 Business Basic (optional)", "\u2014", "$7.20 / user / mo"],
        ["Logo / icons (if outsourced)", "$50 \u2013 $500", "\u2014"],
        ["Emergent subscription", "\u2014", "as-is"],
        ["Total typical first year", "~$75", "~$110 / yr"],
    ], colWidths=[3.6 * inch, 1.6 * inch, 1.6 * inch])
    cost_t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, -1), (-1, -1), BRAND_TERTIARY),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCD7")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    story.append(cost_t)

    # 7. Known limitations
    story.append(Paragraph("7. Known Limitations / Mocked Pieces", h2))
    story.append(ListFlowable([
        ListItem(Paragraph(
            "<b>Push key</b> (<font face='Courier'>EMERGENT_PUSH_KEY</font>) "
            "is a placeholder until the Emergent Publish flow swaps in the real one.",
            body)),
        ListItem(Paragraph(
            "<b>EVV</b> is a manual clock-in/out stub \u2014 replace with a true "
            "EVV vendor (HHAeXchange, Sandata, etc.) when needed.", body)),
        ListItem(Paragraph(
            "<b>Files</b> stored as base64 in MongoDB \u2014 fine for an MVP, "
            "move to S3 / Azure Blob at scale.", body)),
        ListItem(Paragraph(
            "<b>server.py</b> still has inline routes; modular refactor "
            "partially done.", body)),
    ], bulletType="bullet"))

    doc.build(story)
    return buf.getvalue()


# =========================================================
# 2) Fillable Intake Form PDF (AcroForm)
# =========================================================
def build_intake_form_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle("Agency App Replication \u2014 Intake Form")
    c.setAuthor("Sister to Sister, PHCP")
    page_w, page_h = letter
    form = c.acroForm

    def header(title: str):
        c.setFillColor(BRAND_PRIMARY)
        c.rect(0, page_h - 60, page_w, 60, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(0.6 * inch, page_h - 30, title)
        c.setFont("Helvetica", 9)
        c.drawString(0.6 * inch, page_h - 46,
                     "Complete this form digitally and send back to begin app replication.")
        c.setFillColor(BRAND_PRIMARY)

    def footer(page_num: int):
        c.setFillColor(TEXT_DIM)
        c.setFont("Helvetica", 8)
        c.drawString(0.6 * inch, 0.4 * inch,
                     "Sister to Sister, PHCP \u2014 Replication Intake Form")
        c.drawRightString(page_w - 0.6 * inch, 0.4 * inch,
                          f"Page {page_num}")
        c.setFillColor(BRAND_PRIMARY)

    def section_header(text: str, y: float):
        c.setFillColor(BRAND_TERTIARY)
        c.rect(0.5 * inch, y - 2, page_w - 1.0 * inch, 22, stroke=0, fill=1)
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(0.65 * inch, y + 5, text)
        return y - 28

    def field_label(text: str, x: float, y: float, w: float = 1.6 * inch,
                    note: str = ""):
        c.setFillColor(BRAND_PRIMARY)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x, y + 22, text)
        if note:
            c.setFillColor(TEXT_DIM)
            c.setFont("Helvetica-Oblique", 7)
            c.drawString(x, y + 12, note)
        c.setFillColor(BRAND_PRIMARY)

    def text_field(name: str, x: float, y: float, w: float = 3.0 * inch,
                   h: float = 20, multiline: bool = False, value: str = ""):
        form.textfield(
            name=name, value=value,
            x=x, y=y, width=w, height=h,
            borderColor=colors.HexColor("#BCC2BD"),
            fillColor=colors.HexColor("#FBFCFB"),
            textColor=colors.HexColor("#1d2421"),
            forceBorder=True,
            borderWidth=0.6,
            fieldFlags="multiline" if multiline else "",
            fontSize=10, fontName="Helvetica",
        )

    # ============ PAGE 1 ============
    header("Agency App Replication \u2014 Intake Form")
    y = page_h - 90

    # AGENCY INFO
    y = section_header("Agency Information", y)
    field_label("Agency / business name", 0.6 * inch, y)
    text_field("agency_name", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Doing-business-as (if different)", 0.6 * inch, y)
    text_field("dba_name", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Headquarters address", 0.6 * inch, y)
    text_field("address", 0.6 * inch, y - 30, w=page_w - 1.2 * inch,
               h=50, multiline=True)
    y -= 90
    field_label("Primary contact name", 0.6 * inch, y)
    text_field("contact_name", 0.6 * inch, y, w=3.0 * inch)
    field_label("Role / title", 4.0 * inch, y)
    text_field("contact_role", 4.0 * inch, y, w=3.5 * inch)
    y -= 50
    field_label("Contact email", 0.6 * inch, y)
    text_field("contact_email", 0.6 * inch, y, w=3.0 * inch)
    field_label("Contact phone", 4.0 * inch, y)
    text_field("contact_phone", 4.0 * inch, y, w=3.5 * inch)
    y -= 50

    # BRANDING
    y = section_header("Branding & Assets", y)
    field_label("Brand colors (HEX)", 0.6 * inch, y,
                note="Primary / Secondary / Tertiary")
    text_field("brand_primary", 0.6 * inch, y, w=1.8 * inch,
               value="#")
    text_field("brand_secondary", 2.6 * inch, y, w=1.8 * inch,
               value="#")
    text_field("brand_tertiary", 4.6 * inch, y, w=1.8 * inch,
               value="#")
    y -= 50
    field_label("Logo URL (or attach separately)", 0.6 * inch, y,
                note="Vector PNG / SVG with transparent background")
    text_field("logo_url", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Watermark text inside PDFs", 0.6 * inch, y,
                note="e.g. \u201cACME HEALTH SERVICES\u201d (all caps)")
    text_field("watermark_text", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Privacy policy URL", 0.6 * inch, y,
                note="Required by both app stores; must mention SSN + health data")
    text_field("privacy_url", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Public support email", 0.6 * inch, y)
    text_field("support_email", 0.6 * inch, y, w=page_w - 1.2 * inch)

    footer(1)
    c.showPage()

    # ============ PAGE 2 ============
    header("Apple, Google & Firebase Credentials")
    y = page_h - 90

    y = section_header("Apple Developer (iOS)", y)
    field_label("Apple Developer account email", 0.6 * inch, y)
    text_field("apple_account", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Apple Team ID", 0.6 * inch, y,
                note="10-character ID from developer.apple.com \u2192 Membership")
    text_field("apple_team_id", 0.6 * inch, y, w=2.5 * inch)
    field_label("App bundle ID", 3.4 * inch, y,
                note="e.g. com.acmehealth.compliance")
    text_field("ios_bundle_id", 3.4 * inch, y, w=page_w - 4.0 * inch)
    y -= 50
    field_label("App-specific password", 0.6 * inch, y,
                note="Generate at appleid.apple.com \u2192 Security")
    text_field("apple_app_pwd", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50

    y = section_header("Google Play Console (Android)", y)
    field_label("Developer account email", 0.6 * inch, y)
    text_field("play_account", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Android package name", 0.6 * inch, y,
                note="e.g. com.acmehealth.compliance")
    text_field("android_package", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Keystore", 0.6 * inch, y,
                note="Attach keystore file separately + provide passwords below")
    text_field("keystore_passwords", 0.6 * inch, y - 30,
               w=page_w - 1.2 * inch, h=50, multiline=True)
    y -= 90

    y = section_header("Firebase (Push Notifications)", y)
    field_label("Firebase project ID", 0.6 * inch, y)
    text_field("firebase_project_id", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Attached files", 0.6 * inch, y,
                note="google-services.json, APNs .p8 key + Key ID")
    text_field("firebase_files_attached", 0.6 * inch, y, w=page_w - 1.2 * inch)

    footer(2)
    c.showPage()

    # ============ PAGE 3 ============
    header("Microsoft 365 & Hand-off")
    y = page_h - 90

    y = section_header("Microsoft 365 / Azure (Audit Binder Export)", y)
    field_label("Microsoft 365 admin email", 0.6 * inch, y,
                note="The account that will receive monthly audit binders")
    text_field("ms_admin_email", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Has Microsoft 365 Business plan w/ OneDrive?", 0.6 * inch, y,
                note="If No, fallback is Outlook email-attachment mode")
    text_field("ms_has_onedrive", 0.6 * inch, y, w=page_w - 1.2 * inch,
               value="Yes / No")
    y -= 50
    field_label("Azure Tenant ID", 0.6 * inch, y,
                note="Once Azure app is registered (entra.microsoft.com)")
    text_field("ms_tenant_id", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Azure Client ID (Application ID)", 0.6 * inch, y)
    text_field("ms_client_id", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Azure Client Secret VALUE", 0.6 * inch, y,
                note="The masked Value column \u2014 NOT the Secret ID column")
    text_field("ms_client_secret", 0.6 * inch, y, w=page_w - 1.2 * inch)
    y -= 50
    field_label("Monthly export email recipients", 0.6 * inch, y,
                note="Comma-separated list (e.g. owner@…, backup@…)")
    text_field("ms_recipients", 0.6 * inch, y - 30,
               w=page_w - 1.2 * inch, h=50, multiline=True)
    y -= 90

    y = section_header("Initial Admin Account", y)
    field_label("Admin login email", 0.6 * inch, y)
    text_field("admin_email", 0.6 * inch, y, w=3.5 * inch)
    field_label("Admin full name", 4.3 * inch, y)
    text_field("admin_name", 4.3 * inch, y, w=3.0 * inch)
    y -= 60

    y = section_header("Anything Else?", y)
    field_label(
        "Custom requests / agency-specific docs / known gotchas",
        0.6 * inch, y,
        note="e.g. additional onboarding PDFs, EVV vendor name, integrations",
    )
    text_field("extra_notes", 0.6 * inch, y - 70,
               w=page_w - 1.2 * inch, h=90, multiline=True)

    footer(3)
    c.save()
    return buf.getvalue()


def build_both() -> Tuple[bytes, bytes]:
    return build_playbook_pdf(), build_intake_form_pdf()
