"""
Management command: rebuild_leaderboards

Fully rebuilds all ranking state from the database.
"""

import redis as redis_lib
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from categories.models import Category
from engagement.models import EngagementEvent
from posts.models import Post
from ranking.constants import (
    GLOBAL_LEADERBOARD_KEY,
    category_leaderboard_key,
    dirty_posts_key,
    engagement_hash_key,
)
from ranking.scoring import compute_score


class Command(BaseCommand):
    help = "Rebuild all leaderboards from engagement events in the database."

    def handle(self, *args, **options):
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        now = timezone.now()

        self.stdout.write("Clearing existing ranking keys...")

        # Clear dirty posts
        r.delete(dirty_posts_key())

        # Clear global leaderboard
        r.delete(GLOBAL_LEADERBOARD_KEY)

        # Clear all category leaderboards
        categories = list(Category.objects.all())
        for cat in categories:
            r.delete(category_leaderboard_key(cat.slug))

        # Clear all engagement hashes
        posts = list(
            Post.objects.select_related("category").all()
        )
        for post in posts:
            r.delete(engagement_hash_key(post.pk))

        self.stdout.write(f"Rebuilding engagement counters for {len(posts)} posts...")

        # Aggregate engagement events per post per type
        aggregation = (
            EngagementEvent.objects.values("post_id", "type")
            .annotate(count=Count("id"))
        )

        # Build counters dict: {post_id: {likes: N, comments: N, shares: N}}
        counters = {}
        for row in aggregation:
            pid = row["post_id"]
            if pid not in counters:
                counters[pid] = {"likes": 0, "comments": 0, "shares": 0}
            type_map = {
                "LIKE": "likes",
                "COMMENT": "comments",
                "SHARE": "shares",
            }
            field = type_map.get(row["type"])
            if field:
                counters[pid][field] = row["count"]

        # Write counters to Redis and compute scores
        pipe = r.pipeline()
        category_scores = {}  # {slug: {post_id_str: score}}

        for post in posts:
            pid = post.pk
            c = counters.get(pid, {"likes": 0, "comments": 0, "shares": 0})

            # Set engagement hash
            if any(v > 0 for v in c.values()):
                pipe.hset(
                    engagement_hash_key(pid),
                    mapping={
                        "likes": c["likes"],
                        "comments": c["comments"],
                        "shares": c["shares"],
                    },
                )

            # Compute score
            age_hours = max(1.0, (now - post.created_at).total_seconds() / 3600)
            score = compute_score(c["likes"], c["comments"], c["shares"], age_hours)

            cat_slug = post.category.slug
            if cat_slug not in category_scores:
                category_scores[cat_slug] = {}
            category_scores[cat_slug][str(pid)] = score

        pipe.execute()

        self.stdout.write("Rebuilding category leaderboards...")

        pipe = r.pipeline()
        for slug, scores in category_scores.items():
            if scores:
                pipe.zadd(category_leaderboard_key(slug), scores)
        pipe.execute()

        self.stdout.write("Rebuilding global leaderboard...")

        # Merge top 50 from each category, exclude flagged
        flagged_ids = set(
            Post.objects.filter(is_flagged=True).values_list("pk", flat=True)
        )

        merged = {}
        for slug, scores in category_scores.items():
            # Sort by score descending, take top 50
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:50]
            for pid_str, score in top:
                if int(pid_str) not in flagged_ids:
                    if pid_str not in merged or score > merged[pid_str]:
                        merged[pid_str] = score

        pipe = r.pipeline()
        pipe.delete(GLOBAL_LEADERBOARD_KEY)
        if merged:
            pipe.zadd(GLOBAL_LEADERBOARD_KEY, merged)
        pipe.execute()

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Rebuilt {len(category_scores)} category leaderboards "
                f"and global leaderboard with {len(merged)} posts."
            )
        )

