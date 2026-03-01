"""
Concurrency tests — 100 parallel likes, no duplicates, correct final count.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import redis as redis_lib
from django import db as django_db
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from categories.models import Category
from engagement.models import EngagementEvent, EngagementType, UserPostLike
from posts.models import Post
from ranking.constants import dirty_posts_key, engagement_hash_key
from ranking.tasks import merge_global_leaderboard, recalculate_dirty_scores

User = get_user_model()


@pytest.mark.django_db(transaction=True)
class TestConcurrentLikes:
    """Test 100 parallel likes — no duplicates, correct final ranking."""

    def test_100_parallel_likes_no_duplicates(self):
        # Setup
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.flushdb()

        admin_user = User.objects.create_user(username="admin_conc", password="pass")
        cat = Category.objects.create(name="ConcTest", slug="conctest")
        post = Post.objects.create(author=admin_user, category=cat, content="Concurrent test")

        # Create 100 unique users
        users = []
        for i in range(100):
            u = User.objects.create_user(username=f"concuser{i}", password="pass")
            users.append(u)

        results = {"success": 0, "conflict": 0, "error": 0}

        def like_post(user_obj):
            """Each user likes the post via the API."""
            client = APIClient()
            client.force_authenticate(user=user_obj)
            try:
                resp = client.post(f"/posts/{post.pk}/like/")
                if resp.status_code == 201:
                    return "success"
                elif resp.status_code == 409:
                    return "conflict"
                else:
                    return "error"
            except Exception:
                return "error"
            finally:
                django_db.close_old_connections()
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(like_post, u): u for u in users}
            for future in as_completed(futures):
                result = future.result()
                results[result] += 1

        # All 100 should succeed (unique users)
        assert results["success"] == 100, f"Expected 100 successes, got {results}"
        assert results["conflict"] == 0
        assert results["error"] == 0

        # Verify DB state
        like_count = UserPostLike.objects.filter(post=post).count()
        assert like_count == 100

        event_count = EngagementEvent.objects.filter(
            post=post, type=EngagementType.LIKE
        ).count()
        assert event_count == 100

        # Verify Redis counter
        likes_in_redis = r.hget(engagement_hash_key(post.pk), "likes")
        assert int(likes_in_redis) == 100

        # Verify dirty set has the post
        assert r.zscore(dirty_posts_key(), str(post.pk)) is not None

    def test_duplicate_likes_from_same_user_concurrent(self):
        """Multiple concurrent likes from the same user — only one succeeds."""
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.flushdb()

        user = User.objects.create_user(username="dupe_conc", password="pass")
        cat = Category.objects.create(name="DupeTest", slug="dupetest")
        post = Post.objects.create(author=user, category=cat, content="Dupe test")

        results = {"success": 0, "conflict": 0, "error": 0}

        def like_post():
            client = APIClient()
            client.force_authenticate(user=user)
            try:
                resp = client.post(f"/posts/{post.pk}/like/")
                if resp.status_code == 201:
                    return "success"
                elif resp.status_code == 409:
                    return "conflict"
                else:
                    return "error"
            except Exception:
                return "error"
            finally:
                django_db.close_old_connections()
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(like_post) for _ in range(10)]
            for future in as_completed(futures):
                result = future.result()
                results[result] += 1

        # Exactly one success, rest are conflicts
        assert results["success"] == 1, f"Expected 1 success, got {results}"
        assert results["conflict"] == 9

        # Only one UserPostLike record
        assert UserPostLike.objects.filter(post=post, user=user).count() == 1

    def test_concurrent_likes_ranking_correct(self):
        """After concurrent likes, ranking worker produces correct scores."""
        r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.flushdb()

        user = User.objects.create_user(username="rank_conc", password="pass")
        cat = Category.objects.create(name="RankConc", slug="rankconc")
        p1 = Post.objects.create(author=user, category=cat, content="Popular post")
        p2 = Post.objects.create(author=user, category=cat, content="Less popular")

        # 50 likes on p1, 10 on p2
        for i in range(50):
            u = User.objects.create_user(username=f"rc1u{i}", password="pass")
            client = APIClient()
            client.force_authenticate(user=u)
            client.post(f"/posts/{p1.pk}/like/")

        for i in range(10):
            u = User.objects.create_user(username=f"rc2u{i}", password="pass")
            client = APIClient()
            client.force_authenticate(user=u)
            client.post(f"/posts/{p2.pk}/like/")

        # Make dirty posts old enough
        now = time.time()
        r.zadd(dirty_posts_key(), {str(p1.pk): now - 10, str(p2.pk): now - 10})

        # Run ranking
        recalculate_dirty_scores()
        merge_global_leaderboard()

        from ranking.constants import GLOBAL_LEADERBOARD_KEY, category_leaderboard_key

        cat_key = category_leaderboard_key("rankconc")
        s1 = r.zscore(cat_key, str(p1.pk))
        s2 = r.zscore(cat_key, str(p2.pk))

        assert s1 is not None
        assert s2 is not None
        assert s1 > s2  # p1 should rank higher

