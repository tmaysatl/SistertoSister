from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, Query, Request
from fastapi.responses import StreamingResponse, Response
from starlette.middleware.cors import CORSMiddleware
import os
import io
import csv
import re
import asyncio
import base64
import logging
import tempfile
from urllib.parse import quote as _urlquote
from pydantic import BaseModel, Field, EmailStr
from typing import Any, Dict, List, Optional, Literal
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
from core import supa_data
try:
    from core.push import send_push, _push_client
except Exception as e:  # pragma: no cover - defensive: push is best-effort,
    # never let a broken/missing optional integration take the whole app down
    # (this exact failure mode -- an unguarded import of a file that didn't
    # exist -- previously crashed the entire backend at startup; see the
    # matching guard around `routers.ms_graph` below).
    logging.getLogger(__name__).warning(
        "core.push unavailable (%s) -- push notifications disabled, rest of the app is unaffected", e,
    )
    send_push = None
    _push_client = None
from core.settings import EMERGENT_LLM_KEY
from core.pdf_utils import _load_logo, _make_watermark, stamp_pdf
from core.scheduling import (
    start_scheduler as _start_expiration_scheduler,
    stop_scheduler as _stop_expiration_scheduler,
    run_expiration_reminder_sweep,
)
from pdf_parser import parse_pdf as _parse_pdf_fields


def _safe_disposition(filename: str, mode: str = "inline") -> str:
    """Build an RFC 5987-compliant Content-Disposition header value.

    The `filename` parameter is restricted to ASCII (Starlette encodes
    headers as latin-1 and will raise UnicodeEncodeError on smart quotes,
    em-dashes etc.), but `filename*` may carry UTF-8 percent-encoded text.
    Browsers / mobile WebViews honour the UTF-8 form when present and fall
    back to the ASCII form otherwise — so we always supply both.
    """
    safe_ascii = re.sub(r'[^\x20-\x7e]', '_', filename or 'document')
    # Quote any embedded double-quotes for the legacy filename= form.
    safe_ascii = safe_ascii.replace('"', '_')
    utf8 = _urlquote(filename or 'document', safe='')
    return f'{mode}; filename="{safe_ascii}"; filename*=UTF-8\'\'{utf8}'
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
try:
    from routers import ms_graph as ms_graph_router
except Exception as e:  # pragma: no cover - defensive: this file does not
    # exist in this checkout at all (Microsoft Graph / SharePoint audit-binder
    # auto-export -- MS_TENANT_ID etc. default to "", i.e. off-by-default even
    # when present). Previously an unguarded `from routers import ms_graph`
    # crashed the ENTIRE backend at import time, before it could serve a
    # single request -- independent of Mongo/Supabase config. Guard it so a
    # missing/broken optional integration never does that again.
    logging.getLogger(__name__).warning(
        "routers.ms_graph unavailable (%s) -- Microsoft Graph integration and the scheduled "
        "audit-binder export are disabled, rest of the app is unaffected", e,
    )
    ms_graph_router = None
from routers import supabase_router


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
    # Slice I: also create the matching Supabase Auth user (same UUID -> FKs
    # line up across both DBs). Best-effort; trigger will populate profiles.
    await supa_data.create_supabase_auth_user(
        user_id=uid, email=req.email, password=req.password,
        name=req.name, role=req.role,
    )
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
    # Phase 4: source from Supabase Postgres profiles, with a Mongo fallback
    # (same pattern as /stats) so a Postgres/Supabase outage doesn't also
    # take down the caregiver list every admin screen depends on.
    try:
        rows = await supa_data.list_users_by_role("caregiver")
    except Exception as e:
        logger.warning("caregivers: Postgres list failed, falling back to Mongo: %s", e)
        rows = await db.users.find({"role": "caregiver"}, {"_id": 0, "hashed_password": 0}).sort("name", 1).to_list(1000)
    return [UserPublic(**r) for r in rows]


# ---------- CLIENTS ----------
@api.post("/clients", response_model=Client)
async def create_client(req: ClientCreate, _: UserPublic = Depends(require_admin)):
    obj = Client(**req.dict())
    await db.clients.insert_one(obj.dict())
    # Dual-write: also upsert into Supabase Postgres (best-effort).
    await supa_data.upsert_client(obj.dict())
    return obj


@api.get("/clients", response_model=List[Client])
async def list_clients(_: UserPublic = Depends(get_current_user)):
    # Phase 4: source from Supabase Postgres, with a Mongo fallback (same
    # pattern as /stats and /caregivers) so a Postgres/Supabase outage
    # doesn't also take down the client list.
    try:
        rows = await supa_data.list_clients()
    except Exception as e:
        logger.warning("clients: Postgres list failed, falling back to Mongo: %s", e)
        rows = await db.clients.find({}, {"_id": 0}).sort("created_at", -1).to_list(2000)
    return [Client(**r) for r in rows]


@api.get("/clients/{client_id}", response_model=Client)
async def get_client(client_id: str, _: UserPublic = Depends(get_current_user)):
    # Phase 4: source from Supabase Postgres
    d = await supa_data.get_client(client_id)
    if not d:
        raise HTTPException(404, "Client not found")
    return Client(**d)


@api.delete("/clients/{client_id}")
async def delete_client(client_id: str, _: UserPublic = Depends(require_admin)):
    await db.clients.delete_one({"id": client_id})
    await db.assignments.delete_many({"client_id": client_id})
    await db.client_tasks.delete_many({"client_id": client_id})
    # Dual-write: cascade is automatic in Postgres via ON DELETE CASCADE.
    await supa_data.delete_client(client_id)
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
        await supa_data.upsert_client_task(task.dict())
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
    completed_at = now_iso() if new_completed else None
    update = {"completed": new_completed, "completed_at": completed_at}
    await db.client_tasks.update_one({"id": task_id}, {"$set": update})
    await supa_data.toggle_client_task(task_id, new_completed, completed_at)
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
    await supa_data.update_user_photo(user_id, b)
    return {"ok": True}


@api.put("/clients/{client_id}/photo")
async def set_client_photo(client_id: str, p: PhotoPayload,
                           _: UserPublic = Depends(require_admin)):
    b = p.photo_base64.split(",", 1)[-1]
    await db.clients.update_one({"id": client_id}, {"$set": {"photo_base64": b}})
    await supa_data.update_client_photo(client_id, b)
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
    # Slice E: dual-write to Postgres
    await supa_data.insert_dm(msg)
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
    # Slice E: read from Postgres via a single SQL query (photo + role joined in).
    return await supa_data.list_dm_threads(current.id)


@api.get("/chat/messages")
async def get_messages(with_user: str = Query(..., alias="with"),
                       current: UserPublic = Depends(get_current_user)):
    # Slice E: read from Postgres; dual-update read-marker (Mongo + PG).
    msgs = await supa_data.get_dm_conversation(current.id, with_user)
    await db.chat_dms.update_many(
        {"to_id": current.id, "from_id": with_user, "read": False},
        {"$set": {"read": True}},
    )
    await supa_data.mark_dm_read(current.id, with_user)
    return msgs


