"""
Tests for the debounced ranking worker and global merge worker.
Uses real Redis with per-test namespace isolation — no fakeredis, no mocks.
"""

import time

from django.contrib.auth import get_user_model

from categories.models import Category
from posts.models import Post
from ranking.tasks import merge_global_leaderboard, recalculate_dirty_scores
from tests.base import RealRedisTestCase

User = get_user_model()


class RankingTaskTestCase(RealRedisTestCase):
    """Base — provides user, category and posts for ranking tests."""

    def setUp(self):
        super().setUp()
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
        self.r.hset(
            self.engagement_hash_key(self.post.pk),
            mapping={"likes": "5", "comments": "2", "shares": "1"},
        )
        self.r.zadd(self.dirty_posts_key(), {str(self.post.pk): time.time() - 10})

        recalculate_dirty_scores()

        self.assertIsNone(self.r.zscore(self.dirty_posts_key(), str(self.post.pk)))
        score = self.r.zscore(
            self.category_leaderboard_key(self.post.category.slug), str(self.post.pk)
        )
        self.assertIsNotNone(score)
        self.assertGreater(score, 0)

    def test_ignores_recent_dirty_posts(self):
        self.r.hset(
            self.engagement_hash_key(self.post.pk),
            mapping={"likes": "3", "comments": "0", "shares": "0"},
        )
        # Timestamp is NOW — not past the 5-second debounce threshold
        self.r.zadd(self.dirty_posts_key(), {str(self.post.pk): time.time()})

        recalculate_dirty_scores()

        # Should still be in dirty set
        self.assertIsNotNone(self.r.zscore(self.dirty_posts_key(), str(self.post.pk)))

    def test_cleans_up_deleted_posts(self):
        tmp = Post.objects.create(author=self.user, category=self.category, content="Temp")
        pid = tmp.pk
        tmp.delete()
        self.r.zadd(self.dirty_posts_key(), {str(pid): time.time() - 10})

        recalculate_dirty_scores()

        self.assertIsNone(self.r.zscore(self.dirty_posts_key(), str(pid)))

    def test_idempotent(self):
        self.r.hset(
            self.engagement_hash_key(self.post.pk),
            mapping={"likes": "5", "comments": "2", "shares": "1"},
        )
        self.r.zadd(self.dirty_posts_key(), {str(self.post.pk): time.time() - 10})
        recalculate_dirty_scores()
        score1 = self.r.zscore(
            self.category_leaderboard_key(self.post.category.slug), str(self.post.pk)
        )

        self.r.zadd(self.dirty_posts_key(), {str(self.post.pk): time.time() - 10})
        recalculate_dirty_scores()
        score2 = self.r.zscore(
            self.category_leaderboard_key(self.post.category.slug), str(self.post.pk)
        )

        self.assertEqual(score1, score2)


class TestMergeGlobalLeaderboard(RankingTaskTestCase):
    def test_merges_category_leaderboards(self):
        self.r.zadd(
            self.category_leaderboard_key(self.post.category.slug),
            {str(self.post.pk): 50.0},
        )
        self.r.zadd(
            self.category_leaderboard_key(self.post2.category.slug),
            {str(self.post2.pk): 30.0},
        )

        merge_global_leaderboard()

        self.assertEqual(self.r.zscore(self.GLOBAL_LEADERBOARD_KEY, str(self.post.pk)), 50.0)
        self.assertEqual(self.r.zscore(self.GLOBAL_LEADERBOARD_KEY, str(self.post2.pk)), 30.0)

    def test_excludes_flagged_posts(self):
        self.post.is_flagged = True
        self.post.save()
        self.r.zadd(
            self.category_leaderboard_key(self.post.category.slug),
            {str(self.post.pk): 50.0},
        )

        merge_global_leaderboard()

        self.assertIsNone(self.r.zscore(self.GLOBAL_LEADERBOARD_KEY, str(self.post.pk)))

    def test_empty_categories(self):
        merge_global_leaderboard()
        self.assertEqual(self.r.zcard(self.GLOBAL_LEADERBOARD_KEY), 0)

    def test_atomic_replacement(self):
        # Pre-populate global with stale data under the test namespace
        self.r.zadd(self.GLOBAL_LEADERBOARD_KEY, {"999": 100.0})
        self.r.zadd(
            self.category_leaderboard_key(self.post.category.slug),
            {str(self.post.pk): 25.0},
        )

        merge_global_leaderboard()

        self.assertIsNone(self.r.zscore(self.GLOBAL_LEADERBOARD_KEY, "999"))
        self.assertEqual(self.r.zscore(self.GLOBAL_LEADERBOARD_KEY, str(self.post.pk)), 25.0)
