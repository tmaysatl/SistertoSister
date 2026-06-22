from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, Query
from fastapi.responses import StreamingResponse, Response
from starlette.middleware.cors import CORSMiddleware
import os
import io
import base64
import logging
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
import uuid
from datetime import datetime, timedelta, timezone
from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# --- Modular core / shared infrastructure ---
from core.db import client, db
from core.security import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin, oauth2_scheme,
)
from core.push import send_push, _push_client
from core.settings import EMERGENT_LLM_KEY
from core.pdf_utils import _load_logo, _make_watermark, stamp_pdf
from models import (
    UserPublic, RegisterRequest, LoginRequest, TokenResponse,
    Client, ClientCreate, ClientTask,
    Document, DocumentCreate, DocCategory,
    Assignment, AssignmentCreate,
    TrainingItem, TrainingCreate, TrainingCompletion,
    OnboardingStep, OnboardingStepCreate,
    Shift, ShiftCreate, ShiftUpdate,
    ChatMessageReq, now_iso,
)
from routers import shifts as shifts_router
from routers import ms_graph as ms_graph_router


app = FastAPI()
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



# ---------- AUTH ROUTES ----------
@api.post("/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest):
    existing = await db.users.find_one({"email": req.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    uid = str(uuid.uuid4())
    doc = {
        "id": uid,
        "email": req.email,
        "name": req.name,
        "role": req.role,
        "hashed_password": hash_password(req.password),
        "created_at": now_iso(),
    }
    await db.users.insert_one(doc)
    user_public = UserPublic(id=uid, email=req.email, name=req.name,
                             role=req.role, created_at=doc["created_at"])
    token = create_access_token({"sub": uid, "role": req.role})
    return TokenResponse(access_token=token, user=user_public)


@api.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    doc = await db.users.find_one({"email": req.email})
    if not doc or not verify_password(req.password, doc["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    user_public = UserPublic(
        id=doc["id"], email=doc["email"], name=doc["name"],
        role=doc["role"], created_at=doc["created_at"]
    )
    token = create_access_token({"sub": doc["id"], "role": doc["role"]})
    return TokenResponse(access_token=token, user=user_public)


@api.get("/auth/me", response_model=UserPublic)
async def me(current: UserPublic = Depends(get_current_user)):
    return current


# ---------- USERS / CAREGIVERS ----------
@api.get("/caregivers", response_model=List[UserPublic])
async def list_caregivers(_: UserPublic = Depends(get_current_user)):
    docs = await db.users.find(
        {"role": "caregiver"}, {"_id": 0, "hashed_password": 0}
    ).to_list(500)
    return [UserPublic(**d) for d in docs]


# ---------- CLIENTS ----------
@api.post("/clients", response_model=Client)
async def create_client(req: ClientCreate, _: UserPublic = Depends(require_admin)):
    obj = Client(**req.dict())
    await db.clients.insert_one(obj.dict())
    return obj


@api.get("/clients", response_model=List[Client])
async def list_clients(_: UserPublic = Depends(get_current_user)):
    docs = await db.clients.find({}, {"_id": 0}).to_list(500)
    return [Client(**d) for d in docs]


@api.get("/clients/{client_id}", response_model=Client)
async def get_client(client_id: str, _: UserPublic = Depends(get_current_user)):
    d = await db.clients.find_one({"id": client_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Client not found")
    return Client(**d)


@api.delete("/clients/{client_id}")
async def delete_client(client_id: str, _: UserPublic = Depends(require_admin)):
    await db.clients.delete_one({"id": client_id})
    await db.assignments.delete_many({"client_id": client_id})
    await db.client_tasks.delete_many({"client_id": client_id})
    return {"ok": True}


# ---------- CLIENT TASKS (per-client onboarding checklist) ----------
class BulkAssignClientReq(BaseModel):
    client_id: str


@api.post("/clients/{client_id}/bulk-assign-onboarding")
async def bulk_assign_client(client_id: str,
                             _: UserPublic = Depends(require_admin)):
    cl = await db.clients.find_one({"id": client_id})
    if not cl:
        raise HTTPException(404, "Client not found")
    docs = await db.documents.find(
        {"category": "client_onboarding"}, {"_id": 0}
    ).sort("seq", 1).to_list(200)
    created = 0
    for d in docs:
        existing = await db.client_tasks.find_one({
            "client_id": client_id, "title": d["title"],
        })
        if existing:
            continue
        task = ClientTask(
            client_id=client_id,
            title=d["title"],
            description=f"Review and sign: {d['title']}",
            seq=d.get("seq"),
        )
        await db.client_tasks.insert_one(task.dict())
        created += 1
    return {"created": created, "total_tasks": len(docs)}


@api.get("/clients/{client_id}/tasks", response_model=List[ClientTask])
async def list_client_tasks(client_id: str,
                            _: UserPublic = Depends(get_current_user)):
    docs = await db.client_tasks.find(
        {"client_id": client_id}, {"_id": 0}
    ).sort("seq", 1).to_list(200)
    return [ClientTask(**d) for d in docs]


@api.post("/client-tasks/{task_id}/toggle", response_model=ClientTask)
async def toggle_client_task(task_id: str,
                             _: UserPublic = Depends(require_admin)):
    d = await db.client_tasks.find_one({"id": task_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Task not found")
    new_completed = not d.get("completed", False)
    update = {
        "completed": new_completed,
        "completed_at": now_iso() if new_completed else None,
    }
    await db.client_tasks.update_one({"id": task_id}, {"$set": update})
    d.update(update)
    return ClientTask(**d)


# ---------- PHOTO UPLOAD ----------
class PhotoPayload(BaseModel):
    photo_base64: str


@api.put("/users/{user_id}/photo")
async def set_user_photo(user_id: str, p: PhotoPayload,
                         current: UserPublic = Depends(get_current_user)):
    if current.role != "admin" and current.id != user_id:
        raise HTTPException(403, "Not allowed")
    b = p.photo_base64.split(",", 1)[-1]
    await db.users.update_one({"id": user_id}, {"$set": {"photo_base64": b}})
    return {"ok": True}


@api.put("/clients/{client_id}/photo")
async def set_client_photo(client_id: str, p: PhotoPayload,
                           _: UserPublic = Depends(require_admin)):
    b = p.photo_base64.split(",", 1)[-1]
    await db.clients.update_one({"id": client_id}, {"$set": {"photo_base64": b}})
    return {"ok": True}


# ---------- CLIENT DETAIL (drill-in) ----------
@api.get("/caregivers/{caregiver_id}/detail")
async def caregiver_detail(caregiver_id: str,
                           current: UserPublic = Depends(get_current_user)):
    if current.role == "caregiver" and current.id != caregiver_id:
        raise HTTPException(403, "Not allowed")
    cg = await db.users.find_one({"id": caregiver_id, "role": "caregiver"},
                                  {"_id": 0, "hashed_password": 0})
    if not cg:
        raise HTTPException(404, "Caregiver not found")
    assignments = await db.assignments.find(
        {"caregiver_id": caregiver_id}, {"_id": 0}
    ).to_list(100)
    client_ids = [a["client_id"] for a in assignments]
    clients = []
    if client_ids:
        async for c in db.clients.find({"id": {"$in": client_ids}}, {"_id": 0}):
            clients.append(c)
    shifts = await db.shifts.find(
        {"caregiver_id": caregiver_id}, {"_id": 0}
    ).sort("date", -1).to_list(200)
    credentials = await db.documents.find(
        {"category": "credential", "owner_id": caregiver_id},
        {"_id": 0, "file_base64": 0},
    ).sort("uploaded_at", -1).to_list(200)
    steps = await db.onboarding.find(
        {"caregiver_id": caregiver_id}, {"_id": 0}
    ).to_list(200)
    return {
        "caregiver": cg, "clients": clients, "assignments": assignments,
        "shifts": shifts, "credentials": credentials, "onboarding": steps,
    }



@api.get("/clients/{client_id}/detail")
async def client_detail(client_id: str,
                        _: UserPublic = Depends(get_current_user)):
    cl = await db.clients.find_one({"id": client_id}, {"_id": 0})
    if not cl:
        raise HTTPException(404, "Client not found")
    tasks = await db.client_tasks.find(
        {"client_id": client_id}, {"_id": 0}
    ).sort("seq", 1).to_list(200)
    assignments = await db.assignments.find(
        {"client_id": client_id}, {"_id": 0}
    ).to_list(50)
    cg_ids = [a["caregiver_id"] for a in assignments]
    caregivers = []
    if cg_ids:
        async for u in db.users.find(
            {"id": {"$in": cg_ids}}, {"_id": 0, "hashed_password": 0}
        ):
            caregivers.append(u)
    shifts = await db.shifts.find(
        {"client_id": client_id}, {"_id": 0}
    ).sort("date", -1).to_list(100)
    return {
        "client": cl,
        "tasks": tasks,
        "assignments": assignments,
        "caregivers": caregivers,
        "shifts": shifts,
    }


class RegisterPushBody(BaseModel):
    user_id: str
    platform: str
    device_token: str


@api.post("/register-push", status_code=201)
async def register_push(body: RegisterPushBody):
    try:
        resp = await _push_client.post(
            "/api/v1/push/users/register", json=body.model_dump()
        )
        if resp.status_code == 401:
            raise HTTPException(500, "EMERGENT_PUSH_KEY missing or invalid")
        if resp.status_code >= 500:
            raise HTTPException(502, "Push provider unavailable")
        resp.raise_for_status()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"register-push relay failed: {e}")
    return {"status": "registered"}



# ---------- IN-APP CHAT (1:1 direct messages) ----------
class ChatSendReq(BaseModel):
    to_user_id: str
    text: str


@api.post("/chat/messages")
async def send_message(req: ChatSendReq,
                       current: UserPublic = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(400, "Empty message")
    recipient = await db.users.find_one({"id": req.to_user_id}, {"_id": 0})
    if not recipient:
        raise HTTPException(404, "Recipient not found")
    msg = {
        "id": str(uuid.uuid4()),
        "from_id": current.id,
        "from_name": current.name,
        "to_id": req.to_user_id,
        "to_name": recipient["name"],
        "text": req.text.strip(),
        "created_at": now_iso(),
        "read": False,
    }
    await db.chat_dms.insert_one(msg)
    # Fire push (non-blocking)
    try:
        await send_push(
            recipients=[req.to_user_id],
            data={
                "title": f"Message from {current.name}",
                "message": req.text.strip()[:160],
                "action_url": f"/chat/{current.id}",
            },
        )
    except Exception as e:
        logger.warning(f"chat push failed: {e}")
    return {k: v for k, v in msg.items() if k != "_id"}


@api.get("/chat/threads")
async def list_threads(current: UserPublic = Depends(get_current_user)):
    """Return list of conversations with most recent message + unread count."""
    pipeline = [
        {"$match": {"$or": [{"from_id": current.id}, {"to_id": current.id}]}},
        {"$sort": {"created_at": -1}},
    ]
    threads: dict = {}
    async for m in db.chat_dms.aggregate(pipeline):
        other = m["to_id"] if m["from_id"] == current.id else m["from_id"]
        other_name = m["to_name"] if m["from_id"] == current.id else m["from_name"]
        if other not in threads:
            threads[other] = {
                "other_id": other, "other_name": other_name,
                "last_message": m["text"], "last_at": m["created_at"],
                "unread": 0,
            }
        if m["to_id"] == current.id and not m.get("read"):
            threads[other]["unread"] += 1
    # Look up other_user photo for nicer UI
    ids = list(threads.keys())
    if ids:
        async for u in db.users.find(
            {"id": {"$in": ids}}, {"_id": 0, "id": 1, "photo_base64": 1, "role": 1}
        ):
            if u["id"] in threads:
                threads[u["id"]]["photo_base64"] = u.get("photo_base64")
                threads[u["id"]]["role"] = u.get("role")
    return list(threads.values())


@api.get("/chat/messages")
async def get_messages(with_user: str = Query(..., alias="with"),
                       current: UserPublic = Depends(get_current_user)):
    q = {"$or": [
        {"from_id": current.id, "to_id": with_user},
        {"from_id": with_user, "to_id": current.id},
    ]}
    msgs = await db.chat_dms.find(q, {"_id": 0}).sort("created_at", 1).to_list(500)
    # Mark inbound as read
    await db.chat_dms.update_many(
        {"to_id": current.id, "from_id": with_user, "read": False},
        {"$set": {"read": True}},
    )
    return msgs


@api.get("/chat/contacts")
async def list_contacts(current: UserPublic = Depends(get_current_user)):
    """For admin: list all caregivers. For caregiver: list all admins."""
    target_role = "caregiver" if current.role == "admin" else "admin"
    docs = await db.users.find(
        {"role": target_role}, {"_id": 0, "hashed_password": 0}
    ).to_list(500)
    return docs


# ---------- AUDIT BINDER PDF ----------
from playbook_pdf import build_playbook_pdf, build_intake_form_pdf


@api.get("/reports/replication-playbook.pdf")
async def replication_playbook_pdf():
    """Public download — generic replication playbook (no agency data)."""
    pdf = build_playbook_pdf()
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 'inline; filename="Agency_Replication_Playbook.pdf"'},
    )


@api.get("/reports/replication-intake-form.pdf")
async def replication_intake_form_pdf():
    """Public download — blank fillable intake form (no agency data)."""
    pdf = build_intake_form_pdf()
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 'inline; filename="Agency_Replication_IntakeForm.pdf"'},
    )


@api.get("/reports/audit-binder")
async def audit_binder(
    client_id: Optional[str] = None,
    caregiver_id: Optional[str] = None,
    _: UserPublic = Depends(require_admin),
):
    """Generate single PDF binder of all docs + audit trail (surveyor-ready).

    Optional filters:
    - client_id: include only client_onboarding + that client's signed copies
    - caregiver_id: include only caregiver_onboarding + that caregiver's credentials
    """
    pdf_bytes = await _build_audit_binder_bytes(client_id, caregiver_id)
    fname = f"SisterToSister_AuditBinder_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


async def _build_audit_binder_bytes(client_id: Optional[str] = None,
                                    caregiver_id: Optional[str] = None) -> bytes:
    """Build the Audit Binder PDF and return raw bytes. Used by both the
    /reports/audit-binder endpoint and the scheduled Microsoft Graph export."""
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    )
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet

    # === build cover + index + audit log ===
    front_buf = io.BytesIO()
    doc = SimpleDocTemplate(front_buf, pagesize=letter,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    story = []

    # Cover
    story.append(Spacer(1, 1.2 * inch))
    logo = _load_logo()
    if logo:
        try:
            img = ImageReader(io.BytesIO(logo))
            from reportlab.platypus import Image as RLImage
            tmp = io.BytesIO(logo)
            story.append(RLImage(tmp, width=2.4 * inch, height=1.6 * inch, kind='proportional'))
        except Exception as e:
            logger.warning(f"binder logo: {e}")
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("<font size=24><b>Sister to Sister, PHCP</b></font>", styles["Title"]))
    story.append(Paragraph("<font size=14>Compliance Audit Binder</font>", styles["Title"]))
    story.append(Spacer(1, 0.4 * inch))
    ts = datetime.now(timezone.utc).strftime("%B %d, %Y · %H:%M UTC")
    story.append(Paragraph(f"<font size=11>Generated: {ts}</font>", styles["Normal"]))

    # Stats
    n_clients = await db.clients.count_documents({})
    n_caregivers = await db.users.count_documents({"role": "caregiver"})
    n_docs_total = await db.documents.count_documents({})
    n_signed = await db.documents.count_documents({"notes": {"$regex": "Signed"}})
    n_views = await db.document_views.count_documents({})
    story.append(Spacer(1, 0.3 * inch))
    stats_data = [
        ["Clients", str(n_clients)],
        ["Caregivers", str(n_caregivers)],
        ["Documents on file", str(n_docs_total)],
        ["Signed documents", str(n_signed)],
        ["Document views logged", str(n_views)],
    ]
    t = Table(stats_data, colWidths=[3 * inch, 1.5 * inch])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#204231")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BCC2BD")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E3EBE6")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(PageBreak())

    # Index
    story.append(Paragraph("<b>Document Index</b>", styles["Title"]))
    story.append(Spacer(1, 0.2 * inch))
    sections = [
        ("Client Onboarding Packet", "client_onboarding"),
        ("Caregiver Onboarding Packet", "caregiver_onboarding"),
        ("Policies & Procedures", "policy"),
        ("Caregiver Credentials", "credential"),
        ("Training Materials", "training"),
    ]
    if client_id:
        sections = [s for s in sections if s[1] in ("client_onboarding", "policy")]
    elif caregiver_id:
        sections = [s for s in sections if s[1] in ("caregiver_onboarding", "credential", "policy", "training")]
    for label, cat in sections:
        docs = await db.documents.find(
            {"category": cat}, {"_id": 0, "file_base64": 0}
        ).sort("seq", 1).to_list(500)
        if not docs:
            continue
        story.append(Paragraph(f"<font size=12><b>{label}</b> ({len(docs)})</font>", styles["Heading2"]))
        for d in docs:
            attached = "Attached" if True else "—"  # placeholder
            story.append(Paragraph(f"&nbsp;&nbsp;• {d['title']}", styles["Normal"]))
        story.append(Spacer(1, 0.15 * inch))
    story.append(PageBreak())

    # Audit Trail (last 100 views)
    story.append(Paragraph("<b>Audit Trail</b>", styles["Title"]))
    story.append(Paragraph("<font size=9>Last 100 document interactions</font>", styles["Normal"]))
    story.append(Spacer(1, 0.15 * inch))
    views = await db.document_views.find({}, {"_id": 0}).sort("viewed_at", -1).to_list(100)
    if views:
        # build doc id -> title map
        ids = list({v.get("document_id") for v in views if v.get("document_id")})
        title_map = {}
        if ids:
            async for d in db.documents.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "title": 1}):
                title_map[d["id"]] = d["title"]
        rows = [["When (UTC)", "Action", "By", "Document"]]
        for v in views:
            action = v.get("action") or "viewed"
            rows.append([
                (v.get("viewed_at") or "")[:19].replace("T", " "),
                action.upper(),
                (v.get("viewer_name") or "—")[:24],
                (title_map.get(v.get("document_id"), "—"))[:32],
            ])
        tbl = Table(rows, colWidths=[1.4 * inch, 0.8 * inch, 1.6 * inch, 2.6 * inch])
        tbl.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#BCC2BD")),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#E3E5E3")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#204231")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("PADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph("<font color='#888888'>No views logged yet.</font>", styles["Normal"]))

    doc.build(story)
    front_buf.seek(0)

    # === merge all attached PDFs (each watermarked) ===
    writer = PdfWriter()
    front_reader = PdfReader(front_buf)
    for p in front_reader.pages:
        writer.add_page(p)

    ts_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for _label, cat in sections:
        cdocs = await db.documents.find(
            {"category": cat, "file_base64": {"$ne": None}}, {"_id": 0}
        ).sort("seq", 1).to_list(500)
        for d in cdocs:
            try:
                raw = base64.b64decode(d["file_base64"])
                if (d.get("mime_type") or "").lower() != "application/pdf":
                    continue
                stamped = stamp_pdf(raw, "Audit Binder", ts_stamp)
                r = PdfReader(io.BytesIO(stamped))
                for p in r.pages:
                    writer.add_page(p)
            except Exception as e:
                logger.warning(f"binder skip {d.get('title')}: {e}")

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()


# ---------- DOCUMENTS ----------
@api.post("/documents", response_model=Document)
async def create_document(req: DocumentCreate,
                          current: UserPublic = Depends(get_current_user)):
    obj = Document(**req.dict(), uploaded_by=current.id)
    await db.documents.insert_one(obj.dict())
    return obj


@api.get("/documents", response_model=List[Document])
async def list_documents(
    category: Optional[str] = None,
    owner_id: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    q = {}
    if category:
        q["category"] = category
    if owner_id:
        q["owner_id"] = owner_id
    # Caregivers only see their own + agency-wide
    if current.role == "caregiver":
        q = {"$and": [q, {"$or": [
            {"owner_id": current.id},
            {"owner_type": "agency"},
            {"category": "training"},
            {"category": "policy"},
            {"category": "client_onboarding"},
            {"category": "caregiver_onboarding"},
        ]}]}
    docs = await db.documents.find(q, {"_id": 0}).sort([("seq", 1), ("uploaded_at", -1)]).to_list(500)
    return [Document(**d) for d in docs]


@api.get("/documents/{doc_id}", response_model=Document)
async def get_document(doc_id: str, _: UserPublic = Depends(get_current_user)):
    d = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Document not found")
    return Document(**d)


@api.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, _: UserPublic = Depends(require_admin)):
    await db.documents.delete_one({"id": doc_id})
    return {"ok": True}


class DocumentPushReq(BaseModel):
    targets: List[dict]  # [{owner_id, owner_type}]


@api.post("/documents/{doc_id}/push")
async def push_document(doc_id: str, req: DocumentPushReq,
                        current: UserPublic = Depends(require_admin)):
    """Clone an existing document and assign each clone to a specific
    caregiver or client. Pushed copies appear in the recipient's profile
    documents list (owner_id + owner_type). Keeps signature flow intact."""
    src = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not src:
        raise HTTPException(404, "Source document not found")
    created_ids: list[str] = []
    for t in req.targets:
        oid = t.get("owner_id")
        otype = t.get("owner_type")
        if not oid or otype not in ("caregiver", "client"):
            continue
        clone = {**src}
        clone.pop("_id", None)
        clone["id"] = str(uuid.uuid4())
        clone["owner_id"] = oid
        clone["owner_type"] = otype
        clone["uploaded_by"] = current.id
        clone["uploaded_at"] = now_iso()
        clone["is_template"] = False
        clone["seq"] = None
        await db.documents.insert_one(clone)
        created_ids.append(clone["id"])
    return {"created": len(created_ids), "ids": created_ids}


@api.get("/documents/{doc_id}/stamped")
async def get_stamped_document(
    doc_id: str,
    token: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    """Return the document with Sister to Sister watermark + audit trail footer."""
    d = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Document not found")
    if not d.get("file_base64"):
        raise HTTPException(404, "No file attached")
    raw = base64.b64decode(d["file_base64"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if (d.get("mime_type") or "").lower() == "application/pdf":
        try:
            stamped = stamp_pdf(raw, current.name, ts)
        except Exception as e:
            logger.error(f"stamp error: {e}")
            stamped = raw
    else:
        stamped = raw
    # log view for audit
    await db.document_views.insert_one({
        "id": str(uuid.uuid4()),
        "document_id": doc_id,
        "viewer_id": current.id,
        "viewer_name": current.name,
        "viewed_at": now_iso(),
    })
    return Response(
        content=stamped,
        media_type=d.get("mime_type") or "application/pdf",
        headers={"Content-Disposition": f'inline; filename="{d["title"]}.pdf"'},
    )


@api.post("/documents/{doc_id}/sign")
async def sign_document(
    doc_id: str,
    payload: dict,
    current: UserPublic = Depends(get_current_user),
):
    """Apply a drawn signature image (base64 PNG) onto the LAST page of the PDF
    in the bottom-right and persist as a new document under the signer."""
    d = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not d or not d.get("file_base64"):
        raise HTTPException(404, "Document not found")
    sig_b64 = (payload.get("signature_base64") or "").strip()
    if not sig_b64:
        raise HTTPException(400, "signature_base64 required")
    if sig_b64.startswith("data:"):
        sig_b64 = sig_b64.split(",", 1)[1]
    try:
        sig_bytes = base64.b64decode(sig_b64)
    except Exception:
        raise HTTPException(400, "invalid signature_base64")

    raw = base64.b64decode(d["file_base64"])
    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for i, page in enumerate(reader.pages):
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if i == len(reader.pages) - 1:
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            try:
                img = ImageReader(io.BytesIO(sig_bytes))
                c.drawImage(img, w - 230, 60, width=200, height=70,
                            preserveAspectRatio=True, mask='auto')
            except Exception as e:
                logger.error(f"signature draw error: {e}")
            c.setFillColorRGB(0.2, 0.2, 0.2)
            c.setFont("Helvetica", 8)
            c.drawString(w - 230, 50, f"Signed: {current.name}")
            c.drawString(w - 230, 38, f"{ts}")
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            page.merge_page(overlay)
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    new_b64 = base64.b64encode(out.getvalue()).decode()

    signed = Document(
        title=f"{d['title']} (signed by {current.name})",
        category=d["category"],
        owner_id=current.id,
        owner_type="caregiver" if current.role == "caregiver" else "agency",
        file_base64=new_b64,
        mime_type="application/pdf",
        notes=f"Signed by {current.name} on {ts}",
        uploaded_by=current.id,
        seq=d.get("seq"),
        is_template=False,
    )
    await db.documents.insert_one(signed.dict())
    await db.document_views.insert_one({
        "id": str(uuid.uuid4()),
        "document_id": doc_id,
        "viewer_id": current.id,
        "viewer_name": current.name,
        "action": "signed",
        "viewed_at": now_iso(),
    })
    return signed


# ---------- STANDARD ONBOARDING TEMPLATES (admin) ----------
CLIENT_ONBOARDING_TEMPLATES = [
    "Welcome Letter",
    "Advanced Directives",
    "Abuse, Licensing & State Hotline Phones",
    "HIPAA / Notice of Privacy Rights",
    "Client's Authorization Form",
    "Provider Complaint",
    "Home Safe Guidelines",
    "Disaster Planning / Emergency Plan",
    "Auto Release",
    "Third Party Payer Information",
    "Client's Rights & Responsibilities",
    "Authorization of Use of Personal Funds",
    "Client-Specific Medication & Dietary",
]

CAREGIVER_ONBOARDING_TEMPLATES = [
    "Employment Application",
    "Form I-9 Employment Eligibility",
    "Form W-4 Tax Withholding",
    "Direct Deposit Authorization",
    "OIG / Background Check Authorization",
    "Caregiver Competency Checklist",
    "HIPAA Confidentiality Agreement",
    "Code of Conduct Acknowledgment",
    "Job Description Acknowledgment",
    "Emergency Contact Form",
    "Drug Screening Consent",
    "Vehicle Driver Authorization",
    "Policy Handbook Acknowledgment",
    "Emergency Contact & Availability",
]

CAREGIVER_CREDENTIAL_TEMPLATES = [
    "State License / Certification",
    "OIG Background Check",
    "Training Certificate",
    "CPR / First Aid Certification",
    "TB Test Result",
    "Driver's License",
    "Auto Insurance",
]

POLICY_TEMPLATES = [
    "Code of Conduct",
    "HIPAA Privacy Policy",
    "Bloodborne Pathogens & Infection Control",
    "Emergency Preparedness Plan",
    "Medication Management Policy",
    "Incident & Accident Reporting",
    "Anti-Discrimination & Harassment Policy",
    "Grievance Procedure",
    "Documentation Standards",
    "Caregiver Code of Ethics",
]


from forms import all_fillable_pdfs, CLIENT_BUILDERS, CAREGIVER_BUILDERS


@api.post("/documents/rebuild-fillable")
async def rebuild_fillable(current: UserPublic = Depends(require_admin)):
    """Regenerate the canonical fillable AcroForm PDFs for the 5 client +
    14 caregiver onboarding forms and replace the file_base64 in each
    matching template document. Also dedupes any extra/duplicate template
    rows so the lists are exactly 13 client + 14 caregiver."""
    import base64 as _b64
    updated = 0
    deleted = 0

    # 1) For each generated PDF: find template by category+title, set file_base64
    for category, title, pdf_bytes, seq in all_fillable_pdfs():
        b64 = _b64.b64encode(pdf_bytes).decode("ascii")
        existing = await db.documents.find_one(
            {"category": category, "title": title, "is_template": True}
        )
        if existing:
            await db.documents.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "file_base64": b64,
                    "mime_type": "application/pdf",
                    "uploaded_at": now_iso(),
                    "seq": seq,
                }},
            )
        else:
            doc = Document(
                title=title, category=category,  # type: ignore
                owner_type="agency", uploaded_by=current.id,
                file_base64=b64, mime_type="application/pdf",
                seq=seq, is_template=True,
                notes="Fillable form \u2014 open and complete in any PDF viewer.",
            )
            await db.documents.insert_one(doc.dict())
        updated += 1

    # 2) Dedupe: keep only template rows whose titles match the canonical lists
    canonical_client = set(CLIENT_ONBOARDING_TEMPLATES)
    canonical_caregiver = set(CAREGIVER_ONBOARDING_TEMPLATES)
    cur = db.documents.find(
        {"category": {"$in": ["client_onboarding", "caregiver_onboarding"]},
         "is_template": True}
    )
    seen: set = set()
    async for d in cur:
        # extract title stripped of seq prefix (e.g. "05 - Foo" -> "Foo")
        raw = d.get("title", "")
        stripped = raw.split(" - ", 1)[-1] if " - " in raw else raw
        cat = d["category"]
        canon = canonical_client if cat == "client_onboarding" else canonical_caregiver
        key = (cat, stripped)
        if stripped not in canon or key in seen:
            await db.documents.delete_one({"id": d["id"]})
            deleted += 1
        else:
            seen.add(key)

    return {"updated": updated, "deleted_duplicates": deleted}


# ---------- POLICY ACKNOWLEDGMENT ----------
class PolicyAckReq(BaseModel):
    policy_id: str


@api.get("/policies/acknowledgments")
async def list_acknowledgments(
    user_id: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    """List policy acknowledgments. Caregivers see only their own.
    Admins can pass `user_id` to view a specific caregiver's acks."""
    q: dict = {}
    if current.role == "caregiver":
        q["user_id"] = current.id
    elif user_id:
        q["user_id"] = user_id
    docs = await db.policy_acks.find(q, {"_id": 0}).to_list(500)
    return docs


@api.post("/policies/acknowledge")
async def acknowledge_policy(
    req: PolicyAckReq, current: UserPublic = Depends(get_current_user),
):
    """Caregiver (or admin self-acknowledge) confirms they've read a policy."""
    # Verify the policy exists
    pol = await db.documents.find_one(
        {"id": req.policy_id, "category": "policy"}, {"_id": 0}
    )
    if not pol:
        raise HTTPException(404, "Policy not found")
    ack = {
        "id": str(uuid.uuid4()),
        "policy_id": req.policy_id,
        "policy_title": pol.get("title"),
        "user_id": current.id,
        "user_name": current.name,
        "acknowledged_at": now_iso(),
    }
    # Upsert: one ack per (policy_id, user_id)
    await db.policy_acks.update_one(
        {"policy_id": req.policy_id, "user_id": current.id},
        {"$set": ack}, upsert=True,
    )
    return ack


@api.delete("/policies/acknowledge/{policy_id}")
async def un_acknowledge_policy(
    policy_id: str, current: UserPublic = Depends(get_current_user),
):
    await db.policy_acks.delete_one(
        {"policy_id": policy_id, "user_id": current.id}
    )
    return {"ok": True}


@api.post("/documents/seed-templates")
async def seed_templates(current: UserPublic = Depends(require_admin)):
    """Seed standard numbered onboarding templates + blank policy stubs.

    For onboarding templates: any existing *empty* template stub
    (is_template=True, no file attached) is removed first, so that the live
    template list always reflects the latest version. Any stub the admin has
    already attached a file to is preserved.
    """
    created = 0

    async def seed(category: str, titles: list[str], is_template: bool = True):
        nonlocal created
        if is_template:
            await db.documents.delete_many({
                "category": category,
                "is_template": True,
                "$or": [{"file_base64": None}, {"file_base64": ""}],
            })
        for i, t in enumerate(titles, start=1):
            title = f"{i:02d} - {t}"
            exists = await db.documents.find_one({"category": category, "title": title})
            if exists:
                continue
            doc = Document(
                title=title,
                category=category,  # type: ignore
                owner_type="agency",
                notes="Standard template — upload completed file or attach blank PDF",
                uploaded_by=current.id,
                seq=i,
                is_template=is_template,
            )
            await db.documents.insert_one(doc.dict())
            created += 1

    await seed("client_onboarding", CLIENT_ONBOARDING_TEMPLATES)
    await seed("caregiver_onboarding", CAREGIVER_ONBOARDING_TEMPLATES)
    await seed("policy", POLICY_TEMPLATES, is_template=False)
    return {"created": created}


@api.get("/credentials/templates")
async def credential_templates(_: UserPublic = Depends(get_current_user)):
    """Return suggested credential titles for caregivers to upload."""
    return {"titles": CAREGIVER_CREDENTIAL_TEMPLATES}


@api.get("/credentials/expiring")
async def expiring_credentials(
    days: int = 60,
    current: UserPublic = Depends(get_current_user),
):
    """List credentials expiring within `days` days (or already expired)."""
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    q = {"category": "credential", "expires_at": {"$ne": None, "$lte": cutoff}}
    if current.role == "caregiver":
        q["owner_id"] = current.id
    docs = await db.documents.find(q, {"_id": 0}).sort("expires_at", 1).to_list(200)
    return [Document(**d) for d in docs]


# ---------- ASSIGNMENTS ----------
@api.post("/assignments", response_model=Assignment)
async def create_assignment(req: AssignmentCreate,
                            _: UserPublic = Depends(require_admin)):
    # Prevent duplicate caregiver↔client links (idempotent assignment)
    existing = await db.assignments.find_one(
        {"caregiver_id": req.caregiver_id, "client_id": req.client_id}, {"_id": 0}
    )
    if existing:
        return Assignment(**existing)
    obj = Assignment(**req.dict())
    await db.assignments.insert_one(obj.dict())
    return obj


@api.get("/assignments", response_model=List[Assignment])
async def list_assignments(current: UserPublic = Depends(get_current_user)):
    q = {}
    if current.role == "caregiver":
        q["caregiver_id"] = current.id
    docs = await db.assignments.find(q, {"_id": 0}).to_list(500)
    return [Assignment(**d) for d in docs]


@api.delete("/assignments/{aid}")
async def delete_assignment(aid: str, _: UserPublic = Depends(require_admin)):
    await db.assignments.delete_one({"id": aid})
    return {"ok": True}


# ---------- TRAINING ----------
@api.post("/training", response_model=TrainingItem)
async def create_training(req: TrainingCreate, _: UserPublic = Depends(require_admin)):
    obj = TrainingItem(**req.dict())
    await db.training.insert_one(obj.dict())
    return obj


@api.get("/training", response_model=List[TrainingItem])
async def list_training(_: UserPublic = Depends(get_current_user)):
    docs = await db.training.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [TrainingItem(**d) for d in docs]


@api.delete("/training/{tid}")
async def delete_training(tid: str, _: UserPublic = Depends(require_admin)):
    await db.training.delete_one({"id": tid})
    await db.training_completions.delete_many({"training_id": tid})
    return {"ok": True}


@api.post("/training/{tid}/complete", response_model=TrainingCompletion)
async def complete_training(tid: str, current: UserPublic = Depends(get_current_user)):
    existing = await db.training_completions.find_one(
        {"training_id": tid, "caregiver_id": current.id}
    )
    if existing:
        existing.pop("_id", None)
        return TrainingCompletion(**existing)
    obj = TrainingCompletion(training_id=tid, caregiver_id=current.id)
    await db.training_completions.insert_one(obj.dict())
    return obj


@api.get("/training/completions", response_model=List[TrainingCompletion])
async def list_completions(
    caregiver_id: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    q = {}
    if current.role == "caregiver":
        q["caregiver_id"] = current.id
    elif caregiver_id:
        q["caregiver_id"] = caregiver_id
    docs = await db.training_completions.find(q, {"_id": 0}).to_list(500)
    return [TrainingCompletion(**d) for d in docs]


# ---------- ONBOARDING ----------
@api.post("/onboarding", response_model=OnboardingStep)
async def create_step(req: OnboardingStepCreate,
                      _: UserPublic = Depends(require_admin)):
    obj = OnboardingStep(**req.dict())
    await db.onboarding.insert_one(obj.dict())
    return obj


@api.get("/onboarding", response_model=List[OnboardingStep])
async def list_steps(
    caregiver_id: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    q = {}
    if current.role == "caregiver":
        q["caregiver_id"] = current.id
    elif caregiver_id:
        q["caregiver_id"] = caregiver_id
    docs = await db.onboarding.find(q, {"_id": 0}).to_list(500)
    return [OnboardingStep(**d) for d in docs]


@api.post("/onboarding/{step_id}/toggle", response_model=OnboardingStep)
async def toggle_step(step_id: str, current: UserPublic = Depends(get_current_user)):
    d = await db.onboarding.find_one({"id": step_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Step not found")
    if current.role == "caregiver" and d["caregiver_id"] != current.id:
        raise HTTPException(403, "Not your step")
    new_completed = not d.get("completed", False)
    update = {
        "completed": new_completed,
        "completed_at": now_iso() if new_completed else None,
    }
    await db.onboarding.update_one({"id": step_id}, {"$set": update})
    d.update(update)
    return OnboardingStep(**d)


@api.delete("/onboarding/{step_id}")
async def delete_step(step_id: str, _: UserPublic = Depends(require_admin)):
    await db.onboarding.delete_one({"id": step_id})
    return {"ok": True}


class BulkAssignReq(BaseModel):
    caregiver_id: str


@api.post("/onboarding/bulk-assign")
async def bulk_assign_onboarding(req: BulkAssignReq,
                                 _: UserPublic = Depends(require_admin)):
    """Auto-create an onboarding step per caregiver_onboarding document.

    Skips steps already created for that caregiver (idempotent).
    """
    cg = await db.users.find_one({"id": req.caregiver_id, "role": "caregiver"})
    if not cg:
        raise HTTPException(404, "Caregiver not found")

    docs = await db.documents.find(
        {"category": "caregiver_onboarding"}, {"_id": 0}
    ).sort("seq", 1).to_list(200)

    created = 0
    for d in docs:
        existing = await db.onboarding.find_one({
            "caregiver_id": req.caregiver_id,
            "title": d["title"],
        })
        if existing:
            continue
        step = OnboardingStep(
            caregiver_id=req.caregiver_id,
            title=d["title"],
            description=f"Review and sign: {d['title']}",
        )
        await db.onboarding.insert_one(step.dict())
        created += 1
    return {"created": created, "total_steps": len(docs)}


# ---------- PACKET SHARE LINK ----------
class PacketLinkReq(BaseModel):
    recipient_name: str
    recipient_role: Literal["client", "caregiver"]
    category: Literal["client_onboarding", "caregiver_onboarding"]
    delivery: Optional[Literal["email", "sms", "link"]] = "link"
    recipient_email: Optional[EmailStr] = None
    recipient_phone: Optional[str] = None


@api.post("/packets/share")
async def share_packet(req: PacketLinkReq,
                       current: UserPublic = Depends(require_admin)):
    """Create a tokenized share link for the entire numbered packet.

    Returns the link; delivery via email/SMS is added in a follow-up.
    """
    token_str = uuid.uuid4().hex
    pkt = {
        "id": str(uuid.uuid4()),
        "token": token_str,
        "recipient_name": req.recipient_name,
        "recipient_role": req.recipient_role,
        "category": req.category,
        "recipient_email": req.recipient_email,
        "recipient_phone": req.recipient_phone,
        "created_by": current.id,
        "created_at": now_iso(),
        "viewed_at": None,
        "completed_at": None,
        "signed_ids": [],
    }
    await db.packet_shares.insert_one(pkt)

    # Public link (frontend will route /packet/<token>)
    frontend_origin = os.environ.get("PUBLIC_APP_ORIGIN", "")
    link = f"{frontend_origin}/packet/{token_str}" if frontend_origin else f"/packet/{token_str}"

    return {
        "token": token_str,
        "link": link,
        "delivery": req.delivery or "link",
        "delivered": False,
        "note": "Email / SMS delivery pending integration choice.",
    }


@api.get("/packets/{token_str}")
async def get_packet(token_str: str):
    """Public endpoint: fetch packet metadata + numbered docs (no PDFs)."""
    pkt = await db.packet_shares.find_one({"token": token_str}, {"_id": 0})
    if not pkt:
        raise HTTPException(404, "Packet not found")
    if not pkt.get("viewed_at"):
        await db.packet_shares.update_one(
            {"token": token_str}, {"$set": {"viewed_at": now_iso()}}
        )
    docs = await db.documents.find(
        {"category": pkt["category"]}, {"_id": 0, "file_base64": 0}
    ).sort("seq", 1).to_list(200)
    return {"packet": pkt, "documents": docs}


@api.get("/packets/{token_str}/document/{doc_id}")
async def packet_doc_stamped(token_str: str, doc_id: str):
    """Public — return the stamped PDF for a packet recipient."""
    pkt = await db.packet_shares.find_one({"token": token_str})
    if not pkt:
        raise HTTPException(404, "Packet not found")
    d = await db.documents.find_one(
        {"id": doc_id, "category": pkt["category"]}, {"_id": 0}
    )
    if not d or not d.get("file_base64"):
        raise HTTPException(404, "Document not found")
    raw = base64.b64decode(d["file_base64"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        stamped = stamp_pdf(raw, pkt["recipient_name"], ts)
    except Exception:
        stamped = raw
    return Response(
        content=stamped,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{d["title"]}.pdf"'},
    )


@api.post("/packets/{token_str}/sign/{doc_id}")
async def packet_sign(token_str: str, doc_id: str, payload: dict):
    """Public — submit a drawn signature for a packet document."""
    pkt = await db.packet_shares.find_one({"token": token_str}, {"_id": 0})
    if not pkt:
        raise HTTPException(404, "Packet not found")
    sig = (payload.get("signature_base64") or "").strip()
    if sig.startswith("data:"):
        sig = sig.split(",", 1)[1]
    if not sig:
        raise HTTPException(400, "signature_base64 required")
    try:
        sig_bytes = base64.b64decode(sig)
    except Exception:
        raise HTTPException(400, "invalid signature")

    d = await db.documents.find_one(
        {"id": doc_id, "category": pkt["category"]}, {"_id": 0}
    )
    if not d or not d.get("file_base64"):
        raise HTTPException(404, "Document not found")

    raw = base64.b64decode(d["file_base64"])
    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for i, page in enumerate(reader.pages):
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if i == len(reader.pages) - 1:
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            try:
                img = ImageReader(io.BytesIO(sig_bytes))
                c.drawImage(img, w - 230, 60, width=200, height=70,
                            preserveAspectRatio=True, mask='auto')
            except Exception as e:
                logger.error(f"sig draw: {e}")
            c.setFillColorRGB(0.2, 0.2, 0.2)
            c.setFont("Helvetica", 8)
            c.drawString(w - 230, 50, f"Signed: {pkt['recipient_name']}")
            c.drawString(w - 230, 38, f"{ts}")
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            page.merge_page(overlay)
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    new_b64 = base64.b64encode(out.getvalue()).decode()

    signed_doc = Document(
        title=f"{d['title']} (signed by {pkt['recipient_name']})",
        category=d["category"],
        owner_id=pkt["token"],  # link signed copy back to the packet
        owner_type="agency",
        file_base64=new_b64,
        mime_type="application/pdf",
        notes=f"Signed via packet link by {pkt['recipient_name']} on {ts}",
        uploaded_by="public-share",
        seq=d.get("seq"),
        is_template=False,
    )
    await db.documents.insert_one(signed_doc.dict())
    await db.packet_shares.update_one(
        {"token": token_str},
        {"$addToSet": {"signed_ids": doc_id}},
    )
    # mark complete if all signed
    total = await db.documents.count_documents({"category": pkt["category"]})
    refreshed = await db.packet_shares.find_one({"token": token_str})
    if refreshed and len(refreshed.get("signed_ids", [])) >= total:
        await db.packet_shares.update_one(
            {"token": token_str}, {"$set": {"completed_at": now_iso()}}
        )
    return {"ok": True, "signed_doc_id": signed_doc.id}


# ---------- DASHBOARD STATS ----------
@api.get("/stats")
async def stats(current: UserPublic = Depends(get_current_user)):
    total_clients = await db.clients.count_documents({})
    total_caregivers = await db.users.count_documents({"role": "caregiver"})
    total_documents = await db.documents.count_documents({})
    total_assignments = await db.assignments.count_documents({})
    total_training = await db.training.count_documents({})

    # compliance: % onboarding steps complete + % training complete
    total_steps = await db.onboarding.count_documents({})
    done_steps = await db.onboarding.count_documents({"completed": True})

    expected_completions = total_training * max(total_caregivers, 1)
    actual_completions = await db.training_completions.count_documents({})

    onboard_pct = (done_steps / total_steps * 100) if total_steps else 100
    training_pct = (actual_completions / expected_completions * 100) if expected_completions else 100
    audit_readiness = round((onboard_pct + training_pct) / 2)

    # Pending actions
    pending_onboarding = total_steps - done_steps
    pending_training = max(0, expected_completions - actual_completions)

    return {
        "total_clients": total_clients,
        "total_caregivers": total_caregivers,
        "total_documents": total_documents,
        "total_assignments": total_assignments,
        "total_training": total_training,
        "audit_readiness": audit_readiness,
        "pending_onboarding": pending_onboarding,
        "pending_training": pending_training,
        "onboarding_pct": round(onboard_pct),
        "training_pct": round(training_pct),
    }


# ---------- AI COMPLIANCE ASSISTANT (Claude) ----------
SYSTEM_PROMPT = (
    "You are HealthGuard, an expert compliance assistant for home health agency "
    "owners in the United States. You help with Medicare/Medicaid Conditions of "
    "Participation, state licensing audits, OASIS, HIPAA, caregiver onboarding, "
    "training requirements, and audit preparation. Be concise, factual, and "
    "actionable. When asked about specific regulations, cite the source "
    "(e.g., 42 CFR 484, HIPAA Privacy Rule). Always remind the user that you "
    "provide guidance, not legal advice."
)


@api.post("/assistant/chat")
async def chat_with_assistant(req: ChatMessageReq,
                              current: UserPublic = Depends(get_current_user)):
    """Streaming SSE chat with Claude Sonnet 4.5."""
    # store user message
    await db.chat_messages.insert_one({
        "id": str(uuid.uuid4()),
        "session_id": req.session_id,
        "user_id": current.id,
        "role": "user",
        "content": req.message,
        "created_at": now_iso(),
    })

    async def event_gen():
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=req.session_id,
            system_message=SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        full_text = ""
        try:
            async for ev in chat.stream_message(UserMessage(text=req.message)):
                if isinstance(ev, TextDelta):
                    full_text += ev.content
                    yield f"data: {ev.content}\n\n"
                elif isinstance(ev, StreamDone):
                    break
        except Exception as e:
            logger.error(f"LLM error: {e}")
            yield f"data: [Error: {str(e)}]\n\n"
        # persist assistant reply
        await db.chat_messages.insert_one({
            "id": str(uuid.uuid4()),
            "session_id": req.session_id,
            "user_id": current.id,
            "role": "assistant",
            "content": full_text,
            "created_at": now_iso(),
        })
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api.get("/assistant/history/{session_id}")
async def chat_history(session_id: str, current: UserPublic = Depends(get_current_user)):
    docs = await db.chat_messages.find(
        {"session_id": session_id, "user_id": current.id}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)
    return docs


# ---------- HEALTH ----------
@api.get("/")
async def root():
    return {"ok": True, "service": "HealthGuard Compliance API"}


# ---------- STARTUP: SEED ADMIN ----------
@app.on_event("startup")
async def seed_admin():
    existing = await db.users.find_one({"email": "admin@healthguard.com"})
    if not existing:
        uid = str(uuid.uuid4())
        await db.users.insert_one({
            "id": uid,
            "email": "admin@healthguard.com",
            "name": "Sister to Sister, PHCP",
            "role": "admin",
            "hashed_password": hash_password("Admin@123"),
            "created_at": now_iso(),
        })
        logger.info("Seeded default admin: admin@healthguard.com / Admin@123")
    elif existing.get("name") == "Agency Owner":
        # Rebrand legacy seeded admin
        await db.users.update_one(
            {"email": "admin@healthguard.com"},
            {"$set": {"name": "Sister to Sister, PHCP"}},
        )

    existing_cg = await db.users.find_one({"email": "caregiver@healthguard.com"})
    if not existing_cg:
        uid = str(uuid.uuid4())
        await db.users.insert_one({
            "id": uid,
            "email": "caregiver@healthguard.com",
            "name": "Sarah Johnson",
            "role": "caregiver",
            "hashed_password": hash_password("Caregiver@123"),
            "created_at": now_iso(),
        })
        logger.info("Seeded default caregiver")


app.include_router(api)

# Mount modular routers under the same /api prefix
app.include_router(shifts_router.router, prefix="/api")
app.include_router(ms_graph_router.router, prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _start_ms_scheduler():
    ms_graph_router.start_scheduler()


@app.on_event("shutdown")
async def _stop_ms_scheduler():
    ms_graph_router.stop_scheduler()


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