@api.get("/chat/contacts")
async def list_contacts(current: UserPublic = Depends(get_current_user)):
    """For admin: list all caregivers. For caregiver: list all admins."""
    # Slice E: source from Postgres profiles
    target_role = "caregiver" if current.role == "admin" else "admin"
    return await supa_data.list_users_by_role(target_role)


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
        # fname is fully static/ASCII today (no user-supplied text), but use
        # the same Unicode-safe helper as the other PDF responses anyway --
        # a prior bug (see _safe_disposition's docstring) crashed this exact
        # header with a 500 whenever a title had a non-ASCII character, and
        # this endpoint would silently become unsafe again the moment
        # anyone adds a name/title into `fname`.
        headers={"Content-Disposition": _safe_disposition(fname)},
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
            # NOTE: was a hardcoded "Attached" placeholder (`if True else`) --
            # never actually reflected whether a file was attached.
            attached = "Attached" if d.get("file_base64") else "Pending upload"
            story.append(Paragraph(f"&nbsp;&nbsp;• {d['title']} — <i>{attached}</i>", styles["Normal"]))
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


# ---------- CSV AUDIT EXPORT ----------
def _csv_response(filename: str, header: List[str], rows: List[List[Any]]) -> Response:
    """Build a CSV Response. Includes a UTF-8 BOM so Excel (the realistic
    destination for this) auto-detects the encoding instead of mangling
    any non-ASCII name -- the exact class of bug _safe_disposition already
    guards against for filenames, applied here to the file body too."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": _safe_disposition(filename)},
    )


@api.get("/reports/compliance-roster.csv")
async def compliance_roster_csv(_: UserPublic = Depends(require_admin)):
    """CSV export: one row per caregiver + one row per client, each with a
    compliance-status snapshot (onboarding, policy acknowledgments,
    credential expiration, training completions, documents on file, last
    recorded activity) -- the "who's not audit-ready right now" view a
    surveyor visit or an internal check would need.

    Reads MongoDB directly rather than through supa_data/Postgres: Mongo is
    the authoritative store, and (found while building this) several of the
    Postgres-only read paths this would otherwise depend on
    (list_caregivers, list_clients, list_policy_acks) have no fallback if
    Supabase is unreachable -- see the fixes applied alongside this export.
    Bulk-fetches each collection once and aggregates in memory rather than
    querying per-person, so this stays fast regardless of roster size.
    """
    now_dt = datetime.now(timezone.utc)
    now_s = now_dt.isoformat()
    cutoff_30 = (now_dt + timedelta(days=30)).isoformat()

    caregivers = await db.users.find({"role": "caregiver"}, {"_id": 0, "hashed_password": 0}).to_list(5000)
    clients = await db.clients.find({}, {"_id": 0}).to_list(5000)
    total_policies = await db.documents.count_documents({"category": "policy"})
    total_trainings = await db.training.count_documents({})

    onboarding_by_cg: Dict[str, List[dict]] = {}
    async for step in db.onboarding.find({}, {"_id": 0, "caregiver_id": 1, "completed": 1}):
        onboarding_by_cg.setdefault(step.get("caregiver_id"), []).append(step)

    acks_by_user: Dict[str, int] = {}
    async for a in db.policy_acks.find({}, {"_id": 0, "user_id": 1}):
        uid = a.get("user_id")
        if uid:
            acks_by_user[uid] = acks_by_user.get(uid, 0) + 1

    creds_by_cg: Dict[str, Dict[str, int]] = {}
    async for cred in db.documents.find(
        {"category": "credential", "owner_id": {"$ne": None}, "expires_at": {"$ne": None}},
        {"_id": 0, "owner_id": 1, "expires_at": 1},
    ):
        bucket = creds_by_cg.setdefault(cred["owner_id"], {"expired": 0, "expiring": 0})
        exp = cred.get("expires_at") or ""
        if exp < now_s:
            bucket["expired"] += 1
        elif exp <= cutoff_30:
            bucket["expiring"] += 1

    training_by_cg: Dict[str, int] = {}
    async for t in db.training_completions.find({}, {"_id": 0, "caregiver_id": 1}):
        cid = t.get("caregiver_id")
        if cid:
            training_by_cg[cid] = training_by_cg.get(cid, 0) + 1

    docs_by_owner_total: Dict[str, int] = {}
    docs_by_owner_cat: Dict[str, Dict[str, int]] = {}
    async for d in db.documents.find(
        {"is_template": False, "owner_id": {"$ne": None}}, {"_id": 0, "owner_id": 1, "category": 1},
    ):
        oid, cat = d["owner_id"], (d.get("category") or "")
        docs_by_owner_total[oid] = docs_by_owner_total.get(oid, 0) + 1
        bucket = docs_by_owner_cat.setdefault(oid, {})
        bucket[cat] = bucket.get(cat, 0) + 1

    # document_views also captures "signed"/"submitted" actions (not just
    # reads), so this alone is a reasonable "last touched anything" signal.
    last_activity: Dict[str, str] = {}
    async for v in db.document_views.find({}, {"_id": 0, "viewer_id": 1, "viewed_at": 1}).sort("viewed_at", 1):
        vid = v.get("viewer_id")
        if vid:
            last_activity[vid] = v.get("viewed_at") or last_activity.get(vid, "")

    client_onboarding_total = await db.documents.count_documents(
        {"category": "client_onboarding", "is_template": True}
    )

    rows: List[List[Any]] = []
    for u in caregivers:
        cg_id = u.get("id")
        steps = onboarding_by_cg.get(cg_id, [])
        creds = creds_by_cg.get(cg_id, {"expired": 0, "expiring": 0})
        rows.append([
            "Caregiver", u.get("name") or "", u.get("email") or "",
            sum(1 for s in steps if s.get("completed")), len(steps),
            acks_by_user.get(cg_id, 0), total_policies,
            creds["expired"], creds["expiring"],
            training_by_cg.get(cg_id, 0), total_trainings,
            docs_by_owner_total.get(cg_id, 0),
            last_activity.get(cg_id, ""),
        ])
    for c in clients:
        cl_id = c.get("id")
        onboarding_done = docs_by_owner_cat.get(cl_id, {}).get("client_onboarding", 0)
        rows.append([
            "Client", c.get("name") or "", "",
            onboarding_done, client_onboarding_total,
            "", "",
            "", "",
            "", "",
            docs_by_owner_total.get(cl_id, 0),
            last_activity.get(cl_id, ""),
        ])

    header = [
        "Type", "Name", "Email",
        "Onboarding Done", "Onboarding Total",
        "Policies Acknowledged", "Policies Total",
        "Credentials Expired", "Credentials Expiring (30d)",
        "Trainings Completed", "Trainings Total",
        "Documents On File",
        "Last Activity (UTC)",
    ]
    fname = f"SisterToSister_ComplianceRoster_{now_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(fname, header, rows)


@api.get("/reports/audit-log.csv")
async def audit_log_csv(
    days: int = Query(365, ge=1, le=3650),
    person_id: Optional[str] = None,
    _: UserPublic = Depends(require_admin),
):
    """CSV export of the raw audit trail: one row per logged document
    view/sign/submit event in the window, newest first -- "prove exactly
    what happened and when" to complement the roster's "are we compliant
    right now" summary above. `days` bounds the window (default 1 year);
    optional `person_id` scopes to one caregiver/client/admin's activity.

    Where a row corresponds to a locked e-signature submission (see
    locked_pdf.py / create_document_submission), the submitter's IP and
    the signed PDF's SHA-256 are included -- matched by (document, actor,
    exact timestamp), which is reliable for anything submitted through
    today's code (view + submission rows are written from the same
    timestamp variable) but won't backfill for older data that never
    captured this.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q: Dict[str, Any] = {"viewed_at": {"$gte": since}}
    if person_id:
        q["viewer_id"] = person_id
    views = await db.document_views.find(q, {"_id": 0}).sort("viewed_at", -1).to_list(20000)

    doc_ids = list({v.get("document_id") for v in views if v.get("document_id")})
    titles: Dict[str, str] = {}
    if doc_ids:
        async for d in db.documents.find({"id": {"$in": doc_ids}}, {"_id": 0, "id": 1, "title": 1}):
            titles[d["id"]] = d.get("title") or ""

    sub_q: Dict[str, Any] = {"submitted_at": {"$gte": since}}
    if person_id:
        sub_q["submitted_by"] = person_id
    subs = await db.submissions.find(sub_q, {"_id": 0}).to_list(20000)
    sub_by_key = {
        (s.get("document_id"), s.get("submitted_by"), s.get("submitted_at")): s for s in subs
    }

    rows: List[List[Any]] = []
    for v in views:
        key = (v.get("document_id"), v.get("viewer_id"), v.get("viewed_at"))
        sub = sub_by_key.get(key, {})
        rows.append([
            v.get("viewed_at") or "",
            v.get("viewer_name") or "",
            v.get("action") or "viewed",
            titles.get(v.get("document_id"), v.get("document_id") or ""),
            sub.get("submitter_ip") or "",
            sub.get("pdf_sha256") or "",
        ])

    header = ["Timestamp (UTC)", "Actor", "Action", "Document", "IP Address", "PDF SHA-256"]
    fname = f"SisterToSister_AuditLog_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return _csv_response(fname, header, rows)


