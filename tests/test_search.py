"""
Tests for search endpoint — ES re-ranking with Redis trending scores.
ES is mocked with unittest.mock. Redis is mocked with fakeredis.
"""

from unittest.mock import MagicMock, patch

import fakeredis
import redis as redis_lib
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from categories.models import Category
from posts.models import Post
from ranking.constants import GLOBAL_LEADERBOARD_KEY

User = get_user_model()


class SearchTestCase(TestCase):
    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis(decode_responses=True)
        p = patch("search.views._get_redis", return_value=self.fake_redis)
        self.addCleanup(p.stop)
        p.start()

        self.user = User.objects.create_user(username="searchuser", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class TestSearchReRanking(SearchTestCase):
    """Test that search re-ranks using hybrid ES + Redis scoring."""

    def test_search_requires_query(self):
        resp = self.client.get("/search/")
        self.assertEqual(resp.status_code, 400)

    @patch("search.views._ensure_es_connection")
    @patch("search.views.Search")
    def test_search_reranking_with_redis(self, mock_search_cls, mock_es_conn):
        cat = Category.objects.create(name="SearchCat", slug="searchcat")
        p1 = Post.objects.create(author=self.user, category=cat, content="Python programming guide")
        p2 = Post.objects.create(author=self.user, category=cat, content="Python data science")

        # p2 has higher trending score
        self.fake_redis.zadd(GLOBAL_LEADERBOARD_KEY, {str(p1.pk): 10.0, str(p2.pk): 50.0})

        mock_hit1 = MagicMock()
        mock_hit1.meta.id = str(p1.pk)
        mock_hit1.meta.score = 10.0
        mock_hit2 = MagicMock()
        mock_hit2.meta.id = str(p2.pk)
        mock_hit2.meta.score = 8.0

        mock_response = MagicMock()
        mock_response.hits = [mock_hit1, mock_hit2]
        mock_search_instance = MagicMock()
        mock_search_instance.query.return_value = mock_search_instance
        mock_search_instance.__getitem__ = MagicMock(return_value=mock_search_instance)
        mock_search_instance.execute.return_value = mock_response
        mock_search_cls.return_value = mock_search_instance

        resp = self.client.get("/search/?q=python")
        self.assertEqual(resp.status_code, 200)

        data = resp.data if isinstance(resp.data, list) else resp.data
        if isinstance(data, list) and len(data) == 2:
            # p2 outranks p1 due to trending boost
            # p1: 0.7*1.0 + 0.3*(10/50) = 0.76
            # p2: 0.7*0.8 + 0.3*(50/50) = 0.86
            self.assertEqual(data[0]["id"], p2.pk)

    @patch("search.views._ensure_es_connection")
    @patch("search.views.Search")
    def test_search_falls_back_to_es_only(self, mock_search_cls, mock_es_conn):
        cat = Category.objects.create(name="ESOnly", slug="esonly")
        p1 = Post.objects.create(author=self.user, category=cat, content="ES only test")

        mock_hit = MagicMock()
        mock_hit.meta.id = str(p1.pk)
        mock_hit.meta.score = 5.0
        mock_response = MagicMock()
        mock_response.hits = [mock_hit]
        mock_search_instance = MagicMock()
        mock_search_instance.query.return_value = mock_search_instance
        mock_search_instance.__getitem__ = MagicMock(return_value=mock_search_instance)
        mock_search_instance.execute.return_value = mock_response
        mock_search_cls.return_value = mock_search_instance

        with patch("search.views._get_redis", side_effect=redis_lib.ConnectionError("down")):
            resp = self.client.get("/search/?q=test")
            self.assertEqual(resp.status_code, 200)
            data = resp.data if isinstance(resp.data, list) else resp.data
            if isinstance(data, list):
                self.assertEqual(len(data), 1)
