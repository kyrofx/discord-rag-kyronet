"""
Database operations for platform mode.

Uses MongoDB for storing users, sessions, invite codes, and conversations.
"""
import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from bson import ObjectId

from platform_app.models import UserRole, UserStatus, MessageRole

logger = logging.getLogger(__name__)

# MongoDB connection
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "discord_rag")

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def get_database() -> AsyncIOMotorDatabase:
    """Get or create the MongoDB database connection."""
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(MONGODB_URL)
        _db = _client[MONGODB_DB]
        await _ensure_indexes()
    return _db


async def _ensure_indexes():
    """Create database indexes for performance."""
    db = _db

    # Users collection
    await db.platform_users.create_index("username", unique=True)
    await db.platform_users.create_index("email", unique=True)
    await db.platform_users.create_index("status")

    # Sessions collection
    await db.platform_sessions.create_index("user_id")
    await db.platform_sessions.create_index("expires_at", expireAfterSeconds=0)

    # Invite codes collection
    await db.platform_invite_codes.create_index("code", unique=True)
    await db.platform_invite_codes.create_index("expires_at")

    # Conversations collection
    await db.platform_conversations.create_index("user_id")
    await db.platform_conversations.create_index("updated_at")

    logger.info("Platform database indexes created")


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hash a password with a salt. Returns (hash, salt)."""
    if salt is None:
        salt = secrets.token_hex(32)
    password_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    return password_hash, salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Verify a password against a hash."""
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, password_hash)


def generate_session_token() -> str:
    """Generate a secure session token."""
    return secrets.token_urlsafe(48)


def generate_invite_code() -> str:
    """Generate a unique invite code."""
    return secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16].upper()


# ============== User Operations ==============

async def create_user(
    username: str,
    email: str,
    password: str,
    role: UserRole = UserRole.USER,
    invite_code_used: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new user."""
    db = await get_database()

    password_hash, salt = hash_password(password)

    user_doc = {
        "username": username.lower(),
        "email": email.lower(),
        "password_hash": password_hash,
        "password_salt": salt,
        "role": role.value,
        "status": UserStatus.ACTIVE.value,
        "created_at": datetime.utcnow(),
        "last_login": None,
        "invite_code_used": invite_code_used,
    }

    result = await db.platform_users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return user_doc


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Get a user by ID."""
    db = await get_database()
    return await db.platform_users.find_one({"_id": ObjectId(user_id)})


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Get a user by username."""
    db = await get_database()
    return await db.platform_users.find_one({"username": username.lower()})


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get a user by email."""
    db = await get_database()
    return await db.platform_users.find_one({"email": email.lower()})


async def update_user(user_id: str, updates: Dict[str, Any]) -> bool:
    """Update a user."""
    db = await get_database()
    result = await db.platform_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": updates}
    )
    return result.modified_count > 0


async def update_last_login(user_id: str):
    """Update the last login time for a user."""
    db = await get_database()
    await db.platform_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"last_login": datetime.utcnow()}}
    )


async def change_password(user_id: str, new_password: str) -> bool:
    """Change a user's password."""
    password_hash, salt = hash_password(new_password)
    return await update_user(user_id, {
        "password_hash": password_hash,
        "password_salt": salt
    })


async def list_users(
    skip: int = 0,
    limit: int = 50,
    role: Optional[UserRole] = None,
    status: Optional[UserStatus] = None
) -> List[Dict[str, Any]]:
    """List users with optional filtering."""
    db = await get_database()

    query = {}
    if role:
        query["role"] = role.value
    if status:
        query["status"] = status.value

    cursor = db.platform_users.find(query).skip(skip).limit(limit).sort("created_at", -1)
    return await cursor.to_list(length=limit)


async def count_users(
    role: Optional[UserRole] = None,
    status: Optional[UserStatus] = None,
    since: Optional[datetime] = None
) -> int:
    """Count users with optional filtering."""
    db = await get_database()

    query = {}
    if role:
        query["role"] = role.value
    if status:
        query["status"] = status.value
    if since:
        query["created_at"] = {"$gte": since}

    return await db.platform_users.count_documents(query)


# ============== Session Operations ==============

