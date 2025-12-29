"""
Usage statistics tracking for the Discord RAG API.
Stores stats in Redis for persistence across restarts.
"""
import os
import json
import redis
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class QueryStats:
    total_queries: int = 0
    queries_today: int = 0
    queries_this_week: int = 0
    queries_this_month: int = 0
    avg_response_time_ms: float = 0
    avg_sources_per_query: float = 0
    last_query_time: Optional[str] = None
    top_hours: dict = None  # Hour -> count mapping
    error_count: int = 0

    def __post_init__(self):
        if self.top_hours is None:
            self.top_hours = {}


class StatsTracker:
    """Tracks API usage statistics using Redis."""

    STATS_KEY = "discord_rag:stats"
    QUERIES_KEY = "discord_rag:queries"
    HOURLY_KEY = "discord_rag:hourly"

    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self._ensure_stats_exist()

    def _ensure_stats_exist(self):
        """Initialize stats if they don't exist."""
        if not self.redis.exists(self.STATS_KEY):
            self._save_stats(QueryStats())

    def _get_stats(self) -> QueryStats:
        """Load stats from Redis."""
        data = self.redis.get(self.STATS_KEY)
        if data:
            parsed = json.loads(data)
            return QueryStats(**parsed)
        return QueryStats()

    def _save_stats(self, stats: QueryStats):
        """Save stats to Redis."""
        self.redis.set(self.STATS_KEY, json.dumps(asdict(stats)))

    def record_query(self, response_time_ms: float, sources_count: int, success: bool = True):
        """Record a query execution."""
        stats = self._get_stats()
        now = datetime.utcnow()
        hour_key = now.strftime("%H")

        # Update totals
        stats.total_queries += 1
        stats.last_query_time = now.isoformat()

        if not success:
            stats.error_count += 1

        # Update hourly distribution
        if stats.top_hours is None:
            stats.top_hours = {}
        stats.top_hours[hour_key] = stats.top_hours.get(hour_key, 0) + 1

        # Update rolling averages
        n = stats.total_queries
        if n > 0:
            stats.avg_response_time_ms = (
                (stats.avg_response_time_ms * (n - 1) + response_time_ms) / n
            )
            stats.avg_sources_per_query = (
                (stats.avg_sources_per_query * (n - 1) + sources_count) / n
            )
        else:
            stats.avg_response_time_ms = response_time_ms
            stats.avg_sources_per_query = float(sources_count)

        # Record timestamp for time-based queries
        self.redis.zadd(self.QUERIES_KEY, {now.isoformat(): now.timestamp()})

        # Clean up old entries (keep last 30 days)
        cutoff = (now - timedelta(days=30)).timestamp()
        self.redis.zremrangebyscore(self.QUERIES_KEY, "-inf", cutoff)

        self._save_stats(stats)

    def get_stats(self) -> QueryStats:
        """Get current statistics."""
        stats = self._get_stats()
        now = datetime.utcnow()

        # Calculate time-based counts
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = today_start.replace(day=1)

        stats.queries_today = self.redis.zcount(
            self.QUERIES_KEY, today_start.timestamp(), "+inf"
        )
        stats.queries_this_week = self.redis.zcount(
            self.QUERIES_KEY, week_start.timestamp(), "+inf"
        )
        stats.queries_this_month = self.redis.zcount(
            self.QUERIES_KEY, month_start.timestamp(), "+inf"
        )

        return stats

    def get_recent_queries_count(self, hours: int = 24) -> list:
        """Get query counts per hour for the last N hours."""
        now = datetime.utcnow()
        hourly_counts = []

        for i in range(hours, 0, -1):
            hour_start = now - timedelta(hours=i)
            hour_end = now - timedelta(hours=i-1)
            count = self.redis.zcount(
                self.QUERIES_KEY,
                hour_start.timestamp(),
                hour_end.timestamp()
            )
            hourly_counts.append({
                "hour": hour_start.strftime("%H:%M"),
                "count": count
            })

        return hourly_counts

    def reset_stats(self):
        """Reset all statistics."""
        self.redis.delete(self.STATS_KEY)
        self.redis.delete(self.QUERIES_KEY)
        self._ensure_stats_exist()


# Global instance
_tracker: Optional[StatsTracker] = None


def get_stats_tracker() -> StatsTracker:
    """Get or create the global stats tracker."""
    global _tracker
    if _tracker is None:
        _tracker = StatsTracker()
    return _tracker
