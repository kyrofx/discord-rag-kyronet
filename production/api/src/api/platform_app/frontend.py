"""
Platform mode frontend - ChatGPT-style interface.

Provides a modern chat interface with:
- Login/Register pages
- Conversation sidebar
- Streaming chat with chain-of-thought visibility
- Admin dashboard
"""
import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from platform_app.auth import get_current_user, require_user, require_admin

router = APIRouter(tags=["platform-ui"])

# App name from environment
APP_NAME = os.getenv("PLATFORM_APP_NAME", "Discord RAG Chat")


def render_page(title: str, content: str, include_chat_js: bool = False) -> str:
    """Render a full HTML page with common styles."""
    chat_js = CHAT_JS if include_chat_js else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - {APP_NAME}</title>
    <style>{BASE_STYLES}</style>
</head>
<body>
    {content}
    {chat_js}
</body>
</html>"""


# ============== Routes ==============

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page - redirect to chat or login."""
    user = await get_current_user(request, None)
    if user:
        return RedirectResponse(url="/chat", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = "", registered: str = ""):
    """Login page."""
    user = await get_current_user(request, None)
    if user:
        return RedirectResponse(url="/chat", status_code=302)

    error_html = f'<div class="alert alert-error">{error}</div>' if error else ""
    success_html = f'<div class="alert alert-success">Account created! Please log in.</div>' if registered else ""

    return HTMLResponse(render_page("Login", f"""
    <div class="auth-container">
        <div class="auth-card">
            <div class="auth-header">
                <div class="logo">Chat</div>
                <h1>{APP_NAME}</h1>
                <p>Sign in to continue</p>
            </div>
            {error_html}
            {success_html}
            <form id="loginForm" class="auth-form">
                <div class="form-group">
                    <label for="username">Username</label>
                    <input type="text" id="username" name="username" required autocomplete="username">
                </div>
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" name="password" required autocomplete="current-password">
                </div>
                <button type="submit" class="btn btn-primary btn-block">Sign In</button>
            </form>
            <div class="auth-footer">
                <p>Don't have an account? <a href="/register">Register with invite code</a></p>
            </div>
        </div>
    </div>
    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = e.target.querySelector('button');
            btn.disabled = true;
            btn.textContent = 'Signing in...';

            try {{
                const res = await fetch('/platform/auth/login', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value
                    }})
                }});

                if (res.ok) {{
                    window.location.href = '/chat';
                }} else {{
                    const data = await res.json();
                    alert(data.detail || 'Login failed');
                    btn.disabled = false;
                    btn.textContent = 'Sign In';
                }}
            }} catch (err) {{
                alert('Network error');
                btn.disabled = false;
                btn.textContent = 'Sign In';
            }}
        }});
    </script>
    """))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, error: str = ""):
    """Registration page."""
    user = await get_current_user(request, None)
    if user:
        return RedirectResponse(url="/chat", status_code=302)

    error_html = f'<div class="alert alert-error">{error}</div>' if error else ""

    return HTMLResponse(render_page("Register", f"""
    <div class="auth-container">
        <div class="auth-card">
            <div class="auth-header">
                <div class="logo">Chat</div>
                <h1>Join {APP_NAME}</h1>
                <p>Create your account</p>
            </div>
            {error_html}
            <form id="registerForm" class="auth-form">
                <div class="form-group">
                    <label for="invite_code">Invite Code</label>
                    <input type="text" id="invite_code" name="invite_code" required placeholder="Enter your invite code">
                </div>
                <div class="form-group">
                    <label for="username">Username</label>
                    <input type="text" id="username" name="username" required minlength="3" maxlength="50" autocomplete="username">
                </div>
                <div class="form-group">
                    <label for="email">Email</label>
                    <input type="email" id="email" name="email" required autocomplete="email">
                </div>
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" name="password" required minlength="8" autocomplete="new-password">
                    <small>Minimum 8 characters</small>
                </div>
                <button type="submit" class="btn btn-primary btn-block">Create Account</button>
            </form>
            <div class="auth-footer">
                <p>Already have an account? <a href="/login">Sign in</a></p>
            </div>
        </div>
    </div>
    <script>
        document.getElementById('registerForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = e.target.querySelector('button');
            btn.disabled = true;
            btn.textContent = 'Creating account...';

            try {{
                const res = await fetch('/platform/auth/register', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        invite_code: document.getElementById('invite_code').value,
                        username: document.getElementById('username').value,
                        email: document.getElementById('email').value,
                        password: document.getElementById('password').value
                    }})
                }});

                if (res.ok) {{
                    window.location.href = '/chat';
                }} else {{
                    const data = await res.json();
                    alert(data.detail || 'Registration failed');
                    btn.disabled = false;
                    btn.textContent = 'Create Account';
                }}
            }} catch (err) {{
                alert('Network error');
                btn.disabled = false;
                btn.textContent = 'Create Account';
            }}
        }});
    </script>
    """))


