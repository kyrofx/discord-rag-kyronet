"""
User token-based message import for Discord DMs and Group Chats.

WARNING: Using user tokens (selfbots) violates Discord's Terms of Service
and may result in account termination. Use at your own risk.
"""
import os
import json
import httpx
import asyncio
import logging
import redis
from typing import Optional, List, Dict, Any
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

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

        # Redis setup for stats
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)

    async def close(self):
        """Close the MongoDB connection."""
        self.mongo_client.close()

    def _update_stats(self, guild_id: str, channel_id: str, channel_name: str, messages_imported: int, oldest_timestamp: int = None, newest_timestamp: int = None):
        """Update Redis stats after importing messages."""
        if messages_imported == 0:
            return

        stats_key = f"discord_rag:guild:{guild_id}:stats"
        channels_key = f"discord_rag:guild:{guild_id}:channels"

        # Update message count
        self.redis_client.hincrby(stats_key, "total_messages", messages_imported)

        # Update last indexed time
        self.redis_client.hset(stats_key, "last_indexed", datetime.utcnow().isoformat())

        # Update date range if we have timestamps
        if oldest_timestamp:
            current_oldest = self.redis_client.hget(stats_key, "oldest_message")
            if not current_oldest or oldest_timestamp < int(current_oldest):
                self.redis_client.hset(stats_key, "oldest_message", str(oldest_timestamp))

        if newest_timestamp:
            current_newest = self.redis_client.hget(stats_key, "newest_message")
            if not current_newest or newest_timestamp > int(current_newest):
                self.redis_client.hset(stats_key, "newest_message", str(newest_timestamp))

        # Update channel info
        channel_data = self.redis_client.hget(channels_key, channel_id)
        if channel_data:
            try:
                info = json.loads(channel_data)
                info["message_count"] = info.get("message_count", 0) + messages_imported
            except:
                info = {"name": channel_name or channel_id, "message_count": messages_imported}
        else:
            info = {"name": channel_name or channel_id, "message_count": messages_imported}
            # Increment indexed channels count for new channel
            self.redis_client.hincrby(stats_key, "indexed_channels", 1)

        self.redis_client.hset(channels_key, channel_id, json.dumps(info))
        logger.info(f"Updated stats for guild {guild_id}: +{messages_imported} messages")

    async def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get channel information to determine type (DM, Group DM, or Guild)."""
        logger.info(f"Fetching channel info for {channel_id}")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/channels/{channel_id}",
                headers=self.headers
            )

            logger.info(f"Channel info response: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Channel type: {data.get('type')}, name: {data.get('name')}")
                return data
            elif response.status_code == 401:
                logger.error("Invalid user token")
                raise ValueError("Invalid user token")
            elif response.status_code == 403:
                logger.error("No access to this channel")
                raise ValueError("No access to this channel")
            elif response.status_code == 404:
                logger.error("Channel not found")
                raise ValueError("Channel not found")
            else:
                logger.error(f"Discord API error: {response.status_code} - {response.text}")
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
        before: Optional[str] = None,
        limit: int = MAX_MESSAGES_PER_REQUEST
    ) -> List[Dict[str, Any]]:
        """Fetch messages from Discord API."""
        params = {"limit": min(limit, MAX_MESSAGES_PER_REQUEST)}
        if after:
            params["after"] = after
        if before:
            params["before"] = before

        logger.info(f"Fetching messages for channel {channel_id} with params: {params}")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=self.headers,
                params=params
            )

            logger.info(f"Messages response: {response.status_code}")
            if response.status_code == 200:
                messages = response.json()
                logger.info(f"Fetched {len(messages)} messages")
                return messages
            elif response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = response.json().get("retry_after", 5)
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self.fetch_messages(channel_id, after, limit)
            elif response.status_code == 401:
                logger.error("Invalid user token when fetching messages")
                raise ValueError("Invalid user token")
            elif response.status_code == 403:
                logger.error("No access to this channel when fetching messages")
                raise ValueError("No access to this channel")
            else:
                logger.error(f"Discord API error: {response.status_code} - {response.text}")
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
        max_messages: Optional[int] = None,
        guild_id_override: Optional[str] = None,
        full_history: bool = False
    ) -> Dict[str, Any]:
        """
        Import messages from a Discord channel.

        Args:
            channel_id: Discord channel ID to import from
            max_messages: Maximum messages to import
            guild_id_override: Override guild ID for stats (useful for group DMs)
            full_history: If True, fetch all historical messages. If False, only fetch new ones.

        Returns statistics about the import.
        """
        logger.info(f"Starting import for channel {channel_id}, max_messages={max_messages}, full_history={full_history}")

        # Get channel info first
        channel_info = await self.get_channel_info(channel_id)
        channel_type = channel_info.get("type", 0)
        channel_type_name = {0: "guild", 1: "dm", 3: "group_dm"}.get(channel_type, "unknown")
        logger.info(f"Channel type: {channel_type_name}")

        # Get last stored message to resume from (for incremental mode)
        last_message_id = await self.get_latest_stored_message_id(channel_id)
        logger.info(f"Last stored message ID: {last_message_id}")

        messages_imported = 0
        messages_skipped = 0
        oldest_id = None
        newest_id = None
        oldest_timestamp = None
        newest_timestamp = None
        batch_count = 0

        # Determine import mode
        # If full_history=True OR no stored messages, do full historical import using 'before'
        # Otherwise, do incremental import using 'after'
        use_full_history = full_history or (last_message_id is None)
        logger.info(f"Import mode: {'full_history' if use_full_history else 'incremental'}")

        if use_full_history:
            # FULL HISTORY MODE: Use 'before' to paginate backwards through all messages
            current_before = None  # Start from newest

            while True:
                if max_messages and messages_imported >= max_messages:
                    logger.info(f"Reached max_messages limit: {max_messages}")
                    break

                batch = await self.fetch_messages(channel_id, before=current_before)
                batch_count += 1
                logger.info(f"Batch {batch_count}: fetched {len(batch) if batch else 0} messages")

                if not batch:
                    logger.info("Empty batch received, ending import")
                    break

                # Discord returns newest first - process in that order for 'before' pagination
                # Filter out bot messages and empty content
                valid_messages = []
                bot_count = 0
                empty_count = 0
                for msg in batch:
                    if msg.get("author", {}).get("bot"):
                        messages_skipped += 1
                        bot_count += 1
                        continue
                    if not msg.get("content", "").strip():
                        messages_skipped += 1
                        empty_count += 1
                        continue
                    valid_messages.append(msg)

                logger.info(f"Batch {batch_count}: {len(valid_messages)} valid, {bot_count} bots, {empty_count} empty")

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
                    logger.info(f"Stored {len(documents)} messages, total imported: {messages_imported}")

                    # Track range (documents are newest-first)
                    if newest_id is None:
                        newest_id = documents[0]["_id"]
                        newest_timestamp = documents[0]["timestamp"]
                    oldest_id = documents[-1]["_id"]
                    oldest_timestamp = documents[-1]["timestamp"]

                # Update cursor - use the oldest message ID from batch for 'before' pagination
                current_before = batch[-1]["id"]
                logger.info(f"Next cursor: before={current_before}")

                # Rate limit protection
                await asyncio.sleep(RATE_LIMIT_DELAY)

                # If we got fewer than max, we've reached the end
                if len(batch) < MAX_MESSAGES_PER_REQUEST:
                    logger.info(f"Batch had {len(batch)} messages (< {MAX_MESSAGES_PER_REQUEST}), ending import")
                    break
        else:
            # INCREMENTAL MODE: Use 'after' to get only new messages since last import
            current_after = last_message_id
            all_new_messages = []

            # First, collect all new messages
            while True:
                if max_messages and len(all_new_messages) >= max_messages:
                    break

                batch = await self.fetch_messages(channel_id, after=current_after)
                batch_count += 1
                logger.info(f"Batch {batch_count}: fetched {len(batch) if batch else 0} messages")

                if not batch:
                    break

                all_new_messages.extend(batch)

                # Discord returns newest first, so get the newest ID for next pagination
                current_after = batch[0]["id"]

                await asyncio.sleep(RATE_LIMIT_DELAY)

                if len(batch) < MAX_MESSAGES_PER_REQUEST:
                    break

            # Process collected messages (reverse to oldest-first for storage)
            all_new_messages.reverse()

            for msg in all_new_messages:
                if msg.get("author", {}).get("bot"):
                    messages_skipped += 1
                    continue
                if not msg.get("content", "").strip():
                    messages_skipped += 1
                    continue

                doc = self._convert_message(msg, channel_info, channel_id)
                await self.collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": doc},
                    upsert=True
                )
                messages_imported += 1

                if oldest_id is None:
                    oldest_id = doc["_id"]
                    oldest_timestamp = doc["timestamp"]
                newest_id = doc["_id"]
                newest_timestamp = doc["timestamp"]

            logger.info(f"Incremental import complete: {messages_imported} imported, {messages_skipped} skipped")

        logger.info(f"Import complete: {messages_imported} imported, {messages_skipped} skipped")

        # Update Redis stats
        # Use override guild_id if provided, otherwise use channel's guild_id or channel_id
        stats_guild_id = guild_id_override or channel_info.get("guild_id") or channel_id
        self._update_stats(
            guild_id=stats_guild_id,
            channel_id=channel_id,
            channel_name=channel_info.get("name") or f"Channel {channel_id}",
            messages_imported=messages_imported,
            oldest_timestamp=oldest_timestamp,
            newest_timestamp=newest_timestamp
        )

        return {
            "channel_id": channel_id,
            "channel_type": channel_type_name,
            "channel_name": channel_info.get("name"),
            "messages_imported": messages_imported,
            "messages_skipped": messages_skipped,
            "resumed_from": last_message_id if not use_full_history else None,
            "oldest_message_id": oldest_id,
            "newest_message_id": newest_id
        }


async def run_import(
    user_token: str,
    channel_id: str,
    max_messages: Optional[int] = None,
    guild_id_override: Optional[str] = None,
    full_history: bool = False
) -> Dict[str, Any]:
    """
    Run a user token import.

    This is the main entry point for the API endpoint.
    """
    importer = UserTokenImporter(user_token)

    try:
        result = await importer.import_messages(
            channel_id,
            max_messages,
            guild_id_override=guild_id_override,
            full_history=full_history
        )
        return result
    finally:
        await importer.close()
