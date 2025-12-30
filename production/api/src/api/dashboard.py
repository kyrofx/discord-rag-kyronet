"""
Dashboard routes with authentication.
"""
import os
import secrets
import threading
import logging
from datetime import datetime
from functools import wraps
from fastapi import APIRouter, Request, Response, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from stats import get_stats_tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Simple session store (in production, use Redis)
sessions: dict[str, dict] = {}

# Track indexing job status
indexing_status: dict = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "error": None
}

# Dashboard credentials from environment
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "")


def get_current_user(request: Request) -> Optional[str]:
    """Get current logged-in user from session cookie."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in sessions:
        return sessions[session_id].get("user")
    return None


def require_auth(request: Request) -> str:
    """Dependency that requires authentication."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def render_template(template_name: str, **context) -> str:
    """Simple template rendering."""
    templates = {
        "login": LOGIN_TEMPLATE,
        "dashboard": DASHBOARD_TEMPLATE,
    }
    template = templates.get(template_name, "")
    for key, value in context.items():
        template = template.replace(f"{{{{ {key} }}}}", str(value))
    return template


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    """Show login page."""
    if get_current_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)

    error_html = f'<div class="error">{error}</div>' if error else ""
    return HTMLResponse(render_template("login", error=error_html))


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Process login."""
    if not DASHBOARD_PASS:
        return RedirectResponse(
            url="/dashboard/login?error=Dashboard+password+not+configured",
            status_code=302
        )

    if username == DASHBOARD_USER and password == DASHBOARD_PASS:
        session_id = secrets.token_urlsafe(32)
        sessions[session_id] = {"user": username, "created": datetime.utcnow()}

        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            max_age=86400,  # 24 hours
            samesite="lax"
        )
        return response

    return RedirectResponse(
        url="/dashboard/login?error=Invalid+credentials",
        status_code=302
    )


@router.get("/logout")
async def logout(request: Request):
    """Log out and clear session."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in sessions:
        del sessions[session_id]

    response = RedirectResponse(url="/dashboard/login", status_code=302)
    response.delete_cookie("session_id")
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    import redis

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    tracker = get_stats_tracker()
    stats = tracker.get_stats()
    hourly_data = tracker.get_recent_queries_count(24)

    # Format hourly data for chart
    hours_labels = [h["hour"] for h in hourly_data]
    hours_values = [h["count"] for h in hourly_data]

    # Format last query time
    last_query = "Never"
    if stats.last_query_time:
        try:
            dt = datetime.fromisoformat(stats.last_query_time)
            last_query = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except:
            last_query = stats.last_query_time

    # Get index stats
    from utils.vector_store import check_index_status

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    index_status = check_index_status()

    total_messages = 0
    vector_chunks = index_status.get("num_docs", 0)
    indexed_channels = 0
    oldest_timestamp = None
    newest_timestamp = None
    last_indexed = None
    index_exists = index_status.get("exists", False)
    index_error = index_status.get("error")

    try:
        r = redis.from_url(redis_url, decode_responses=True)
        guild_keys = r.keys("discord_rag:guild:*:stats")
        for key in guild_keys:
            guild_stats = r.hgetall(key)
            total_messages += int(guild_stats.get("total_messages", 0))
            indexed_channels += int(guild_stats.get("indexed_channels", 0))

            if guild_stats.get("oldest_message"):
                ts = guild_stats.get("oldest_message")
                if oldest_timestamp is None or ts < oldest_timestamp:
                    oldest_timestamp = ts

            if guild_stats.get("newest_message"):
                ts = guild_stats.get("newest_message")
                if newest_timestamp is None or ts > newest_timestamp:
                    newest_timestamp = ts

            if guild_stats.get("last_indexed"):
                li = guild_stats.get("last_indexed")
                if last_indexed is None or li > last_indexed:
                    last_indexed = li
    except Exception:
        pass

    # Format date range
    date_range = "N/A"
    if oldest_timestamp and newest_timestamp:
        date_range = f"{oldest_timestamp} - {newest_timestamp}"

    # Format last indexed
    last_indexed_display = "Never"
    if last_indexed:
        try:
            dt = datetime.fromisoformat(last_indexed)
            last_indexed_display = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except:
            last_indexed_display = last_indexed

    # Index status display
    if index_error:
        index_status_display = f"Error: {index_error}"
        index_status_class = "error"
    elif not index_exists:
        index_status_display = "Not Created"
        index_status_class = "warning"
    elif vector_chunks == 0:
        index_status_display = "Empty"
        index_status_class = "warning"
    else:
        index_status_display = "Ready"
        index_status_class = "success"

    # Indexing pipeline status
    indexing_running = indexing_status.get("running", False)
    indexing_last_result = indexing_status.get("last_result", "")

    # Get current model settings
    current_model = get_current_model()
    current_thinking = get_current_thinking()

    # Build model options HTML
    model_options = ""
    for m in AVAILABLE_MODELS:
        selected = "selected" if m["id"] == current_model else ""
        model_options += f'<option value="{m["id"]}" {selected}>{m["name"]}</option>'

    # Build thinking options HTML
    thinking_options = ""
    for t in THINKING_LEVELS:
        selected = "selected" if t["id"] == current_thinking else ""
        thinking_options += f'<option value="{t["id"]}" {selected}>{t["name"]}</option>'

    return HTMLResponse(render_template(
        "dashboard",
        user=user,
        total_queries=stats.total_queries,
        queries_today=stats.queries_today,
        queries_this_week=stats.queries_this_week,
        queries_this_month=stats.queries_this_month,
        avg_response_time=f"{stats.avg_response_time_ms:.0f}",
        avg_sources=f"{stats.avg_sources_per_query:.1f}",
        error_count=stats.error_count,
        last_query=last_query,
        hours_labels=hours_labels,
        hours_values=hours_values,
        # Index stats
        total_messages=total_messages,
        vector_chunks=vector_chunks,
        indexed_channels=indexed_channels,
        date_range=date_range,
        last_indexed=last_indexed_display,
        index_status_display=index_status_display,
        index_status_class=index_status_class,
        indexing_running="true" if indexing_running else "false",
        indexing_last_result=indexing_last_result,
        # Model settings
        model_options=model_options,
        thinking_options=thinking_options,
        current_model=current_model,
        current_thinking=current_thinking,
    ))