# ---------- DOCUMENTS ----------
async def _extract_and_store_schema(doc_id: str, file_base64: str,
                                    mime_type: Optional[str]) -> int:
    """Run the pdf_parser on the uploaded PDF and upsert the field schema
    into MongoDB (collection `field_schemas`, key = document_id).

    Returns the number of fields extracted (0 if the file isn't a PDF, if
    parsing raised, or if the PDF has no detectable fields). Never
    propagates parse errors — extraction failures must not block uploads.
    """
    mime = (mime_type or "application/pdf").lower()
    if "pdf" not in mime:
        return 0
    try:
        raw = base64.b64decode(file_base64)
    except Exception as e:
        logger.warning("field-schema: base64 decode failed for %s: %s", doc_id, e)
        return 0
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f"schema_{doc_id}_",
                                         suffix=".pdf", delete=False) as fp:
            fp.write(raw)
            tmp_path = fp.name
        fields = _parse_pdf_fields(tmp_path)
    except Exception as e:
        logger.warning("field-schema: parse failed for %s: %s", doc_id, e)
        fields = []
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    try:
        await db.field_schemas.update_one(
            {"document_id": doc_id},
            {"$set": {
                "document_id": doc_id,
                "fields": fields,
                "field_count": len(fields),
                "source": (fields[0].get("source") if fields else "empty"),
                "extracted_at": now_iso(),
                "parser_version": "1.0",
            }},
            upsert=True,
        )
    except Exception as e:
        logger.warning("field-schema: mongo upsert failed for %s: %s", doc_id, e)
    return len(fields)


async def _get_cached_fields(document_id: str) -> List[dict]:
    """Read the cached pdf_parser field list for `document_id` (same data
    the /schema endpoint serves), without re-parsing the PDF. Used by
    create_document_submission to know each submitted field's real type
    (checkbox/radio/text/...) when filling+locking a signed PDF. Returns
    [] if no schema was ever extracted for this document."""
    row = await db.field_schemas.find_one({"document_id": document_id}, {"_id": 0})
    return (row or {}).get("fields") or []


@api.post("/documents", response_model=Document)
async def create_document(req: DocumentCreate,
                          current: UserPublic = Depends(get_current_user)):
    obj = Document(**req.dict(), uploaded_by=current.id)
    # Phase 4 Slice C: upload blob to Supabase Storage FIRST so we can persist
    # storage_path on the Mongo record (frontend uses this as the canonical
    # "file is viewable" flag).
    storage_path: Optional[str] = None
    if obj.file_base64:
        storage_path = supa_data.upload_document_blob_sync(
            obj.id, obj.file_base64, obj.mime_type or "application/pdf"
        )
        obj.storage_path = storage_path
    await db.documents.insert_one(obj.dict())
    # Postgres mirror
    d_for_pg = obj.dict()
    d_for_pg["storage_path"] = storage_path
    await supa_data.upsert_document(d_for_pg)
    # Phase-1 PDF field extraction: parse widget/text fields, persist schema
    # in Mongo (`field_schemas` collection). Best-effort — never blocks the
    # upload response on a parse failure.
    if obj.file_base64:
        try:
            n = await _extract_and_store_schema(
                obj.id, obj.file_base64, obj.mime_type,
            )
            logger.info("field-schema: extracted %d field(s) for %s", n, obj.id)
        except Exception as e:  # defensive — helper is already try/except
            logger.warning("field-schema: extract_and_store failed: %s", e)
    return obj


