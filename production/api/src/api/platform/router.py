"""
Platform mode API router.

Provides endpoints for:
- User authentication (login, logout, register)
- Conversation management
- Admin settings and user management
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Request, Response, HTTPException, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from platform.auth import (
    require_user,
    require_admin,
    get_current_user,
    login_user,
    logout_user,
    user_to_response,
)
from platform.database import (
    create_user,
    get_user_by_username,
    get_user_by_email,
    get_user_by_id,
    update_user,
    change_password,
    list_users,
    count_users,
    delete_user_sessions,
    validate_invite_code,
    use_invite_code,
    create_invite_code,
    list_invite_codes,
    deactivate_invite_code,
    count_active_invite_codes,
    create_conversation,
    get_conversation,
    update_conversation,
    delete_conversation,
    list_conversations,
    count_conversations,
    count_messages,
    add_message_to_conversation,
    generate_conversation_title,
    verify_password,
)
from platform.models import (
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
    UserRole,
    UserStatus,
    PasswordChange,
    TokenResponse,
    InviteCodeCreate,
    InviteCodeResponse,
    ConversationCreate,
    ConversationResponse,
    ConversationDetailResponse,
    ConversationUpdate,
    PlatformChatRequest,
    AdminStatsResponse,
    MessageRole,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform", tags=["platform"])


# ============== Authentication ==============

@router.post("/auth/register")
async def register(request: UserCreate):
    """Register a new user with an invite code."""
    # Validate invite code
    is_valid, error = await validate_invite_code(request.invite_code)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Check if username exists
    if await get_user_by_username(request.username):
        raise HTTPException(status_code=400, detail="Username already taken")

    # Check if email exists
    if await get_user_by_email(request.email):
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    user = await create_user(
        username=request.username,
        email=request.email,
        password=request.password,
        invite_code_used=request.invite_code.upper()
    )

    # Mark invite code as used
    await use_invite_code(request.invite_code, str(user["_id"]))

    # Log in the user
    result = await login_user(request.username, request.password)

    response = Response(
        content=json.dumps({
            "user": user_to_response(result["user"]),
            "access_token": result["session"]["token"],
            "expires_at": result["session"]["expires_at"].isoformat()
        }),
        media_type="application/json"
    )

    # Set session cookie
    response.set_cookie(
        key="platform_session",
        value=result["session"]["token"],
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax",
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true"
    )

    return response


@router.post("/auth/login")
async def login(request: UserLogin):
    """Log in a user."""
    result = await login_user(request.username, request.password)

    if not result:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    response = Response(
        content=json.dumps({
            "user": user_to_response(result["user"]),
            "access_token": result["session"]["token"],
            "expires_at": result["session"]["expires_at"].isoformat()
        }),
        media_type="application/json"
    )

    # Set session cookie
    response.set_cookie(
        key="platform_session",
        value=result["session"]["token"],
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax",
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true"
    )

    return response


@router.post("/auth/logout")
async def logout(request: Request):
    """Log out the current user."""
    token = request.cookies.get("platform_session")
    if token:
        await logout_user(token)

    response = Response(content=json.dumps({"status": "ok"}), media_type="application/json")
    response.delete_cookie("platform_session")
    return response


@router.get("/auth/me")
async def get_me(user: dict = Depends(require_user)):
    """Get the current user's info."""
    return user_to_response(user)