async def create_session(user_id: str, expires_hours: int = 24 * 7) -> Dict[str, Any]:
    """Create a new session for a user."""
    db = await get_database()

    session_doc = {
        "user_id": user_id,
        "token": generate_session_token(),
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(hours=expires_hours),
    }

    result = await db.platform_sessions.insert_one(session_doc)
    session_doc["_id"] = result.inserted_id
    return session_doc


async def get_session_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Get a session by token."""
    db = await get_database()
    session = await db.platform_sessions.find_one({
        "token": token,
        "expires_at": {"$gt": datetime.utcnow()}
    })
    return session


async def delete_session(token: str) -> bool:
    """Delete a session."""
    db = await get_database()
    result = await db.platform_sessions.delete_one({"token": token})
    return result.deleted_count > 0


async def delete_user_sessions(user_id: str) -> int:
    """Delete all sessions for a user."""
    db = await get_database()
    result = await db.platform_sessions.delete_many({"user_id": user_id})
    return result.deleted_count


# ============== Invite Code Operations ==============

async def create_invite_code(
    created_by: str,
    max_uses: int = 1,
    expires_in_days: Optional[int] = 7,
    note: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new invite code."""
    db = await get_database()

    expires_at = None
    if expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

    code_doc = {
        "code": generate_invite_code(),
        "created_by": created_by,
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,
        "max_uses": max_uses,
        "current_uses": 0,
        "used_by": [],
        "note": note,
        "is_active": True,
    }

    result = await db.platform_invite_codes.insert_one(code_doc)
    code_doc["_id"] = result.inserted_id
    return code_doc


async def get_invite_code(code: str) -> Optional[Dict[str, Any]]:
    """Get an invite code."""
    db = await get_database()
    return await db.platform_invite_codes.find_one({"code": code.upper()})


async def use_invite_code(code: str, user_id: str) -> bool:
    """Mark an invite code as used by a user."""
    db = await get_database()

    invite = await get_invite_code(code)
    if not invite:
        return False

    if not invite["is_active"]:
        return False

    if invite["expires_at"] and invite["expires_at"] < datetime.utcnow():
        return False

    if invite["current_uses"] >= invite["max_uses"]:
        return False

    result = await db.platform_invite_codes.update_one(
        {"code": code.upper()},
        {
            "$inc": {"current_uses": 1},
            "$push": {"used_by": {"user_id": user_id, "used_at": datetime.utcnow()}}
        }
    )
    return result.modified_count > 0


async def validate_invite_code(code: str) -> tuple[bool, str]:
    """Validate an invite code. Returns (is_valid, error_message)."""
    invite = await get_invite_code(code)

    if not invite:
        return False, "Invalid invite code"

    if not invite["is_active"]:
        return False, "This invite code has been deactivated"

    if invite["expires_at"] and invite["expires_at"] < datetime.utcnow():
        return False, "This invite code has expired"

    if invite["current_uses"] >= invite["max_uses"]:
        return False, "This invite code has reached its maximum uses"

    return True, ""


async def deactivate_invite_code(code: str) -> bool:
    """Deactivate an invite code."""
    db = await get_database()
    result = await db.platform_invite_codes.update_one(
        {"code": code.upper()},
        {"$set": {"is_active": False}}
    )
    return result.modified_count > 0


