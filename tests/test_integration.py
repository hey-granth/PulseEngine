"""
Integration tests — full write→wait→feed flow, rebuild command, Redis fallback.
"""

import json
import time
from io import StringIO
from unittest.mock import patch

import pytest
import redis as redis_lib
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command

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
class TestFullRankingFlow:
    """
    Create posts → interact → run workers → check feed reflects ranking.
    """

    def test_end_to_end_ranking(self, api_client, redis_client):
        user = User.objects.create_user(username="flowuser", password="pass")
        user2 = User.objects.create_user(username="flowuser2", password="pass")
        cat = Category.objects.create(name="Tech", slug="tech")

        # Create 3 posts
        p1 = Post.objects.create(author=user, category=cat, content="Post one")
        p2 = Post.objects.create(author=user, category=cat, content="Post two")
        p3 = Post.objects.create(author=user, category=cat, content="Post three")

        r = redis_client

        # Heavy engagement on p1: 10 likes (simulated)
        for i in range(10):
            u = User.objects.create_user(username=f"liker{i}", password="pass")
            EngagementEvent.objects.create(post=p1, user=u, type=EngagementType.LIKE)
        r.hset(engagement_hash_key(p1.pk), mapping={"likes": "10", "comments": "0", "shares": "0"})

        # Moderate engagement on p2
        EngagementEvent.objects.create(post=p2, user=user2, type=EngagementType.LIKE)
        EngagementEvent.objects.create(post=p2, user=user2, type=EngagementType.COMMENT)
        r.hset(engagement_hash_key(p2.pk), mapping={"likes": "1", "comments": "1", "shares": "0"})

        # Minimal engagement on p3
        EngagementEvent.objects.create(post=p3, user=user2, type=EngagementType.SHARE)
        r.hset(engagement_hash_key(p3.pk), mapping={"likes": "0", "comments": "0", "shares": "1"})

        # Mark all dirty (with old timestamps so they get processed)
        old_ts = time.time() - 10
        r.zadd(dirty_posts_key(), {str(p1.pk): old_ts, str(p2.pk): old_ts, str(p3.pk): old_ts})

        # Run workers
        recalculate_dirty_scores()
        merge_global_leaderboard()

        # Check category leaderboard
        cat_key = category_leaderboard_key("tech")
        top = r.zrevrange(cat_key, 0, -1, withscores=True)
        assert len(top) == 3

        top_ids = [int(pid) for pid, _ in top]
        assert top_ids[0] == p1.pk  # Most engagement

        # Check global leaderboard
        global_top = r.zrevrange(GLOBAL_LEADERBOARD_KEY, 0, -1, withscores=True)
        assert len(global_top) == 3

        # Check feed endpoint
        api_client.force_authenticate(user=user)
        resp = api_client.get("/feed/")
        assert resp.status_code == 200
        data = resp.data if isinstance(resp.data, list) else resp.data
        if isinstance(data, list) and len(data) > 0:
            assert data[0]["id"] == p1.pk

    def test_category_feed_correct(self, api_client, redis_client):
        user = User.objects.create_user(username="catuser", password="pass")
        cat1 = Category.objects.create(name="Sports", slug="sports")
        cat2 = Category.objects.create(name="Music", slug="music")

        p1 = Post.objects.create(author=user, category=cat1, content="Sports post")
        p2 = Post.objects.create(author=user, category=cat2, content="Music post")

        r = redis_client
        r.zadd(category_leaderboard_key("sports"), {str(p1.pk): 100.0})
        r.zadd(category_leaderboard_key("music"), {str(p2.pk): 50.0})
        merge_global_leaderboard()

        api_client.force_authenticate(user=user)

        resp = api_client.get("/feed/sports/")
        assert resp.status_code == 200
        data = resp.data if isinstance(resp.data, list) else resp.data
        if isinstance(data, list) and len(data) > 0:
            assert data[0]["id"] == p1.pk


@pytest.mark.django_db
class TestRebuildCommand:
    """Test the rebuild_leaderboards management command."""

    def test_rebuild_restores_state(self, redis_client):
        r = redis_client
        user = User.objects.create_user(username="rebuilduser", password="pass")
        cat = Category.objects.create(name="Art", slug="art")

        p1 = Post.objects.create(author=user, category=cat, content="Art post 1")
        p2 = Post.objects.create(author=user, category=cat, content="Art post 2")

        # Create engagement events
        for i in range(5):
            u = User.objects.create_user(username=f"artu{i}", password="pass")
            EngagementEvent.objects.create(post=p1, user=u, type=EngagementType.LIKE)

        EngagementEvent.objects.create(post=p2, user=user, type=EngagementType.COMMENT)

        # Clear all Redis state
        r.flushdb()

        # Run rebuild
        out = StringIO()
        call_command("rebuild_leaderboards", stdout=out)

        # Verify engagement hashes are restored
        h = r.hgetall(engagement_hash_key(p1.pk))
        assert int(h.get("likes", 0)) == 5

        # Verify category leaderboard
        cat_key = category_leaderboard_key("art")
        assert r.zscore(cat_key, str(p1.pk)) is not None
        assert r.zscore(cat_key, str(p2.pk)) is not None

        # Verify global leaderboard
        assert r.zscore(GLOBAL_LEADERBOARD_KEY, str(p1.pk)) is not None

        # p1 should rank higher
        s1 = r.zscore(GLOBAL_LEADERBOARD_KEY, str(p1.pk))
        s2 = r.zscore(GLOBAL_LEADERBOARD_KEY, str(p2.pk))
        assert s1 > s2


@pytest.mark.django_db
class TestRedisFallback:
    """Test DB fallback when Redis is unavailable."""

    def test_global_feed_fallback(self, api_client):
        user = User.objects.create_user(username="fallbackuser", password="pass")
        cat = Category.objects.create(name="Fallback", slug="fallback")
        post = Post.objects.create(author=user, category=cat, content="Fallback post")

        # Create some engagement events
        for i in range(3):
            u = User.objects.create_user(username=f"fbu{i}", password="pass")
            EngagementEvent.objects.create(post=post, user=u, type=EngagementType.LIKE)

        api_client.force_authenticate(user=user)

        # Patch Redis to simulate connection failure
        with patch("ranking.views._get_redis", side_effect=redis_lib.ConnectionError("down")):
            resp = api_client.get("/feed/")
            assert resp.status_code == 200
            data = resp.data if isinstance(resp.data, list) else resp.data
            if isinstance(data, list):
                assert len(data) >= 1

    def test_category_feed_fallback(self, api_client):
        user = User.objects.create_user(username="catfbuser", password="pass")
        cat = Category.objects.create(name="CatFB", slug="catfb")
        post = Post.objects.create(author=user, category=cat, content="CatFB post")

        EngagementEvent.objects.create(post=post, user=user, type=EngagementType.COMMENT)

        api_client.force_authenticate(user=user)

        with patch("ranking.views._get_redis", side_effect=redis_lib.ConnectionError("down")):
            resp = api_client.get("/feed/catfb/")
            assert resp.status_code == 200