@api.get("/documents/{document_id}/schema")
async def get_document_schema(document_id: str,
                              current: UserPublic = Depends(get_current_user)):
    """Return the auto-extracted PDF field schema for `document_id`.

    Response shape:
        {
          "document_id": str,
          "field_count": int,
          "source": "acroform" | "text-heuristic" | "empty",
          "fields": [ { field_name, field_type, page, position, options,
                        required, value, source }, ... ],
          "extracted_at": iso8601,
          "parser_version": "1.0",
        }

    If the schema was never extracted (e.g. the document was uploaded
    before this feature shipped), a lazy re-extraction is attempted from
    the stored `file_base64` blob so old rows also get a schema on first
    access. If the doc is not a PDF or has no fields, `fields` will be [].
    """
    doc = await db.documents.find_one({"id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    row = await db.field_schemas.find_one({"document_id": document_id})
    if row is None:
        # Lazy backfill on first read — keeps this endpoint 200 for docs
        # that pre-date the auto-extraction hook.
        if doc.get("file_base64"):
            try:
                await _extract_and_store_schema(
                    document_id, doc["file_base64"], doc.get("mime_type"),
                )
                row = await db.field_schemas.find_one({"document_id": document_id})
            except Exception as e:
                logger.warning("field-schema: lazy backfill failed for %s: %s",
                               document_id, e)
        if row is None:
            # No blob and no cached schema — return the empty envelope.
            row = {
                "document_id": document_id,
                "fields": [],
                "field_count": 0,
                "source": "empty",
                "extracted_at": now_iso(),
                "parser_version": "1.0",
            }
    row.pop("_id", None)
    return row


# ----- Dynamic form submissions (Phase 2) -----------------------------------
class DynamicSubmissionCreate(BaseModel):
    """Payload for POST /documents/{document_id}/submissions.

    `values` is an untyped dict keyed by the schema's `field_name`. Values
    may be strings, booleans, numbers, or arrays for multi-select fields —
    we do not enforce a shape here because the schema itself is dynamic.
    Optional `signature_b64` reserved for future phases; ignored for now.
    """
    values: Dict[str, Any] = Field(default_factory=dict)
    signature_b64: Optional[str] = None


@api.post("/documents/{document_id}/submissions", status_code=201)
async def create_document_submission(
    document_id: str,
    payload: DynamicSubmissionCreate,
    request: Request,
    current: UserPublic = Depends(get_current_user),
):
    """Persist a filled-form submission for `document_id`, AND (new) turn
    it into a locked, hashed signed PDF wherever possible so the signer's
    submission is a real, tamper-evident document -- not just a raw values
    blob with nothing to show for it.

    Stored in the `submissions` collection with shape:
        {
          id, document_id, submitted_by, submitter_email, submitter_ip,
          values, signature_b64, submitted_at,
          pdf_source, pdf_sha256, locked, signed_document_id,   # new
        }
    The last four are only present when PDF generation succeeded; the raw
    `values`/`signature_b64` audit record is always saved regardless, so a
    generation failure never loses the submission itself (same guarantee
    the endpoint always had).

    Three-tier PDF generation, cheapest/most-reliable first:
      1. Curated schema (form_schemas.py) if this title has one -- same
         renderer /documents/{id}/submit-form already uses.
      2. Generic AcroForm fill -- fills the document's REAL fillable PDF
         (the one forms.py generated) using the field list already cached
         from /schema, covers every fillable document, not just the
         curated 5.
      3. Generic summary PDF -- last-resort "cover sheet" for a document
         with no real fillable fields at all (e.g. a flat scanned upload).

    Returns the created submission id and a snapshot of `field_count`
    (the number of populated fields, for the client's confirmation UI),
    plus `signed_document_id`/`locked` so a caller can offer to show the
    locked copy (today's UI already surfaces it via the normal document
    list + viewer once created -- no other endpoint needed).
    """
    doc = await db.documents.find_one({"id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    ts = now_iso()
    sub_id = str(uuid.uuid4())
    title = doc.get("title", "")
    values = payload.values or {}
    client_ip = request.client.host if request and request.client else None

    sub = {
        "id": sub_id,
        "document_id": document_id,
        "document_title": title,
        "submitted_by": current.id,
        "submitter_email": current.email,
        "submitter_role": current.role,
        "values": values,
        "signature_b64": payload.signature_b64,
        "submitted_at": ts,
        "submitter_ip": client_ip,
    }

    pdf_bytes: Optional[bytes] = None
    pdf_source: Optional[str] = None
    try:
        if _has_schema(title):
            pdf_bytes = _render_filled(title, values, payload.signature_b64, current.name)
            pdf_source = "curated-schema"
        else:
            cached_fields = await _get_cached_fields(document_id)
            is_pdf = (doc.get("mime_type") or "").lower() == "application/pdf"
            if doc.get("file_base64") and is_pdf:
                filled = _fill_acroform_pdf(base64.b64decode(doc["file_base64"]), values, cached_fields)
                if filled is not None:
                    pdf_bytes = filled
                    pdf_source = "acroform-fill"
            if pdf_bytes is None and cached_fields:
                # No real fillable fields to fill (no file, not a PDF, or
                # nothing acroform-sourced in the cached schema -- e.g. a
                # flat/scanned upload with only text-heuristic matches).
                # Still produce SOMETHING signable rather than nothing.
                pdf_bytes = _render_generic_submission_pdf(
                    title, cached_fields, values, payload.signature_b64, current.name,
                )
                pdf_source = "generic-summary"
    except Exception as e:
        logger.warning("submissions: pdf generation failed for %s (%s): %s", document_id, title, e)
        pdf_bytes = None
        pdf_source = None

    signed_document_id: Optional[str] = None
    if pdf_bytes:
        digest = _sha256_hex(pdf_bytes)
        sub["pdf_source"] = pdf_source
        sub["pdf_sha256"] = digest
        sub["locked"] = True

        signed = Document(
            title=f"COMPLETED - {title} - {current.name}",
            category=doc.get("category"),
            owner_id=current.id,
            owner_type="caregiver" if current.role == "caregiver" else "agency",
            file_base64=base64.b64encode(pdf_bytes).decode("ascii"),
            mime_type="application/pdf",
            notes=f"Signed & locked submission by {current.name} on {ts} — SHA-256 {digest[:16]}…",
            uploaded_by=current.id,
            seq=doc.get("seq"),
            is_template=False,
            locked=True,
            pdf_sha256=digest,
        )
        await db.documents.insert_one(signed.dict())
        signed_document_id = signed.id
        sub["signed_document_id"] = signed_document_id

        # Slice C dual-write, same pattern as sign_document()/submit_doc_form().
        signed_dict = signed.dict()
        signed_dict["signed_at"] = ts
        signed_dict["signed_by"] = current.id
        signed_dict["form_data"] = values
        if payload.signature_b64:
            signed_dict["signature_image"] = True  # presence flag only -- see supa_data.upsert_document
        if signed.file_base64:
            path = supa_data.upload_document_blob_sync(signed.id, signed.file_base64, "application/pdf")
            signed_dict["storage_path"] = path
        try:
            await supa_data.upsert_document(signed_dict)
        except Exception as e:
            logger.warning("submissions: postgres mirror failed for %s: %s", signed.id, e)

        await db.document_views.insert_one({
            "id": str(uuid.uuid4()),
            "document_id": document_id,
            "viewer_id": current.id,
            "viewer_name": current.name,
            "action": "submitted",
            "viewed_at": ts,
        })

    await db.submissions.insert_one(sub)
    populated = sum(
        1 for v in values.values()
        if v not in (None, "", [], {}, False)
    )
    return {
        "id": sub_id,
        "document_id": document_id,
        "submitted_at": ts,
        "field_count": populated,
        "signed_document_id": signed_document_id,
        "locked": bool(pdf_bytes),
    }





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


@api.get("/documents/{doc_id}/url")
async def get_document_signed_url(doc_id: str,
                                  current: UserPublic = Depends(get_current_user)):
    """Phase 4 Slice C (additive): return a short-lived Supabase Storage signed
    URL for the PDF blob. Frontend can switch to loading from this URL instead
    of pulling base64 over the API for faster document opens.

    Returns 404 if the document is unknown OR if no blob has been uploaded
    yet (e.g., template-only metadata).
    """
    # Resolve metadata via Postgres first (Slice A territory)
    meta = await supa_data.get_document_storage_path(doc_id)
    if not meta:
        # Fallback to Mongo if doc not yet in Postgres (legacy uploads pre-Slice-C)
        d = await db.documents.find_one({"id": doc_id}, {"_id": 0, "file_base64": 0})
        if not d:
            raise HTTPException(404, "Document not found")
        # Synthesize the path we *would* use; useful when Storage upload happened
        # but the Postgres row hasn't been refreshed.
        meta = supa_data._doc_storage_path(doc_id, d.get("mime_type") or "application/pdf")
    url = supa_data.signed_url_for_document(meta, expires_in_seconds=3600)
    if not url:
        raise HTTPException(404, "No stored file for this document")
    return {"url": url, "expires_in": 3600, "storage_path": meta}


@api.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, _: UserPublic = Depends(require_admin)):
    await db.documents.delete_one({"id": doc_id})
    # Phase 4 Slice C: also remove from Postgres + Storage.
    storage_path = await supa_data.delete_document(doc_id)
    if storage_path:
        supa_data.delete_document_blob_sync(storage_path)
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
        # Slice C: dual-write clone metadata. Re-upload blob under new id so
        # signed URLs are stable per recipient.
        if clone.get("file_base64"):
            path = supa_data.upload_document_blob_sync(
                clone["id"], clone["file_base64"], clone.get("mime_type") or "application/pdf"
            )
            clone["storage_path"] = path
        await supa_data.upsert_document(clone)
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
        headers={"Content-Disposition": _safe_disposition(f'{d["title"]}.pdf')},
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

    # Lock: the signature is now baked into the last page's content stream
    # (merge_page above) -- strip any pre-existing form interactivity so
    # the signed copy can't be re-edited. A no-op for the common case
    # (signing a read-only policy/notice PDF with no form fields).
    _strip_form_interactivity(writer)

    out = io.BytesIO()
    writer.write(out)
    signed_bytes = out.getvalue()
    new_b64 = base64.b64encode(signed_bytes).decode()
    digest = _sha256_hex(signed_bytes)

    signed = Document(
        title=f"{d['title']} (signed by {current.name})",
        category=d["category"],
        owner_id=current.id,
        owner_type="caregiver" if current.role == "caregiver" else "agency",
        file_base64=new_b64,
        mime_type="application/pdf",
        notes=f"Signed by {current.name} on {ts} — SHA-256 {digest[:16]}…",
        uploaded_by=current.id,
        seq=d.get("seq"),
        is_template=False,
        locked=True,
        pdf_sha256=digest,
    )
    await db.documents.insert_one(signed.dict())
    # Slice C: dual-write the signed PDF
    signed_dict = signed.dict()
    signed_dict["signed_at"] = ts
    signed_dict["signed_by"] = current.id
    signed_dict["signature_image"] = True
    if signed.file_base64:
        path = supa_data.upload_document_blob_sync(
            signed.id, signed.file_base64, signed.mime_type or "application/pdf"
        )
        signed_dict["storage_path"] = path
    await supa_data.upsert_document(signed_dict)
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


from forms import (
    all_fillable_pdfs, CLIENT_BUILDERS, CAREGIVER_BUILDERS,
    build_policy_pdf, POLICY_BODIES,
    build_client_onboarding_pdf, CLIENT_ONBOARDING_BODIES,
)
from form_schemas import SCHEMAS as _FORM_SCHEMAS, has_schema as _has_schema, get_schema as _get_schema, render_filled_pdf as _render_filled
from locked_pdf import (
    sha256_hex as _sha256_hex,
    fill_acroform_pdf as _fill_acroform_pdf,
    render_generic_submission_pdf as _render_generic_submission_pdf,
    strip_form_interactivity as _strip_form_interactivity,
)


@api.get("/documents/{doc_id}/form-schema")
async def get_doc_form_schema(doc_id: str, current: UserPublic = Depends(get_current_user)):
    d = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Document not found")
    schema = _get_schema(d.get("title", ""))
    if not schema:
        return {"has_form": False}
    return {"has_form": True, "title": d.get("title"), "schema": schema}


class SubmitFormReq(BaseModel):
    values: dict
    signature_b64: Optional[str] = None
    target_owner_id: Optional[str] = None       # admin can submit on behalf
    target_owner_type: Optional[str] = "caregiver"


@api.post("/documents/{doc_id}/submit-form")
async def submit_doc_form(doc_id: str, req: SubmitFormReq,
                          current: UserPublic = Depends(get_current_user)):
    src = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not src:
        raise HTTPException(404, "Document not found")
    title = src.get("title", "")
    if not _has_schema(title):
        raise HTTPException(400, "This document has no fillable schema")

    pdf_bytes = _render_filled(title, req.values, req.signature_b64, current.name)
    digest = _sha256_hex(pdf_bytes)
    ts = now_iso()
    new_doc = {
        "id": str(uuid.uuid4()),
        "title": f"COMPLETED - {title} - {current.name}",
        "category": src.get("category"),
        "owner_id": req.target_owner_id or current.id,
        "owner_type": req.target_owner_type if current.role == "admin" else (
            "caregiver" if current.role == "caregiver" else "agency"
        ),
        "file_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "mime_type": "application/pdf",
        "uploaded_by": current.id,
        "uploaded_at": ts,
        "is_template": False,
        "notes": f"Filled from template: {title} — SHA-256 {digest[:16]}…",
        # NOTE: was "form_values" -- renamed to match supa_data.upsert_document's
        # meta allowlist ("form_data"), which the old key silently missed
        # (the Postgres mirror kept dropping the submitted values).
        "form_data": req.values,
        "locked": True,
        "pdf_sha256": digest,
    }
    await db.documents.insert_one(new_doc)
    # Slice C: dual-write filled-form document
    pg_doc = dict(new_doc)
    pg_doc["signed_at"] = ts
    pg_doc["signed_by"] = current.id
    if req.signature_b64:
        pg_doc["signature_image"] = True
    if new_doc.get("file_base64"):
        path = supa_data.upload_document_blob_sync(
            new_doc["id"], new_doc["file_base64"], "application/pdf"
        )
        new_doc["storage_path"] = path
        pg_doc["storage_path"] = path
    try:
        await supa_data.upsert_document(pg_doc)
    except Exception as e:
        logger.warning("submit-form: postgres mirror failed for %s: %s", new_doc["id"], e)
    return {"id": new_doc["id"], "title": new_doc["title"], "locked": True, "pdf_sha256": digest}


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
        # Phase 4 Slice C mirror: upload to Supabase Storage + Postgres too
        # (same dual-write pattern as create_document()), so these fillable
        # forms are also visible to Postgres-sourced counts/signed-URL reads.
        existing = await db.documents.find_one(
            {"category": category, "title": title, "is_template": True}
        )
        if existing:
            storage_path = supa_data.upload_document_blob_sync(
                existing["id"], b64, "application/pdf"
            )
            await db.documents.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "file_base64": b64,
                    "mime_type": "application/pdf",
                    "uploaded_at": now_iso(),
                    "seq": seq,
                    "storage_path": storage_path,
                }},
            )
            pg_doc = {**existing, "file_base64": b64, "mime_type": "application/pdf",
                      "seq": seq, "storage_path": storage_path}
        else:
            doc = Document(
                title=title, category=category,  # type: ignore
                owner_type="agency", uploaded_by=current.id,
                file_base64=b64, mime_type="application/pdf",
                seq=seq, is_template=True,
                notes="Fillable form \u2014 open and complete in any PDF viewer.",
            )
            storage_path = supa_data.upload_document_blob_sync(doc.id, b64, "application/pdf")
            doc.storage_path = storage_path
            await db.documents.insert_one(doc.dict())
            pg_doc = doc.dict()
        if storage_path:
            try:
                await supa_data.upsert_document(pg_doc)
            except Exception as e:
                logger.warning("rebuild-fillable: postgres mirror failed for %s: %s", title, e)
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
    # Slice H: read from Postgres, with a Mongo fallback (same pattern as
    # /stats/caregivers/clients) so a Postgres/Supabase outage doesn't also
    # hide who has and hasn't acknowledged policies.
    target = current.id if current.role == "caregiver" else user_id
    try:
        return await supa_data.list_policy_acks(user_id=target)
    except Exception as e:
        logger.warning("policy-acks: Postgres list failed, falling back to Mongo: %s", e)
        q = {"user_id": target} if target else {}
        return await db.policy_acks.find(q, {"_id": 0}).sort("acknowledged_at", -1).to_list(2000)


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
    # Slice H: mirror upsert to Postgres
    await supa_data.upsert_policy_ack(ack)
    return ack