@router.get("/api/stats")
async def api_stats(user: str = Depends(require_auth)):
    """Get stats as JSON."""
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


@router.post("/api/reset")
async def reset_stats(user: str = Depends(require_auth)):
    """Reset all statistics."""
    tracker = get_stats_tracker()
    tracker.reset_stats()
    return {"status": "ok", "message": "Stats reset successfully"}


@router.get("/api/index-stats")
async def api_index_stats(user: str = Depends(require_auth)):
    """Get index statistics."""
    from utils.vector_store import check_index_status, INDEX_NAME
    import redis

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
    except Exception as e:
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
        "error": index_status.get("error"),
        "indexing_status": indexing_status
    }


def _run_indexing_pipeline():
    """Run the indexing pipeline in a background thread."""
    global indexing_status

    indexing_status["running"] = True
    indexing_status["error"] = None
    indexing_status["last_run"] = datetime.utcnow().isoformat()

    logger.info("Starting indexing pipeline from dashboard...")

    try:
        # Try to import and run the indexing pipeline directly
        from utils.ingestion import ingest_documents
        from utils.preprocessing import preprocess_documents
        from utils.chunking import chunk_documents
        from utils.vector_store import index_documents_to_redis, check_index_status

        # Check index status before
        status_before = check_index_status()
        logger.info(f"Index status before: exists={status_before['exists']}, num_docs={status_before['num_docs']}")

        # Ingest documents
        logger.info("Ingesting documents from MongoDB...")
        documents = ingest_documents()

        if len(documents) == 0:
            indexing_status["last_result"] = "failed"
            indexing_status["error"] = "No documents found in MongoDB"
            logger.warning("No documents found in the database")
            return

        logger.info(f"Found {len(documents)} documents")

        # Preprocess
        logger.info("Preprocessing documents...")
        preprocessed = preprocess_documents(documents)
        logger.info(f"Preprocessing complete: {len(preprocessed)} conversation chunks")

        if len(preprocessed) == 0:
            indexing_status["last_result"] = "failed"
            indexing_status["error"] = "No documents left after preprocessing"
            return

        # Chunk and index in batches
        BATCH_SIZE = 10
        total_indexed = 0
        errors = 0

        for i in range(0, len(preprocessed), BATCH_SIZE):
            batch = preprocessed[i:i+BATCH_SIZE]
            try:
                chunks = chunk_documents(batch)
                if chunks:
                    index_documents_to_redis(chunks)
                    total_indexed += len(chunks)
                    logger.info(f"Indexed batch {i//BATCH_SIZE + 1}: {len(chunks)} chunks")
            except Exception as e:
                errors += 1
                logger.error(f"Error indexing batch {i//BATCH_SIZE + 1}: {e}")

        # Check status after
        status_after = check_index_status()
        logger.info(f"Index status after: exists={status_after['exists']}, num_docs={status_after['num_docs']}")

        indexing_status["last_result"] = "success"
        indexing_status["error"] = None if errors == 0 else f"{errors} batch errors"
        logger.info(f"Indexing complete: {total_indexed} chunks indexed, {errors} errors")

    except Exception as e:
        indexing_status["last_result"] = "failed"
        indexing_status["error"] = str(e)
        logger.exception(f"Indexing pipeline failed: {e}")
    finally:
        indexing_status["running"] = False