@router.get("/chat", response_class=HTMLResponse)
@router.get("/chat/{conversation_id}", response_class=HTMLResponse)
async def chat_page(request: Request, conversation_id: str = None):
    """Main chat interface."""
    user = await get_current_user(request, None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    is_admin = user.get("role") == "admin"
    admin_link = '<a href="/admin" class="sidebar-link">Admin</a>' if is_admin else ""

    return HTMLResponse(render_page("Chat", f"""
    <div class="chat-layout">
        <!-- Sidebar -->
        <aside class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <button class="btn btn-primary new-chat-btn" onclick="newConversation()">
                    + New Chat
                </button>
            </div>
            <div class="conversations-list" id="conversationsList">
                <!-- Conversations loaded via JS -->
            </div>
            <div class="sidebar-footer">
                <div class="user-info">
                    <span class="user-avatar">U</span>
                    <span class="user-name">{user.get('username', 'User')}</span>
                </div>
                {admin_link}
                <a href="#" onclick="logout()" class="sidebar-link">Logout</a>
            </div>
        </aside>

        <!-- Main Chat Area -->
        <main class="chat-main">
            <header class="chat-header">
                <button class="sidebar-toggle" onclick="toggleSidebar()">=</button>
                <h1 id="chatTitle">New Chat</h1>
            </header>

            <div class="messages-container" id="messagesContainer">
                <div class="welcome-message" id="welcomeMessage">
                    <div class="welcome-icon"></div>
                    <h2>Welcome to {APP_NAME}</h2>
                    <p>Ask questions about the Discord chat history. I'll search through messages and provide answers with sources.</p>
                </div>
            </div>

            <!-- Thinking/Status Panel -->
            <div class="thinking-panel" id="thinkingPanel" style="display: none;">
                <div class="thinking-header">
                    <span class="thinking-icon">...</span>
                    <span class="thinking-title">Searching...</span>
                </div>
                <div class="thinking-content" id="thinkingContent"></div>
            </div>

            <!-- Input Area -->
            <div class="input-container">
                <form id="chatForm" class="chat-input-form">
                    <textarea
                        id="messageInput"
                        placeholder="Ask a question..."
                        rows="1"
                        onkeydown="handleKeyDown(event)"
                    ></textarea>
                    <button type="submit" id="sendBtn" class="send-btn">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
                        </svg>
                    </button>
                </form>
            </div>
        </main>
    </div>
    <script>
        const INITIAL_CONVERSATION_ID = {f'"{conversation_id}"' if conversation_id else 'null'};
    </script>
    """, include_chat_js=True))


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin dashboard."""
    user = await get_current_user(request, None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse(url="/chat", status_code=302)

    return HTMLResponse(render_page("Admin Dashboard", f"""
    <div class="admin-layout">
        <aside class="admin-sidebar">
            <div class="admin-logo">
                <a href="/chat">{APP_NAME}</a>
            </div>
            <nav class="admin-nav">
                <a href="#stats" class="admin-nav-item active" onclick="showSection('stats')">Statistics</a>
                <a href="#users" class="admin-nav-item" onclick="showSection('users')">Users</a>
                <a href="#invites" class="admin-nav-item" onclick="showSection('invites')">Invite Codes</a>
                <a href="#discord" class="admin-nav-item" onclick="showSection('discord')">Discord Bot</a>
                <a href="#settings" class="admin-nav-item" onclick="showSection('settings')">Settings</a>
                <a href="#indexing" class="admin-nav-item" onclick="showSection('indexing')">Indexing</a>
            </nav>
            <div class="admin-sidebar-footer">
                <a href="/chat">Back to Chat</a>
            </div>
        </aside>

        <main class="admin-main">
            <!-- Stats Section -->
            <section id="stats-section" class="admin-section active">
                <h2>Platform Statistics</h2>
                <div class="stats-grid" id="statsGrid">
                    <div class="stat-card"><div class="stat-label">Total Users</div><div class="stat-value" id="statTotalUsers">-</div></div>
                    <div class="stat-card"><div class="stat-label">Active Users</div><div class="stat-value" id="statActiveUsers">-</div></div>
                    <div class="stat-card"><div class="stat-label">Conversations</div><div class="stat-value" id="statConversations">-</div></div>
                    <div class="stat-card"><div class="stat-label">Messages</div><div class="stat-value" id="statMessages">-</div></div>
                    <div class="stat-card"><div class="stat-label">Active Invites</div><div class="stat-value" id="statInvites">-</div></div>
                    <div class="stat-card"><div class="stat-label">New Today</div><div class="stat-value" id="statNewToday">-</div></div>
                </div>

                <h3>Query Statistics</h3>
                <div class="stats-grid" id="queryStatsGrid">
                    <div class="stat-card"><div class="stat-label">Total Queries</div><div class="stat-value" id="statQueries">-</div></div>
                    <div class="stat-card"><div class="stat-label">Today</div><div class="stat-value" id="statQueriesToday">-</div></div>
                    <div class="stat-card"><div class="stat-label">Avg Response</div><div class="stat-value" id="statAvgResponse">-</div></div>
                    <div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value" id="statErrors">-</div></div>
                </div>
            </section>

            <!-- Users Section -->
            <section id="users-section" class="admin-section">
                <h2>User Management</h2>
                <div class="table-container">
                    <table class="admin-table">
                        <thead>
                            <tr>
                                <th>Username</th>
                                <th>Email</th>
                                <th>Role</th>
                                <th>Status</th>
                                <th>Created</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="usersTableBody">
                        </tbody>
                    </table>
                </div>
            </section>

            <!-- Invites Section -->
            <section id="invites-section" class="admin-section">
                <h2>Invite Codes</h2>
                <div class="section-actions">
                    <button class="btn btn-primary" onclick="showCreateInviteModal()">+ Create Invite Code</button>
                </div>
                <div class="table-container">
                    <table class="admin-table">
                        <thead>
                            <tr>
                                <th>Code</th>
                                <th>Uses</th>
                                <th>Expires</th>
                                <th>Note</th>
                                <th>Status</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="invitesTableBody">
                        </tbody>
                    </table>
                </div>
            </section>

            <!-- Discord Section -->
            <section id="discord-section" class="admin-section">
                <h2>Discord Bot Management</h2>

                <div class="settings-group">
                    <h3>Bot Status</h3>
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-label">Status</div>
                            <div class="stat-value" id="discordBotOnline">-</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Bot User</div>
                            <div class="stat-value" id="discordBotUsername" style="font-size: 0.9rem;">-</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Connected Guilds</div>
                            <div class="stat-value" id="discordGuildCount">-</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Uptime</div>
                            <div class="stat-value" id="discordUptime">-</div>
                        </div>
                    </div>
                    <div class="stats-grid" style="margin-top: 0.5rem;">
                        <div class="stat-card">
                            <div class="stat-label">Token Configured</div>
                            <div class="stat-value" id="discordBotStatus">-</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Client ID</div>
                            <div class="stat-value" id="discordClientId" style="font-size: 0.75rem;">-</div>
                        </div>
                    </div>
                </div>

                <div class="settings-group">
                    <h3>Bot Invite Link</h3>
                    <p style="margin-bottom: 1rem; color: var(--text-secondary);">Generate an invite link to add the bot to your Discord server.</p>
                    <div class="form-group">
                        <label for="invitePreset">Permission Preset</label>
                        <select id="invitePreset" style="width: 100%;">
                            <option value="minimal">Minimal (Read Only) - Basic indexing</option>
                            <option value="standard" selected>Standard (Recommended) - Commands & responses</option>
                            <option value="full">Full Features - Including threads</option>
                            <option value="custom">Custom Permissions</option>
                        </select>
                    </div>
                    <div id="customPermissions" style="display: none; margin-bottom: 1rem;">
                        <label style="margin-bottom: 0.5rem; display: block;">Select Permissions:</label>
                        <div id="permissionCheckboxes" class="permission-grid"></div>
                    </div>
                    <button class="btn btn-primary" onclick="generateInviteLink()">Generate Invite Link</button>
                    <div id="inviteLinkResult" style="margin-top: 1rem;"></div>
                </div>

                <div class="settings-group">
                    <h3>Monitored Channels</h3>
                    <div class="form-group">
                        <label for="discordChannelIds">Channel IDs (comma-separated)</label>
                        <input type="text" id="discordChannelIds" placeholder="123456789,987654321" style="width: 100%;">
                    </div>
                    <button class="btn btn-primary" onclick="saveDiscordChannels()">Save Channels</button>
                </div>

                <div class="settings-group">
                    <h3>Scheduler Settings</h3>
                    <div class="form-group">
                        <label>
                            <input type="checkbox" id="autoIngestEnabled"> Auto-ingest Enabled
                        </label>
                    </div>
                    <div class="form-group">
                        <label for="scheduleCron">Cron Schedule</label>
                        <input type="text" id="scheduleCron" placeholder="0 3 * * *">
                        <small>Format: minute hour day month weekday (e.g., "0 3 * * *" = 3 AM daily)</small>
                    </div>
                    <div class="form-group">
                        <label for="quietPeriod">Quiet Period (minutes)</label>
                        <input type="number" id="quietPeriod" min="0" max="1440">
                    </div>
                    <div class="form-group">
                        <label for="backoffMinutes">Backoff (minutes)</label>
                        <input type="number" id="backoffMinutes" min="0" max="1440">
                    </div>
                    <button class="btn btn-primary" onclick="saveSchedulerSettings()">Save Scheduler Settings</button>
                </div>

                <div class="settings-group">
                    <h3>Manual Ingestion</h3>
                    <div class="form-group">
                        <label for="ingestChannelId">Channel ID (optional, leave empty for all)</label>
                        <input type="text" id="ingestChannelId" placeholder="Optional: specific channel ID">
                    </div>
                    <button class="btn btn-primary" onclick="triggerIngestion()">Trigger Ingestion</button>
                    <div id="ingestStatus" style="margin-top: 0.5rem;"></div>
                </div>

                <div class="settings-group">
                    <h3>Connected Servers</h3>
                    <div class="table-container">
                        <table class="admin-table">
                            <thead>
                                <tr>
                                    <th>Server</th>
                                    <th>Members</th>
                                    <th>Messages</th>
                                    <th>Channels</th>
                                    <th>Last Indexed</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="guildsTableBody">
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            <!-- Guild Channels Modal -->
            <div class="modal" id="guildChannelsModal">
                <div class="modal-content" style="max-width: 600px;">
                    <h3>Guild Channels - <span id="guildChannelsTitle">Loading...</span></h3>
                    <div id="guildChannelsList" style="max-height: 400px; overflow-y: auto;">
                        <p>Loading channels...</p>
                    </div>
                    <div class="modal-actions">
                        <button type="button" class="btn" onclick="hideGuildChannelsModal()">Close</button>
                        <button type="button" class="btn btn-primary" onclick="addSelectedChannels()">Add Selected to Monitored</button>
                    </div>
                </div>
            </div>

            <!-- Settings Section -->
            <section id="settings-section" class="admin-section">
                <h2>System Settings</h2>

                <div class="settings-group">
                    <h3>Model Configuration</h3>
                    <div class="form-group">
                        <label for="modelSelect">AI Model</label>
                        <select id="modelSelect"></select>
                    </div>
                    <div class="form-group">
                        <label for="thinkingSelect">Thinking Level</label>
                        <select id="thinkingSelect"></select>
                    </div>
                    <button class="btn btn-primary" onclick="saveModelSettings()">Save Model Settings</button>
                </div>

                <div class="settings-group">
                    <h3>Platform Settings</h3>
                    <div class="form-group">
                        <label>
                            <input type="checkbox" id="registrationEnabled"> Registration Enabled
                        </label>
                    </div>
                    <div class="form-group">
                        <label for="maxConversations">Max Conversations per User</label>
                        <input type="number" id="maxConversations" min="1" max="10000">
                    </div>
                    <div class="form-group">
                        <label for="maxMessages">Max Messages per Conversation</label>
                        <input type="number" id="maxMessages" min="1" max="10000">
                    </div>
                    <button class="btn btn-primary" onclick="savePlatformSettings()">Save Platform Settings</button>
                </div>
            </section>

            <!-- Indexing Section -->
            <section id="indexing-section" class="admin-section">
                <h2>Vector Index</h2>
                <div class="stats-grid" id="indexStatsGrid">
                    <div class="stat-card"><div class="stat-label">Status</div><div class="stat-value" id="indexStatus">-</div></div>
                    <div class="stat-card"><div class="stat-label">Vector Chunks</div><div class="stat-value" id="indexChunks">-</div></div>
                    <div class="stat-card"><div class="stat-label">Total Messages</div><div class="stat-value" id="indexMessages">-</div></div>
                    <div class="stat-card"><div class="stat-label">Channels</div><div class="stat-value" id="indexChannels">-</div></div>
                </div>
                <div class="section-actions">
                    <button class="btn btn-primary" id="runIndexingBtn" onclick="runIndexing()">Run Indexing</button>
                </div>
                <div id="indexingStatus"></div>
            </section>
        </main>
    </div>

    <!-- Create Invite Modal -->
    <div class="modal" id="createInviteModal">
        <div class="modal-content">
            <h3>Create Invite Code</h3>
            <form id="createInviteForm">
                <div class="form-group">
                    <label for="inviteMaxUses">Max Uses</label>
                    <input type="number" id="inviteMaxUses" value="1" min="1" max="100">
                </div>
                <div class="form-group">
                    <label for="inviteExpiresDays">Expires in (days)</label>
                    <input type="number" id="inviteExpiresDays" value="7" min="1" max="365">
                </div>
                <div class="form-group">
                    <label for="inviteNote">Note (optional)</label>
                    <input type="text" id="inviteNote" maxlength="200">
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn" onclick="hideCreateInviteModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create</button>
                </div>
            </form>
        </div>
    </div>

    <script>{ADMIN_JS}</script>
    """))


# ============== Styles ==============

BASE_STYLES = """
* { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg-primary: #212121;
    --bg-secondary: #171717;
    --bg-tertiary: #2f2f2f;
    --text-primary: #ececec;
    --text-secondary: #b4b4b4;
    --accent: #10a37f;
    --accent-hover: #0d8a6c;
    --border: #3f3f3f;
    --error: #ef4444;
    --success: #22c55e;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    min-height: 100vh;
}

/* Auth Pages */
.auth-container {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.auth-card {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 2rem;
    width: 100%;
    max-width: 400px;
}

.auth-header {
    text-align: center;
    margin-bottom: 2rem;
}

.auth-header .logo {
    font-size: 3rem;
    margin-bottom: 1rem;
}

.auth-header h1 {
    font-size: 1.5rem;
    margin-bottom: 0.5rem;
}

.auth-header p {
    color: var(--text-secondary);
}

.auth-form .form-group {
    margin-bottom: 1rem;
}

.auth-form label {
    display: block;
    margin-bottom: 0.5rem;
    color: var(--text-secondary);
    font-size: 0.875rem;
}

.auth-form input {
    width: 100%;
    padding: 0.75rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-primary);
    font-size: 1rem;
}

.auth-form input:focus {
    outline: none;
    border-color: var(--accent);
}

.auth-form small {
    color: var(--text-secondary);
    font-size: 0.75rem;
}

.auth-footer {
    margin-top: 1.5rem;
    text-align: center;
    color: var(--text-secondary);
}

.auth-footer a {
    color: var(--accent);
    text-decoration: none;
}

/* Buttons */
.btn {
    padding: 0.75rem 1.5rem;
    border-radius: 8px;
    font-size: 0.875rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    transition: all 0.2s;
}

.btn:hover {
    background: var(--border);
}

.btn-primary {
    background: var(--accent);
    border-color: var(--accent);
    color: white;
}

.btn-primary:hover {
    background: var(--accent-hover);
}

.btn-block {
    width: 100%;
}

.btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

/* Alerts */
.alert {
    padding: 0.75rem 1rem;
    border-radius: 8px;
    margin-bottom: 1rem;
}

.alert-error {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid var(--error);
    color: var(--error);
}

.alert-success {
    background: rgba(34, 197, 94, 0.1);
    border: 1px solid var(--success);
    color: var(--success);
}

/* Chat Layout */
.chat-layout {
    display: flex;
    height: 100vh;
}

.sidebar {
    width: 260px;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    transition: transform 0.3s;
}

.sidebar.hidden {
    transform: translateX(-100%);
    position: absolute;
}

.sidebar-header {
    padding: 1rem;
    border-bottom: 1px solid var(--border);
}

.new-chat-btn {
    width: 100%;
}

.conversations-list {
    flex: 1;
    overflow-y: auto;
    padding: 0.5rem;
}

.conversation-item {
    padding: 0.75rem 1rem;
    border-radius: 8px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--text-secondary);
    transition: background 0.2s;
    margin-bottom: 2px;
}