@router.post("/auth/change-password")
async def change_user_password(
    request: PasswordChange,
    user: dict = Depends(require_user)
):
    """Change the current user's password."""
    if not verify_password(request.current_password, user["password_hash"], user["password_salt"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    await change_password(str(user["_id"]), request.new_password)
    return {"status": "ok"}


# ============== Conversations ==============

@router.post("/conversations")
async def create_new_conversation(
    request: ConversationCreate = None,
    user: dict = Depends(require_user)
):
    """Create a new conversation."""
    title = request.title if request else None
    conversation = await create_conversation(str(user["_id"]), title)

    return {
        "id": str(conversation["_id"]),
        "user_id": str(user["_id"]),
        "title": conversation["title"],
        "created_at": conversation["created_at"].isoformat(),
        "updated_at": conversation["updated_at"].isoformat(),
        "message_count": 0,
        "preview": None
    }


@router.get("/conversations")
async def list_user_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(require_user)
):
    """List the current user's conversations."""
    conversations = await list_conversations(str(user["_id"]), skip, limit)

    return [{
        "id": str(c["_id"]),
        "user_id": c["user_id"],
        "title": c["title"],
        "created_at": c["created_at"].isoformat(),
        "updated_at": c["updated_at"].isoformat(),
        "message_count": c.get("message_count", 0),
        "preview": c.get("preview", "")[:100] if c.get("preview") else None
    } for c in conversations]


@router.get("/conversations/{conversation_id}")
async def get_user_conversation(
    conversation_id: str,
    user: dict = Depends(require_user)
):
    """Get a specific conversation with messages."""
    conversation = await get_conversation(conversation_id, str(user["_id"]))

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "id": str(conversation["_id"]),
        "user_id": conversation["user_id"],
        "title": conversation["title"],
        "created_at": conversation["created_at"].isoformat(),
        "updated_at": conversation["updated_at"].isoformat(),
        "messages": [{
            "role": m["role"],
            "content": m["content"],
            "timestamp": m["timestamp"].isoformat() if m.get("timestamp") else None,
            "thinking": m.get("thinking"),
            "sources": m.get("sources"),
            "metadata": m.get("metadata")
        } for m in conversation.get("messages", [])]
    }


@router.patch("/conversations/{conversation_id}")
async def update_user_conversation(
    conversation_id: str,
    request: ConversationUpdate,
    user: dict = Depends(require_user)
):
    """Update a conversation (e.g., rename it)."""
    updates = {}
    if request.title is not None:
        updates["title"] = request.title

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    success = await update_conversation(conversation_id, str(user["_id"]), updates)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "ok"}


@router.delete("/conversations/{conversation_id}")
async def delete_user_conversation(
    conversation_id: str,
    user: dict = Depends(require_user)
):
    """Delete a conversation."""
    success = await delete_conversation(conversation_id, str(user["_id"]))
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "ok"}


# ============== Chat (Streaming) ==============

