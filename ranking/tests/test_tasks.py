"""
Tests for the debounced ranking worker and global merge worker.
Redis is mocked using fakeredis — no real Redis required.
"""

import time

import fakeredis
from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import patch

from categories.models import Category
from engagement.models import EngagementEvent, EngagementType
from posts.models import Post
from ranking.constants import (
    GLOBAL_LEADERBOARD_KEY,
    category_leaderboard_key,
    dirty_posts_key,
    engagement_hash_key,
)
from ranking.tasks import merge_global_leaderboard, recalculate_dirty_scores

User = get_user_model()

# A single shared fakeredis server so all patched calls share the same state
# within one test. A new server is created per test via setUp.
_FAKE_SERVER = None


def _make_fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


class RankingTaskTestCase(TestCase):
    """Base class — patches all Redis access with a fresh fakeredis instance."""

    def setUp(self):
        self.fake_redis = _make_fake_redis()
        # Patch every place that opens a Redis connection in ranking code
        patcher_tasks = patch("ranking.tasks._get_redis", return_value=self.fake_redis)
        patcher_views = patch("ranking.views._get_redis", return_value=self.fake_redis)
        self.addCleanup(patcher_tasks.stop)
        self.addCleanup(patcher_views.stop)
        patcher_tasks.start()
        patcher_views.start()

        self.user = User.objects.create_user(username="rankuser", password="pass")
        self.category = Category.objects.create(name="Technology", slug="technology")
        self.post = Post.objects.create(
            author=self.user, category=self.category, content="Test post"
        )
        self.category2 = Category.objects.create(name="Science", slug="science")
        self.post2 = Post.objects.create(
            author=self.user, category=self.category2, content="Test post 2"
        )


class TestRecalculateDirtyScores(RankingTaskTestCase):

    def test_processes_dirty_posts(self):
        r = self.fake_redis
        r.hset(engagement_hash_key(self.post.pk), mapping={"likes": "5", "comments": "2", "shares": "1"})
        r.zadd(dirty_posts_key(), {str(self.post.pk): time.time() - 10})

        recalculate_dirty_scores()

        self.assertIsNone(r.zscore(dirty_posts_key(), str(self.post.pk)))
        score = r.zscore(category_leaderboard_key(self.post.category.slug), str(self.post.pk))
        self.assertIsNotNone(score)
        self.assertGreater(score, 0)

    def test_ignores_recent_dirty_posts(self):
        r = self.fake_redis
        r.hset(engagement_hash_key(self.post.pk), mapping={"likes": "3", "comments": "0", "shares": "0"})
        r.zadd(dirty_posts_key(), {str(self.post.pk): time.time()})  # too recent

        recalculate_dirty_scores()

        self.assertIsNotNone(r.zscore(dirty_posts_key(), str(self.post.pk)))

    def test_cleans_up_deleted_posts(self):
        r = self.fake_redis
        tmp = Post.objects.create(author=self.user, category=self.category, content="Temp")
        pid = tmp.pk
        tmp.delete()
        r.zadd(dirty_posts_key(), {str(pid): time.time() - 10})

        recalculate_dirty_scores()

        self.assertIsNone(r.zscore(dirty_posts_key(), str(pid)))

    def test_idempotent(self):
        r = self.fake_redis
        r.hset(engagement_hash_key(self.post.pk), mapping={"likes": "5", "comments": "2", "shares": "1"})
        r.zadd(dirty_posts_key(), {str(self.post.pk): time.time() - 10})
        recalculate_dirty_scores()
        score1 = r.zscore(category_leaderboard_key(self.post.category.slug), str(self.post.pk))

        r.zadd(dirty_posts_key(), {str(self.post.pk): time.time() - 10})
        recalculate_dirty_scores()
        score2 = r.zscore(category_leaderboard_key(self.post.category.slug), str(self.post.pk))

        self.assertEqual(score1, score2)


class TestMergeGlobalLeaderboard(RankingTaskTestCase):

    def test_merges_category_leaderboards(self):
        r = self.fake_redis
        r.zadd(category_leaderboard_key(self.post.category.slug), {str(self.post.pk): 50.0})
        r.zadd(category_leaderboard_key(self.post2.category.slug), {str(self.post2.pk): 30.0})

        merge_global_leaderboard()

        self.assertEqual(r.zscore(GLOBAL_LEADERBOARD_KEY, str(self.post.pk)), 50.0)
        self.assertEqual(r.zscore(GLOBAL_LEADERBOARD_KEY, str(self.post2.pk)), 30.0)

    def test_excludes_flagged_posts(self):
        r = self.fake_redis
        self.post.is_flagged = True
        self.post.save()
        r.zadd(category_leaderboard_key(self.post.category.slug), {str(self.post.pk): 50.0})

        merge_global_leaderboard()

        self.assertIsNone(r.zscore(GLOBAL_LEADERBOARD_KEY, str(self.post.pk)))

    def test_empty_categories(self):
        merge_global_leaderboard()
        self.assertEqual(self.fake_redis.zcard(GLOBAL_LEADERBOARD_KEY), 0)

    def test_atomic_replacement(self):
        r = self.fake_redis
        r.zadd(GLOBAL_LEADERBOARD_KEY, {"999": 100.0})  # stale data
        r.zadd(category_leaderboard_key(self.post.category.slug), {str(self.post.pk): 25.0})

        merge_global_leaderboard()

        self.assertIsNone(r.zscore(GLOBAL_LEADERBOARD_KEY, "999"))
        self.assertEqual(r.zscore(GLOBAL_LEADERBOARD_KEY, str(self.post.pk)), 25.0)