.conversation-item:hover {
    background: var(--bg-tertiary);
}

.conversation-item.active {
    background: var(--bg-tertiary);
    color: var(--text-primary);
}

.conversation-item .title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.875rem;
}

.conversation-item .delete-btn {
    opacity: 0;
    background: none;
    border: none;
    color: var(--text-secondary);
    cursor: pointer;
    padding: 0.25rem;
}

.conversation-item:hover .delete-btn {
    opacity: 1;
}

.sidebar-footer {
    padding: 1rem;
    border-top: 1px solid var(--border);
}

.user-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
    color: var(--text-primary);
}

.sidebar-link {
    display: block;
    padding: 0.5rem 0;
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 0.875rem;
}

.sidebar-link:hover {
    color: var(--text-primary);
}

/* Chat Main */
.chat-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
}

.chat-header {
    padding: 1rem;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 1rem;
}

.chat-header h1 {
    font-size: 1rem;
    font-weight: 500;
}

.sidebar-toggle {
    display: none;
    background: none;
    border: none;
    color: var(--text-primary);
    font-size: 1.25rem;
    cursor: pointer;
}

@media (max-width: 768px) {
    .sidebar {
        position: absolute;
        z-index: 100;
        height: 100%;
    }
    .sidebar.hidden {
        transform: translateX(-100%);
    }
    .sidebar-toggle {
        display: block;
    }
}