@router.post("/chat")
async def platform_chat(
    request: PlatformChatRequest,
    user: dict = Depends(require_user)
):
    """
    Send a chat message and get a streaming response.

    Returns a stream of Server-Sent Events with the following event types:
    - thinking: Agent's reasoning process
    - tool_call: When the agent calls a tool
    - tool_result: Results from a tool call
    - content: Final answer content (streamed in chunks)
    - sources: Citation sources
    - done: Completion event with metadata
    - error: Error event
    """
    from inference.streaming_chat import get_streaming_inferencer, create_sse_event

    user_id = str(user["_id"])

    # Get or create conversation
    conversation_id = request.conversation_id
    if conversation_id:
        conversation = await get_conversation(conversation_id, user_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conversation = await create_conversation(user_id)
        conversation_id = str(conversation["_id"])

    # Add user message to conversation
    await add_message_to_conversation(
        conversation_id,
        user_id,
        MessageRole.USER,
        request.message
    )

    # Build history from conversation
    conversation = await get_conversation(conversation_id, user_id)
    history = []
    for msg in conversation.get("messages", [])[:-1]:  # Exclude the message we just added
        history.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Get the streaming inferencer
    inferencer = get_streaming_inferencer()

    async def event_stream():
        collected_content = ""
        collected_thinking = ""
        collected_sources = []

        try:
            # Yield conversation ID first
            yield create_sse_event("conversation", {"id": conversation_id})

            # Stream the chat response
            for event in inferencer.chat_stream(
                message=request.message,
                history=history,
                max_iterations=10
            ):
                yield event

                # Parse the event to collect data
                if event.startswith("event: content"):
                    try:
                        data_line = event.split("\n")[1]
                        if data_line.startswith("data: "):
                            data = json.loads(data_line[6:])
                            collected_content += data.get("text", "")
                    except:
                        pass
                elif event.startswith("event: thinking"):
                    try:
                        data_line = event.split("\n")[1]
                        if data_line.startswith("data: "):
                            data = json.loads(data_line[6:])
                            collected_thinking += data.get("content", "") + "\n"
                    except:
                        pass
                elif event.startswith("event: sources"):
                    try:
                        data_line = event.split("\n")[1]
                        if data_line.startswith("data: "):
                            data = json.loads(data_line[6:])
                            collected_sources = data.get("sources", [])
                    except:
                        pass

            # Save assistant response to conversation
            await add_message_to_conversation(
                conversation_id,
                user_id,
                MessageRole.ASSISTANT,
                collected_content,
                thinking=collected_thinking if collected_thinking else None,
                sources=collected_sources if collected_sources else None
            )

            # Auto-generate title if this is the first exchange
            conv = await get_conversation(conversation_id, user_id)
            if conv and len(conv.get("messages", [])) == 2 and conv["title"] == "New Conversation":
                new_title = await generate_conversation_title(conversation_id, user_id)
                await update_conversation(conversation_id, user_id, {"title": new_title})
                yield create_sse_event("title_update", {"title": new_title})

        except Exception as e:
            logger.error(f"Chat stream error: {e}", exc_info=True)
            yield create_sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ============== Admin: User Management ==============

@router.get("/admin/users")
async def admin_list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    role: Optional[UserRole] = None,
    status: Optional[UserStatus] = None,
    admin: dict = Depends(require_admin)
):
    """List all users (admin only)."""
    users = await list_users(skip, limit, role, status)

    return [{
        "id": str(u["_id"]),
        "username": u["username"],
        "email": u["email"],
        "role": u["role"],
        "status": u["status"],
        "created_at": u["created_at"].isoformat(),
        "last_login": u["last_login"].isoformat() if u.get("last_login") else None
    } for u in users]


@router.get("/admin/users/{user_id}")
async def admin_get_user(
    user_id: str,
    admin: dict = Depends(require_admin)
):
    """Get a specific user (admin only)."""
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
        "created_at": user["created_at"].isoformat(),
        "last_login": user["last_login"].isoformat() if user.get("last_login") else None,
        "invite_code_used": user.get("invite_code_used")
    }


@router.patch("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    request: UserUpdate,
    admin: dict = Depends(require_admin)
):
    """Update a user (admin only)."""
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = {}
    if request.email is not None:
        updates["email"] = request.email.lower()
    if request.role is not None:
        updates["role"] = request.role.value
    if request.status is not None:
        updates["status"] = request.status.value
        # If suspending, log out all sessions
        if request.status == UserStatus.SUSPENDED:
            await delete_user_sessions(user_id)

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    await update_user(user_id, updates)
    return {"status": "ok"}


@router.post("/admin/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: str,
    new_password: str = Form(..., min_length=8),
    admin: dict = Depends(require_admin)
):
    """Reset a user's password (admin only)."""
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await change_password(user_id, new_password)
    await delete_user_sessions(user_id)
    return {"status": "ok"}


# ============== Admin: Invite Codes ==============

@router.post("/admin/invite-codes")
async def admin_create_invite_code(
    request: InviteCodeCreate,
    admin: dict = Depends(require_admin)
):
    """Create a new invite code (admin only)."""
    code = await create_invite_code(
        created_by=str(admin["_id"]),
        max_uses=request.max_uses,
        expires_in_days=request.expires_in_days,
        note=request.note
    )

    return {
        "code": code["code"],
        "created_by": code["created_by"],
        "created_at": code["created_at"].isoformat(),
        "expires_at": code["expires_at"].isoformat() if code.get("expires_at") else None,
        "max_uses": code["max_uses"],
        "current_uses": code["current_uses"],
        "note": code.get("note"),
        "is_active": code["is_active"]
    }


