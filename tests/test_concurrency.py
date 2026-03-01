"""
Concurrency tests — parallel likes, no duplicates, correct final count.
Uses TransactionTestCase (real DB transactions) and real Redis namespace isolation.

Worker counts are intentionally small (5 / 3) to stay within Neon's connection
limit and avoid network-latency-induced deadlocks on the remote DB.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django import db as django_db
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from categories.models import Category
from engagement.models import EngagementEvent, EngagementType, UserPostLike
from posts.models import Post
from ranking.tasks import merge_global_leaderboard, recalculate_dirty_scores
from tests.base import RealRedisTransactionTestCase

User = get_user_model()

# HTTP_HOST header required so Django's ALLOWED_HOSTS check passes in threads.
_CLIENT_DEFAULTS = {"SERVER_NAME": "localhost", "HTTP_HOST": "localhost"}


class TestConcurrentLikes(RealRedisTransactionTestCase):
    """Parallel likes — no duplicates, correct final ranking."""

    def test_20_parallel_likes_no_duplicates(self):
        """
        20 distinct users each like the same post concurrently.
        Every request must succeed (201) — no conflicts, no errors.
        Redis counter and DB count must both equal 20.
        """
        admin = User.objects.create_user(username="admin_conc", password="pass")
        cat = Category.objects.create(name="ConcTest", slug="conctest")
        post = Post.objects.create(author=admin, category=cat, content="Concurrent test")

        users = [
            User.objects.create_user(username=f"concuser{i}", password="pass")
            for i in range(20)
        ]

        results = {"success": 0, "conflict": 0, "error": 0}

        def like_post(user_obj):
            client = APIClient(**_CLIENT_DEFAULTS)
            client.force_authenticate(user=user_obj)
            try:
                resp = client.post(f"/posts/{post.pk}/like/")
                if resp.status_code == 201:
                    return "success"
                elif resp.status_code == 409:
                    return "conflict"
                return "error"
            except Exception:
                return "error"
            finally:
                django_db.close_old_connections()

        # 5 workers — keeps Neon connection count well within its limit.
        with ThreadPoolExecutor(max_workers=5) as executor:
            for result in as_completed(executor.submit(like_post, u) for u in users):
                results[result.result()] += 1

        self.assertEqual(results["success"], 20, f"Got: {results}")
        self.assertEqual(results["conflict"], 0)
        self.assertEqual(results["error"], 0)
        self.assertEqual(UserPostLike.objects.filter(post=post).count(), 20)
        self.assertEqual(
            EngagementEvent.objects.filter(post=post, type=EngagementType.LIKE).count(), 20
        )
        likes_in_redis = self.r.hget(self.engagement_hash_key(post.pk), "likes")
        self.assertEqual(int(likes_in_redis), 20)
        self.assertIsNotNone(self.r.zscore(self.dirty_posts_key(), str(post.pk)))

    def test_duplicate_likes_from_same_user_concurrent(self):
        """
        5 concurrent requests from the same user — exactly 1 must succeed,
        the remaining 4 must return 409 Conflict.
        """
        user = User.objects.create_user(username="dupe_conc", password="pass")
        cat = Category.objects.create(name="DupeTest", slug="dupetest")
        post = Post.objects.create(author=user, category=cat, content="Dupe test")

        results = {"success": 0, "conflict": 0, "error": 0}

        def like_post():
            client = APIClient(**_CLIENT_DEFAULTS)
            client.force_authenticate(user=user)
            try:
                resp = client.post(f"/posts/{post.pk}/like/")
                if resp.status_code == 201:
                    return "success"
                elif resp.status_code == 409:
                    return "conflict"
                return "error"
            except Exception:
                return "error"
            finally:
                django_db.close_old_connections()

        # 3 workers — enough to exercise the unique-constraint race without
        # hammering the remote DB.
        with ThreadPoolExecutor(max_workers=3) as executor:
            for result in as_completed(executor.submit(like_post) for _ in range(5)):
                results[result.result()] += 1

        self.assertEqual(results["success"], 1, f"Got: {results}")
        self.assertEqual(results["conflict"], 4)
        self.assertEqual(UserPostLike.objects.filter(post=post, user=user).count(), 1)

    def test_concurrent_likes_ranking_correct(self):
        """
        p1 receives more engagement than p2 via direct model writes (no HTTP
        overhead).  After running the ranking tasks, p1 must score higher.
        """
        user = User.objects.create_user(username="rank_conc", password="pass")
        cat = Category.objects.create(name="RankConc", slug="rankconc")
        p1 = Post.objects.create(author=user, category=cat, content="Popular post")
        p2 = Post.objects.create(author=user, category=cat, content="Less popular")

        # Create engagement directly in DB — avoids 60 sequential HTTP round-trips.
        p1_likers = [
            User.objects.create_user(username=f"rc1u{i}", password="pass")
            for i in range(5)
        ]
        p2_likers = [
            User.objects.create_user(username=f"rc2u{i}", password="pass")
            for i in range(2)
        ]

        for u in p1_likers:
            EngagementEvent.objects.create(post=p1, user=u, type=EngagementType.LIKE)
            UserPostLike.objects.create(post=p1, user=u)
        for u in p2_likers:
            EngagementEvent.objects.create(post=p2, user=u, type=EngagementType.LIKE)
            UserPostLike.objects.create(post=p2, user=u)

        # Write engagement counters and mark posts dirty (past the debounce window).
        self.r.hset(
            self.engagement_hash_key(p1.pk),
            mapping={"likes": str(len(p1_likers)), "comments": "0", "shares": "0"},
        )
        self.r.hset(
            self.engagement_hash_key(p2.pk),
            mapping={"likes": str(len(p2_likers)), "comments": "0", "shares": "0"},
        )
        now = time.time()
        self.r.zadd(
            self.dirty_posts_key(),
            {str(p1.pk): now - 10, str(p2.pk): now - 10},
        )

        recalculate_dirty_scores()
        merge_global_leaderboard()

        cat_key = self.category_leaderboard_key("rankconc")
        s1 = self.r.zscore(cat_key, str(p1.pk))
        s2 = self.r.zscore(cat_key, str(p2.pk))
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        self.assertGreater(s1, s2)