@api.delete("/policies/acknowledge/{policy_id}")
async def un_acknowledge_policy(
    policy_id: str, current: UserPublic = Depends(get_current_user),
):
    await db.policy_acks.delete_one(
        {"policy_id": policy_id, "user_id": current.id}
    )
    # Slice H: mirror delete to Postgres
    await supa_data.delete_policy_ack(policy_id, current.id)
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
        def _seed_generator(raw_title: str):
            """Return (pdf_builder_fn, notes, source_label) for auto-generated
            seed content, or None if this (category, title) has no generator
            -- those stay blank stubs requiring a manual admin upload, same
            as always. Two generators exist today: the policy library
            (forms.build_policy_pdf) and the 4 client-intake notices sourced
            from the agency's handbook (forms.build_client_onboarding_pdf)."""
            if category == "policy":
                return (build_policy_pdf,
                        "Auto-generated from agency policy library")
            if category == "client_onboarding" and raw_title in CLIENT_ONBOARDING_BODIES:
                return (build_client_onboarding_pdf,
                        "Auto-generated from agency client intake packet")
            return None

        def _gen_seed_pdf_b64(raw_title: str) -> Optional[str]:
            gen = _seed_generator(raw_title)
            if not gen:
                return None
            builder_fn, _ = gen
            try:
                return base64.b64encode(builder_fn(raw_title)).decode()
            except Exception as e:
                logger.warning("seed: pdf build failed for %s/%s: %s", category, raw_title, e)
                return None

        for i, t in enumerate(titles, start=1):
            title = f"{i:02d} - {t}"
            exists = await db.documents.find_one({"category": category, "title": title})
            gen = _seed_generator(t)

            if exists and gen and not exists.get("file_base64"):
                # Row was seeded before a generator existed for it (or before
                # this fix) and is stuck as an empty stub forever (the plain
                # `if exists: continue` below would otherwise skip it on
                # every future call). Backfill it in place. A row that
                # already has a file -- including an admin's own custom
                # upload -- is never touched.
                _, notes = gen
                file_b64 = _gen_seed_pdf_b64(t)
                if file_b64:
                    storage_path = supa_data.upload_document_blob_sync(
                        exists["id"], file_b64, "application/pdf"
                    )
                    await db.documents.update_one(
                        {"id": exists["id"]},
                        {"$set": {
                            "file_base64": file_b64,
                            "mime_type": "application/pdf",
                            "notes": notes,
                            "storage_path": storage_path,
                        }},
                    )
                    if storage_path:
                        pg_doc = {**exists, "file_base64": file_b64,
                                  "mime_type": "application/pdf", "storage_path": storage_path}
                        await supa_data.upsert_document(pg_doc)
                    created += 1
                continue

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
            if gen:
                # This title has a generator (policy library, or one of the
                # 4 client-intake notices) -- never leave it as an empty
                # stub with nothing to open.
                _, notes = gen
                doc.file_base64 = _gen_seed_pdf_b64(t)
                if doc.file_base64:
                    doc.mime_type = "application/pdf"
                    doc.notes = notes

                storage_path = None
                if doc.file_base64:
                    # Mirrors the dual-write pattern in create_document():
                    # upload to Supabase Storage first, then persist the
                    # returned storage_path on both Mongo and Postgres.
                    storage_path = supa_data.upload_document_blob_sync(
                        doc.id, doc.file_base64, doc.mime_type or "application/pdf"
                    )
                    doc.storage_path = storage_path

                await db.documents.insert_one(doc.dict())

                if doc.file_base64:
                    d_for_pg = doc.dict()
                    d_for_pg["storage_path"] = storage_path
                    await supa_data.upsert_document(d_for_pg)
            else:
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


