"""
Celery tasks for the ranking system.

- recalculate_dirty_scores: runs every 5s via Beat
- merge_global_leaderboard: runs every 10s via Beat
"""

import logging
import time

import redis as redis_lib
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from posts.models import Post
import ranking.constants as rc
from ranking.fraud import check_fraud
from ranking.scoring import compute_score

logger = logging.getLogger(__name__)


def _get_redis():
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


@shared_task(name="ranking.tasks.recalculate_dirty_scores")
def recalculate_dirty_scores():
    """
    Debounced ranking worker.

    Pull posts from dirty_posts where timestamp < now - 5s.
    Read engagement counters.
    Compute score with fraud penalty.
    Update category leaderboard.
    Remove from dirty set.
    """
    r = _get_redis()
    cutoff = time.time() - 5.0

    # Get posts that have been dirty for at least 5 seconds
    dirty_members = r.zrangebyscore(rc.dirty_posts_key(), "-inf", cutoff)
    if not dirty_members:
        return

    post_ids = [int(pid) for pid in dirty_members]

    # Bulk fetch posts with category info
    posts = {p.pk: p for p in Post.objects.select_related("category").filter(pk__in=post_ids)}

    now = timezone.now()
    pipe = r.pipeline()

    for post_id in post_ids:
        post = posts.get(post_id)
        if post is None:
            # Post deleted — clean up
            pipe.zrem(rc.dirty_posts_key(), str(post_id))
            continue

        # Read engagement counters
        counters = r.hgetall(rc.engagement_hash_key(post_id))
        likes = int(counters.get("likes", 0))
        comments = int(counters.get("comments", 0))
        shares = int(counters.get("shares", 0))

        # Compute age
        age_delta = now - post.created_at
        age_hours = max(1.0, age_delta.total_seconds() / 3600)

        # Compute base score
        score = compute_score(likes, comments, shares, age_hours)

        # Apply fraud check
        fraud_multiplier, should_flag = check_fraud(post_id, r)
        score *= fraud_multiplier

        # Update category leaderboard
        cat_key = rc.category_leaderboard_key(post.category.slug)
        pipe.zadd(cat_key, {str(post_id): score})

        # Remove from dirty set
        pipe.zrem(rc.dirty_posts_key(), str(post_id))

    pipe.execute()
    logger.info("Recalculated scores for %d posts.", len(post_ids))


@shared_task(name="ranking.tasks.merge_global_leaderboard")
def merge_global_leaderboard():
    """
    Global merge worker.

    Pull top 50 from each category leaderboard.
    Merge in memory.
    Replace ranking:global atomically.
    Exclude flagged posts.
    """
    r = _get_redis()

    # Find all category leaderboard keys
    from categories.models import Category

    category_slugs = list(Category.objects.values_list("slug", flat=True))

    merged = {}  # post_id_str -> score

    for slug in category_slugs:
        cat_key = rc.category_leaderboard_key(slug)
        # Top 50 from each category (highest scores)
        top_entries = r.zrevrange(cat_key, 0, 49, withscores=True)
        for post_id_str, score in top_entries:
            # Keep highest score if post appears in multiple categories
            if post_id_str not in merged or score > merged[post_id_str]:
                merged[post_id_str] = score

    if not merged:
        # No data — clear global
        r.delete(rc.GLOBAL_LEADERBOARD_KEY)
        return

    # Exclude flagged posts
    post_ids = [int(pid) for pid in merged.keys()]
    flagged_ids = set(
        Post.objects.filter(pk__in=post_ids, is_flagged=True).values_list("pk", flat=True)
    )

    # Build final mapping
    final = {pid_str: score for pid_str, score in merged.items() if int(pid_str) not in flagged_ids}

    # Replace global leaderboard atomically via pipeline
    pipe = r.pipeline()
    pipe.delete(rc.GLOBAL_LEADERBOARD_KEY)
    if final:
        pipe.zadd(rc.GLOBAL_LEADERBOARD_KEY, final)
    pipe.execute()

    logger.info(
        "Merged global leaderboard: %d posts (%d flagged excluded).",
        len(final),
        len(flagged_ids),
    )
