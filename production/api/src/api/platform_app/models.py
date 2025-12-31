"""
Pydantic models for the platform mode.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class UserStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


# ============== User Models ==============

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=5)
    password: str = Field(..., min_length=8)
    invite_code: str = Field(..., min_length=8)


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: UserRole
    status: UserStatus
    created_at: datetime
    last_login: Optional[datetime] = None


class UserUpdate(BaseModel):
    email: Optional[str] = None
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


# ============== Auth Models ==============

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime


# ============== Invite Code Models ==============

class InviteCodeCreate(BaseModel):
    max_uses: int = Field(1, ge=1, le=100)
    expires_in_days: Optional[int] = Field(7, ge=1, le=365)
    note: Optional[str] = Field(None, max_length=200)


class InviteCodeResponse(BaseModel):
    code: str
    created_by: str
    created_at: datetime
    expires_at: Optional[datetime]
    max_uses: int
    current_uses: int
    note: Optional[str] = None
    is_active: bool


# ============== Conversation Models ==============

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationMessage(BaseModel):
    role: MessageRole
    content: str
    timestamp: datetime
    thinking: Optional[str] = None
    sources: Optional[List[dict]] = None
    metadata: Optional[dict] = None


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    preview: Optional[str] = None


class ConversationDetailResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: List[ConversationMessage]


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


# ============== Chat Models ==============

class PlatformChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: Optional[str] = None


class PlatformChatResponse(BaseModel):
    conversation_id: str
    message_id: str


# ============== Admin Models ==============

class AdminStatsResponse(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    total_conversations: int
    total_messages: int
    active_invite_codes: int
    users_registered_today: int
    users_registered_this_week: int


class SystemSettingsUpdate(BaseModel):
    registration_enabled: Optional[bool] = None
    max_conversations_per_user: Optional[int] = None
    max_messages_per_conversation: Optional[int] = None
