"""
Dashboard routes with authentication.
"""
import os
import secrets
from datetime import datetime
from functools import wraps
from fastapi import APIRouter, Request, Response, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from stats import get_stats_tracker

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Simple session store (in production, use Redis)
sessions: dict[str, dict] = {}

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

        // Auto-refresh every 30 seconds
        setTimeout(() => window.location.reload(), 30000);
    </script>
</body>
</html>"""
