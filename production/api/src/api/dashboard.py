"""
Dashboard routes with authentication.
"""
import os
import secrets
import subprocess
import threading
from datetime import datetime
from functools import wraps
from fastapi import APIRouter, Request, Response, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from stats import get_stats_tracker

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

    try:
        # Run the indexing pipeline
        result = subprocess.run(
            ["python", "-m", "indexing_pipeline.main"],
            cwd="/app/indexing_pipeline",  # Adjust path as needed for your deployment
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )

        if result.returncode == 0:
            indexing_status["last_result"] = "success"
            indexing_status["error"] = None
        else:
            indexing_status["last_result"] = "failed"
            indexing_status["error"] = result.stderr[-1000:] if result.stderr else "Unknown error"

    except subprocess.TimeoutExpired:
        indexing_status["last_result"] = "timeout"
        indexing_status["error"] = "Indexing timed out after 1 hour"
    except FileNotFoundError:
        indexing_status["last_result"] = "failed"
        indexing_status["error"] = "Indexing pipeline not found. Make sure the indexing_pipeline package is installed."
    except Exception as e:
        indexing_status["last_result"] = "failed"
        indexing_status["error"] = str(e)
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

        <div class="actions">
            <button class="btn btn-primary" onclick="refreshStats()">🔄 Refresh Stats</button>
            <button class="btn btn-danger" onclick="resetStats()">🗑️ Reset Stats</button>
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