.messages-container {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
}

.welcome-message {
    text-align: center;
    padding: 4rem 2rem;
    color: var(--text-secondary);
}

.welcome-icon {
    font-size: 4rem;
    margin-bottom: 1rem;
}

.welcome-message h2 {
    color: var(--text-primary);
    margin-bottom: 0.5rem;
}

/* Messages */
.message {
    max-width: 800px;
    margin: 0 auto 1.5rem;
    padding: 1rem;
}

.message.user {
    background: var(--bg-tertiary);
    border-radius: 12px;
}

.message.assistant {
    background: transparent;
}

.message-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
    font-weight: 600;
    font-size: 0.875rem;
}

.message-content {
    line-height: 1.6;
    white-space: pre-wrap;
}

.message-content p {
    margin-bottom: 0.5rem;
}

.message-content code {
    background: var(--bg-tertiary);
    padding: 0.2rem 0.4rem;
    border-radius: 4px;
    font-family: monospace;
}

.message-sources {
    margin-top: 1rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
}

.message-sources h4 {
    font-size: 0.75rem;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
}

.source-item {
    background: var(--bg-tertiary);
    padding: 0.5rem;
    border-radius: 6px;
    margin-bottom: 0.5rem;
    font-size: 0.8rem;
}

.source-item a {
    color: var(--accent);
    text-decoration: none;
}

/* Thinking Panel */
.thinking-panel {
    background: var(--bg-secondary);
    border-top: 1px solid var(--border);
    padding: 0.75rem 1rem;
    max-height: 200px;
    overflow-y: auto;
}

.thinking-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
    font-size: 0.875rem;
    color: var(--accent);
}

.thinking-content {
    font-size: 0.8rem;
    color: var(--text-secondary);
    font-family: monospace;
}

.thinking-item {
    padding: 0.25rem 0;
    border-bottom: 1px solid var(--border);
}

/* Input Area */
.input-container {
    padding: 1rem;
    border-top: 1px solid var(--border);
}

.chat-input-form {
    max-width: 800px;
    margin: 0 auto;
    display: flex;
    gap: 0.5rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.5rem;
}

.chat-input-form textarea {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--text-primary);
    font-size: 1rem;
    resize: none;
    padding: 0.5rem;
    max-height: 200px;
    line-height: 1.5;
}

.chat-input-form textarea:focus {
    outline: none;
}

.send-btn {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    background: var(--accent);
    border: none;
    color: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.2s;
}

.send-btn:hover {
    background: var(--accent-hover);
}

.send-btn:disabled {
    background: var(--border);
    cursor: not-allowed;
}

.send-btn svg {
    width: 20px;
    height: 20px;
}

/* Admin Layout */
.admin-layout {
    display: flex;
    min-height: 100vh;
}

.admin-sidebar {
    width: 220px;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
}

.admin-logo {
    padding: 1rem;
    border-bottom: 1px solid var(--border);
}

.admin-logo a {
    color: var(--text-primary);
    text-decoration: none;
    font-weight: 600;
}

.admin-nav {
    flex: 1;
    padding: 0.5rem;
}

.admin-nav-item {
    display: block;
    padding: 0.75rem 1rem;
    color: var(--text-secondary);
    text-decoration: none;
    border-radius: 8px;
    margin-bottom: 2px;
}

.admin-nav-item:hover, .admin-nav-item.active {
    background: var(--bg-tertiary);
    color: var(--text-primary);
}

.admin-sidebar-footer {
    padding: 1rem;
    border-top: 1px solid var(--border);
}