@router.post("/api/run-indexing")
async def run_indexing(user: str = Depends(require_auth)):
    """Trigger the indexing pipeline."""
    global indexing_status

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


@router.get("/api/indexing-status")
async def get_indexing_status(user: str = Depends(require_auth)):
    """Get the current indexing pipeline status."""
    return indexing_status


# Available Gemini models
AVAILABLE_MODELS = [
    {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash", "description": "Fast Gemini 3 model"},
    {"id": "gemini-3-pro-preview", "name": "Gemini 3 Pro", "description": "Most capable Gemini 3 model"},
    {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "description": "Fast, efficient model"},
    {"id": "gemini-2.5-flash-preview-05-20", "name": "Gemini 2.5 Flash Preview", "description": "Latest 2.5 flash"},
    {"id": "gemini-2.5-pro-preview-05-06", "name": "Gemini 2.5 Pro Preview", "description": "Capable 2.5 model"},
]

THINKING_LEVELS = [
    {"id": "low", "name": "Low", "description": "Minimal reasoning, fastest"},
    {"id": "medium", "name": "Medium", "description": "Balanced reasoning (Flash only)"},
    {"id": "high", "name": "High", "description": "Maximum reasoning depth (default)"},
]

DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_THINKING = "low"


def get_current_model() -> str:
    """Get the currently configured model from Redis."""
    import redis
    try:
        r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        model = r.get("discord_rag:settings:model")
        return model if model else DEFAULT_MODEL
    except Exception:
        return DEFAULT_MODEL


def get_current_thinking() -> str:
    """Get the currently configured thinking level from Redis."""
    import redis
    try:
        r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        thinking = r.get("discord_rag:settings:thinking")
        return thinking if thinking else DEFAULT_THINKING
    except Exception:
        return DEFAULT_THINKING


def set_current_model(model_id: str) -> bool:
    """Set the current model in Redis."""
    import redis
    try:
        r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        r.set("discord_rag:settings:model", model_id)
        return True
    except Exception:
        return False


def set_current_thinking(thinking_level: str) -> bool:
    """Set the current thinking level in Redis."""
    import redis
    try:
        r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        r.set("discord_rag:settings:thinking", thinking_level)
        return True
    except Exception:
        return False


@router.get("/api/settings")
async def get_settings(user: str = Depends(require_auth)):
    """Get current settings."""
    return {
        "model": get_current_model(),
        "thinking": get_current_thinking(),
        "available_models": AVAILABLE_MODELS,
        "thinking_levels": THINKING_LEVELS
    }


@router.post("/api/settings/model")
async def update_model(user: str = Depends(require_auth), model: str = Form(...)):
    """Update the model setting."""
    valid_ids = [m["id"] for m in AVAILABLE_MODELS]
    if model not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid model. Must be one of: {valid_ids}")

    if set_current_model(model):
        return {"status": "ok", "model": model}
    else:
        raise HTTPException(status_code=500, detail="Failed to save setting")


@router.post("/api/settings/thinking")
async def update_thinking(user: str = Depends(require_auth), thinking: str = Form(...)):
    """Update the thinking level setting."""
    valid_ids = [t["id"] for t in THINKING_LEVELS]
    if thinking not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid thinking level. Must be one of: {valid_ids}")

    if set_current_thinking(thinking):
        return {"status": "ok", "thinking": thinking}
    else:
        raise HTTPException(status_code=500, detail="Failed to save setting")


# HTML Templates
LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Discord RAG - Login</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-card {
            background: white;
            padding: 2rem;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 1.5rem;
            font-size: 1.5rem;
        }
        .logo {
            text-align: center;
            font-size: 3rem;
            margin-bottom: 1rem;
        }
        .form-group {
            margin-bottom: 1rem;
        }
        label {
            display: block;
            margin-bottom: 0.5rem;
            color: #555;
            font-weight: 500;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 0.75rem;
            border: 2px solid #e1e1e1;
            border-radius: 8px;
            font-size: 1rem;
            transition: border-color 0.2s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            width: 100%;
            padding: 0.75rem;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }
        .error {
            background: #fee;
            color: #c00;
            padding: 0.75rem;
            border-radius: 8px;
            margin-bottom: 1rem;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">📊</div>
        <h1>Discord RAG Dashboard</h1>
        {{ error }}
        <form method="POST" action="/dashboard/login">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autocomplete="username">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
            </div>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>"""

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Discord RAG - Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 1.25rem; }
        .header-right {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        .user-badge {
            background: rgba(255,255,255,0.2);
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-size: 0.875rem;
        }
        .logout-btn {
            background: rgba(255,255,255,0.2);
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            text-decoration: none;
            font-size: 0.875rem;
            transition: background 0.2s;
        }
        .logout-btn:hover { background: rgba(255,255,255,0.3); }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .stat-card {
            background: white;
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .stat-label {
            color: #888;
            font-size: 0.875rem;
            margin-bottom: 0.5rem;
        }
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            color: #333;
        }
        .stat-value.highlight { color: #667eea; }
        .stat-value.success { color: #22c55e; }
        .stat-value.warning { color: #f59e0b; }
        .stat-value.error { color: #ef4444; }
        .chart-container {
            background: white;
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            margin-bottom: 2rem;
        }
        .chart-title {
            font-size: 1.125rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: #333;
        }
        .actions {
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
        }
        .btn {
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-size: 0.875rem;
            font-weight: 600;
            cursor: pointer;
            border: none;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .btn:hover {
            transform: translateY(-2px);
        }
        .btn-primary {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .btn-danger {
            background: #ef4444;
            color: white;
        }
        .btn-secondary {
            background: #e5e7eb;
            color: #374151;
        }
        .info-text {
            color: #888;
            font-size: 0.875rem;
            margin-top: 1rem;
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        .section-header h2 {
            font-size: 1.25rem;
            color: #333;
            margin: 0;
        }
        .section-actions {
            display: flex;
            gap: 0.5rem;
        }
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        .btn-success {
            background: #22c55e;
            color: white;
        }
        .spinner {
            display: inline-block;
            width: 14px;
            height: 14px;
            border: 2px solid rgba(255,255,255,0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
            margin-right: 0.5rem;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .alert {
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        .alert-info {
            background: #dbeafe;
            color: #1e40af;
        }
        .alert-success {
            background: #dcfce7;
            color: #166534;
        }
        .alert-error {
            background: #fee2e2;
            color: #991b1b;
        }
        .endpoints {
            background: white;
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            margin-bottom: 2rem;
        }
        .endpoint {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 0.75rem 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .endpoint:last-child { border-bottom: none; }
        .method {
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
            min-width: 50px;
            text-align: center;
        }
        .method.get { background: #dcfce7; color: #166534; }
        .method.post { background: #dbeafe; color: #1e40af; }
        .path { font-family: monospace; color: #333; }
        .desc { color: #888; font-size: 0.875rem; margin-left: auto; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Discord RAG Dashboard</h1>
        <div class="header-right">
            <span class="user-badge">👤 {{ user }}</span>
            <a href="/dashboard/logout" class="logout-btn">Logout</a>
        </div>
    </div>

    <div class="container">
        <!-- Index Stats Section -->
        <div class="section-header">
            <h2>📚 Index Status</h2>
            <div class="section-actions">
                <button class="btn btn-primary" id="runIndexingBtn" onclick="runIndexing()">
                    <span id="indexingBtnText">🔄 Run Indexing</span>
                </button>
            </div>
        </div>
        <div class="stats-grid" style="margin-bottom: 2rem;">
            <div class="stat-card">
                <div class="stat-label">Index Status</div>
                <div class="stat-value {{ index_status_class }}">{{ index_status_display }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Messages</div>
                <div class="stat-value highlight">{{ total_messages }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Vector Chunks</div>
                <div class="stat-value">{{ vector_chunks }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Indexed Channels</div>
                <div class="stat-value">{{ indexed_channels }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Date Range</div>
                <div class="stat-value" style="font-size: 0.75rem;">{{ date_range }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Last Indexed</div>
                <div class="stat-value" style="font-size: 0.75rem;">{{ last_indexed }}</div>
            </div>
        </div>

        <!-- Query Stats Section -->
        <div class="section-header">
            <h2>📊 Query Statistics</h2>
        </div>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Queries</div>
                <div class="stat-value highlight">{{ total_queries }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Today</div>
                <div class="stat-value">{{ queries_today }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">This Week</div>
                <div class="stat-value">{{ queries_this_week }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">This Month</div>
                <div class="stat-value">{{ queries_this_month }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Avg Response Time</div>
                <div class="stat-value success">{{ avg_response_time }}ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Avg Sources/Query</div>
                <div class="stat-value">{{ avg_sources }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Errors</div>
                <div class="stat-value error">{{ error_count }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Last Query</div>
                <div class="stat-value" style="font-size: 0.875rem;">{{ last_query }}</div>
            </div>
        </div>

        <div class="chart-container">
            <div class="chart-title">Queries (Last 24 Hours)</div>
            <canvas id="hourlyChart" height="100"></canvas>
        </div>

        <div class="endpoints">
            <div class="chart-title">API Endpoints</div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <span class="path">/health</span>
                <span class="desc">Health check</span>
            </div>
            <div class="endpoint">
                <span class="method post">POST</span>
                <span class="path">/infer</span>
                <span class="desc">RAG inference (form: text)</span>
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <span class="path">/dashboard</span>
                <span class="desc">This dashboard</span>
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <span class="path">/dashboard/api/stats</span>
                <span class="desc">Get stats as JSON</span>
            </div>
            <div class="endpoint">
                <span class="method post">POST</span>
                <span class="path">/dashboard/api/reset</span>
                <span class="desc">Reset statistics</span>
            </div>
        </div>

        <!-- Model Settings Section -->
        <div class="endpoints" style="margin-bottom: 2rem;">
            <div class="chart-title">Model Settings</div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1rem;">
                <div>
                    <label style="display: block; margin-bottom: 0.5rem; color: #555; font-weight: 500;">Model</label>
                    <select id="modelSelect" style="width: 100%; padding: 0.75rem; border: 2px solid #e1e1e1; border-radius: 8px; font-size: 1rem;">
                        {{ model_options }}
                    </select>
                </div>
                <div>
                    <label style="display: block; margin-bottom: 0.5rem; color: #555; font-weight: 500;">Thinking Level</label>
                    <select id="thinkingSelect" style="width: 100%; padding: 0.75rem; border: 2px solid #e1e1e1; border-radius: 8px; font-size: 1rem;">
                        {{ thinking_options }}
                    </select>
                </div>
            </div>
            <div style="margin-top: 1rem;">
                <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
                <span id="settingsStatus" style="margin-left: 1rem; color: #22c55e; display: none;">Saved!</span>
            </div>
            <p style="color: #888; font-size: 0.875rem; margin-top: 0.75rem;">
                Note: Thinking is only supported for Gemini 3 models. Medium level is only available for Flash.
            </p>
        </div>

        <div class="actions">
            <button class="btn btn-primary" onclick="refreshStats()">Refresh Stats</button>
            <button class="btn btn-danger" onclick="resetStats()">Reset Stats</button>
        </div>

        <p class="info-text">Stats are stored in Redis and persist across restarts. Dashboard auto-refreshes every 30 seconds.</p>
    </div>

    <script>
        const hourlyLabels = {{ hours_labels }};
        const hourlyData = {{ hours_values }};

        const ctx = document.getElementById('hourlyChart').getContext('2d');
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: hourlyLabels,
                datasets: [{
                    label: 'Queries',
                    data: hourlyData,
                    backgroundColor: 'rgba(102, 126, 234, 0.8)',
                    borderRadius: 4,
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: { beginAtZero: true, ticks: { stepSize: 1 } }
                }
            }
        });

        function refreshStats() {
            window.location.reload();
        }

        async function resetStats() {
            if (!confirm('Are you sure you want to reset all statistics?')) return;

            const response = await fetch('/dashboard/api/reset', { method: 'POST' });
            if (response.ok) {
                alert('Stats reset successfully!');
                window.location.reload();
            } else {
                alert('Failed to reset stats');
            }
        }

        async function saveSettings() {
            const model = document.getElementById('modelSelect').value;
            const thinking = document.getElementById('thinkingSelect').value;
            const statusEl = document.getElementById('settingsStatus');

            try {
                // Save model
                const modelForm = new FormData();
                modelForm.append('model', model);
                const modelRes = await fetch('/dashboard/api/settings/model', {
                    method: 'POST',
                    body: modelForm
                });

                // Save thinking
                const thinkingForm = new FormData();
                thinkingForm.append('thinking', thinking);
                const thinkingRes = await fetch('/dashboard/api/settings/thinking', {
                    method: 'POST',
                    body: thinkingForm
                });

                if (modelRes.ok && thinkingRes.ok) {
                    statusEl.style.display = 'inline';
                    statusEl.textContent = 'Saved!';
                    statusEl.style.color = '#22c55e';
                    setTimeout(() => { statusEl.style.display = 'none'; }, 3000);
                } else {
                    statusEl.style.display = 'inline';
                    statusEl.textContent = 'Failed to save';
                    statusEl.style.color = '#ef4444';
                }
            } catch (error) {
                statusEl.style.display = 'inline';
                statusEl.textContent = 'Error: ' + error.message;
                statusEl.style.color = '#ef4444';
            }
        }

        // Indexing status
        let indexingRunning = {{ indexing_running }};

        async function runIndexing() {
            const btn = document.getElementById('runIndexingBtn');
            const btnText = document.getElementById('indexingBtnText');

            if (indexingRunning) {
                alert('Indexing is already running!');
                return;
            }

            if (!confirm('This will rebuild the vector index from all messages in MongoDB. Continue?')) {
                return;
            }

            btn.disabled = true;
            btnText.innerHTML = '<span class="spinner"></span>Starting...';

            try {
                const response = await fetch('/dashboard/api/run-indexing', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'started') {
                    indexingRunning = true;
                    btnText.innerHTML = '<span class="spinner"></span>Indexing...';
                    alert('Indexing started! This may take several minutes. The page will refresh when complete.');
                    checkIndexingStatus();
                } else if (data.status === 'already_running') {
                    alert('Indexing is already running!');
                    btnText.innerHTML = '<span class="spinner"></span>Indexing...';
                    checkIndexingStatus();
                } else {
                    btn.disabled = false;
                    btnText.textContent = '🔄 Run Indexing';
                    alert('Failed to start indexing: ' + (data.message || 'Unknown error'));
                }
            } catch (error) {
                btn.disabled = false;
                btnText.textContent = '🔄 Run Indexing';
                alert('Error: ' + error.message);
            }
        }

        async function checkIndexingStatus() {
            try {
                const response = await fetch('/dashboard/api/indexing-status');
                const status = await response.json();

                if (status.running) {
                    // Still running, check again in 5 seconds
                    setTimeout(checkIndexingStatus, 5000);
                } else {
                    // Done, refresh the page
                    window.location.reload();
                }
            } catch (error) {
                // On error, just reload
                setTimeout(() => window.location.reload(), 5000);
            }
        }

        // If indexing was running on page load, monitor it
        if (indexingRunning) {
            const btn = document.getElementById('runIndexingBtn');
            const btnText = document.getElementById('indexingBtnText');
            btn.disabled = true;
            btnText.innerHTML = '<span class="spinner"></span>Indexing...';
            checkIndexingStatus();
        }

        // Auto-refresh every 30 seconds
        setTimeout(() => window.location.reload(), 30000);
    </script>
</body>
</html>"""
