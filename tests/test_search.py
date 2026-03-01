"""
Tests for search endpoint — ES indexing and re-ranking with Redis trending scores.
Uses real Elasticsearch (per-test isolated index) and real Redis — no mocks.
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from categories.models import Category
from posts.models import Post
from search.tasks import index_post_to_es
from tests.base import RealESTestCase

User = get_user_model()


class SearchTestCase(RealESTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="searchuser", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class TestSearchRequiresQuery(SearchTestCase):
    def test_search_requires_query(self):
        resp = self.client.get("/search/")
        self.assertEqual(resp.status_code, 400)


class TestSearchIndexAndRetrieve(SearchTestCase):
    """
    Index real posts into the test ES index, then search for them.
    """

    def _index_and_refresh(self, *posts):
        """Index posts via the Celery task (eager) and force ES refresh."""
        for post in posts:
            index_post_to_es(post.pk)
        self.refresh_es_index()

    def test_search_finds_indexed_post(self):
        cat = Category.objects.create(name="SearchCat", slug="searchcat")
        post = Post.objects.create(
            author=self.user, category=cat, content="Python programming guide"
        )
        self._index_and_refresh(post)

        resp = self.client.get("/search/?q=python")
        self.assertEqual(resp.status_code, 200)
        ids = [item["id"] for item in resp.data]
        self.assertIn(post.pk, ids)

    def test_search_reranking_with_redis_trending(self):
        """
        p2 has lower ES relevance but higher trending score.
        Re-ranking should place p2 ahead of p1.
        """
        cat = Category.objects.create(name="RerankCat", slug="rerankchat")
        # Both posts contain the query term; p1 has it more prominently
        p1 = Post.objects.create(
            author=self.user,
            category=cat,
            content="Python programming guide python python python",
        )
        p2 = Post.objects.create(
            author=self.user,
            category=cat,
            content="Python data science tutorial",
        )
        self._index_and_refresh(p1, p2)

        # Give p2 a dominant trending score
        self.r.zadd(self.GLOBAL_LEADERBOARD_KEY, {str(p1.pk): 1.0, str(p2.pk): 9999.0})

        resp = self.client.get("/search/?q=python")
        self.assertEqual(resp.status_code, 200)

        ids = [item["id"] for item in resp.data]
        self.assertIn(p1.pk, ids)
        self.assertIn(p2.pk, ids)
        # p2 should be first due to trending dominance
        self.assertEqual(ids[0], p2.pk)

    def test_search_category_filter(self):
        cat1 = Category.objects.create(name="FilterCat1", slug="filtercat1")
        cat2 = Category.objects.create(name="FilterCat2", slug="filtercat2")
        p1 = Post.objects.create(
            author=self.user, category=cat1, content="Django rest framework tutorial"
        )
        p2 = Post.objects.create(author=self.user, category=cat2, content="Django ORM tutorial")
        self._index_and_refresh(p1, p2)

        resp = self.client.get("/search/?q=django&category=filtercat1")
        self.assertEqual(resp.status_code, 200)
        ids = [item["id"] for item in resp.data]
        self.assertIn(p1.pk, ids)
        self.assertNotIn(p2.pk, ids)

    def test_search_empty_results(self):
        resp = self.client.get("/search/?q=zzznoresultszzz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])


class TestSearchFraudSuppression(SearchTestCase):
    """
    Verify fraud-flagged posts get lower search scores due to
    zero trending contribution.
    """

    def test_flagged_post_absent_from_global_leaderboard(self):
        cat = Category.objects.create(name="FraudCat", slug="fraudcat")
        post = Post.objects.create(author=self.user, category=cat, content="Viral spam content")
        post.is_flagged = True
        post.save()

        # Flagged post should not appear in global leaderboard
        # (merge_global_leaderboard excludes flagged posts)
        from ranking.tasks import merge_global_leaderboard

        self.r.zadd(self.category_leaderboard_key("fraudcat"), {str(post.pk): 500.0})
        merge_global_leaderboard()

        self.assertIsNone(self.r.zscore(self.GLOBAL_LEADERBOARD_KEY, str(post.pk)))
