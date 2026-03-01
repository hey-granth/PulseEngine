"""
Fraud detection logic — velocity-based.
"""

import time

import redis as redis_lib
from django.conf import settings

from posts.models import Post


def check_fraud(post_id: int, r: redis_lib.Redis | None = None) -> tuple[float, bool]:
    """
    Check engagement velocity for a post over a short window.

    Returns (multiplier, should_flag).
    - multiplier: 1.0 (normal) or 0.5 (suspicious)
    - should_flag: True if extreme velocity detected
    """
    if r is None:
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    window_seconds = 60  # 1-minute window
    now = time.time()
    window_start = now - window_seconds

    # Count recent engagement events using the dirty_posts ZSET
    # But for more accuracy, we count from the DB
    from engagement.models import EngagementEvent
    from django.utils import timezone
    import datetime

    cutoff = timezone.now() - datetime.timedelta(seconds=window_seconds)
    recent_count = EngagementEvent.objects.filter(
        post_id=post_id,
        created_at__gte=cutoff,
    ).count()

    multiplier = 1.0
    should_flag = False

    # Thresholds: >50 events/min = suspicious, >200 = extreme
    if recent_count >= 200:
        multiplier = 0.5
        should_flag = True
        # Flag the post in DB
        Post.objects.filter(pk=post_id).update(is_flagged=True)
    elif recent_count >= 50:
        multiplier = 0.5

    return multiplier, should_flag