.admin-sidebar-footer a {
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 0.875rem;
}

.admin-main {
    flex: 1;
    padding: 2rem;
    overflow-y: auto;
}

.admin-section {
    display: none;
}

.admin-section.active {
    display: block;
}

.admin-section h2 {
    margin-bottom: 1.5rem;
}

.admin-section h3 {
    margin: 1.5rem 0 1rem;
    color: var(--text-secondary);
    font-size: 0.875rem;
    text-transform: uppercase;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1rem;
    margin-bottom: 1.5rem;
}

.stat-card {
    background: var(--bg-secondary);
    padding: 1rem;
    border-radius: 8px;
    border: 1px solid var(--border);
}

.stat-label {
    color: var(--text-secondary);
    font-size: 0.75rem;
    margin-bottom: 0.5rem;
}

.stat-value {
    font-size: 1.5rem;
    font-weight: 600;
}

.table-container {
    overflow-x: auto;
}

.admin-table {
    width: 100%;
    border-collapse: collapse;
}

.admin-table th, .admin-table td {
    padding: 0.75rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
}

.admin-table th {
    color: var(--text-secondary);
    font-size: 0.75rem;
    text-transform: uppercase;
}

.section-actions {
    margin-bottom: 1rem;
}

.settings-group {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 1.5rem;
}

.settings-group h3 {
    margin-top: 0;
}

.settings-group .form-group {
    margin-bottom: 1rem;
}

.settings-group label {
    display: block;
    margin-bottom: 0.5rem;
    color: var(--text-secondary);
}

.settings-group input, .settings-group select {
    padding: 0.5rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-primary);
    min-width: 200px;
}

.settings-group input[type="checkbox"] {
    min-width: auto;
    margin-right: 0.5rem;
}

/* Modal */
.modal {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.7);
    align-items: center;
    justify-content: center;
    z-index: 1000;
}

.modal.show {
    display: flex;
}

.modal-content {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    width: 100%;
    max-width: 400px;
}

.modal-content h3 {
    margin-bottom: 1rem;
}

.modal-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
    margin-top: 1rem;
}

.permission-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 0.5rem;
    background: var(--bg-tertiary);
    padding: 1rem;
    border-radius: 8px;
}

.permission-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
}

.permission-item input[type="checkbox"] {
    width: 16px;
    height: 16px;
}

.invite-link-box {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
}

.invite-link-box input {
    width: 100%;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 0.75rem;
    border-radius: 6px;
    font-family: monospace;
    font-size: 0.85rem;
    margin-bottom: 0.5rem;
}

.invite-link-box .btn-row {
    display: flex;
    gap: 0.5rem;
}

.channel-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}

.channel-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem;
    background: var(--bg-tertiary);
    border-radius: 6px;
}

.channel-item.category {
    background: transparent;
    font-weight: 600;
    color: var(--text-secondary);
    margin-top: 0.5rem;
    padding-left: 0;
}

.channel-item input[type="checkbox"] {
    width: 16px;
    height: 16px;
}

.channel-icon {
    color: var(--text-secondary);
}

.status-online {
    color: #43b581;
}

.status-offline {
    color: var(--text-secondary);
}

.guild-icon {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--bg-tertiary);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 600;
}

/* Scrollbar */
::-webkit-scrollbar {
    width: 6px;
}

::-webkit-scrollbar-track {
    background: transparent;
}

::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--text-secondary);
}
"""


CHAT_JS = """
<script>
let currentConversationId = INITIAL_CONVERSATION_ID;
let isStreaming = false;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadConversations();
    if (currentConversationId) {
        loadConversation(currentConversationId);
    }

    // Auto-resize textarea
    const textarea = document.getElementById('messageInput');
    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    });
});

// Handle form submission
document.getElementById('chatForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (isStreaming) return;

    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    input.style.height = 'auto';

    await sendMessage(message);
});

function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('chatForm').dispatchEvent(new Event('submit'));
    }
}

async function sendMessage(message) {
    isStreaming = true;
    const sendBtn = document.getElementById('sendBtn');
    sendBtn.disabled = true;

    // Hide welcome message
    document.getElementById('welcomeMessage').style.display = 'none';

    // Add user message
    addMessage('user', message);

    // Show thinking panel
    const thinkingPanel = document.getElementById('thinkingPanel');
    const thinkingContent = document.getElementById('thinkingContent');
    thinkingPanel.style.display = 'block';
    thinkingContent.innerHTML = '';

    // Create assistant message placeholder
    const assistantMsg = addMessage('assistant', '');
    const contentEl = assistantMsg.querySelector('.message-content');

    try {
        const response = await fetch('/platform/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                message: message,
                conversation_id: currentConversationId
            })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let sources = [];

        while (true) {
            const {done, value} = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, {stream: true});
            const lines = buffer.split('\\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    const eventType = line.slice(7);
                    continue;
                }
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleSSEEvent(data, contentEl, thinkingContent, sources);
                    } catch {}
                }
            }
        }

        // Add sources if any
        if (sources.length > 0) {
            addSourcesToMessage(assistantMsg, sources);
        }

    } catch (err) {
        contentEl.textContent = 'Error: ' + err.message;
    }

    thinkingPanel.style.display = 'none';
    isStreaming = false;
    sendBtn.disabled = false;

    // Reload conversations to update sidebar
    loadConversations();
}

function handleSSEEvent(data, contentEl, thinkingContent, sources) {
    if (data.id) {
        // Conversation ID
        currentConversationId = data.id;
        history.replaceState(null, '', '/chat/' + data.id);
    }
    if (data.content) {
        // Thinking event
        const item = document.createElement('div');
        item.className = 'thinking-item';
        item.textContent = data.content;
        thinkingContent.appendChild(item);
        thinkingContent.scrollTop = thinkingContent.scrollHeight;
    }
    if (data.tool) {
        // Tool call
        const item = document.createElement('div');
        item.className = 'thinking-item';
        item.textContent = `Searching: "${data.query}"`;
        thinkingContent.appendChild(item);
    }
    if (data.text) {
        // Content chunk
        contentEl.textContent += data.text;
        scrollToBottom();
    }
    if (data.sources) {
        // Sources
        sources.push(...data.sources);
    }
    if (data.title) {
        // Title update
        document.getElementById('chatTitle').textContent = data.title;
        loadConversations();
    }
}

function addMessage(role, content) {
    const container = document.getElementById('messagesContainer');
    const msg = document.createElement('div');
    msg.className = 'message ' + role;
    msg.innerHTML = `
        <div class="message-header">
            ${role === 'user' ? 'You' : 'Assistant'}
        </div>
        <div class="message-content">${escapeHtml(content)}</div>
    `;
    container.appendChild(msg);
    scrollToBottom();
    return msg;
}

