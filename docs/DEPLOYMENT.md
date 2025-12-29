# Production Deployment Guide

Complete guide for deploying Discord RAG to production.

## Prerequisites

- Docker & Docker Compose installed
- A server with at least 2GB RAM
- Domain name (optional, for HTTPS)
- Google Cloud account (for Gemini API)
- Discord account (for user token import)

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/kyrofx/discord-rag-kyronet.git
cd discord-rag-kyronet

# 2. Create environment file
cp .env.example .env

# 3. Edit .env with your values (see below)
nano .env

# 4. Start all services
docker compose up -d

# 5. Import your messages (see Import section)

# 6. Build the vector index
docker compose --profile indexing up indexer
```

---

## Environment Variables

Create a `.env` file in the project root:

```bash
# ===========================================
# REQUIRED
# ===========================================

# Google AI - Get from https://aistudio.google.com/apikey
GOOGLE_API_KEY=AIza...your_key_here

# API Authentication - Generate a strong random string
# Used to authenticate API requests
API_KEY=your_secure_api_key_here

# Dashboard Authentication
DASHBOARD_USER=admin
DASHBOARD_PASS=your_secure_password_here

# MongoDB
MONGODB_DB=discord_rag
MONGODB_COLLECTION=messages

# ===========================================
# OPTIONAL (have defaults)
# ===========================================

# Internal service URLs (use defaults for docker-compose)
MONGODB_URL=mongodb://discord_rag_mongo:27017
REDIS_URL=redis://discord_rag_redis:6379
RAG_API_BASE_URL=http://discord_rag_api:8000

# ===========================================
# FOR DISCORD BOT (if using server mode)
# ===========================================

# Get from https://discord.com/developers/applications
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_BOT_CLIENT_ID=your_client_id

# ===========================================
# FOR SCHEDULER (if using automated imports)
# ===========================================

# Comma-separated Discord channel IDs
DISCORD_CHANNEL_IDS=123456789,987654321

# Cron schedule (default: 3 AM daily)
SCHEDULE_CRON=0 3 * * *

# Minutes of quiet required before running
QUIET_PERIOD_MINUTES=15

# Backoff if activity detected
BACKOFF_MINUTES=10
```

---

## Services Overview

| Service | Port | Description |
|---------|------|-------------|
| `api` | 8000 | FastAPI server (main API + dashboard) |
| `mongo` | 27017 | MongoDB (message storage) |
| `redis` | 6379 | Redis Stack (vector store + stats) |
| `bot` | - | Discord bot for /ask command |
| `scheduler` | - | Automated ingestion at 3 AM |
| `indexer` | - | One-shot indexing (run manually) |

---

## Step-by-Step Deployment

### 1. Get Google API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Click "Create API Key"
3. Copy the key to `GOOGLE_API_KEY` in `.env`

### 2. Generate Secure Credentials

```bash
# Generate API key
openssl rand -base64 32

# Generate dashboard password
openssl rand -base64 24
```

Add these to your `.env` file.

### 3. Start Core Services

```bash
# Start MongoDB, Redis, and API
docker compose up -d mongo redis api

# Check logs
docker compose logs -f api
```

### 4. Access Dashboard

Open `http://your-server:8000/dashboard` and login with your credentials.

### 5. Import Messages

#### Option A: User Token Import (for Group DMs)

```bash
curl -X POST http://your-server:8000/v1/import/user-token \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_token": "YOUR_DISCORD_USER_TOKEN",
    "channel_id": "GROUP_DM_CHANNEL_ID"
  }'
```

**How to get your Discord user token:**

1. Open Discord in a web browser (not the app)
2. Press F12 to open Developer Tools
3. Go to the Network tab
4. Send any message or refresh the page
5. Click on any request to `discord.com/api`
6. In Headers, find `authorization` (NOT starting with "Bot")
7. Copy that value

**How to get the channel ID:**

1. Enable Developer Mode in Discord (Settings > Advanced)
2. Right-click the group chat
3. Click "Copy Channel ID"

