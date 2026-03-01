"""
Integration tests — full write→rank→feed flow, rebuild command, Redis fallback.
All Redis interactions use fakeredis. No real Redis required.
"""

import time
from io import StringIO
from unittest.mock import patch

import fakeredis
import redis as redis_lib
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

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


class IntegrationTestCase(TestCase):
    """Base — patches all Redis access with a shared fakeredis instance per test."""

    def setUp(self):
        self.r = fakeredis.FakeRedis(decode_responses=True)
        for target in (
            "ranking.tasks._get_redis",
            "ranking.views._get_redis",
            "engagement.views._get_redis",
            "ranking.management.commands.rebuild_leaderboards.redis_lib.Redis.from_url",
        ):
            p = patch(target, return_value=self.r)
            self.addCleanup(p.stop)
            p.start()


class TestFullRankingFlow(IntegrationTestCase):
    """
    Create posts → interact → run workers → check feed reflects ranking.
    """

    def test_end_to_end_ranking(self):
        user = User.objects.create_user(username="flowuser", password="pass")
        user2 = User.objects.create_user(username="flowuser2", password="pass")
        cat = Category.objects.create(name="Tech", slug="tech")
        p1 = Post.objects.create(author=user, category=cat, content="Post one")
        p2 = Post.objects.create(author=user, category=cat, content="Post two")
        p3 = Post.objects.create(author=user, category=cat, content="Post three")

        # Simulate heavy engagement on p1
        for i in range(10):
            u = User.objects.create_user(username=f"liker{i}", password="pass")
            EngagementEvent.objects.create(post=p1, user=u, type=EngagementType.LIKE)
        self.r.hset(engagement_hash_key(p1.pk), mapping={"likes": "10", "comments": "0", "shares": "0"})

        EngagementEvent.objects.create(post=p2, user=user2, type=EngagementType.LIKE)
        EngagementEvent.objects.create(post=p2, user=user2, type=EngagementType.COMMENT)
        self.r.hset(engagement_hash_key(p2.pk), mapping={"likes": "1", "comments": "1", "shares": "0"})

        EngagementEvent.objects.create(post=p3, user=user2, type=EngagementType.SHARE)
        self.r.hset(engagement_hash_key(p3.pk), mapping={"likes": "0", "comments": "0", "shares": "1"})

        old_ts = time.time() - 10
        self.r.zadd(dirty_posts_key(), {str(p1.pk): old_ts, str(p2.pk): old_ts, str(p3.pk): old_ts})

        recalculate_dirty_scores()
        merge_global_leaderboard()

        cat_key = category_leaderboard_key("tech")
        top = self.r.zrevrange(cat_key, 0, -1, withscores=True)
        self.assertEqual(len(top), 3)
        self.assertEqual(int(top[0][0]), p1.pk)

        global_top = self.r.zrevrange(GLOBAL_LEADERBOARD_KEY, 0, -1)
        self.assertEqual(len(global_top), 3)

        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/feed/")
        self.assertEqual(resp.status_code, 200)

    def test_category_feed_correct(self):
        user = User.objects.create_user(username="catuser", password="pass")
        cat1 = Category.objects.create(name="Sports", slug="sports")
        cat2 = Category.objects.create(name="Music", slug="music")
        p1 = Post.objects.create(author=user, category=cat1, content="Sports post")
        p2 = Post.objects.create(author=user, category=cat2, content="Music post")

        self.r.zadd(category_leaderboard_key("sports"), {str(p1.pk): 100.0})
        self.r.zadd(category_leaderboard_key("music"), {str(p2.pk): 50.0})
        merge_global_leaderboard()

        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/feed/sports/")
        self.assertEqual(resp.status_code, 200)


class TestRebuildCommand(IntegrationTestCase):
    """Test the rebuild_leaderboards management command."""

    def test_rebuild_restores_state(self):
        user = User.objects.create_user(username="rebuilduser", password="pass")
        cat = Category.objects.create(name="Art", slug="art")
        p1 = Post.objects.create(author=user, category=cat, content="Art post 1")
        p2 = Post.objects.create(author=user, category=cat, content="Art post 2")

        for i in range(5):
            u = User.objects.create_user(username=f"artu{i}", password="pass")
            EngagementEvent.objects.create(post=p1, user=u, type=EngagementType.LIKE)
        EngagementEvent.objects.create(post=p2, user=user, type=EngagementType.COMMENT)

        self.r.flushall()
        call_command("rebuild_leaderboards", stdout=StringIO())

        h = self.r.hgetall(engagement_hash_key(p1.pk))
        self.assertEqual(int(h.get("likes", 0)), 5)

        cat_key = category_leaderboard_key("art")
        self.assertIsNotNone(self.r.zscore(cat_key, str(p1.pk)))
        self.assertIsNotNone(self.r.zscore(cat_key, str(p2.pk)))
        self.assertIsNotNone(self.r.zscore(GLOBAL_LEADERBOARD_KEY, str(p1.pk)))

        s1 = self.r.zscore(GLOBAL_LEADERBOARD_KEY, str(p1.pk))
        s2 = self.r.zscore(GLOBAL_LEADERBOARD_KEY, str(p2.pk))
        self.assertGreater(s1, s2)


class TestRedisFallback(IntegrationTestCase):
    """Test DB fallback when Redis is unavailable."""

    def test_global_feed_fallback(self):
        user = User.objects.create_user(username="fallbackuser", password="pass")
        cat = Category.objects.create(name="Fallback", slug="fallback")
        post = Post.objects.create(author=user, category=cat, content="Fallback post")
        for i in range(3):
            u = User.objects.create_user(username=f"fbu{i}", password="pass")
            EngagementEvent.objects.create(post=post, user=u, type=EngagementType.LIKE)

        client = APIClient()
        client.force_authenticate(user=user)

        with patch("ranking.views._get_redis", side_effect=redis_lib.ConnectionError("down")):
            resp = client.get("/feed/")
            self.assertEqual(resp.status_code, 200)

    def test_category_feed_fallback(self):
        user = User.objects.create_user(username="catfbuser", password="pass")
        cat = Category.objects.create(name="CatFB", slug="catfb")
        post = Post.objects.create(author=user, category=cat, content="CatFB post")
        EngagementEvent.objects.create(post=post, user=user, type=EngagementType.COMMENT)

        client = APIClient()
        client.force_authenticate(user=user)

        with patch("ranking.views._get_redis", side_effect=redis_lib.ConnectionError("down")):
            resp = client.get("/feed/catfb/")
            self.assertEqual(resp.status_code, 200)