@api.post("/admin/run-expiration-reminders")
async def run_expiration_reminders_now(_: UserPublic = Depends(require_admin)):
    """Manually trigger the expiration-reminder sweep (normally runs
    automatically once a day -- see core/scheduling.py). Fully deduped
    against a dedicated `expiration_reminders_sent` collection, so running
    this can never double-send a reminder the daily job already covered --
    safe to use for testing or to force an immediate check.
    """
    return await run_expiration_reminder_sweep()


# ---------- ASSIGNMENTS ----------
@api.post("/assignments", response_model=Assignment)
async def create_assignment(req: AssignmentCreate,
                            _: UserPublic = Depends(require_admin)):
    # Prevent duplicate caregiver↔client links (idempotent assignment)
    existing = await db.assignments.find_one(
        {"caregiver_id": req.caregiver_id, "client_id": req.client_id}, {"_id": 0}
    )
    if existing:
        # Ensure Postgres also has it (heal any drift)
        await supa_data.upsert_assignment(existing)
        return Assignment(**existing)
    obj = Assignment(**req.dict())
    await db.assignments.insert_one(obj.dict())
    await supa_data.upsert_assignment(obj.dict())
    return obj


@api.get("/assignments", response_model=List[Assignment])
async def list_assignments(current: UserPublic = Depends(get_current_user)):
    # Phase 4: source from Supabase Postgres
    cg = current.id if current.role == "caregiver" else None
    rows = await supa_data.list_assignments(caregiver_id=cg)
    return [Assignment(**r) for r in rows]