function addSourcesToMessage(msgEl, sources) {
    const sourcesDiv = document.createElement('div');
    sourcesDiv.className = 'message-sources';
    sourcesDiv.innerHTML = '<h4>Sources</h4>';

    sources.forEach(s => {
        const item = document.createElement('div');
        item.className = 'source-item';
        const url = s.urls && s.urls[0] ? s.urls[0] : '';
        item.innerHTML = `
            <strong>[${s.source_number}]</strong>
            ${escapeHtml(s.snippet.substring(0, 150))}...
            ${url ? `<a href="${url}" target="_blank">View</a>` : ''}
        `;
        sourcesDiv.appendChild(item);
    });

    msgEl.appendChild(sourcesDiv);
}

function scrollToBottom() {
    const container = document.getElementById('messagesContainer');
    container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function loadConversations() {
    try {
        const res = await fetch('/platform/conversations');
        const conversations = await res.json();

        const list = document.getElementById('conversationsList');
        list.innerHTML = conversations.map(c => `
            <div class="conversation-item ${c.id === currentConversationId ? 'active' : ''}"
                 onclick="loadConversation('${c.id}')">
                <span class="title">${escapeHtml(c.title)}</span>
                <button class="delete-btn" onclick="event.stopPropagation(); deleteConversation('${c.id}')">x</button>
            </div>
        `).join('');
    } catch {}
}

async function loadConversation(id) {
    try {
        const res = await fetch('/platform/conversations/' + id);
        const conv = await res.json();

        currentConversationId = id;
        history.replaceState(null, '', '/chat/' + id);

        document.getElementById('chatTitle').textContent = conv.title;
        document.getElementById('welcomeMessage').style.display = 'none';

        const container = document.getElementById('messagesContainer');
        container.innerHTML = '';

        conv.messages.forEach(m => {
            const msg = addMessage(m.role, m.content);
            if (m.sources && m.sources.length > 0) {
                addSourcesToMessage(msg, m.sources);
            }
        });

        loadConversations();
    } catch {}
}

async function newConversation() {
    currentConversationId = null;
    history.replaceState(null, '', '/chat');
    document.getElementById('chatTitle').textContent = 'New Chat';
    document.getElementById('messagesContainer').innerHTML = `
        <div class="welcome-message" id="welcomeMessage">
            <div class="welcome-icon"></div>
            <h2>Start a new conversation</h2>
            <p>Ask questions about the Discord chat history.</p>
        </div>
    `;
    loadConversations();
}

async function deleteConversation(id) {
    if (!confirm('Delete this conversation?')) return;

    try {
        await fetch('/platform/conversations/' + id, {method: 'DELETE'});
        if (currentConversationId === id) {
            newConversation();
        }
        loadConversations();
    } catch {}
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('hidden');
}

async function logout() {
    await fetch('/platform/auth/logout', {method: 'POST'});
    window.location.href = '/login';
}
</script>
"""


ADMIN_JS = """
// Admin JS
document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadUsers();
    loadInvites();
    loadSettings();
    loadIndexStats();
    loadDiscordSettings();
});

function showSection(name) {
    document.querySelectorAll('.admin-section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.admin-nav-item').forEach(n => n.classList.remove('active'));

    document.getElementById(name + '-section').classList.add('active');
    document.querySelector(`[onclick="showSection('${name}')"]`).classList.add('active');
}

async function loadStats() {
    try {
        const [platformRes, queryRes] = await Promise.all([
            fetch('/platform/admin/stats'),
            fetch('/platform/admin/query-stats')
        ]);

        const platform = await platformRes.json();
        const query = await queryRes.json();

        document.getElementById('statTotalUsers').textContent = platform.total_users;
        document.getElementById('statActiveUsers').textContent = platform.active_users;
        document.getElementById('statConversations').textContent = platform.total_conversations;
        document.getElementById('statMessages').textContent = platform.total_messages;
        document.getElementById('statInvites').textContent = platform.active_invite_codes;
        document.getElementById('statNewToday').textContent = platform.users_registered_today;

        document.getElementById('statQueries').textContent = query.stats.total_queries;
        document.getElementById('statQueriesToday').textContent = query.stats.queries_today;
        document.getElementById('statAvgResponse').textContent = Math.round(query.stats.avg_response_time_ms) + 'ms';
        document.getElementById('statErrors').textContent = query.stats.error_count;
    } catch {}
}

async function loadUsers() {
    try {
        const res = await fetch('/platform/admin/users');
        const users = await res.json();

        document.getElementById('usersTableBody').innerHTML = users.map(u => `
            <tr>
                <td>${u.username}</td>
                <td>${u.email}</td>
                <td>${u.role}</td>
                <td>${u.status}</td>
                <td>${new Date(u.created_at).toLocaleDateString()}</td>
                <td>
                    <button class="btn" onclick="editUser('${u.id}')">Edit</button>
                </td>
            </tr>
        `).join('');
    } catch {}
}

async function loadInvites() {
    try {
        const res = await fetch('/platform/admin/invite-codes');
        const codes = await res.json();

        document.getElementById('invitesTableBody').innerHTML = codes.map(c => `
            <tr>
                <td><code>${c.code}</code></td>
                <td>${c.current_uses}/${c.max_uses}</td>
                <td>${c.expires_at ? new Date(c.expires_at).toLocaleDateString() : 'Never'}</td>
                <td>${c.note || '-'}</td>
                <td>${c.is_active ? 'Active' : 'Inactive'}</td>
                <td>
                    ${c.is_active ? `<button class="btn" onclick="deactivateInvite('${c.code}')">Deactivate</button>` : ''}
                </td>
            </tr>
        `).join('');
    } catch {}
}

async function loadSettings() {
    try {
        const res = await fetch('/platform/admin/settings');
        const settings = await res.json();

        // Model options
        const modelSelect = document.getElementById('modelSelect');
        modelSelect.innerHTML = settings.available_models.map(m =>
            `<option value="${m.id}" ${m.id === settings.model ? 'selected' : ''}>${m.name}</option>`
        ).join('');

        // Thinking options
        const thinkingSelect = document.getElementById('thinkingSelect');
        thinkingSelect.innerHTML = settings.thinking_levels.map(t =>
            `<option value="${t.id}" ${t.id === settings.thinking ? 'selected' : ''}>${t.name}</option>`
        ).join('');

        // Platform settings
        document.getElementById('registrationEnabled').checked = settings.registration_enabled;
        document.getElementById('maxConversations').value = settings.max_conversations_per_user;
        document.getElementById('maxMessages').value = settings.max_messages_per_conversation;
    } catch {}
}

