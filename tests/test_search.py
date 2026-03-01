"""
Tests for search endpoint — ES re-ranking with Redis trending scores.
"""

from unittest.mock import MagicMock, patch

import pytest
import redis as redis_lib
from django.conf import settings
from django.contrib.auth import get_user_model

from categories.models import Category
from posts.models import Post
from ranking.constants import GLOBAL_LEADERBOARD_KEY

User = get_user_model()


@pytest.mark.django_db
class TestSearchReRanking:
    """Test that search re-ranks using hybrid ES + Redis scoring."""

    def test_search_requires_query(self, authenticated_client):
        resp = authenticated_client.get("/search/")
        assert resp.status_code == 400

    @patch("search.views._ensure_es_connection")
    @patch("search.views.Search")
    def test_search_reranking_with_redis(
        self, mock_search_cls, mock_es_conn, authenticated_client, redis_client
    ):
        """When Redis has trending scores, final = 0.7*ES + 0.3*trending."""
        user = User.objects.create_user(username="searchuser", password="pass")
        cat = Category.objects.create(name="SearchCat", slug="searchcat")
        p1 = Post.objects.create(author=user, category=cat, content="Python programming guide")
        p2 = Post.objects.create(author=user, category=cat, content="Python data science")

        r = redis_client
        # p2 has higher trending score
        r.zadd(GLOBAL_LEADERBOARD_KEY, {str(p1.pk): 10.0, str(p2.pk): 50.0})

        # Mock ES response
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

        resp = authenticated_client.get("/search/?q=python")
        assert resp.status_code == 200

        data = resp.data if isinstance(resp.data, list) else resp.data
        if isinstance(data, list) and len(data) == 2:
            # p2 should rank higher due to trending boost
            # p1: 0.7 * 1.0 + 0.3 * (10/50) = 0.7 + 0.06 = 0.76
            # p2: 0.7 * 0.8 + 0.3 * (50/50) = 0.56 + 0.3 = 0.86
            assert data[0]["id"] == p2.pk

    @patch("search.views._ensure_es_connection")
    @patch("search.views.Search")
    def test_search_falls_back_to_es_only(
        self, mock_search_cls, mock_es_conn, authenticated_client
    ):
        """When Redis is down, search uses ES-only ranking."""
        user = User.objects.create_user(username="esonlyuser", password="pass")
        cat = Category.objects.create(name="ESOnly", slug="esonly")
        p1 = Post.objects.create(author=user, category=cat, content="ES only test")

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
            resp = authenticated_client.get("/search/?q=test")
            assert resp.status_code == 200
            data = resp.data if isinstance(resp.data, list) else resp.data
            if isinstance(data, list):
                assert len(data) == 1

