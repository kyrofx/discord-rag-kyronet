"""
User token-based message import for Discord DMs and Group Chats.

WARNING: Using user tokens (selfbots) violates Discord's Terms of Service
and may result in account termination. Use at your own risk.
"""
import os
import httpx
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient

DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_MESSAGES_PER_REQUEST = 100
RATE_LIMIT_DELAY = 1.0  # seconds between requests to avoid rate limits


class UserTokenImporter:
    """Import messages from Discord using a user account token."""

    def __init__(self, user_token: str, mongodb_url: str = None, db_name: str = None, collection_name: str = None):
        self.user_token = user_token
        self.headers = {
            "Authorization": user_token,  # No "Bot " prefix for user tokens
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # MongoDB setup
        mongodb_url = mongodb_url or os.getenv("MONGODB_URL", "mongodb://localhost:27017")
        db_name = db_name or os.getenv("MONGODB_DB", "discord_rag")
        collection_name = collection_name or os.getenv("MONGODB_COLLECTION", "messages")

        self.mongo_client = AsyncIOMotorClient(mongodb_url)
        self.db = self.mongo_client[db_name]
        self.collection = self.db[collection_name]

    async def close(self):
        """Close the MongoDB connection."""
        self.mongo_client.close()

    async def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get channel information to determine type (DM, Group DM, or Guild)."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/channels/{channel_id}",
                headers=self.headers
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                raise ValueError("Invalid user token")
            elif response.status_code == 403:
                raise ValueError("No access to this channel")
            elif response.status_code == 404:
                raise ValueError("Channel not found")
            else:
                raise ValueError(f"Discord API error: {response.status_code}")

    async def get_latest_stored_message_id(self, channel_id: str) -> Optional[str]:
        """Get the ID of the most recently stored message for this channel."""
        latest = await self.collection.find_one(
            {"channel.id": channel_id},
            sort=[("timestamp", -1)]
        )
        return latest["_id"] if latest else None

    async def fetch_messages(
        self,
        channel_id: str,
        after: Optional[str] = None,
        limit: int = MAX_MESSAGES_PER_REQUEST
    ) -> List[Dict[str, Any]]:
        """Fetch messages from Discord API."""
        params = {"limit": min(limit, MAX_MESSAGES_PER_REQUEST)}
        if after:
            params["after"] = after

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=self.headers,
                params=params
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = response.json().get("retry_after", 5)
                await asyncio.sleep(retry_after)
                return await self.fetch_messages(channel_id, after, limit)
            elif response.status_code == 401:
                raise ValueError("Invalid user token")
            elif response.status_code == 403:
                raise ValueError("No access to this channel")
            else:
                raise ValueError(f"Discord API error: {response.status_code}")

    def _build_message_url(self, channel_info: Dict, channel_id: str, message_id: str) -> str:
        """Build the Discord message URL based on channel type."""
        channel_type = channel_info.get("type", 0)

        # Type 0 = Guild text channel
        # Type 1 = DM
        # Type 3 = Group DM
        if channel_type in (1, 3):  # DM or Group DM
            return f"https://discord.com/channels/@me/{channel_id}/{message_id}"
        else:  # Guild channel
            guild_id = channel_info.get("guild_id", "@me")
            return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

    def _convert_message(self, msg: Dict, channel_info: Dict, channel_id: str) -> Dict[str, Any]:
        """Convert Discord API message to our storage format."""
        # Parse timestamp
        timestamp_str = msg.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            timestamp_ms = int(dt.timestamp() * 1000)
        except:
            timestamp_ms = int(datetime.utcnow().timestamp() * 1000)

        # Build URL
        url = self._build_message_url(channel_info, channel_id, msg["id"])

        # Get guild_id (None for DMs/Group DMs)
        guild_id = channel_info.get("guild_id")

        return {
            "_id": msg["id"],
            "content": msg.get("content", ""),
            "timestamp": timestamp_ms,
            "url": url,
            "channel": {
                "id": channel_id,
                "name": channel_info.get("name"),
                "type": channel_info.get("type", 0)
            },
            "author": {
                "id": msg.get("author", {}).get("id"),
                "username": msg.get("author", {}).get("username")
            },
            "guild": {
                "id": guild_id
            } if guild_id else None,
            "is_dm": channel_info.get("type") in (1, 3)
        }

    async def import_messages(
        self,
        channel_id: str,
        max_messages: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Import messages from a Discord channel.

        Returns statistics about the import.
        """
        # Get channel info first
        channel_info = await self.get_channel_info(channel_id)
        channel_type = channel_info.get("type", 0)
        channel_type_name = {0: "guild", 1: "dm", 3: "group_dm"}.get(channel_type, "unknown")

        # Get last stored message to resume from
        last_message_id = await self.get_latest_stored_message_id(channel_id)

        messages_imported = 0
        messages_skipped = 0
        oldest_id = None
        newest_id = None

        # Fetch messages in batches
        # Note: Discord returns messages newest-first, but 'after' returns messages newer than the ID
        # So we need to fetch all and reverse, or use 'before' for pagination

        # Strategy: Use 'after' with our last stored ID to get only new messages
        current_after = last_message_id or "0"

        while True:
            if max_messages and messages_imported >= max_messages:
                break

            batch = await self.fetch_messages(channel_id, after=current_after)

            if not batch:
                break

            # Discord returns newest first, reverse to process oldest first
            batch.reverse()

            # Filter out bot messages and empty content
            valid_messages = []
            for msg in batch:
                if msg.get("author", {}).get("bot"):
                    messages_skipped += 1
                    continue
                if not msg.get("content", "").strip():
                    messages_skipped += 1
                    continue
                valid_messages.append(msg)

            if valid_messages:
                # Convert and store
                documents = [
                    self._convert_message(msg, channel_info, channel_id)
                    for msg in valid_messages
                ]

                # Upsert to avoid duplicates
                for doc in documents:
                    await self.collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": doc},
                        upsert=True
                    )

                messages_imported += len(documents)

                # Track range
                if oldest_id is None:
                    oldest_id = documents[0]["_id"]
                newest_id = documents[-1]["_id"]

                # Update cursor for next batch
                current_after = batch[-1]["id"]

            # Rate limit protection
            await asyncio.sleep(RATE_LIMIT_DELAY)

            # If we got fewer than max, we've reached the end
            if len(batch) < MAX_MESSAGES_PER_REQUEST:
                break

        return {
            "channel_id": channel_id,
            "channel_type": channel_type_name,
            "channel_name": channel_info.get("name"),
            "messages_imported": messages_imported,
            "messages_skipped": messages_skipped,
            "resumed_from": last_message_id,
            "oldest_message_id": oldest_id,
            "newest_message_id": newest_id
        }


async def run_import(
    user_token: str,
    channel_id: str,
    max_messages: Optional[int] = None
) -> Dict[str, Any]:
    """
    Run a user token import.

    This is the main entry point for the API endpoint.
    """
    importer = UserTokenImporter(user_token)

    try:
        result = await importer.import_messages(channel_id, max_messages)
        return result
    finally:
        await importer.close()
