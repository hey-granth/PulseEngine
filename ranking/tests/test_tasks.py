"""
Unit tests for the debounced ranking worker and global merge worker.
"""

import time

import pytest
import redis as redis_lib
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

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


def _get_redis():
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


@pytest.mark.django_db
class TestRecalculateDirtyScores:
    """Tests for the debounced ranking worker."""

    def test_processes_dirty_posts(self, post, redis_client):
        """Posts in the dirty set with old enough timestamps get processed."""
        r = redis_client

        # Set up engagement counters
        r.hset(engagement_hash_key(post.pk), mapping={"likes": "5", "comments": "2", "shares": "1"})

        # Add to dirty set with timestamp in the past (>5s ago)
        r.zadd(dirty_posts_key(), {str(post.pk): time.time() - 10})

        recalculate_dirty_scores()

        # Post should be removed from dirty set
        assert r.zscore(dirty_posts_key(), str(post.pk)) is None

        # Post should appear in category leaderboard
        cat_key = category_leaderboard_key(post.category.slug)
        score = r.zscore(cat_key, str(post.pk))
        assert score is not None
        assert score > 0

    def test_ignores_recent_dirty_posts(self, post, redis_client):
        """Posts that were recently dirtied (< 5s) should NOT be processed."""
        r = redis_client
        r.hset(engagement_hash_key(post.pk), mapping={"likes": "3", "comments": "0", "shares": "0"})
        r.zadd(dirty_posts_key(), {str(post.pk): time.time()})  # Now

        recalculate_dirty_scores()

        # Post should still be in dirty set
        assert r.zscore(dirty_posts_key(), str(post.pk)) is not None

    def test_cleans_up_deleted_posts(self, redis_client, category, user):
        """If a post was deleted, it should be removed from dirty set."""
        r = redis_client
        post = Post.objects.create(author=user, category=category, content="Temp")
        pid = post.pk
        post.delete()

        r.zadd(dirty_posts_key(), {str(pid): time.time() - 10})

        recalculate_dirty_scores()

        assert r.zscore(dirty_posts_key(), str(pid)) is None

    def test_idempotent(self, post, redis_client):
        """Running the worker twice produces the same result."""
        r = redis_client
        r.hset(engagement_hash_key(post.pk), mapping={"likes": "5", "comments": "2", "shares": "1"})
        r.zadd(dirty_posts_key(), {str(post.pk): time.time() - 10})

        recalculate_dirty_scores()

        cat_key = category_leaderboard_key(post.category.slug)
        score1 = r.zscore(cat_key, str(post.pk))

        # Dirty it again with same data
        r.zadd(dirty_posts_key(), {str(post.pk): time.time() - 10})
        recalculate_dirty_scores()

        score2 = r.zscore(cat_key, str(post.pk))
        assert score1 == score2


@pytest.mark.django_db
class TestMergeGlobalLeaderboard:
    """Tests for the global merge worker."""

    def test_merges_category_leaderboards(self, post, post2, redis_client):
        """Posts from different categories appear in global leaderboard."""
        r = redis_client

        # Manually put scores in category leaderboards
        r.zadd(category_leaderboard_key(post.category.slug), {str(post.pk): 50.0})
        r.zadd(category_leaderboard_key(post2.category.slug), {str(post2.pk): 30.0})

        merge_global_leaderboard()

        # Both should appear in global
        score1 = r.zscore(GLOBAL_LEADERBOARD_KEY, str(post.pk))
        score2 = r.zscore(GLOBAL_LEADERBOARD_KEY, str(post2.pk))

        assert score1 == 50.0
        assert score2 == 30.0

    def test_excludes_flagged_posts(self, post, redis_client):
        """Flagged posts should not appear in the global leaderboard."""
        r = redis_client

        post.is_flagged = True
        post.save()

        r.zadd(category_leaderboard_key(post.category.slug), {str(post.pk): 50.0})

        merge_global_leaderboard()

        assert r.zscore(GLOBAL_LEADERBOARD_KEY, str(post.pk)) is None

    def test_empty_categories(self, redis_client, category):
        """Handles empty category leaderboards gracefully."""
        merge_global_leaderboard()

        members = redis_client.zcard(GLOBAL_LEADERBOARD_KEY)
        assert members == 0

    def test_atomic_replacement(self, post, redis_client):
        """Global leaderboard is replaced atomically (old data removed)."""
        r = redis_client

        # Pre-populate global with stale data
        r.zadd(GLOBAL_LEADERBOARD_KEY, {"999": 100.0})

        # Put real data in category
        r.zadd(category_leaderboard_key(post.category.slug), {str(post.pk): 25.0})

        merge_global_leaderboard()

        # Stale entry should be gone
        assert r.zscore(GLOBAL_LEADERBOARD_KEY, "999") is None
        # Real entry should exist
        assert r.zscore(GLOBAL_LEADERBOARD_KEY, str(post.pk)) == 25.0