@api.delete("/assignments/{aid}")
async def delete_assignment(aid: str, _: UserPublic = Depends(require_admin)):
    await db.assignments.delete_one({"id": aid})
    await supa_data.delete_assignment(aid)
    return {"ok": True}


# ---------- TRAINING ----------
@api.post("/training", response_model=TrainingItem)
async def create_training(req: TrainingCreate, _: UserPublic = Depends(require_admin)):
    obj = TrainingItem(**req.dict())
    await db.training.insert_one(obj.dict())
    # Slice H: dual-write to Postgres. If a file is attached, upload to Storage
    # using the same documents bucket (training id namespace).
    t_dict = obj.dict()
    fb = t_dict.pop("file_base64", None)
    if fb:
        path = supa_data.upload_document_blob_sync(
            obj.id, fb, obj.mime_type or "video/mp4"
        )
        t_dict["storage_path"] = path
    await supa_data.upsert_training(t_dict)
    return obj


@api.get("/training", response_model=List[TrainingItem])
async def list_training(_: UserPublic = Depends(get_current_user)):
    # Slice H: read from Postgres
    rows = await supa_data.list_training_all()
    return [TrainingItem(**r) for r in rows]


@api.delete("/training/{tid}")
async def delete_training(tid: str, _: UserPublic = Depends(require_admin)):
    await db.training.delete_one({"id": tid})
    await db.training_completions.delete_many({"training_id": tid})
    # Slice H: ON DELETE CASCADE handles completions in Postgres
    await supa_data.delete_training(tid)
    return {"ok": True}


@api.post("/training/{tid}/complete", response_model=TrainingCompletion)
async def complete_training(tid: str, current: UserPublic = Depends(get_current_user)):
    existing = await db.training_completions.find_one(
        {"training_id": tid, "caregiver_id": current.id}
    )
    if existing:
        existing.pop("_id", None)
        # Heal any drift to Postgres
        await supa_data.upsert_training_completion(existing)
        return TrainingCompletion(**existing)
    obj = TrainingCompletion(training_id=tid, caregiver_id=current.id)
    await db.training_completions.insert_one(obj.dict())
    # Slice H: dual-write
    await supa_data.upsert_training_completion(obj.dict())
    return obj


