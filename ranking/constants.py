"""
Centralized Redis key templates for the ranking system.
All Redis keys used by the project are defined here.
"""


# ── Engagement counters (HASH) ──────────────────────────────────────────────
# Fields: likes, comments, shares
def engagement_hash_key(post_id: int) -> str:
    return f"post:{post_id}:engagement"


# ── Dirty set (ZSET, score = unix timestamp) ────────────────────────────────
def dirty_posts_key() -> str:
    return "ranking:dirty_posts"


# ── Category leaderboard (ZSET, score = ranking score) ──────────────────────
def category_leaderboard_key(slug: str) -> str:
    return f"ranking:category:{slug}"


# ── Global leaderboard (ZSET, score = ranking score) ────────────────────────
GLOBAL_LEADERBOARD_KEY = "ranking:global"


# ── Feed cache ──────────────────────────────────────────────────────────────
FEED_CACHE_GLOBAL = "cache:feed:global"


def feed_cache_category_key(slug: str) -> str:
    return f"cache:feed:category:{slug}"

