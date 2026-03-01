"""
Concurrency tests — parallel likes, no duplicates, correct final count.
Uses TransactionTestCase (real DB transactions) and real Redis namespace isolation.
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


class TestConcurrentLikes(RealRedisTransactionTestCase):
    """100 parallel likes — no duplicates, correct final ranking."""

    def test_100_parallel_likes_no_duplicates(self):
        admin = User.objects.create_user(username="admin_conc", password="pass")
        cat = Category.objects.create(name="ConcTest", slug="conctest")
        post = Post.objects.create(author=admin, category=cat, content="Concurrent test")

        users = [
            User.objects.create_user(username=f"concuser{i}", password="pass") for i in range(100)
        ]

        results = {"success": 0, "conflict": 0, "error": 0}

        def like_post(user_obj):
            client = APIClient()
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

        with ThreadPoolExecutor(max_workers=20) as executor:
            for result in as_completed(executor.submit(like_post, u) for u in users):
                results[result.result()] += 1

        self.assertEqual(results["success"], 100, f"Got: {results}")
        self.assertEqual(results["conflict"], 0)
        self.assertEqual(results["error"], 0)
        self.assertEqual(UserPostLike.objects.filter(post=post).count(), 100)
        self.assertEqual(
            EngagementEvent.objects.filter(post=post, type=EngagementType.LIKE).count(), 100
        )
        likes_in_redis = self.r.hget(self.engagement_hash_key(post.pk), "likes")
        self.assertEqual(int(likes_in_redis), 100)
        self.assertIsNotNone(self.r.zscore(self.dirty_posts_key(), str(post.pk)))

    def test_duplicate_likes_from_same_user_concurrent(self):
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
                return "error"
            except Exception:
                return "error"
            finally:
                django_db.close_old_connections()

        with ThreadPoolExecutor(max_workers=10) as executor:
            for result in as_completed(executor.submit(like_post) for _ in range(10)):
                results[result.result()] += 1

        self.assertEqual(results["success"], 1, f"Got: {results}")
        self.assertEqual(results["conflict"], 9)
        self.assertEqual(UserPostLike.objects.filter(post=post, user=user).count(), 1)

    def test_concurrent_likes_ranking_correct(self):
        user = User.objects.create_user(username="rank_conc", password="pass")
        cat = Category.objects.create(name="RankConc", slug="rankconc")
        p1 = Post.objects.create(author=user, category=cat, content="Popular post")
        p2 = Post.objects.create(author=user, category=cat, content="Less popular")

        for i in range(50):
            u = User.objects.create_user(username=f"rc1u{i}", password="pass")
            client = APIClient()
            client.force_authenticate(user=u)
            client.post(f"/posts/{p1.pk}/like/")
            django_db.close_old_connections()

        for i in range(10):
            u = User.objects.create_user(username=f"rc2u{i}", password="pass")
            client = APIClient()
            client.force_authenticate(user=u)
            client.post(f"/posts/{p2.pk}/like/")
            django_db.close_old_connections()

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
