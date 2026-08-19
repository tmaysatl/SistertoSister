"""Shared Pydantic data models used across routers.

Kept dependency-light (no imports from `routers` or `core.security`) so it can
be imported from anywhere without circular issues.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional
from pydantic import BaseModel, EmailStr, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Auth / user ---
class UserPublic(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: Literal['admin', 'caregiver']
    created_at: str
    photo_base64: Optional[str] = None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: Literal['admin', 'caregiver'] = 'caregiver'


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    user: UserPublic


# --- Clients ---
class Client(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    address: Optional[str] = ''
    phone: Optional[str] = ''
    notes: Optional[str] = ''
    photo_base64: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class ClientCreate(BaseModel):
    name: str
    address: Optional[str] = ''
    phone: Optional[str] = ''
    notes: Optional[str] = ''
    photo_base64: Optional[str] = None


class ClientTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str
    title: str
    description: Optional[str] = ''
    seq: Optional[int] = None
    completed: bool = False
    completed_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


# --- Documents ---
DocCategory = Literal[
    'client', 'caregiver',
    'client_onboarding', 'caregiver_onboarding',
    'credential', 'training', 'policy',
]


class Document(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    category: DocCategory
    owner_id: Optional[str] = None
    owner_type: Optional[Literal['client', 'caregiver', 'agency']] = 'agency'
    file_base64: Optional[str] = None
    mime_type: Optional[str] = 'application/pdf'
    notes: Optional[str] = ''
    uploaded_by: str
    uploaded_at: str = Field(default_factory=now_iso)
    expires_at: Optional[str] = None
    seq: Optional[int] = None
    is_template: bool = False
    # Phase 4 Slice C: pointer to Supabase Storage object. Frontend uses this
    # as the canonical "file is viewable" flag (set after a successful upload).
    storage_path: Optional[str] = None
    # Locked e-signature PDFs: set when this document is a generated,
    # flattened signed record (form fields baked in + interactivity
    # stripped -- see locked_pdf.py). pdf_sha256 is the integrity hash of
    # file_base64 at the moment it was generated, for tamper detection.
    # Both optional/None for every pre-existing document and upload.
    locked: Optional[bool] = None
    pdf_sha256: Optional[str] = None


class DocumentCreate(BaseModel):
    title: str
    category: DocCategory
    owner_id: Optional[str] = None
    owner_type: Optional[Literal['client', 'caregiver', 'agency']] = 'agency'
    file_base64: Optional[str] = None
    mime_type: Optional[str] = 'application/pdf'
    notes: Optional[str] = ''
    expires_at: Optional[str] = None
    seq: Optional[int] = None
    is_template: bool = False


# --- Assignments (caregiver \u2194 client) ---
class Assignment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    caregiver_id: str
    client_id: str
    schedule: Optional[str] = ''
    notes: Optional[str] = ''
    created_at: str = Field(default_factory=now_iso)


class AssignmentCreate(BaseModel):
    caregiver_id: str
    client_id: str
    schedule: Optional[str] = ''
    notes: Optional[str] = ''


# --- Training ---
class TrainingItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = ''
    file_base64: Optional[str] = None
    mime_type: Optional[str] = 'video/mp4'
    required: bool = True
    created_at: str = Field(default_factory=now_iso)


class TrainingCreate(BaseModel):
    title: str
    description: Optional[str] = ''
    file_base64: Optional[str] = None
    mime_type: Optional[str] = 'video/mp4'
    required: bool = True


class TrainingCompletion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    training_id: str
    caregiver_id: str
    completed_at: str = Field(default_factory=now_iso)


# --- Onboarding ---
class OnboardingStep(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    caregiver_id: str
    title: str
    description: Optional[str] = ''
    completed: bool = False
    completed_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class OnboardingStepCreate(BaseModel):
    caregiver_id: str
    title: str
    description: Optional[str] = ''


# --- Shifts ---
class Shift(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    caregiver_id: str
    client_id: str
    kind: Literal['recurring', 'one_off'] = 'one_off'
    date: Optional[str] = None  # YYYY-MM-DD
    weekdays: Optional[List[str]] = None
    recurring_until: Optional[str] = None  # YYYY-MM-DD inclusive
    parent_shift_id: Optional[str] = None
    start_time: str
    end_time: str
    notes: Optional[str] = ''
    service_type: Optional[str] = ''
    status: Literal['scheduled', 'in_progress', 'completed', 'cancelled'] = 'scheduled'
    clocked_in_at: Optional[str] = None
    clocked_out_at: Optional[str] = None
    clock_location: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: Optional[str] = None


class ShiftCreate(BaseModel):
    caregiver_id: str
    client_id: str
    kind: Literal['recurring', 'one_off'] = 'one_off'
    date: Optional[str] = None
    weekdays: Optional[List[str]] = None
    recurring_until: Optional[str] = None
    start_time: str
    end_time: str
    notes: Optional[str] = ''
    service_type: Optional[str] = ''


class ShiftUpdate(BaseModel):
    caregiver_id: Optional[str] = None
    client_id: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    notes: Optional[str] = None
    service_type: Optional[str] = None
    status: Optional[Literal['scheduled', 'in_progress', 'completed', 'cancelled']] = None


class ChatMessageReq(BaseModel):
    session_id: str
    message: str