@api.get("/training/completions", response_model=List[TrainingCompletion])
async def list_completions(
    caregiver_id: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    # Slice H: read from Postgres
    cg = current.id if current.role == "caregiver" else caregiver_id
    rows = await supa_data.list_training_completions(caregiver_id=cg)
    return [TrainingCompletion(**r) for r in rows]


# ---------- ONBOARDING ----------
@api.post("/onboarding", response_model=OnboardingStep)
async def create_step(req: OnboardingStepCreate,
                      _: UserPublic = Depends(require_admin)):
    obj = OnboardingStep(**req.dict())
    await db.onboarding.insert_one(obj.dict())
    # Slice F: dual-write
    await supa_data.upsert_onboarding_step(obj.dict())
    return obj


@api.get("/onboarding", response_model=List[OnboardingStep])
async def list_steps(
    caregiver_id: Optional[str] = None,
    current: UserPublic = Depends(get_current_user),
):
    # Slice F: read from Postgres
    cg = current.id if current.role == "caregiver" else caregiver_id
    rows = await supa_data.list_onboarding_steps(caregiver_id=cg)
    return [OnboardingStep(**r) for r in rows]


@api.post("/onboarding/{step_id}/toggle", response_model=OnboardingStep)
async def toggle_step(step_id: str, current: UserPublic = Depends(get_current_user)):
    d = await db.onboarding.find_one({"id": step_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Step not found")
    if current.role == "caregiver" and d["caregiver_id"] != current.id:
        raise HTTPException(403, "Not your step")
    new_completed = not d.get("completed", False)
    completed_at = now_iso() if new_completed else None
    update = {"completed": new_completed, "completed_at": completed_at}
    await db.onboarding.update_one({"id": step_id}, {"$set": update})
    # Slice F: dual-write
    await supa_data.toggle_onboarding_step(step_id, new_completed, completed_at)
    d.update(update)
    return OnboardingStep(**d)


@api.delete("/onboarding/{step_id}")
async def delete_step(step_id: str, _: UserPublic = Depends(require_admin)):
    await db.onboarding.delete_one({"id": step_id})
    # Slice F: dual-delete
    await supa_data.delete_onboarding_step(step_id)
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
        # Slice F: dual-write
        await supa_data.upsert_onboarding_step(step.dict())
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
    # Slice G: dual-write to Postgres
    await supa_data.upsert_packet(pkt)

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
    # Slice G: read from Postgres
    pkt = await supa_data.get_packet_by_token(token_str)
    if not pkt:
        raise HTTPException(404, "Packet not found")
    if not pkt.get("viewed_at"):
        ts = now_iso()
        await db.packet_shares.update_one(
            {"token": token_str}, {"$set": {"viewed_at": ts}}
        )
        await supa_data.mark_packet_viewed(token_str, ts)
        pkt["viewed_at"] = ts
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
        headers={"Content-Disposition": _safe_disposition(f'{d["title"]}.pdf')},
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

    # Lock: signature is now baked into the last page's content stream --
    # strip any pre-existing form interactivity (no-op if there was none).
    _strip_form_interactivity(writer)

    out = io.BytesIO()
    writer.write(out)
    signed_bytes = out.getvalue()
    new_b64 = base64.b64encode(signed_bytes).decode()
    digest = _sha256_hex(signed_bytes)

    signed_doc = Document(
        title=f"{d['title']} (signed by {pkt['recipient_name']})",
        category=d["category"],
        owner_id=pkt["token"],  # link signed copy back to the packet
        owner_type="agency",
        file_base64=new_b64,
        mime_type="application/pdf",
        notes=f"Signed via packet link by {pkt['recipient_name']} on {ts} — SHA-256 {digest[:16]}…",
        uploaded_by="public-share",
        seq=d.get("seq"),
        is_template=False,
        locked=True,
        pdf_sha256=digest,
    )
    await db.documents.insert_one(signed_doc.dict())
    # Slice G: dual-write signed PDF to Postgres + Storage
    signed_dict = signed_doc.dict()
    # owner_id is the packet token (non-UUID) so don't FK-link in PG
    signed_dict["owner_id"] = None
    # uploaded_by is "public-share" (non-UUID) so null it for the FK
    signed_dict["uploaded_by"] = None
    signed_dict["signed_at"] = ts
    signed_dict["signature_image"] = True
    storage_path = supa_data.upload_document_blob_sync(
        signed_doc.id, new_b64, "application/pdf"
    )
    signed_dict["storage_path"] = storage_path
    await supa_data.upsert_document(signed_dict)
    await db.packet_shares.update_one(
        {"token": token_str},
        {"$addToSet": {"signed_ids": doc_id}},
    )
    # Slice G: mirror signed_id to Postgres
    await supa_data.packet_add_signed_id(token_str, doc_id)
    # mark complete if all signed
    total = await db.documents.count_documents({"category": pkt["category"]})
    refreshed = await db.packet_shares.find_one({"token": token_str})
    if refreshed and len(refreshed.get("signed_ids", [])) >= total:
        completion_ts = now_iso()
        await db.packet_shares.update_one(
            {"token": token_str}, {"$set": {"completed_at": completion_ts}}
        )
        await supa_data.mark_packet_completed(token_str, completion_ts)
    return {"ok": True, "signed_doc_id": signed_doc.id}


# ---------- DASHBOARD STATS ----------
async def _dashboard_counts_from_mongo() -> dict:
    """Fallback for get_dashboard_counts() when Postgres/Supabase is
    unreachable (e.g. free-tier auto-pause). Mongo is still the
    authoritative store, so every count here has a live source -- this
    keeps the compliance dashboard up instead of a hard 500."""
    clients = await db.clients.count_documents({})
    caregivers = await db.users.count_documents({"role": "caregiver"})
    documents = await db.documents.count_documents({})
    trainings = await db.training.count_documents({})
    onboarding_total = await db.onboarding.count_documents({})
    onboarding_done = await db.onboarding.count_documents({"completed": True})
    training_completions = await db.training_completions.count_documents({})
    return {
        'clients': clients,
        'caregivers': caregivers,
        'documents': documents,
        'trainings': trainings,
        'onboarding_total': onboarding_total,
        'onboarding_done': onboarding_done,
        'training_completions': training_completions,
    }


@api.get("/stats")
async def stats(current: UserPublic = Depends(get_current_user)):
    # Phase 4: source from Supabase Postgres, with a Mongo fallback so a
    # Postgres/Supabase outage (e.g. free-tier auto-pause) doesn't take the
    # whole compliance dashboard down with an unhandled 500.
    try:
        counts = await supa_data.get_dashboard_counts()
    except Exception as e:
        logger.warning("stats: Postgres dashboard counts failed, falling back to Mongo: %s", e)
        counts = await _dashboard_counts_from_mongo()
    # assignments still in Mongo for now (Phase 4b)
    total_assignments = await db.assignments.count_documents({})

    total_caregivers = counts['caregivers']
    total_training = counts['trainings']
    total_steps = counts['onboarding_total']
    done_steps = counts['onboarding_done']

    expected_completions = total_training * max(total_caregivers, 1)
    actual_completions = counts['training_completions']

    onboard_pct = (done_steps / total_steps * 100) if total_steps else 100
    training_pct = (actual_completions / expected_completions * 100) if expected_completions else 100
    audit_readiness = round((onboard_pct + training_pct) / 2)

    pending_onboarding = total_steps - done_steps
    pending_training = max(0, expected_completions - actual_completions)

    return {
        "total_clients": counts['clients'],
        "total_caregivers": total_caregivers,
        "total_documents": counts['documents'],
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
    user_msg = {
        "id": str(uuid.uuid4()),
        "session_id": req.session_id,
        "user_id": current.id,
        "role": "user",
        "content": req.message,
        "created_at": now_iso(),
    }
    await db.chat_messages.insert_one(user_msg)
    await supa_data.insert_chat_message(user_msg)

    async def event_gen():
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=req.session_id,
            system_message=SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        full_text = ""
        try:
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
            yield "data: [DONE]\n\n"
        finally:
            # Persist the assistant reply even if the client disconnects mid-stream,
            # so /assistant/history is never left with an orphan user message.
            if full_text:
                assistant_msg = {
                    "id": str(uuid.uuid4()),
                    "session_id": req.session_id,
                    "user_id": current.id,
                    "role": "assistant",
                    "content": full_text,
                    "created_at": now_iso(),
                }
                try:
                    await asyncio.shield(db.chat_messages.insert_one(assistant_msg))
                    await asyncio.shield(supa_data.insert_chat_message(assistant_msg))
                except Exception as persist_err:
                    logger.warning(f"Assistant msg persist failed: {persist_err}")

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api.get("/assistant/history/{session_id}")
async def chat_history(session_id: str, current: UserPublic = Depends(get_current_user)):
    # Slice E: read from Postgres
    return await supa_data.list_chat_messages(session_id, current.id)


# ---------- HEALTH ----------
@api.get("/")
async def root():
    return {"ok": True, "service": "HealthGuard Compliance API"}


# ---------- STARTUP: SEED ADMIN ----------
@app.on_event("startup")
async def seed_admin():
    """Seed default admin + caregiver in BOTH Mongo and Supabase Auth.

    Slice I: Supabase users (if missing) are created with the same UUID as
    the Mongo record so cross-DB foreign keys stay aligned.
    """
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
        await supa_data.create_supabase_auth_user(
            user_id=uid, email="admin@healthguard.com",
            password="AdminPassword123!",
            name="Sister to Sister, PHCP", role="admin",
        )
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
        await supa_data.create_supabase_auth_user(
            user_id=uid, email="caregiver@healthguard.com",
            password="Caregiver123!",
            name="Sarah Johnson", role="caregiver",
        )


app.include_router(api)

# Mount modular routers under the same /api prefix
app.include_router(shifts_router.router, prefix="/api")
if ms_graph_router is not None:
    app.include_router(ms_graph_router.router, prefix="/api")
app.include_router(supabase_router.router, prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _start_ms_scheduler():
    if ms_graph_router is not None:
        ms_graph_router.start_scheduler()


@app.on_event("shutdown")
async def _stop_ms_scheduler():
    if ms_graph_router is not None:
        ms_graph_router.stop_scheduler()


@app.on_event("shutdown")
async def _close_push_client():
    if _push_client is not None:
        await _push_client.aclose()


@app.on_event("startup")
async def _start_expiration_reminder_scheduler():
    try:
        _start_expiration_scheduler()
    except Exception as e:  # pragma: no cover - defensive: a scheduling bug
        # (e.g. a bad timezone string) should never take the whole app down
        logger.warning("expiration reminder scheduler failed to start: %s", e)


@app.on_event("shutdown")
async def _stop_expiration_reminder_scheduler():
    try:
        _stop_expiration_scheduler()
    except Exception as e:
        logger.warning("expiration reminder scheduler failed to stop cleanly: %s", e)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
