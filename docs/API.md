# Discord RAG API Documentation

Complete API reference for the Discord RAG system.

## Base URL

```
http://your-server:8000
```

## Authentication

All `/v1/*` endpoints require Bearer token authentication:

```bash
Authorization: Bearer YOUR_API_KEY
```

Set via `API_KEY` environment variable. Leave empty to disable auth (dev mode only).

---

## Core Endpoints

### Health Check

```http
GET /v1/health
```

Check API status and model info.

**Response:**
```json
{
  "status": "ok",
  "model": "gemini-3-flash-preview",
  "version": "1.0.0"
}
```

---

### Query (RAG Search)

```http
POST /v1/query
```

Ask a question and get an AI-generated answer with citations.

**Request Body:**
```json
{
  "query": "What did everyone think about the new update?",
  "guild_id": "123456789",
  "channel_ids": ["111111", "222222"],  // optional - filter channels
  "top_k": 5,                            // optional - number of sources (1-20)
  "before": "2024-01-01T00:00:00Z",     // optional - messages before date
  "after": "2023-01-01T00:00:00Z"       // optional - messages after date
}
```

**Response:**
```json
{
  "answer": "Based on the chat history, users had mixed reactions...",
  "sources": [
    {
      "source_number": 1,
      "snippet": "john: I really like the new update...",
      "urls": [
        "https://discord.com/channels/@me/123/456",
        "https://discord.com/channels/@me/123/457"
      ],
      "channel": "general",
      "timestamp": "1703980800000"
    }
  ],
  "query_time_ms": 1523
}
```

---

## Message Import

### Import with User Token (DMs/Group DMs)

```http
POST /v1/import/user-token
```

Import messages from DMs, Group DMs, or servers using a Discord user account token.

> **Warning:** Using user tokens violates Discord ToS and may result in account termination.

**Request Body:**
```json
{
  "user_token": "YOUR_DISCORD_USER_TOKEN",
  "channel_id": "GROUP_DM_CHANNEL_ID",
  "max_messages": 10000  // optional - limit import size
}
```

**Response:**
```json
{
  "status": "completed",
  "channel_id": "123456789",
  "channel_type": "group_dm",  // "dm", "group_dm", or "guild"
  "channel_name": "Squad Chat",
  "messages_imported": 1523,
  "messages_skipped": 47,
  "resumed_from": "987654321",  // last message ID from previous import
  "oldest_message_id": "111111111",
  "newest_message_id": "999999999"
}
```

**Features:**
- Automatically resumes from last imported message
- Skips bot messages and empty content
- Rate-limited to avoid Discord API bans
- Generates correct URLs (`@me` for DMs)

---

### Real-time Message Webhook

```http
POST /v1/messages
```

Add a single message in real-time (for bot integration).

**Request Body:**
```json
{
  "id": "message_id",
  "guild_id": "guild_id",
  "channel_id": "channel_id",
  "author_id": "user_id",
  "author_name": "username",
  "content": "Message content here",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Response:**
```json
{
  "status": "queued"
}
```

---

### Delete Message

```http
DELETE /v1/guilds/{guild_id}/messages/{message_id}
```

Remove a deleted message from the index.

**Response:**
```json
{
  "status": "deleted",
  "message_id": "123456789"
}
```

---

## Ingestion & Indexing

### Trigger Guild Ingestion

```http
POST /v1/guilds/{guild_id}/ingest
```

Start background job to fetch message history from Discord.

**Request Body:**
```json
{
  "channel_ids": ["111", "222"],  // optional - specific channels
  "after": "2023-01-01T00:00:00Z", // optional - only after date
  "limit": 50000                   // optional - max messages
}
```

**Response:**
```json
{
  "status": "started",
  "job_id": "abc123",
  "estimated_messages": null
}
```

---

### Rebuild Vector Index

```http
POST /v1/guilds/{guild_id}/index
```

Re-chunk and re-embed all messages for a guild.

**Response:**
```json
{
  "status": "started",
  "job_id": "def456",
  "chunks_created": null
}
```

---

## Statistics

### Guild Stats

```http
GET /v1/guilds/{guild_id}/stats
```

Get message counts and indexing status.

**Response:**
```json
{
  "guild_id": "123456789",
  "total_messages": 15000,
  "total_chunks": 450,
  "indexed_channels": 5,
  "date_range": {
    "oldest": "2022-01-15",
    "newest": "2024-01-15"
  },
  "last_indexed": "2024-01-15T10:00:00Z"
}
```

---

### List Indexed Channels

```http
GET /v1/guilds/{guild_id}/channels
```

**Response:**
```json
{
  "guild_id": "123456789",
  "channels": [
    {
      "id": "111111",
      "name": "general",
      "message_count": 5000
    },
    {
      "id": "222222",
      "name": "random",
      "message_count": 3000
    }
  ]
}
```

---

## Debug

### Test Embedding

```http
POST /v1/debug/embed
```

Generate embedding for test text.

**Request Body:**
```json
{
  "text": "Test message for embedding"
}
```

**Response:**
```json
{
  "text": "Test message for embedding",
  "embedding": [0.123, -0.456, ...],
  "dimensions": 3072,
  "model": "gemini-embedding-001"
}
```

---

## Dashboard Endpoints

### Dashboard UI

```http
GET /dashboard
```

Web-based dashboard with usage statistics (requires session auth).

### Dashboard Login

```http
GET /dashboard/login
POST /dashboard/login
```

Authentication for dashboard access.

**Form Data:**
```
username=admin
password=your_password
```

### Dashboard Stats API

```http
GET /dashboard/api/stats
```

Get stats as JSON (requires session auth).

**Response:**
```json
{
  "stats": {
    "total_queries": 1500,
    "queries_today": 45,
    "queries_this_week": 320,
    "queries_this_month": 1200,
    "avg_response_time_ms": 1523,
    "avg_sources_per_query": 3.2,
    "error_count": 5,
    "last_query_time": "2024-01-15T10:30:00Z"
  },
  "hourly": [
    {"hour": "00:00", "count": 5},
    {"hour": "01:00", "count": 3}
  ]
}
```

### Reset Stats

```http
POST /dashboard/api/reset
```

Reset all statistics (requires session auth).

---

## Legacy Endpoints

These endpoints are kept for backward compatibility. Use v1 endpoints instead.

### Legacy Health

```http
GET /health
```

### Legacy Inference

```http
POST /infer
Content-Type: application/x-www-form-urlencoded

text=your+question+here
```

---

## Error Responses

All errors follow a standard format:

```json
{
  "error": {
    "code": "error_code",
    "message": "Human readable message",
    "details": {}  // optional
  }
}
```

**Error Codes:**

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `unauthorized` | 401 | Invalid or missing API key |
| `forbidden` | 403 | Access denied |
| `not_found` | 404 | Resource not found |
| `validation_error` | 422 | Invalid request body |
| `rate_limited` | 429 | Too many requests |
| `internal_error` | 500 | Server error |

---

## Rate Limits

- User token import: 1 request/second to Discord API (built-in)
- API endpoints: No hard limits (add nginx/cloudflare for production)

---

## Discord Bot Commands

The bot provides a `/ask` slash command:

```
/ask prompt:What did everyone think about the movie?
```

Response appears as an embed with:
- Generated answer
- Up to 3 source citations with "Jump to message" links
- Message snippets