#### Option B: Discord Bot (for Servers)

```bash
# Start the bot
docker compose up -d bot

# The bot will respond to /ask commands in your server
```

### 6. Build Vector Index

After importing messages, build the search index:

```bash
docker compose --profile indexing up indexer
```

This:
- Loads messages from MongoDB
- Groups them into conversation chunks (30-min windows)
- Generates embeddings with Gemini
- Stores vectors in Redis

**Note:** Re-run this after importing new messages.

### 7. Test the API

```bash
curl -X POST http://your-server:8000/v1/query \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What have we been talking about lately?",
    "guild_id": "any"
  }'
```

---

## Production Hardening

### Use HTTPS with Nginx

Create `nginx.conf`:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Enable Firewall

```bash
# Only allow SSH, HTTP, HTTPS
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable

# Block direct access to internal ports
# (MongoDB, Redis are only accessible via Docker network)
```

### Data Persistence

Data is stored in Docker volumes:
- `discord_rag_mongo_data` - Messages
- `discord_rag_redis_data` - Vectors and stats

Backup volumes:
```bash
# Backup MongoDB
docker exec discord_rag_mongo mongodump --out /data/backup
docker cp discord_rag_mongo:/data/backup ./mongo-backup

# Backup Redis
docker exec discord_rag_redis redis-cli BGSAVE
docker cp discord_rag_redis:/data/dump.rdb ./redis-backup.rdb
```

### Resource Limits

Add to `docker-compose.yml`:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          memory: 1G
        reservations:
          memory: 512M
```

---

## Scheduled Imports

For automatic nightly imports:

```bash
# Start the scheduler service
docker compose up -d scheduler
```

The scheduler:
1. Runs at 3 AM (configurable via `SCHEDULE_CRON`)
2. Checks if anyone messaged in the last 15 minutes
3. If quiet, fetches new messages and rebuilds the index
4. If active, waits 10 minutes and retries (up to 6 times)

---

## Updating

```bash
# Pull latest changes
git pull origin main

# Rebuild and restart
docker compose build
docker compose up -d

# If embedding model changed, rebuild index
docker compose --profile indexing up indexer
```

---

## Troubleshooting

### API not responding

```bash
docker compose logs api
```

Common issues:
- `GOOGLE_API_KEY` not set or invalid
- Redis not running

### Import fails

```bash
# Check if user token is valid
curl -H "Authorization: YOUR_TOKEN" https://discord.com/api/v10/users/@me
```

Common issues:
- Token expired (re-fetch from browser)
- Channel ID incorrect
- Rate limited (wait and retry)

### No search results

1. Check if messages were imported:
```bash
docker exec discord_rag_mongo mongosh discord_rag --eval "db.messages.countDocuments()"
```

2. Check if index was built:
```bash
docker exec discord_rag_redis redis-cli FT._LIST
```

3. Rebuild index:
```bash
docker compose --profile indexing up indexer
```

### High memory usage

Redis stores all vectors in memory. For large datasets:
- Use Redis persistence (already enabled)
- Increase server RAM
- Consider sharding

---

## Architecture

```
┌─────────────┐     ┌─────────────┐
│   Client    │────▶│   Nginx     │
│  (Browser)  │     │   (HTTPS)   │
└─────────────┘     └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   FastAPI   │
                    │    (API)    │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │   MongoDB   │  │    Redis    │  │   Gemini    │
   │  (messages) │  │  (vectors)  │  │    (AI)     │
   └─────────────┘  └─────────────┘  └─────────────┘
```

---

## Cost Estimates

| Component | Cost |
|-----------|------|
| Gemini API | ~$0.075 per 1M input tokens |
| VPS (2GB) | ~$10-20/month |
| Domain | ~$12/year |

For a typical group chat (~50k messages):
- Initial indexing: ~$0.50
- Monthly queries: ~$1-5 depending on usage

---

## Support

- Issues: https://github.com/kyrofx/discord-rag-kyronet/issues
- API Docs: http://your-server:8000/docs (Swagger UI)