async function loadIndexStats() {
    try {
        const res = await fetch('/platform/admin/index-stats');
        const stats = await res.json();

        document.getElementById('indexStatus').textContent = stats.index_exists ? 'Ready' : 'Not Created';
        document.getElementById('indexChunks').textContent = stats.vector_chunks;
        document.getElementById('indexMessages').textContent = stats.total_messages;
        document.getElementById('indexChannels').textContent = stats.indexed_channels;
    } catch {}
}

async function saveModelSettings() {
    const model = document.getElementById('modelSelect').value;
    const thinking = document.getElementById('thinkingSelect').value;

    try {
        await Promise.all([
            fetch('/platform/admin/settings/model', {
                method: 'POST',
                body: new URLSearchParams({model})
            }),
            fetch('/platform/admin/settings/thinking', {
                method: 'POST',
                body: new URLSearchParams({thinking})
            })
        ]);
        alert('Settings saved!');
    } catch {
        alert('Failed to save settings');
    }
}

async function savePlatformSettings() {
    const formData = new URLSearchParams();
    formData.append('registration_enabled', document.getElementById('registrationEnabled').checked);
    formData.append('max_conversations_per_user', document.getElementById('maxConversations').value);
    formData.append('max_messages_per_conversation', document.getElementById('maxMessages').value);

    try {
        await fetch('/platform/admin/settings/platform', {
            method: 'POST',
            body: formData
        });
        alert('Settings saved!');
    } catch {
        alert('Failed to save settings');
    }
}

function showCreateInviteModal() {
    document.getElementById('createInviteModal').classList.add('show');
}

function hideCreateInviteModal() {
    document.getElementById('createInviteModal').classList.remove('show');
}

document.getElementById('createInviteForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    try {
        const res = await fetch('/platform/admin/invite-codes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                max_uses: parseInt(document.getElementById('inviteMaxUses').value),
                expires_in_days: parseInt(document.getElementById('inviteExpiresDays').value),
                note: document.getElementById('inviteNote').value
            })
        });

        const code = await res.json();
        hideCreateInviteModal();
        alert('Invite code created: ' + code.code);
        loadInvites();
    } catch {
        alert('Failed to create invite code');
    }
});

async function deactivateInvite(code) {
    if (!confirm('Deactivate this invite code?')) return;

    try {
        await fetch('/platform/admin/invite-codes/' + code, {method: 'DELETE'});
        loadInvites();
    } catch {}
}

async function runIndexing() {
    const btn = document.getElementById('runIndexingBtn');
    btn.disabled = true;
    btn.textContent = 'Running...';

    try {
        const res = await fetch('/platform/admin/indexing/run', {method: 'POST'});
        const data = await res.json();

        if (data.status === 'started') {
            checkIndexingStatus();
        } else {
            alert(data.message);
            btn.disabled = false;
            btn.textContent = 'Run Indexing';
        }
    } catch {
        btn.disabled = false;
        btn.textContent = 'Run Indexing';
    }
}

async function checkIndexingStatus() {
    try {
        const res = await fetch('/platform/admin/indexing/status');
        const status = await res.json();

        if (status.running) {
            setTimeout(checkIndexingStatus, 2000);
        } else {
            document.getElementById('runIndexingBtn').disabled = false;
            document.getElementById('runIndexingBtn').textContent = 'Run Indexing';
            loadIndexStats();
            alert('Indexing complete!');
        }
    } catch {}
}

function editUser(id) {
    // TODO: Implement user edit modal
    alert('Edit user: ' + id);
}

// ============== Discord Management ==============

let discordPermissions = {};

async function loadDiscordSettings() {
    try {
        const [discordRes, guildsRes, statusRes, inviteRes] = await Promise.all([
            fetch('/platform/admin/discord'),
            fetch('/platform/admin/discord/guilds'),
            fetch('/platform/admin/discord/status'),
            fetch('/platform/admin/discord/invite')
        ]);

        const discord = await discordRes.json();
        const guildsData = await guildsRes.json();
        const status = await statusRes.json();
        const invite = await inviteRes.json();

        // Bot online status
        const onlineEl = document.getElementById('discordBotOnline');
        if (status.online) {
            onlineEl.innerHTML = '<span class="status-online">Online</span>';
        } else {
            onlineEl.innerHTML = '<span class="status-offline">Offline</span>';
        }

        // Bot username
        const usernameEl = document.getElementById('discordBotUsername');
        if (status.username) {
            usernameEl.textContent = status.username;
        } else {
            usernameEl.textContent = 'Not connected';
        }

        // Guild count
        document.getElementById('discordGuildCount').textContent = status.guilds_connected || '0';

        // Uptime
        const uptimeEl = document.getElementById('discordUptime');
        if (status.uptime_seconds > 0) {
            const hours = Math.floor(status.uptime_seconds / 3600);
            const mins = Math.floor((status.uptime_seconds % 3600) / 60);
            uptimeEl.textContent = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
        } else {
            uptimeEl.textContent = '-';
        }

        // Token & Client ID
        document.getElementById('discordBotStatus').textContent = discord.bot_token_set ? 'Yes' : 'No';
        document.getElementById('discordClientId').textContent = discord.bot_client_id || 'Not Set';

        // Channel IDs
        document.getElementById('discordChannelIds').value = discord.channel_ids.join(', ');

        // Scheduler settings
        document.getElementById('autoIngestEnabled').checked = discord.auto_ingest_enabled;
        document.getElementById('scheduleCron').value = discord.schedule_cron;
        document.getElementById('quietPeriod').value = discord.quiet_period_minutes;
        document.getElementById('backoffMinutes').value = discord.backoff_minutes;

        // Store permissions for invite link generation
        if (invite.permissions) {
            discordPermissions = invite.permissions;
            setupPermissionCheckboxes();
        }

        // Guilds table with improved display
        document.getElementById('guildsTableBody').innerHTML = guildsData.guilds.map(g => {
            const iconHtml = g.guild_icon
                ? `<img src="${g.guild_icon}" class="guild-icon" alt="">`
                : `<div class="guild-icon">${(g.guild_name || 'U')[0].toUpperCase()}</div>`;
            return `
            <tr>
                <td style="display: flex; align-items: center; gap: 0.5rem;">
                    ${iconHtml}
                    <div>
                        <div>${g.guild_name || 'Unknown'}</div>
                        <small style="color: var(--text-secondary);">${g.guild_id}</small>
                    </div>
                </td>
                <td>${g.member_count || '-'}</td>
                <td>${g.total_messages}</td>
                <td>${g.indexed_channels}</td>
                <td>${g.last_indexed ? new Date(g.last_indexed).toLocaleString() : 'Never'}</td>
                <td>
                    <button class="btn" onclick="showGuildChannels('${g.guild_id}', '${g.guild_name || 'Unknown'}')">Channels</button>
                </td>
            </tr>
        `}).join('') || '<tr><td colspan="6">No guilds indexed yet</td></tr>';

    } catch (err) {
        console.error('Failed to load Discord settings:', err);
    }
}

