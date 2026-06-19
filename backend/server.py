from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, Query
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
import uuid
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt, JWTError
from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Config
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
JWT_SECRET = os.environ['JWT_SECRET_KEY']
JWT_ALG = os.environ.get('JWT_ALGORITHM', 'HS256')
JWT_EXP_MIN = int(os.environ.get('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '10080'))
EMERGENT_LLM_KEY = os.environ['EMERGENT_LLM_KEY']

# DB
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

# Crypto
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

app = FastAPI()
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXP_MIN)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALG)


# ---------- MODELS ----------
class UserPublic(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: Literal["admin", "caregiver"]
    created_at: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: Literal["admin", "caregiver"] = "caregiver"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class Client(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    address: Optional[str] = ""
    phone: Optional[str] = ""
    notes: Optional[str] = ""
    created_at: str = Field(default_factory=now_iso)


class ClientCreate(BaseModel):
    name: str
    address: Optional[str] = ""
    phone: Optional[str] = ""
    notes: Optional[str] = ""


class Document(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    category: Literal["client", "caregiver", "onboarding", "training", "policy"]
    owner_id: Optional[str] = None  # client_id or caregiver_id
    owner_type: Optional[Literal["client", "caregiver", "agency"]] = "agency"
    file_base64: Optional[str] = None  # base64 data uri
    mime_type: Optional[str] = "application/pdf"
    notes: Optional[str] = ""
    uploaded_by: str
    uploaded_at: str = Field(default_factory=now_iso)
    expires_at: Optional[str] = None


class DocumentCreate(BaseModel):
    title: str
    category: Literal["client", "caregiver", "onboarding", "training", "policy"]
    owner_id: Optional[str] = None
    owner_type: Optional[Literal["client", "caregiver", "agency"]] = "agency"
    file_base64: Optional[str] = None
    mime_type: Optional[str] = "application/pdf"
    notes: Optional[str] = ""
    expires_at: Optional[str] = None


class Assignment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    caregiver_id: str
    client_id: str
    schedule: Optional[str] = ""  # e.g. "Mon/Wed/Fri 9am-12pm"
    notes: Optional[str] = ""
    created_at: str = Field(default_factory=now_iso)


class AssignmentCreate(BaseModel):
    caregiver_id: str
    client_id: str
    schedule: Optional[str] = ""
    notes: Optional[str] = ""


class TrainingItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = ""
    file_base64: Optional[str] = None
    mime_type: Optional[str] = "video/mp4"
    required: bool = True
    created_at: str = Field(default_factory=now_iso)


class TrainingCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    file_base64: Optional[str] = None
    mime_type: Optional[str] = "video/mp4"
    required: bool = True


class TrainingCompletion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    training_id: str
    caregiver_id: str
    completed_at: str = Field(default_factory=now_iso)


class OnboardingStep(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    caregiver_id: str
    title: str
    description: Optional[str] = ""
    completed: bool = False
    completed_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class OnboardingStepCreate(BaseModel):
    caregiver_id: str
    title: str
    description: Optional[str] = ""


class ChatMessageReq(BaseModel):
    session_id: str
    message: str


# ---------- AUTH HELPERS ----------
async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        user_id = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    doc = await db.users.find_one({"id": user_id}, {"_id": 0, "hashed_password": 0})
    if not doc:
        raise credentials_exception
    return UserPublic(**doc)


def require_admin(current: UserPublic = Depends(get_current_user)) -> UserPublic:
    if current.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return current


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
    return {"ok": True}


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
            {"category": "onboarding", "owner_id": current.id},
        ]}]}
    docs = await db.documents.find(q, {"_id": 0}).sort("uploaded_at", -1).to_list(500)
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


# ---------- ASSIGNMENTS ----------
@api.post("/assignments", response_model=Assignment)
async def create_assignment(req: AssignmentCreate,
                            _: UserPublic = Depends(require_admin)):
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
            "name": "Agency Owner",
            "role": "admin",
            "hashed_password": hash_password("Admin@123"),
            "created_at": now_iso(),
        })
        logger.info("Seeded default admin: admin@healthguard.com / Admin@123")

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

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