@router.get("/admin/invite-codes")
async def admin_list_invite_codes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    active_only: bool = Query(False),
    admin: dict = Depends(require_admin)
):
    """List all invite codes (admin only)."""
    codes = await list_invite_codes(active_only=active_only, skip=skip, limit=limit)

    return [{
        "code": c["code"],
        "created_by": c["created_by"],
        "created_at": c["created_at"].isoformat(),
        "expires_at": c["expires_at"].isoformat() if c.get("expires_at") else None,
        "max_uses": c["max_uses"],
        "current_uses": c["current_uses"],
        "note": c.get("note"),
        "is_active": c["is_active"]
    } for c in codes]


@router.delete("/admin/invite-codes/{code}")
async def admin_deactivate_invite_code(
    code: str,
    admin: dict = Depends(require_admin)
):
    """Deactivate an invite code (admin only)."""
    success = await deactivate_invite_code(code)
    if not success:
        raise HTTPException(status_code=404, detail="Invite code not found")
    return {"status": "ok"}


# ============== Admin: Statistics ==============

@router.get("/admin/stats")
async def admin_get_stats(admin: dict = Depends(require_admin)):
    """Get platform statistics (admin only)."""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)

    total_users = await count_users()
    active_users = await count_users(status=UserStatus.ACTIVE)
    suspended_users = await count_users(status=UserStatus.SUSPENDED)
    total_conversations = await count_conversations()
    total_messages = await count_messages()
    active_invite_codes = await count_active_invite_codes()
    users_today = await count_users(since=today)
    users_this_week = await count_users(since=week_ago)

    return {
        "total_users": total_users,
        "active_users": active_users,
        "suspended_users": suspended_users,
        "total_conversations": total_conversations,
        "total_messages": total_messages,
        "active_invite_codes": active_invite_codes,
        "users_registered_today": users_today,
        "users_registered_this_week": users_this_week
    }


# ============== Admin: System Settings ==============

import redis