async def list_invite_codes(
    created_by: Optional[str] = None,
    active_only: bool = False,
    skip: int = 0,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """List invite codes."""
    db = await get_database()

    query = {}
    if created_by:
        query["created_by"] = created_by
    if active_only:
        query["is_active"] = True
        query["$or"] = [
            {"expires_at": None},
            {"expires_at": {"$gt": datetime.utcnow()}}
        ]
        query["$expr"] = {"$lt": ["$current_uses", "$max_uses"]}

    cursor = db.platform_invite_codes.find(query).skip(skip).limit(limit).sort("created_at", -1)
    return await cursor.to_list(length=limit)


async def count_active_invite_codes() -> int:
    """Count active invite codes."""
    db = await get_database()
    return await db.platform_invite_codes.count_documents({
        "is_active": True,
        "$or": [
            {"expires_at": None},
            {"expires_at": {"$gt": datetime.utcnow()}}
        ]
    })


# ============== Conversation Operations ==============

async def create_conversation(
    user_id: str,
    title: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new conversation."""
    db = await get_database()

    conversation_doc = {
        "user_id": user_id,
        "title": title or "New Conversation",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "messages": [],
    }

    result = await db.platform_conversations.insert_one(conversation_doc)
    conversation_doc["_id"] = result.inserted_id
    return conversation_doc


async def get_conversation(conversation_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a conversation by ID. Optionally verify user ownership."""
    db = await get_database()

    query = {"_id": ObjectId(conversation_id)}
    if user_id:
        query["user_id"] = user_id

    return await db.platform_conversations.find_one(query)


async def update_conversation(conversation_id: str, user_id: str, updates: Dict[str, Any]) -> bool:
    """Update a conversation."""
    db = await get_database()
    updates["updated_at"] = datetime.utcnow()
    result = await db.platform_conversations.update_one(
        {"_id": ObjectId(conversation_id), "user_id": user_id},
        {"$set": updates}
    )
    return result.modified_count > 0


async def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Delete a conversation."""
    db = await get_database()
    result = await db.platform_conversations.delete_one({
        "_id": ObjectId(conversation_id),
        "user_id": user_id
    })
    return result.deleted_count > 0


async def add_message_to_conversation(
    conversation_id: str,
    user_id: str,
    role: MessageRole,
    content: str,
    thinking: Optional[str] = None,
    sources: Optional[List[dict]] = None,
    metadata: Optional[dict] = None
) -> bool:
    """Add a message to a conversation."""
    db = await get_database()

    message = {
        "role": role.value,
        "content": content,
        "timestamp": datetime.utcnow(),
        "thinking": thinking,
        "sources": sources,
        "metadata": metadata,
    }

    result = await db.platform_conversations.update_one(
        {"_id": ObjectId(conversation_id), "user_id": user_id},
        {
            "$push": {"messages": message},
            "$set": {"updated_at": datetime.utcnow()}
        }
    )
    return result.modified_count > 0


async def list_conversations(
    user_id: str,
    skip: int = 0,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """List conversations for a user."""
    db = await get_database()

    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$sort": {"updated_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$project": {
            "_id": 1,
            "user_id": 1,
            "title": 1,
            "created_at": 1,
            "updated_at": 1,
            "message_count": {"$size": "$messages"},
            "preview": {"$arrayElemAt": ["$messages.content", 0]}
        }}
    ]

    cursor = db.platform_conversations.aggregate(pipeline)
    return await cursor.to_list(length=limit)


async def count_conversations(user_id: Optional[str] = None) -> int:
    """Count conversations."""
    db = await get_database()
    query = {}
    if user_id:
        query["user_id"] = user_id
    return await db.platform_conversations.count_documents(query)


async def count_messages() -> int:
    """Count total messages across all conversations."""
    db = await get_database()
    pipeline = [
        {"$project": {"message_count": {"$size": "$messages"}}},
        {"$group": {"_id": None, "total": {"$sum": "$message_count"}}}
    ]
    result = await db.platform_conversations.aggregate(pipeline).to_list(1)
    return result[0]["total"] if result else 0


async def generate_conversation_title(conversation_id: str, user_id: str) -> str:
    """Generate a title from the first user message."""
    conversation = await get_conversation(conversation_id, user_id)
    if not conversation or not conversation.get("messages"):
        return "New Conversation"

    # Find first user message
    for msg in conversation["messages"]:
        if msg["role"] == "user":
            content = msg["content"]
            # Truncate and clean up
            title = content[:50].strip()
            if len(content) > 50:
                title += "..."
            return title

    return "New Conversation"


# ============== Admin Setup ==============

async def setup_admin_user():
    """Create the initial admin user if it doesn't exist."""
    admin_username = os.getenv("PLATFORM_ADMIN_USER", "admin")
    admin_password = os.getenv("PLATFORM_ADMIN_PASS", "")
    admin_email = os.getenv("PLATFORM_ADMIN_EMAIL", "admin@localhost")

    if not admin_password:
        logger.warning("PLATFORM_ADMIN_PASS not set, skipping admin user creation")
        return None

    existing = await get_user_by_username(admin_username)
    if existing:
        logger.info(f"Admin user '{admin_username}' already exists")
        return existing

    try:
        user = await create_user(
            username=admin_username,
            email=admin_email,
            password=admin_password,
            role=UserRole.ADMIN
        )
        logger.info(f"Created admin user: {admin_username}")
        return user
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}")
        return None