function setupPermissionCheckboxes() {
    const container = document.getElementById('permissionCheckboxes');
    container.innerHTML = Object.entries(discordPermissions).map(([key, perm]) => `
        <label class="permission-item">
            <input type="checkbox" name="perm" value="${key}">
            ${perm.description}
        </label>
    `).join('');

    // Show/hide custom permissions based on preset selection
    document.getElementById('invitePreset').addEventListener('change', (e) => {
        const customDiv = document.getElementById('customPermissions');
        customDiv.style.display = e.target.value === 'custom' ? 'block' : 'none';
    });
}

async function generateInviteLink() {
    const preset = document.getElementById('invitePreset').value;
    const resultEl = document.getElementById('inviteLinkResult');

    const formData = new URLSearchParams();

    if (preset === 'custom') {
        const checked = Array.from(document.querySelectorAll('#permissionCheckboxes input:checked'))
            .map(cb => cb.value);
        if (checked.length === 0) {
            resultEl.innerHTML = '<span style="color: var(--error);">Please select at least one permission</span>';
            return;
        }
        formData.append('permissions', checked.join(','));
    } else {
        formData.append('preset', preset);
    }

    try {
        const res = await fetch('/platform/admin/discord/invite/generate', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            const data = await res.json();
            resultEl.innerHTML = `
                <div class="invite-link-box">
                    <input type="text" id="inviteLinkInput" value="${data.invite_url}" readonly>
                    <div class="btn-row">
                        <button class="btn btn-primary" onclick="copyInviteLink()">Copy Link</button>
                        <a href="${data.invite_url}" target="_blank" class="btn">Open in Discord</a>
                    </div>
                    <p style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text-secondary);">
                        Permissions: ${data.permissions_used.join(', ')}
                    </p>
                </div>
            `;
        } else {
            const err = await res.json();
            resultEl.innerHTML = `<span style="color: var(--error);">${err.detail || 'Failed to generate link'}</span>`;
        }
    } catch {
        resultEl.innerHTML = '<span style="color: var(--error);">Network error</span>';
    }
}

function copyInviteLink() {
    const input = document.getElementById('inviteLinkInput');
    input.select();
    document.execCommand('copy');
    alert('Invite link copied to clipboard!');
}

async function showGuildChannels(guildId, guildName) {
    document.getElementById('guildChannelsTitle').textContent = guildName;
    document.getElementById('guildChannelsList').innerHTML = '<p>Loading channels...</p>';
    document.getElementById('guildChannelsModal').classList.add('active');

    try {
        const res = await fetch(`/platform/admin/discord/channels/${guildId}`);
        const data = await res.json();

        if (!data.cached || data.channels.length === 0) {
            document.getElementById('guildChannelsList').innerHTML = `
                <p style="color: var(--text-secondary);">${data.message || 'No channel data available.'}</p>
                <button class="btn btn-primary" onclick="requestGuildSync('${guildId}')">Request Sync</button>
            `;
            return;
        }

        const channelHtml = data.channels.map(ch => `
            <div class="channel-item">
                <input type="checkbox" value="${ch.id}" data-name="${ch.name}">
                <span class="channel-icon">#</span>
                <span>${ch.name}</span>
            </div>
        `).join('');

        document.getElementById('guildChannelsList').innerHTML = `<div class="channel-list">${channelHtml}</div>`;
    } catch {
        document.getElementById('guildChannelsList').innerHTML = '<p style="color: var(--error);">Failed to load channels</p>';
    }
}

function hideGuildChannelsModal() {
    document.getElementById('guildChannelsModal').classList.remove('active');
}

async function requestGuildSync(guildId) {
    try {
        const res = await fetch(`/platform/admin/discord/sync-guild/${guildId}`, {method: 'POST'});
        const data = await res.json();
        alert(data.message || 'Sync requested');
    } catch {
        alert('Failed to request sync');
    }
}

function addSelectedChannels() {
    const selected = Array.from(document.querySelectorAll('#guildChannelsList input:checked'))
        .map(cb => cb.value);

    if (selected.length === 0) {
        alert('No channels selected');
        return;
    }

    // Add to existing channel IDs
    const currentIds = document.getElementById('discordChannelIds').value
        .split(',')
        .map(s => s.trim())
        .filter(s => s);

    const newIds = [...new Set([...currentIds, ...selected])];
    document.getElementById('discordChannelIds').value = newIds.join(', ');

    hideGuildChannelsModal();
    alert(`Added ${selected.length} channel(s). Click "Save Channels" to apply.`);
}

async function saveDiscordChannels() {
    const channelIds = document.getElementById('discordChannelIds').value;

    try {
        const res = await fetch('/platform/admin/discord/channels', {
            method: 'POST',
            body: new URLSearchParams({ channel_ids: channelIds })
        });

        if (res.ok) {
            alert('Channel IDs saved!');
            loadDiscordSettings();
        } else {
            const data = await res.json();
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch {
        alert('Failed to save channel IDs');
    }
}

async function saveSchedulerSettings() {
    const formData = new URLSearchParams();
    formData.append('auto_ingest_enabled', document.getElementById('autoIngestEnabled').checked);
    formData.append('schedule_cron', document.getElementById('scheduleCron').value);
    formData.append('quiet_period_minutes', document.getElementById('quietPeriod').value);
    formData.append('backoff_minutes', document.getElementById('backoffMinutes').value);

    try {
        const res = await fetch('/platform/admin/discord/scheduler', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            alert('Scheduler settings saved!');
        } else {
            const data = await res.json();
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch {
        alert('Failed to save scheduler settings');
    }
}

async function triggerIngestion() {
    const channelId = document.getElementById('ingestChannelId').value.trim();
    const statusEl = document.getElementById('ingestStatus');

    const formData = new URLSearchParams();
    if (channelId) {
        formData.append('channel_id', channelId);
    }

    try {
        statusEl.innerHTML = '<span style="color: var(--accent);">Triggering ingestion...</span>';

        const res = await fetch('/platform/admin/discord/ingest', {
            method: 'POST',
            body: formData
        });

        const data = await res.json();

        if (res.ok) {
            statusEl.innerHTML = `<span style="color: var(--success);">${data.message}</span>`;
            loadDiscordSettings();
        } else {
            statusEl.innerHTML = `<span style="color: var(--error);">${data.detail || 'Failed'}</span>`;
        }
    } catch {
        statusEl.innerHTML = '<span style="color: var(--error);">Network error</span>';
    }
}
"""
