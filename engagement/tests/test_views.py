"""
Tests for engagement endpoints — like enforcement, event creation, Redis updates.
Uses real Redis with per-test namespace isolation — no fakeredis, no mocks.
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from categories.models import Category
from engagement.models import EngagementEvent, EngagementType, UserPostLike
from posts.models import Post
from tests.base import RealRedisTestCase

User = get_user_model()


class EngagementTestCase(RealRedisTestCase):
    """Base — provides user, post, api client and real Redis namespace."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.user2 = User.objects.create_user(username="testuser2", password="pass")
        self.category = Category.objects.create(name="Technology", slug="technology")
        self.post = Post.objects.create(
            author=self.user, category=self.category, content="Test post content."
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class TestLikeEndpoint(EngagementTestCase):
    """Tests for POST /posts/{id}/like/"""

    def test_like_creates_event_and_record(self):
        resp = self.client.post(f"/posts/{self.post.pk}/like/")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["type"], "LIKE")
        self.assertTrue(UserPostLike.objects.filter(post=self.post, user=self.user).exists())
        self.assertTrue(
            EngagementEvent.objects.filter(
                post=self.post, user=self.user, type=EngagementType.LIKE
            ).exists()
        )

    def test_like_updates_redis(self):
        self.client.post(f"/posts/{self.post.pk}/like/")
        self.assertEqual(self.r.hget(self.engagement_hash_key(self.post.pk), "likes"), "1")
        self.assertIsNotNone(self.r.zscore(self.dirty_posts_key(), str(self.post.pk)))

    def test_duplicate_like_returns_409(self):
        self.client.post(f"/posts/{self.post.pk}/like/")
        resp = self.client.post(f"/posts/{self.post.pk}/like/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(UserPostLike.objects.filter(post=self.post).count(), 1)

    def test_different_users_can_like_same_post(self):
        c2 = APIClient()
        c2.force_authenticate(user=self.user2)
        self.assertEqual(self.client.post(f"/posts/{self.post.pk}/like/").status_code, 201)
        self.assertEqual(c2.post(f"/posts/{self.post.pk}/like/").status_code, 201)
        self.assertEqual(UserPostLike.objects.filter(post=self.post).count(), 2)

    def test_like_nonexistent_post_returns_404(self):
        resp = self.client.post("/posts/99999/like/")
        self.assertEqual(resp.status_code, 404)


class TestCommentEndpoint(EngagementTestCase):
    """Tests for POST /posts/{id}/comment/"""

    def test_comment_creates_event(self):
        resp = self.client.post(f"/posts/{self.post.pk}/comment/")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["type"], "COMMENT")
        self.assertTrue(
            EngagementEvent.objects.filter(post=self.post, type=EngagementType.COMMENT).exists()
        )

    def test_comment_updates_redis(self):
        self.client.post(f"/posts/{self.post.pk}/comment/")
        self.assertEqual(self.r.hget(self.engagement_hash_key(self.post.pk), "comments"), "1")

    def test_multiple_comments_allowed(self):
        self.client.post(f"/posts/{self.post.pk}/comment/")
        self.client.post(f"/posts/{self.post.pk}/comment/")
        self.assertEqual(
            EngagementEvent.objects.filter(post=self.post, type=EngagementType.COMMENT).count(),
            2,
        )


class TestShareEndpoint(EngagementTestCase):
    """Tests for POST /posts/{id}/share/"""

    def test_share_creates_event(self):
        resp = self.client.post(f"/posts/{self.post.pk}/share/")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["type"], "SHARE")
        self.assertTrue(
            EngagementEvent.objects.filter(post=self.post, type=EngagementType.SHARE).exists()
        )

    def test_share_updates_redis(self):
        self.client.post(f"/posts/{self.post.pk}/share/")
        self.assertEqual(self.r.hget(self.engagement_hash_key(self.post.pk), "shares"), "1")