def get_redis():
    """Get Redis connection."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    return redis.from_url(redis_url, decode_responses=True)


@router.get("/admin/settings")
async def admin_get_settings(admin: dict = Depends(require_admin)):
    """Get all system settings (admin only)."""
    from dashboard import AVAILABLE_MODELS, THINKING_LEVELS, get_current_model, get_current_thinking

    r = get_redis()

    # Get all settings
    settings = {
        # Model settings
        "model": get_current_model(),
        "thinking": get_current_thinking(),
        "available_models": AVAILABLE_MODELS,
        "thinking_levels": THINKING_LEVELS,

        # Platform settings
        "registration_enabled": r.get("discord_rag:settings:registration_enabled") != "false",
        "max_conversations_per_user": int(r.get("discord_rag:settings:max_conversations") or 100),
        "max_messages_per_conversation": int(r.get("discord_rag:settings:max_messages") or 500),

        # Discord settings (read-only, from env)
        "discord_bot_token_set": bool(os.getenv("DISCORD_BOT_TOKEN")),
        "discord_bot_client_id": os.getenv("DISCORD_BOT_CLIENT_ID", ""),
        "discord_channel_ids": os.getenv("DISCORD_CHANNEL_IDS", "").split(",") if os.getenv("DISCORD_CHANNEL_IDS") else [],

        # Scheduler settings
        "schedule_cron": os.getenv("SCHEDULE_CRON", "0 3 * * *"),
        "quiet_period_minutes": int(os.getenv("QUIET_PERIOD_MINUTES", 15)),
        "backoff_minutes": int(os.getenv("BACKOFF_MINUTES", 10)),

        # API settings
        "api_key_set": bool(os.getenv("API_KEY")),
    }

    return settings


@router.post("/admin/settings/model")
async def admin_update_model(
    model: str = Form(...),
    admin: dict = Depends(require_admin)
):
    """Update the model setting (admin only)."""
    from dashboard import AVAILABLE_MODELS, set_current_model

    valid_ids = [m["id"] for m in AVAILABLE_MODELS]
    if model not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid model. Must be one of: {valid_ids}")

    if set_current_model(model):
        return {"status": "ok", "model": model}
    else:
        raise HTTPException(status_code=500, detail="Failed to save setting")


@router.post("/admin/settings/thinking")
async def admin_update_thinking(
    thinking: str = Form(...),
    admin: dict = Depends(require_admin)
):
    """Update the thinking level setting (admin only)."""
    from dashboard import THINKING_LEVELS, set_current_thinking

    valid_ids = [t["id"] for t in THINKING_LEVELS]
    if thinking not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid thinking level. Must be one of: {valid_ids}")

    if set_current_thinking(thinking):
        return {"status": "ok", "thinking": thinking}
    else:
        raise HTTPException(status_code=500, detail="Failed to save setting")


@router.post("/admin/settings/platform")
async def admin_update_platform_settings(
    registration_enabled: Optional[bool] = Form(None),
    max_conversations_per_user: Optional[int] = Form(None),
    max_messages_per_conversation: Optional[int] = Form(None),
    admin: dict = Depends(require_admin)
):
    """Update platform settings (admin only)."""
    r = get_redis()

    if registration_enabled is not None:
        r.set("discord_rag:settings:registration_enabled", "true" if registration_enabled else "false")

    if max_conversations_per_user is not None:
        if max_conversations_per_user < 1 or max_conversations_per_user > 10000:
            raise HTTPException(status_code=400, detail="max_conversations must be between 1 and 10000")
        r.set("discord_rag:settings:max_conversations", str(max_conversations_per_user))

    if max_messages_per_conversation is not None:
        if max_messages_per_conversation < 1 or max_messages_per_conversation > 10000:
            raise HTTPException(status_code=400, detail="max_messages must be between 1 and 10000")
        r.set("discord_rag:settings:max_messages", str(max_messages_per_conversation))

    return {"status": "ok"}


# ============== Admin: Indexing Control ==============

@router.post("/admin/indexing/run")
async def admin_run_indexing(admin: dict = Depends(require_admin)):
    """Trigger the indexing pipeline (admin only)."""
    from dashboard import indexing_status, _run_indexing_pipeline
    import threading

    if indexing_status["running"]:
        return {
            "status": "already_running",
            "message": "Indexing pipeline is already running",
            "started_at": indexing_status["last_run"]
        }

    # Start indexing in background thread
    thread = threading.Thread(target=_run_indexing_pipeline, daemon=True)
    thread.start()

    return {
        "status": "started",
        "message": "Indexing pipeline started in background"
    }


@router.get("/admin/indexing/status")
async def admin_get_indexing_status(admin: dict = Depends(require_admin)):
    """Get the current indexing pipeline status (admin only)."""
    from dashboard import indexing_status
    return indexing_status


@router.get("/admin/index-stats")
async def admin_get_index_stats(admin: dict = Depends(require_admin)):
    """Get vector index statistics (admin only)."""
    from utils.vector_store import check_index_status, INDEX_NAME

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Get vector index status
    index_status = check_index_status()

    # Get guild stats from Redis (aggregate all guilds)
    total_messages = 0
    total_chunks = index_status.get("num_docs", 0)
    indexed_channels = 0
    oldest_timestamp = None
    newest_timestamp = None
    last_indexed = None

    try:
        r = redis.from_url(redis_url, decode_responses=True)
        # Find all guild stats keys
        guild_keys = r.keys("discord_rag:guild:*:stats")
        for key in guild_keys:
            stats = r.hgetall(key)
            total_messages += int(stats.get("total_messages", 0))
            indexed_channels += int(stats.get("indexed_channels", 0))

            if stats.get("oldest_message"):
                ts = stats.get("oldest_message")
                if oldest_timestamp is None or ts < oldest_timestamp:
                    oldest_timestamp = ts

            if stats.get("newest_message"):
                ts = stats.get("newest_message")
                if newest_timestamp is None or ts > newest_timestamp:
                    newest_timestamp = ts

            if stats.get("last_indexed"):
                li = stats.get("last_indexed")
                if last_indexed is None or li > last_indexed:
                    last_indexed = li
    except Exception:
        pass  # Redis stats are optional

    return {
        "index_name": INDEX_NAME,
        "index_exists": index_status.get("exists", False),
        "vector_chunks": total_chunks,
        "total_messages": total_messages,
        "indexed_channels": indexed_channels,
        "oldest_timestamp": oldest_timestamp,
        "newest_timestamp": newest_timestamp,
        "last_indexed": last_indexed,
        "error": index_status.get("error")
    }


# ============== Admin: Query Stats ==============

@router.get("/admin/query-stats")
async def admin_get_query_stats(admin: dict = Depends(require_admin)):
    """Get query statistics (admin only)."""
    from stats import get_stats_tracker

    tracker = get_stats_tracker()
    stats = tracker.get_stats()
    hourly = tracker.get_recent_queries_count(24)

    return {
        "stats": {
            "total_queries": stats.total_queries,
            "queries_today": stats.queries_today,
            "queries_this_week": stats.queries_this_week,
            "queries_this_month": stats.queries_this_month,
            "avg_response_time_ms": stats.avg_response_time_ms,
            "avg_sources_per_query": stats.avg_sources_per_query,
            "error_count": stats.error_count,
            "last_query_time": stats.last_query_time,
        },
        "hourly": hourly
    }


@router.post("/admin/query-stats/reset")
async def admin_reset_query_stats(admin: dict = Depends(require_admin)):
    """Reset query statistics (admin only)."""
    from stats import get_stats_tracker

    tracker = get_stats_tracker()
    tracker.reset_stats()
    return {"status": "ok", "message": "Stats reset successfully"}


# ============== Admin: Discord Bot Management ==============

@router.get("/admin/discord")
async def admin_get_discord_settings(admin: dict = Depends(require_admin)):
    """Get Discord bot settings (admin only)."""
    r = get_redis()

    # Get channel IDs - prefer Redis override, fall back to env
    channel_ids_redis = r.get("discord_rag:settings:channel_ids")
    if channel_ids_redis:
        channel_ids = [c.strip() for c in channel_ids_redis.split(",") if c.strip()]
    else:
        channel_ids_env = os.getenv("DISCORD_CHANNEL_IDS", "")
        channel_ids = [c.strip() for c in channel_ids_env.split(",") if c.strip()]

    # Get scheduler settings from Redis (or env defaults)
    schedule_cron = r.get("discord_rag:settings:schedule_cron") or os.getenv("SCHEDULE_CRON", "0 3 * * *")
    quiet_period = r.get("discord_rag:settings:quiet_period_minutes") or os.getenv("QUIET_PERIOD_MINUTES", "15")
    backoff = r.get("discord_rag:settings:backoff_minutes") or os.getenv("BACKOFF_MINUTES", "10")

    return {
        "bot_token_set": bool(os.getenv("DISCORD_BOT_TOKEN")),
        "bot_client_id": os.getenv("DISCORD_BOT_CLIENT_ID", ""),
        "channel_ids": channel_ids,
        "schedule_cron": schedule_cron,
        "quiet_period_minutes": int(quiet_period),
        "backoff_minutes": int(backoff),
        "auto_ingest_enabled": r.get("discord_rag:settings:auto_ingest_enabled") != "false",
    }


@router.post("/admin/discord/channels")
async def admin_update_discord_channels(
    channel_ids: str = Form(..., description="Comma-separated channel IDs"),
    admin: dict = Depends(require_admin)
):
    """Update Discord channel IDs to monitor (admin only)."""
    r = get_redis()

    # Validate and clean channel IDs
    ids = [c.strip() for c in channel_ids.split(",") if c.strip()]

    # Store in Redis
    r.set("discord_rag:settings:channel_ids", ",".join(ids))

    return {"status": "ok", "channel_ids": ids}


@router.post("/admin/discord/scheduler")
async def admin_update_scheduler_settings(
    schedule_cron: Optional[str] = Form(None),
    quiet_period_minutes: Optional[int] = Form(None),
    backoff_minutes: Optional[int] = Form(None),
    auto_ingest_enabled: Optional[bool] = Form(None),
    admin: dict = Depends(require_admin)
):
    """Update scheduler settings (admin only)."""
    r = get_redis()

    if schedule_cron is not None:
        # Basic validation of cron expression (5 parts)
        parts = schedule_cron.strip().split()
        if len(parts) != 5:
            raise HTTPException(status_code=400, detail="Invalid cron expression. Must have 5 parts.")
        r.set("discord_rag:settings:schedule_cron", schedule_cron.strip())

    if quiet_period_minutes is not None:
        if quiet_period_minutes < 0 or quiet_period_minutes > 1440:
            raise HTTPException(status_code=400, detail="quiet_period_minutes must be between 0 and 1440")
        r.set("discord_rag:settings:quiet_period_minutes", str(quiet_period_minutes))

    if backoff_minutes is not None:
        if backoff_minutes < 0 or backoff_minutes > 1440:
            raise HTTPException(status_code=400, detail="backoff_minutes must be between 0 and 1440")
        r.set("discord_rag:settings:backoff_minutes", str(backoff_minutes))

    if auto_ingest_enabled is not None:
        r.set("discord_rag:settings:auto_ingest_enabled", "true" if auto_ingest_enabled else "false")

    return {"status": "ok"}


@router.post("/admin/discord/ingest")
async def admin_trigger_ingestion(
    channel_id: Optional[str] = Form(None, description="Specific channel to ingest (optional)"),
    admin: dict = Depends(require_admin)
):
    """Trigger manual message ingestion (admin only)."""
    r = get_redis()

    # Queue an ingestion job
    import json
    from datetime import datetime

    job_id = f"manual_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    job_data = {
        "job_id": job_id,
        "type": "ingest",
        "channel_id": channel_id,
        "triggered_by": admin.get("username", "admin"),
        "triggered_at": datetime.utcnow().isoformat(),
    }

    # Push to ingestion queue
    r.lpush("discord_rag:ingest_queue", json.dumps(job_data))

    return {
        "status": "queued",
        "job_id": job_id,
        "message": f"Ingestion job queued{' for channel ' + channel_id if channel_id else ' for all channels'}"
    }


@router.get("/admin/discord/jobs")
async def admin_get_ingestion_jobs(
    limit: int = Query(10, ge=1, le=50),
    admin: dict = Depends(require_admin)
):
    """Get recent ingestion jobs (admin only)."""
    r = get_redis()

    # Get job history from Redis
    jobs = []
    job_keys = r.keys("discord_rag:job:*")

    for key in sorted(job_keys, reverse=True)[:limit]:
        job_data = r.hgetall(key)
        if job_data:
            jobs.append(job_data)

    return {"jobs": jobs}


@router.get("/admin/discord/guilds")
async def admin_get_indexed_guilds(admin: dict = Depends(require_admin)):
    """Get all indexed guilds with stats (admin only)."""
    r = get_redis()

    guilds = []
    guild_keys = r.keys("discord_rag:guild:*:stats")

    for key in guild_keys:
        guild_id = key.split(":")[2]
        stats = r.hgetall(key)
        guilds.append({
            "guild_id": guild_id,
            "total_messages": int(stats.get("total_messages", 0)),
            "indexed_channels": int(stats.get("indexed_channels", 0)),
            "oldest_message": stats.get("oldest_message"),
            "newest_message": stats.get("newest_message"),
            "last_indexed": stats.get("last_indexed"),
        })

    return {"guilds": guilds}
